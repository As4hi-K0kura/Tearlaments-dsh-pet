package app.pet;

import javafx.animation.AnimationTimer;
import javafx.animation.FadeTransition;
import javafx.application.Platform;
import javafx.geometry.Rectangle2D;
import javafx.scene.image.Image;
import javafx.scene.image.ImageView;

import java.io.InputStream;
import java.util.EnumMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 图集播放器：每个状态一张图集纹理，帧切换 = 改 {@link ImageView#setViewport} 的矩形。
 *
 * <p>为什么用图集而不是视频：JavaFX 的媒体管线没有 alpha 通路，
 * 带 alpha 的 VP8/VP9 WebM 放进去会得到一块不透明的方块；
 * 而图集是一张静态 PNG，JavaFX 原生支持 alpha，且**一态一张纹理**——
 * 帧切换零 GPU 重传、零 GC 抖动，比逐帧加载 PNG 序列省一个数量级的显存。
 *
 * <p>代价是显存：一张 idle 图集在 2x 烘焙下约 37MB RGBA。所以图集**按状态懒加载**，
 * 只保留用过的状态；未加载完的状态视图透明、加载完自动显形。
 */
public final class AtlasPlayer {

    private final PetManifest manifest;
    private final Map<PetState, ImageView> views = new EnumMap<>(PetState.class);
    private final Map<PetState, Image> images = new EnumMap<>(PetState.class);
    private final ExecutorService loader = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "pet-atlas-loader");
        t.setDaemon(true);
        return t;
    });

    private PetState current;
    /** 等待图集解码完成后再执行的切换请求。 */
    private PetState pendingState;
    private int pendingFade;
    private int frame;
    private double accumulator;

    /**
     * 交叉淡入淡出（**由帧计时器统一驱动**，不用 {@link FadeTransition}）。
     *
     * <p>为什么不用 FadeTransition：它有两个致命毛病。
     * <ol>
     *   <li>切得快的时候，**两个过渡会同时改同一个节点的 opacity**（旧的淡入还没跑完，
     *       新的淡出又开始），谁最后落地全看时序。</li>
     *   <li>淡出的 {@code setOnFinished(ov -> ov.setVisible(false))} 会在**这个视图已经又被切回来**
     *       之后才触发 —— 于是当前视图被藏掉，桌宠凭空消失，而且不会自己恢复。
     *       DSH 档位在 tool/call 与 tool/result 之间来回跳时，几秒内就能触发。</li>
     * </ol>
     * 自己按帧算透明度就只有一个写者，不可能打架。
     */
    /** 正在显示（淡入中）的视图。 */
    private ImageView showingView;
    /** 正在淡出的视图（淡入结束后立刻藏掉）。 */
    private ImageView fadingView;
    /** 淡入进度 0~1。 */
    private double fadeProgress = 1.0;
    /** 本次淡入的总时长（毫秒）。 */
    private int fadeDurationMs = 1;
    /** 本状态已播完的整轮数（用于驱动随机链）。 */
    private long loops;
    private long lastNanos;
    /**
     * 占用锁冻结：动画**播到最后一帧就停住**，不循环、不回调。
     *
     * <p>对应 dsh-pet 的占用锁语义：「冻结期间动画链一律不推进——事件动画播完就停在最后一帧」。
     */
    private boolean frozen;

    public interface LoopListener {
        void onLoopFinished(PetState state);
    }

    private LoopListener loopListener;

    public AtlasPlayer(PetManifest manifest) {
        this.manifest = manifest;
    }

    public void setLoopListener(LoopListener l) {
        this.loopListener = l;
    }

    public PetState current() {
        return current;
    }

    /** 取某状态的视图节点（自检用：核对它在场景里的实际几何位置）。 */
    public ImageView viewOf(PetState st) {
        return views.get(st);
    }

    /**
     * 像素级命中测试：画布坐标上的点是否落在当前帧的**不透明像素**上。
     *
     * <p>窗口是 391×395 的矩形，但角色只占其中一部分；透明区不该能抓住角色
     * （否则按在窗口空白处会把人鱼拽走）。
     */
    public boolean hitTest(PetState st, int frame, double canvasX, double canvasY) {
        ImageView v = views.get(st);
        if (v == null) {
            return false;
        }
        Image img = v.getImage();
        if (img == null) {
            return false;
        }
        PetManifest.StateDef d = manifest.state(st);
        double lx = canvasX - d.cellX;
        double ly = canvasY - d.cellY;
        if (lx < 0 || ly < 0 || lx >= d.fw || ly >= d.fh) {
            return false;
        }
        int col = Math.floorMod(frame, d.cols);
        int row = frame / d.cols;
        int px = (int) (col * d.fw + lx);
        int py = (int) (row * d.fh + ly);
        if (px < 0 || py < 0 || px >= (int) img.getWidth() || py >= (int) img.getHeight()) {
            return false;
        }
        int argb = img.getPixelReader().getArgb(px, py);
        return ((argb >>> 24) & 0xFF) > 16;
    }

    public int frame() {
        return frame;
    }

    /**
     * 占用锁冻结开关。
     *
     * <p>冻结时：当前动画继续播到最后一帧，然后**停住**（不再循环、不再回调 onLoopFinished），
     * 动画链整体冻结。解冻后从当前帧继续。
     */
    public void setFrozen(boolean frozen) {
        this.frozen = frozen;
    }

    public boolean frozen() {
        return frozen;
    }

    /** 把一个状态的视图挂到画布上（每个状态一个 ImageView，切换时只改视口与透明度）。 */
    public ImageView attach(PetState st, javafx.scene.Group canvas) {
        ImageView v = views.get(st);
        if (v == null) {
            PetManifest.StateDef d = manifest.state(st);
            v = new ImageView();
            v.setFitWidth(d.fw);
            v.setFitHeight(d.fh);
            v.setPreserveRatio(false);
            v.setSmooth(true);
            v.setTranslateX(d.cellX);
            v.setTranslateY(d.cellY);
            v.setOpacity(0);
            v.setVisible(false);
            views.put(st, v);
            canvas.getChildren().add(v);
            loadAsync(st, v);
        }
        return v;
    }

    private void loadAsync(PetState st, ImageView view) {
        loader.submit(() -> {
            Image img = loadImage(manifest.state(st).atlas);
            Platform.runLater(() -> {
                images.put(st, img);
                view.setImage(img);
                if (pendingState == st) {
                    // 之前挂着一次「等图集就绪再切」的请求，现在兑现
                    PetState n = pendingState;
                    int f = pendingFade;
                    pendingState = null;
                    doSwitch(n, view, f);
                } else if (current == st) {
                    applyFrame(view, manifest.state(st), frame);
                }
            });
        });
    }

    /** 后台预热全部图集：状态切换不再有解码延迟（否则会看到宠物「隐身」）。 */
    public void preloadAll() {
        for (PetState s : PetState.values()) {
            if (s != PetState.IDLE && !views.containsKey(s)) {
                attach(s, canvas());
            }
        }
    }

    /** 同步加载（首帧用，避免开场闪空）。 */
    public void preload(PetState st) {
        Image img = loadImage(manifest.state(st).atlas);
        images.put(st, img);
        ImageView v = views.get(st);
        if (v != null) {
            v.setImage(img);
        }
    }

    private Image loadImage(String name) {
        try (InputStream in = AtlasPlayer.class.getResourceAsStream("/pet/" + name)) {
            if (in == null) {
                throw new IllegalStateException("找不到图集资源: /pet/" + name);
            }
            return new Image(in);
        } catch (Exception e) {
            throw new IllegalStateException("加载图集失败: " + name, e);
        }
    }

    /**
     * 切换状态。
     *
     * @param fadeMs 交叉淡入时长；0 = 瞬时硬切（拖拽态按转场表用硬切）
     */
    public void switchTo(PetState next, int fadeMs) {
        if (next == current) {
            return;
        }
        ImageView newView = attach(next, canvas());
        if (newView.getImage() == null) {
            // 图集还在解码：大图集要 1~2 秒。这时**不能**先淡出旧画面，
            // 否则会出现「宠物凭空消失」。保持当前画面，等图集就绪再切。
            pendingState = next;
            pendingFade = fadeMs;
            return;
        }
        doSwitch(next, newView, fadeMs);
    }

    private void doSwitch(PetState next, ImageView newView, int fadeMs) {
        pendingState = null;   // 本次切换已生效，旧的等待请求作废
        ImageView oldView = showingView;
        // 上一轮淡出还没结束就又切：**直接把它收掉**（定格淡出），不要留两个过渡互相打架
        if (fadingView != null) {
            fadingView.setOpacity(0);
            fadingView.setVisible(false);
            fadingView = null;
        }

        showingView = newView;
        frame = 0;
        accumulator = 0;
        newView.setVisible(true);
        newView.toFront();
        applyFrame(newView, manifest.state(next), 0);

        if (fadeMs <= 0) {
            newView.setOpacity(1);
            if (oldView != null && oldView != newView) {
                oldView.setOpacity(0);
                oldView.setVisible(false);
            }
            fadeProgress = 1.0;
        } else {
            newView.setOpacity(0);
            if (oldView != null && oldView != newView) {
                // 从旧视图**当前**的透明度接着淡出（可能上一轮淡出只走了一半）
                oldView.setOpacity(Math.max(0.0, Math.min(1.0, oldView.getOpacity())));
                oldView.toBack();
                fadingView = oldView;
            } else {
                fadingView = null;
            }
            fadeProgress = 0.0;
            fadeDurationMs = Math.max(1, fadeMs);
        }
        current = next;
    }

    private javafx.scene.Group canvasRef;

    private javafx.scene.Group canvas() {
        return canvasRef;
    }

    public void setCanvas(javafx.scene.Group canvas) {
        this.canvasRef = canvas;
    }

    private void applyFrame(ImageView v, PetManifest.StateDef d, int f) {
        int col = f % d.cols;
        int row = f / d.cols;
        v.setViewport(new Rectangle2D(col * d.fw, row * d.fh, d.fw, d.fh));
    }

    /**
     * 帧驱动。
     *
     * <p>用 {@link AnimationTimer} 而不是 Timeline：素材是 12fps，显示器是 60/120Hz，
     * Timeline 的固定 KeyFrame 在 60Hz 下会抖（12 不整除 60）；帧号累加器能把误差摊平。
     */
    public void start() {
        lastNanos = 0;
        AnimationTimer timer = new AnimationTimer() {
            @Override
            public void handle(long now) {
                if (lastNanos == 0) {
                    lastNanos = now;
                    return;
                }
                double dt = (now - lastNanos) / 1_000_000_000.0;
                lastNanos = now;
                if (current == null) {
                    return;
                }
                // 交叉淡入淡出：唯一写者，按帧推进透明度
                if (fadeProgress < 1.0 && showingView != null) {
                    fadeProgress = Math.min(1.0, fadeProgress + dt * 1000.0 / fadeDurationMs);
                    showingView.setOpacity(fadeProgress);
                    if (fadingView != null) {
                        fadingView.setOpacity(1.0 - fadeProgress);
                        if (fadeProgress >= 1.0) {
                            fadingView.setOpacity(0);
                            fadingView.setVisible(false);
                            fadingView = null;
                        }
                    }
                }
                PetManifest.StateDef d = manifest.state(current);
                ImageView v = views.get(current);
                if (v == null || v.getImage() == null) {
                    return;   // 图集还没加载完，这拍不动
                }
                accumulator += dt * d.fps;
                while (accumulator >= 1.0) {
                    accumulator -= 1.0;
                    frame++;
                    if (frame >= d.frames) {
                        if (frozen) {
                            // 冻结：停在最后一帧，不再推进、不回调（动画链整体冻住）
                            frame = d.frames - 1;
                            accumulator = 0;
                            break;
                        }
                        frame = 0;
                        loops++;
                        if (loopListener != null) {
                            loopListener.onLoopFinished(current);
                        }
                    }
                    applyFrame(v, d, frame);
                }
            }
        };
        timer.start();
    }

    public void shutdown() {
        loader.shutdownNow();
    }
}
