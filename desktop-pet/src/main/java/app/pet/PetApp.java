package app.pet;

import javafx.animation.KeyFrame;
import javafx.animation.Timeline;
import javafx.application.Application;
import javafx.application.Platform;
import javafx.event.ActionEvent;
import javafx.geometry.Rectangle2D;
import javafx.scene.Group;
import javafx.scene.Scene;
import javafx.scene.control.ContextMenu;
import javafx.scene.control.MenuItem;
import javafx.scene.control.SeparatorMenuItem;
import javafx.scene.input.MouseButton;
import javafx.scene.input.MouseEvent;
import javafx.scene.image.Image;
import javafx.scene.layout.Pane;
import javafx.scene.paint.Color;
import javafx.scene.transform.Scale;
import javafx.stage.Screen;
import javafx.stage.Stage;
import javafx.stage.StageStyle;
import javafx.util.Duration;

import java.io.File;
import java.io.IOException;

/**
 * 人鱼桌宠（JavaFX）。
 *
 * <p>架构参考 PC2005-cloud/dsh-pet（MIT）：
 * <ul>
 *   <li>事件动画**不进随机链**，只由事件显式触发；随机链只有 idle / sleeping。</li>
 *   <li>用户交互优先于自动来源；占用锁只冻结自动来源。</li>
 *   <li>切换用双缓冲交叉淡入，不留空白帧。</li>
 *   <li>脚底线统一 —— 宠物始终站在同一条线上。</li>
 * </ul>
 *
 * <p>与 dsh-pet 的关键差异：素材用**图集**而不是 VP9-alpha WebM。
 * JavaFX 的媒体管线没有 alpha 通路，带 alpha 的 WebM 放进去只会得到一块不透明方块。
 *
 * <p><b>缩放必须用显式 {@link Scale} 变换（pivot 在画布原点），不能用 {@code setScaleX/setScaleY}。</b>
 * 后者绕**节点自身包围盒中心**缩放：画布 Group 的包围盒是 (76,0,315,383)（中心在右下），
 * 缩放后整体位移，人鱼会被挤出窗口，只剩一个角 —— 这正是 v1「窗口太小 / 只显示左上角 / 有时隐身」的成因。
 *
 * <p>命令行参数：{@code --height=<px>}（角色在屏幕上的高度，逻辑像素）、
 * {@code --scale=<倍率>}（直接指定缩放倍率）、{@code --x=.. --y=..}（初始位置）、
 * {@code --through}（启动即点击穿透）、{@code --shot=<目录>}（自检：逐态快照并校验内容完整）、
 * {@code --dsh} / {@code --dsh-port=<端口>} / {@code --dsh-pet=<id>}（接入 DSH 宿主真实工作状态）。
 *
 * <p><b>DSH 接入</b>：读 {@code GET /dsh-pet-7340/state} 的 {@code sections.workStatus}
 * （宿主已按 dsh 档位序 {@code waiting > error > working > thinking > result > success} 合并好的
 * 唯一档位），同时消费 {@code pets.<id>.lock}（占用锁）与 {@code pets.<id>.position}（位置指令），
 * 并把实况 {@code POST /pose/report} 回报给宿主。
 */
public final class PetApp extends Application {

    private PetManifest manifest;
    private AtlasPlayer player;
    private StateMachine machine;
    /** DSH 宿主桥接（未启用时为 null）。 */
    private DshBridge dsh;

    private Group canvas;
    /** 主缩放：绕画布原点 (0,0)，画布坐标 → 场景坐标的线性映射。 */
    private Scale baseScale;
    /** 点击挤压：绕脚底线，叠在主缩放之内（先作用于画布坐标）。 */
    private Scale squashScale;
    private Pane root;
    private Scene scene;
    private Stage stage;
    private final ContextMenu menu = new ContextMenu();

    /** 画布 → 场景的缩放倍率；0 表示「未指定，由屏幕自适应」。 */
    private double scale = 0.0;
    /** 角色内容在画布坐标下的高度（各态内容并集高度的最大值）。 */
    private double contentHeight = 1.0;
    /**
     * 基准角色高度（逻辑像素）：**启动时定死**，体型菜单按它的绝对倍率算。
     *
     * <p>必须和「当前高度」分开：菜单项写的是 {@code base * factor}，
     * 如果这里存的是当前值，点一次「紧凑」就把基准污染成 0.72 倍，
     * 后面点「标准」再也回不到原样（连点会连乘，越点越小）。
     */
    private double baseDisplayHeight = 280.0;
    /** 当前角色高度（逻辑像素）：体型菜单改的是它。 */
    private double currentDisplayHeight = 280.0;
    /** 角色在屏幕上的目标高度（逻辑像素）；<=0 表示按屏幕自适应。 */
    private double displayHeight = 0.0;
    /** 拖拽期间暂存的 DSH 档位（交互结束后恢复）。 */
    private DshBridge.TierInfo pendingTier;
    /** 宿主回空闲：当前这段播完就停、然后回落待机（dsh-pet 的「播完即停（回待机）」）。 */
    private boolean stopAfterCurrentLoop;
    /** 位置指令的补间（mode=spring / line 时用）。 */
    private Timeline positionTween;
    /** 拖拽探针的等待计时器。 */
    private Timeline probeWait;

    // 拖拽
    private boolean dragging;
    /** 抓取偏移：光标相对窗口左上角（逻辑像素）。刚体跟随用它算绝对位置，不累积误差。 */
    private double grabDX;
    private double grabDY;
    /** 本次拖拽累计位移（用来区分「点击」和「拖动」）。 */
    private double dragDistance;
    /** 本次拖拽是否已经打过一次「JavaFX 标志位」诊断（避免刷屏）。 */
    private boolean dragDiagLogged;
    /** 本次拖拽是否是**脚本化**的自测拖拽（没有真实鼠标按键，按键态判据要绕开）。 */
    private boolean syntheticDrag;

    public static void main(String[] args) {
        launch(args);
    }

    /**
 * 读原始命令行参数。
 *
 * * <p><b>不能用 {@code getParameters().getUnnamed()}</b>：JavaFX 会把 {@code --key=value}
 * 解析成「命名参数」，放进 {@code getNamed()}，{@code getUnnamed()} 里根本看不到 ——
 * 于是 {@code --scale=} / {@code --height=} / {@code --shot=} 全部静默失效。
 * {@code getRaw()} 才是原样的一串参数。
 */
    private String param(String key) {
        for (String a : getParameters().getRaw()) {
            if (a.startsWith("--" + key + "=")) {
                return a.substring(key.length() + 3);
            }
        }
        String v = getParameters().getNamed().get(key);
        return (v == null || v.isEmpty()) ? null : v;
    }

    private boolean flag(String name) {
        for (String a : getParameters().getRaw()) {
            if (a.equals("--" + name) || a.equals("--" + name + "=true")) {
                return true;
            }
        }
        return false;
    }

    @Override
    public void start(Stage primaryStage) {
        this.stage = primaryStage;
        manifest = PetManifest.load();

        String scaleArg = param("scale");
        String heightArg = param("height");
        if (scaleArg != null) {
            scale = Double.parseDouble(scaleArg);
        }
        if (heightArg != null) {
            displayHeight = Double.parseDouble(heightArg);
        }

        // 角色内容尺寸（画布像素）：取各态内容并集的最大者，作为「显示高度」的换算基准。
        // 注意不能用格子高 fh（那含 padding），也不能用画布高（那含八态并集的空余）。
        for (PetManifest.StateDef d : manifest.states) {
            if (d.bbox != null && d.bbox.length == 4) {
                contentHeight = Math.max(contentHeight, d.bbox[3] - d.bbox[1]);
            }
        }
        if (scale <= 0.0) {
            if (displayHeight > 0.0) {
                baseDisplayHeight = displayHeight;
            } else {
                // 自适应：角色高度取屏幕可视高度的 20%，并夹在 190~340 逻辑像素之间
                double screenH = Screen.getPrimary().getVisualBounds().getHeight();
                baseDisplayHeight = clamp(screenH * 0.20, 190, 340);
            }
            scale = baseDisplayHeight / contentHeight;
        } else {
            baseDisplayHeight = scale * contentHeight;
        }
        // 当前高度 = 基准高度（体型菜单之后两者会分开）
        currentDisplayHeight = baseDisplayHeight;

        canvas = new Group();
        // 关键：显式 Scale + pivot(0,0)。setScaleX/setScaleY 会绕包围盒中心缩放，把内容挤出窗口。
        baseScale = new Scale(scale, scale, 0, 0);
        squashScale = new Scale(1, 1, manifest.canvas.faceCX, manifest.canvas.groundY);
        canvas.getTransforms().add(baseScale);   // 先入列 = 后作用于点（外层）
        canvas.getTransforms().add(squashScale); // 后入列 = 先作用于点（内层，绕脚底）

        root = new Pane(canvas);
        root.setStyle("-fx-background-color: transparent;");
        scene = new Scene(root, manifest.canvas.w * scale, manifest.canvas.h * scale);
        scene.setFill(Color.TRANSPARENT);

        primaryStage.initStyle(StageStyle.TRANSPARENT);
        primaryStage.setTitle("mermaid-desktop-pet-" + System.nanoTime());
        applyStageIcon(primaryStage);
        primaryStage.setScene(scene);
        primaryStage.setAlwaysOnTop(true);
        // 尺寸固定但保留可编程改大小（体型菜单要用），所以用 min=max 而不是 setResizable(false)
        resizeStage(manifest.canvas.w * scale, manifest.canvas.h * scale);

        player = new AtlasPlayer(manifest);
        player.setCanvas(canvas);
        machine = new StateMachine(manifest, this::onStateChanged);
        player.setLoopListener(this::onLoopFinished);

        // 先把 idle 的图集同步加载好，避免开场闪空
        player.attach(PetState.IDLE, canvas);
        player.preload(PetState.IDLE);
        player.switchTo(PetState.IDLE, 0);
        player.start();
        // 后台预热其余图集：切换时不再有解码延迟（否则会看到宠物「隐身」）
        // --lazy 可关闭预热，省约 200MB 内存，代价是首次切到某状态时多等 1~2 秒
        if (!flag("lazy")) {
            player.preloadAll();
        }

        buildMenu();
        installMouseHandlers();

        // 记住 show 之前的前台窗口：桌宠不该把用户的输入焦点抢走（用户可能正在打字）
        com.sun.jna.platform.win32.WinDef.HWND prevForeground = Win32.getForegroundWindow();
        primaryStage.show();
        placeAtBottomRight(primaryStage);
        // 进任务栏/抢焦点这些原生样式必须在 show 之后才有 HWND 可改。
        // （JavaFX 没有公开的 setSkipTaskbar，只能走 Win32 的 WS_EX_TOOLWINDOW）
        applyWindowStyle();
        // 开局就把自己塞进置顶带：DSH 这类直绘窗口会盖过分层窗口，不主动声明就看不见
        Win32.setAlwaysOnTop(Win32.findByTitle(stage.getTitle()));
        Win32.setForegroundWindow(prevForeground);
        startWindowStyleKeeper();

        String shot = param("shot");
        if (shot != null) {
            runSelfCheck(shot);
        }
        if (flag("driftest")) {
            Timeline t = new Timeline(new KeyFrame(Duration.millis(600), e -> runDragTest()));
            t.play();
        }
        if (flag("dsh") || param("dsh-port") != null) {
            startDshBridge();
        }

        if (flag("dshdrag")) {
            runDshDragProbe();
        }

        if (flag("demo")) {
            if (dsh != null || flag("dsh") || param("dsh-port") != null) {
                // 两者互斥：演示器会跟宿主抢状态机，dsh 优先
                System.out.println("[demo] 已启用 DSH 宿主接入，忽略 --demo 模拟器");
            } else {
                // 事件模拟器：没有宿主会话事件源时，用它驱动状态机
                startDemoDriver(machine);
            }
        }
        Platform.runLater(() -> applyClickThrough(flag("through")));
    }

    // ---------------------------------------------------------------- 状态接线

    private void onStateChanged(PetState from, PetState to, StateMachine.Trigger trigger) {
        // 过渡时长以 states.json 的转场表为准：
        //   any → dragging = hard（物理优先，0ms， grabbing 必须瞬时）
        //   dragging → idle = 300ms continue（落地屈膝 + 甩头发；顺带把「头抬高 12px」化掉）
        // 其余没有表项的切换（菜单点播等）用 120ms 交叉淡入。
        int fade = 120;
        PetManifest.Transition t = manifest.transition(from, to);
        if (t != null) {
            fade = "hard".equals(t.strategy) ? 0 : Math.max(0, t.ms);
        } else if (trigger == StateMachine.Trigger.USER) {
            fade = 120;
        }
        // 切到拖拽态时按**脸心**补偿窗口，让头保持在原处（角色是被「拎起来」的，不是自己跳过去）。
        // 不能用内容包围盒角点：idle 的角点是发梢、dragging 的角点是举起的手，差 55px，一按就跳。
        // 脸心 x 八态一致（归一化的硬保证），所以只有 y 需要补。
        if (to == PetState.DRAGGING && from != PetState.DRAGGING) {
            double[] f0 = manifest.state(from).faceCenter;
            double[] f1 = manifest.state(to).faceCenter;
            // 新脸心要落在旧脸心的屏幕位置上 ⇒ 窗口位移 = (旧脸心 − 新脸心) × scale
            double dx = (f0[0] - f1[0]) * scale;
            double dy = (f0[1] - f1[1]) * scale;
            stage.setX(stage.getX() + dx);
            stage.setY(stage.getY() + dy);
            // 抓取偏移同步修正：刚体跟随会用「光标 − 偏移」覆盖窗口位置，
            // 不修正的话这次补偿会被下一帧拖拽事件抹掉。
            if (dragging) {
                grabDX -= dx;
                grabDY -= dy;
            }
        }
        player.switchTo(to, fade);
        System.out.println("[state " + stamp() + "] " + from.id() + " → " + to.id()
                + "  (" + trigger + (trigger == StateMachine.Trigger.EVENT && machine.isEventDriven()
                    ? ", dsh rank=" + machine.currentEventRank() : "") + ")");
    }

    /** 日志时间戳（毫秒）。 */
    private static String stamp() {
        return java.time.LocalTime.now().format(java.time.format.DateTimeFormatter.ofPattern("HH:mm:ss.SSS"));
    }

    /**
     * 一段动画播完后的推进（对齐 dsh-pet 的 handleEnded）。
     *
     * <p>四条路，优先级从高到低：
     * <ol>
     *   <li><b>占用锁</b>：动画链一律不推进（停在最后一帧）。</li>
     *   <li><b>宿主回空闲</b>：当前段播完即停 → 回落待机。</li>
     *   <li><b>DSH 档位驱动中</b>：
     *       <ul>
     *         <li>进行中档位（thinking / working / result / waiting）→ <b>继续循环播</b>，随机链不推进；</li>
     *         <li>终态（success / error）→ <b>播一遍</b>的一次性演出 → 播完回落待机。</li>
     *       </ul>
     *   </li>
     *   <li><b>本地态</b>（随机链 / 菜单点播）：若 DSH 此刻有进行中档位 → 恢复档位循环
     *       （dsh-pet 的 resumeWorkStatusAnim）；否则走随机链。</li>
     * </ol>
     */
    private void onLoopFinished(PetState state) {
        if (dsh != null) {
            System.out.println("[loop " + stamp() + "] " + state.id()
                    + " 播完一轮（eventDriven=" + machine.isEventDriven()
                    + ", terminal=" + machine.isCurrentTerminalEvent() + "）");
        }
        // ① 占用锁：动画链冻结
        if (machine.locked()) {
            return;
        }
        // ② 宿主回空闲：这一段播完就停 → 回落待机
        if (stopAfterCurrentLoop) {
            stopAfterCurrentLoop = false;
            machine.request(PetState.IDLE, StateMachine.Trigger.FALLBACK);
            return;
        }
        // ③ DSH 档位驱动中
        if (machine.isEventDriven()) {
            if (machine.isCurrentTerminalEvent()) {
                // 终态：一次性演出，播完回落待机（对齐 dsh-pet：终态播一遍回 idle）
                machine.request(PetState.IDLE, StateMachine.Trigger.FALLBACK);
            }
            // 进行中档位：继续循环播，随机链不推进
            return;
        }
        // ④ 本地态：DSH 有进行中档位 → 恢复档位循环（resumeWorkStatusAnim）
        DshBridge.TierInfo t = dsh != null ? dsh.currentTier() : null;
        if (t != null && !t.terminal) {
            machine.request(t.state, StateMachine.Trigger.EVENT,
                    new StateMachine.EventPriority(t.rank, t.terminal));
            return;
        }
        PetState next = machine.nextAfterLoop(state);
        machine.request(next, StateMachine.Trigger.CHAIN);
    }

    // ---------------------------------------------------------------- 图标

    /**
     * 设置任务栏图标。
     *
     * <p>图标取自资源 {@code /pet/taskbar-icon.png}（256×256 PNG）。
     * 仓库里**默认不带这个文件** —— 默认那张是第三方卡面素材，不适合再分发；
     * 想换自己的图标，把一张 256×256 的 PNG 放到
     * {@code desktop-pet/src/main/resources/pet/taskbar-icon.png} 即可。
     * 没有这个文件时静默沿用 JavaFX 的默认图标，桌宠照常工作。
     *
     * <p>一个容易踩的点：任务栏上桌宠那一格认的**不是**我们这个
     * {@code WS_EX_TOOLWINDOW} 的舞台窗口，而是 JavaFX 的 toolkit 窗口
     * （{@code GlassToolkitWindowClass}）—— 之前那个灰蓝色机器人图标就是 toolkit 窗口的
     * JavaFX 默认图标。给 stage 设了图标之后任务栏就跟着换了，靠的是 Windows 任务栏
     * 「按进程分组、取组内窗口图标」的规则，不需要额外给 toolkit 窗口发 {@code WM_SETICON}。
     */
    private void applyStageIcon(Stage target) {
        try (java.io.InputStream in = PetApp.class.getResourceAsStream("/pet/taskbar-icon.png")) {
            if (in == null) {
                System.out.println("[icon] 没有 /pet/taskbar-icon.png，沿用默认任务栏图标");
                return;
            }
            Image img = new Image(in);
            if (img.isError()) {
                System.out.println("[icon] 图标解码失败：" + img.getException());
                return;
            }
            target.getIcons().clear();
            target.getIcons().add(img);
            System.out.println("[icon] 任务栏图标已设置（taskbar-icon.png）");
        } catch (Exception e) {
            System.out.println("[icon] 设置任务栏图标失败：" + e);
        }
    }

    // ---------------------------------------------------------------- 交互

    private void installMouseHandlers() {
        root.setOnMousePressed(this::onMousePressed);
        root.setOnMouseDragged(this::onMouseDragged);
        root.setOnMouseReleased(this::onMouseReleased);
        root.setOnMouseClicked(this::onMouseClicked);
        root.setOnContextMenuRequested(e -> {
            menu.show(root, e.getScreenX(), e.getScreenY());
            e.consume();
        });
    }

    private void onMousePressed(MouseEvent e) {
        if (e.getButton() != MouseButton.PRIMARY) {
            return;
        }
        // 只有按在**角色不透明像素**上才算抓住她：窗口四周是透明的，按空白处不该把人鱼拽走
        double canvasX = e.getSceneX() / scale;
        double canvasY = e.getSceneY() / scale;
        if (!player.hitTest(player.current(), player.frame(), canvasX, canvasY)) {
            return;
        }
        beginDrag(e.getScreenX(), e.getScreenY());
        e.consume();
    }

    private void onMouseDragged(MouseEvent e) {
        if (!dragging) {
            return;
        }
        // 拖拽兜底：松手事件**有可能送不到**（鼠标在窗口外松开、系统菜单把鼠标抢走、
        // 别的程序抢走输入）。收不到 release 就会一直卡在 dragging，桌宠跟着光标满屏漂。
        //
        // 判据用 Win32 的 GetAsyncKeyState(VK_LBUTTON) —— **不能用 MouseEvent.isStillSincePress()**：
        // 本机上它对 MOUSE_DRAGGED 一律返回 false，于是拖拽刚起来（~25ms）就被这里判成
        // 「主键已松开」直接 endDrag，表现为「只闪一下 dragging 动画就立刻切回别的动作」。
        // JavaFX 侧的几个标志位在下面单独打一次，用于对账（每次拖拽只打一条）。
        if (!dragDiagLogged) {
            dragDiagLogged = true;
            System.out.println("[drag] 首个 drag 事件：button=" + e.getButton()
                    + " stillSincePress=" + e.isStillSincePress()
                    + " primaryButtonDown=" + e.isPrimaryButtonDown()
                    + " secondaryButtonDown=" + e.isSecondaryButtonDown()
                    + " win32.leftDown=" + Win32.isLeftMouseDown());
        }
        if (!syntheticDrag && !Win32.isLeftMouseDown()) {
            System.out.println("[drag] 检测到主键已松开但未收到 release，自动结束拖拽");
            endDrag();
            return;
        }
        updateDrag(e.getScreenX(), e.getScreenY());
    }

    private void onMouseReleased(MouseEvent e) {
        if (dragging) {
            endDrag();
            e.consume();
        }
    }

    /** 开始拖拽：记下抓取偏移，后续按「光标 − 偏移」刚体跟随。 */
    private void beginDrag(double screenX, double screenY) {
        beginDrag(screenX, screenY, false);
    }

    /**
     * 开始拖拽。
     *
     * @param synthetic true = 脚本化的拖拽自测（{@code --driftest} / {@code --dshdrag}），
     *                  不是用户真的按着鼠标。这种拖拽**没有真实按键**，
     *                  所以不能拿 {@code GetAsyncKeyState} 判「主键还按着吗」——
     *                  否则看门狗会在 1 秒后把脚本化拖拽误判成「松手了」掐掉，
     *                  连带把「拖拽期间档位更新被丢弃」这条断言冲掉。
     */
    private void beginDrag(double screenX, double screenY, boolean synthetic) {
        dragging = true;
        dragDistance = 0;
        dragDiagLogged = false;
        syntheticDrag = synthetic;
        // 刚体跟随：记下光标相对窗口左上角的偏移，之后每帧按「光标 − 偏移」算绝对位置。
        // 增量式（setX(getX()+delta)）会把任何状态切换位移/丢事件累积成漂移。
        grabDX = screenX - stage.getX();
        grabDY = screenY - stage.getY();
        // 用户交互永远优先（对齐 dsh-pet 的「用户交互优先」）
        machine.request(PetState.DRAGGING, StateMachine.Trigger.USER);
    }

    private void updateDrag(double screenX, double screenY) {
        double nx = screenX - grabDX;
        double ny = screenY - grabDY;
        dragDistance += Math.abs(nx - stage.getX()) + Math.abs(ny - stage.getY());
        stage.setX(nx);
        stage.setY(ny);
    }

    private void endDrag() {
        dragging = false;
        syntheticDrag = false;
        // 松手后回到「该回的状态」：DSH 进行中档位 → 恢复档位循环；否则回落待机。
        // 对齐 dsh-pet 的「拖拽松手：workStatus 非终态时恢复状态循环；否则播一遍待机」。
        resumeAfterInteraction();
    }

    private void onMouseClicked(MouseEvent e) {
        // 拖过就不算点击（dsh-pet 用同样的思路区分点与拖）
        if (e.getButton() == MouseButton.PRIMARY && !dragging && dragDistance < 4.0) {
            playSquash();
        }
    }

    /** 点击回应的「Q 弹」挤压（对齐 dsh-pet 的点击反馈）。绕脚底线压，脚不离开地面。 */
    private void playSquash() {
        Timeline tl = new Timeline(
                new KeyFrame(Duration.ZERO, e -> {
                    squashScale.setX(1.07);
                    squashScale.setY(0.93);
                }),
                new KeyFrame(Duration.millis(110), e -> {
                    squashScale.setX(1.0);
                    squashScale.setY(1.0);
                }));
        tl.play();
    }

    // ---------------------------------------------------------------- 菜单

    private void buildMenu() {
        for (PetState s : PetState.values()) {
            MenuItem item = new MenuItem(s.zh() + "（" + s.id() + "）");
            item.setOnAction((ActionEvent ev) ->
                    machine.request(s, StateMachine.Trigger.USER));
            menu.getItems().add(item);
        }
        menu.getItems().add(new SeparatorMenuItem());
        addSizeItem("体型：紧凑", 0.72);
        addSizeItem("体型：标准", 1.0);
        addSizeItem("体型：加大", 1.35);        menu.getItems().add(new SeparatorMenuItem());
        MenuItem through = new MenuItem("切换点击穿透（鼠标穿到底下窗口）");
        through.setOnAction((ActionEvent ev) -> applyClickThrough(!clickThrough));
        menu.getItems().add(through);
        MenuItem lock = new MenuItem("锁定（冻结自动状态切换）");
        lock.setOnAction((ActionEvent ev) -> machine.setLocked(!machine.locked()));
        menu.getItems().add(lock);
        menu.getItems().add(new SeparatorMenuItem());
        MenuItem quit = new MenuItem("退出");
        quit.setOnAction(ev -> {
            // 「正常退出」= 退出码 0。插件的托管器据此区分「用户主动退出」和「崩了」：
            // 前者不再拉起，后者才重拉。否则右键退出后桌宠会自己又冒出来。
            System.out.println("[pet] 用户主动退出（正常退出，退出码 0）");
            Platform.exit();
        });
        menu.getItems().add(quit);
    }

    /** 体型菜单项：按「基准角色高度」的绝对倍率设置，不是相对当前值，避免连点越变越大。 */
    private void addSizeItem(String label, double factor) {
        MenuItem item = new MenuItem(label);
        item.setOnAction(ev -> applyDisplayHeight(baseDisplayHeight * factor));
        // 必须真的加进菜单：漏了这一行，体型三项会**静默消失**（菜单照常弹出，只是少三项）
        menu.getItems().add(item);
    }

    private boolean clickThrough;

    private void applyClickThrough(boolean on) {
        clickThrough = on;
        applyWindowStyle();
    }

    /**
     * 应用 Win32 窗口样式：常驻不进任务栏/Alt-Tab、常驻不抢焦点，点击穿透按开关走。
     *
     * <p>必须走原生样式而不是只靠 {@code setSkipTaskbar}：后者只管任务栏，
     * 而「点它不抢焦点」和「关掉穿透后图标不弹回来」都得改扩展样式。
     */
    private void applyWindowStyle() {
        com.sun.jna.platform.win32.WinDef.HWND hwnd =
                Win32.findByTitle(stage.getTitle());
        Win32.applyWindowStyle(hwnd, clickThrough);
    }

    /**
     * 「常驻桌面」的看门狗。
     *
     * <p>三件事，每秒一次：
     * <ol>
     *   <li><b>样式纠偏</b>：Windows 偶尔把扩展样式重置回去，
     *       {@code WS_EX_TOOLWINDOW} 一掉，任务栏就重新冒出那个通用图标按钮。</li>
     *   <li><b>置顶重申</b>：桌宠是 GDI 分层窗口，DSH 这类 Electron 应用是 DirectComposition
     *       直绘窗口 —— DWM 合成时直绘表面盖过分层窗口，而且任何一次「别的窗口被激活/恢复」
     *       都会把置顶带抢走，桌宠随即整个消失。所以必须真的调 {@code SetWindowPos(HWND_TOPMOST)}
     *       把自己塞回置顶带，光有 {@code WS_EX_TOPMOST} 样式位是不够的。</li>
     *   <li><b>拖拽兜底</b>：主键已经松开、但 release 事件没送达时，
     *       桌宠会一直跟着光标漂。这里读系统层面的真实按键状态把它收回来。</li>
     * </ol>
     * 置顶重申只在「确实被压到前台窗口下面」时才做，避免无谓的 z-order 抖动。
     */
    private void startWindowStyleKeeper() {
        Timeline t = new Timeline(new KeyFrame(Duration.seconds(1), e -> {
            com.sun.jna.platform.win32.WinDef.HWND hwnd =
                    Win32.findByTitle(stage.getTitle());
            if (hwnd == null) {
                return;
            }
            if (Win32.ensureWindowStyle(hwnd, clickThrough)) {
                System.out.println("[win32] 窗口样式被系统改回，已重新设为「不进任务栏 / 不抢焦点」");
            }
            if (Win32.isBehindForeground(hwnd)) {
                Win32.setAlwaysOnTop(hwnd);
            }
            if (dragging && !syntheticDrag && !Win32.isLeftMouseDown()) {
                System.out.println("[drag] 主键已松开但未收到 release，看门狗自动结束拖拽");
                endDrag();
            }
        }));
        t.setCycleCount(Timeline.INDEFINITE);
        t.play();
    }

    // ---------------------------------------------------------------- 尺寸与摆放

    /** 改变体型：更新缩放变换 + 窗口尺寸 + 重新落地。 */
    private void applyScale(double newScale) {
        this.scale = newScale;
        baseScale.setX(newScale);
        baseScale.setY(newScale);
        resizeStage(manifest.canvas.w * newScale, manifest.canvas.h * newScale);
        placeAtBottomRight(stage);
        System.out.println("[size] 应用 scale=" + newScale + " 舞台=" + stage.getWidth() + "x" + stage.getHeight());
    }

    /**
     * 调整窗口尺寸。
     *
     * <p>用 min=max 固定尺寸而不是 {@code setResizable(false)}：
     * 非可缩放窗口下 {@code stage.setWidth()} 会被忽略，改不了体型。
     */
    private void resizeStage(double w, double h) {
        stage.setMinWidth(w);
        stage.setMinHeight(h);
        stage.setMaxWidth(w);
        stage.setMaxHeight(h);
        stage.setWidth(w);
        stage.setHeight(h);
    }

    /** 按「角色在屏幕上的高度」设置体型。 */
    private void applyDisplayHeight(double heightPx) {
        currentDisplayHeight = heightPx;
        System.out.println("[size] 体型切换：基准=" + baseDisplayHeight
                + " 目标=" + heightPx + " scale=" + (heightPx / contentHeight));
        applyScale(heightPx / contentHeight);
    }

    /**
     * 摆到屏幕右下角，**脚底线贴屏幕可视底部**。
     *
     * <p>脚底线在窗口内的 y = canvas.groundY * scale（画布原点即窗口原点），
     * 所以窗口顶边 = 屏幕底 - groundY * scale。
     */
    private void placeAtBottomRight(Stage st) {
        Rectangle2D b = screenOf(st).getVisualBounds();
        double groundInWindow = manifest.canvas.groundY * scale;
        st.setY(b.getMaxY() - groundInWindow);
        // 水平：按**内容**右缘对齐屏幕右缘（画布右侧有透明留白，按画布对齐会偏出去）
        double rightMargin = 24;
        st.setX(b.getMaxX() - rightMargin - contentRight() * scale);
    }

    /** 找出窗口当前所在屏幕（JavaFX 没有 Stage.getScreen()）。 */
    private Screen screenOf(Stage st) {
        double cx = st.getX() + st.getWidth() / 2;
        double cy = st.getY() + st.getHeight() / 2;
        for (Screen s : Screen.getScreens()) {
            Rectangle2D r = s.getVisualBounds();
            if (r.contains(cx, cy)) {
                return s;
            }
        }
        return Screen.getPrimary();
    }

    /** 各态内容在画布坐标下的右缘（格子原点 + padding 起的真实内容宽度）。 */
    private double contentRight() {
        double r = 0;
        for (PetManifest.StateDef d : manifest.states) {
            r = Math.max(r, d.contentOrigin[0] + d.fw - 2.0 * manifest.grid.padding);
        }
        return r;
    }

    private static double clamp(double v, double lo, double hi) {
        return Math.max(lo, Math.min(hi, v));
    }

    // ---------------------------------------------------------------- DSH 宿主接入

    /**
     * 接入 DSH 宿主的真实工作状态。
     *
     * <p>端口来源：{@code --dsh-port=} 显式指定；否则自动发现（独立模式运行状态文件 →
     * 3080..3104 扫描）。DSH 宿主模式（路由挂在 DSH 自己的 web 服务上）端口不在自动发现范围内，
     * 需要显式指定。
     *
     * <p>宠物 id：{@code --dsh-pet=}，默认 {@code main}。
     */
    private void startDshBridge() {
        final String host = "127.0.0.1";
        final String portArg = param("dsh-port");
        final String petIdArg = param("dsh-pet");
        final String petId = (petIdArg == null || petIdArg.isEmpty()) ? "main" : petIdArg;

        // 端口发现放到后台线程：扫描 25 个端口最坏要几十秒，不能卡住 FX 线程（否则窗口白屏几十秒）
        Thread t = new Thread(() -> {
            int port = 0;
            if (portArg != null) {
                try {
                    port = Integer.parseInt(portArg.trim());
                } catch (NumberFormatException e) {
                    System.out.println("[dsh] --dsh-port 解析失败: " + portArg);
                }
            }
            if (port <= 0) {
                System.out.println("[dsh] 正在发现宿主端口（独立模式运行状态文件 → 端口扫描）…");
                port = DshBridge.discoverPort(host);
            }
            if (port <= 0) {
                System.out.println("[dsh] 没找到 dsh-pet 宿主（127.0.0.1:" + DshBridge.DEFAULT_STANDALONE_PORT
                        + "+）。DSH 宿主模式请用 --dsh-port=<端口> 指定。");
                return;
            }
            final int p = port;
            Platform.runLater(() -> attachDsh(host, p, petId));
        }, "dsh-discover");
        t.setDaemon(true);
        t.start();
    }

    /** 真正的桥接装配（在 FX 线程上做）。 */
    private void attachDsh(String host, int port, String petId) {
        DshClient client = new DshClient(host, port, petId);
        dsh = new DshBridge(client, new DshBridge.Listener() {
            @Override
            public void onWorkStatus(DshBridge.TierInfo tier, String task) {
                Platform.runLater(() -> applyDshTier(tier, task));
            }

            @Override
            public void onLock(boolean locked) {
                Platform.runLater(() -> applyDshLock(locked));
            }

            @Override
            public void onPosition(double x, double y, String mode, double durationMs) {
                Platform.runLater(() -> moveToNormalized(x, y, mode, durationMs));
            }
        });
        System.out.println("[dsh] 已接入 " + client.base() + "  pet=" + petId);
        dsh.start();

        // 锁到期自己解除（宿主不推）+ 位置变化上报实况
        Timeline timer = new Timeline(new KeyFrame(Duration.seconds(1), e -> {
            dsh.checkLockExpiry();
            reportPoseIfMoved();
        }));
        timer.setCycleCount(Timeline.INDEFINITE);
        timer.play();
    }

    /** 把 dsh 档位变成状态切换。 */
    private void applyDshTier(DshBridge.TierInfo tier, String task) {
        if (tier == null) {
            // 宿主回空闲：正在播的这一段**播完即停**，然后回落待机
            // （dsh-pet 的「播完即停（回待机）」：不硬切，避免把当前动作砍一半）
            if (machine.isEventDriven()) {
                stopAfterCurrentLoop = true;
                System.out.println("[dsh] 宿主回空闲 → 当前段播完后回落待机");
            }
            return;
        }
        System.out.println("[dsh] 档位 " + tier.name + " (rank " + tier.rank + ")"
                + " → " + tier.state.id()
                + (task == null || task.isEmpty() ? "" : "  任务: " + task));
        if (dragging) {
            // 用户正按着：整拍丢弃，交互结束后恢复（对齐 dsh-pet 的「用户交互优先」）
            pendingTier = tier;
            return;
        }
        machine.request(tier.state, StateMachine.Trigger.EVENT,
                new StateMachine.EventPriority(tier.rank, tier.terminal));
    }

    /** 占用锁：冻结自播与自动事件；显式的用户交互不挡。 */
    private void applyDshLock(boolean locked) {
        machine.setLocked(locked);
        player.setFrozen(locked);
        System.out.println("[dsh] 占用锁 " + (locked ? "生效（冻结自播与自动事件）" : "解除"));
        if (!locked) {
            // 解冻：重新对齐**当前**档位（dsh-pet 的 applyFreeze(false) → resumeWorkStatusAnim）
            DshBridge.TierInfo t = dsh != null ? dsh.currentTier() : null;
            if (t != null && !t.terminal) {
                machine.request(t.state, StateMachine.Trigger.EVENT,
                        new StateMachine.EventPriority(t.rank, t.terminal));
            }
        }
    }

    /** 交互结束后恢复到当前档位（dsh-pet 的 resumeWorkStatusAnim）。 */
    private void resumeAfterInteraction() {
        DshBridge.TierInfo t = pendingTier != null ? pendingTier
                : (dsh != null ? dsh.currentTier() : null);
        pendingTier = null;
        stopAfterCurrentLoop = false;
        if (t != null && !t.terminal) {
            machine.request(t.state, StateMachine.Trigger.EVENT,
                    new StateMachine.EventPriority(t.rank, t.terminal));
        } else {
            machine.request(PetState.IDLE, StateMachine.Trigger.USER);
        }
    }

    /**
     * 宿主下发位置指令：把宠物**中心**放到归一化坐标处。
     *
     * <p>坐标语义（openapi 的 PositionCue）：{@code x}/{@code y} 是宠物中心的归一化坐标，
     * {@code mode} 决定运动方式：{@code instant} 一帧到位 / {@code spring} 过阻尼弹簧追过去 /
     * {@code line} 按 {@code duration} 直线走完。
     */
    private void moveToNormalized(double nx, double ny, String mode, double durationMs) {
        Rectangle2D b = screenOf(stage).getVisualBounds();
        double cx = b.getMinX() + nx * b.getWidth();
        double cy = b.getMinY() + ny * b.getHeight();
        double[] c = contentCenter(player.current());
        // 目标：角色内容中心落在 (cx, cy)
        double tx = cx - c[0] * scale;
        double ty = cy - c[1] * scale;
        double sx = stage.getX();
        double sy = stage.getY();

        if (positionTween != null) {
            positionTween.stop();
            positionTween = null;
        }
        if ("line".equals(mode) || "spring".equals(mode)) {
            // line 用宿主给的 duration（毫秒）；spring 没有 duration，按过阻尼弹簧的观感取 500ms
            long dur = "line".equals(mode)
                    ? Math.max(0L, (long) durationMs)
                    : 500L;
            final double x0 = sx, y0 = sy, dx = tx - sx, dy = ty - sy;
            positionTween = new Timeline(new KeyFrame(Duration.millis(dur), e -> {
                stage.setX(x0 + dx);
                stage.setY(y0 + dy);
            }));
            positionTween.setOnFinished(e -> {
                stage.setX(tx);
                stage.setY(ty);
                reportPose();
            });
            positionTween.play();
        } else {
            stage.setX(tx);
            stage.setY(ty);
            reportPose();
        }
        System.out.printf("[dsh] 位置指令 → 归一化(%.3f,%.3f) mode=%s%n", nx, ny, mode);
    }

    /** 角色内容中心（画布坐标）—— 宿主说的「宠物中心」。 */
    private double[] contentCenter(PetState st) {
        double[] b = manifest.state(st).bbox;
        if (b != null && b.length == 4) {
            return new double[]{(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0};
        }
        return manifest.state(st).faceCenter;
    }

    private double lastPoseX = Double.NaN;
    private double lastPoseY = Double.NaN;

    /** 位置变了才上报（宿主只存内存快照，不写 /state）。 */
    private void reportPoseIfMoved() {
        if (dsh == null) {
            return;
        }
        double[] c = contentCenter(player.current());
        double cx = stage.getX() + c[0] * scale;
        double cy = stage.getY() + c[1] * scale;
        if (Double.isNaN(lastPoseX) || Math.abs(cx - lastPoseX) > 0.5
                || Math.abs(cy - lastPoseY) > 0.5) {
            reportPose();
        }
    }

    private void reportPose() {
        if (dsh == null) {
            return;
        }
        Rectangle2D b = screenOf(stage).getVisualBounds();
        double[] c = contentCenter(player.current());
        double cx = stage.getX() + c[0] * scale;
        double cy = stage.getY() + c[1] * scale;
        double nx = (cx - b.getMinX()) / b.getWidth();
        double ny = (cy - b.getMinY()) / b.getHeight();
        lastPoseX = cx;
        lastPoseY = cy;
        dsh.client().reportPose(nx, ny, "right");
    }

    // ---------------------------------------------------------------- 自检

    /**
     * 拖拽漂移自测（`--driftest`）：**不碰真实鼠标**，用合成坐标走一遍
     * 「按下 → 移动 → 松开」，逐步量「角色脸心相对光标的漂移」。
     *
     * <p>判据：脸心屏幕位移应与光标位移完全一致（漂移 = 0）。
     * 状态切到 dragging 时脸心会换（pose 变了），补偿必须让**屏幕上的脸心**留在原处。
     */
    private void runDragTest() {
        player.preload(PetState.DRAGGING);      // 确保切态不延迟
        PetState s0 = player.current();
        double[] f0 = manifest.state(s0).faceCenter;
        double pressX = stage.getX() + f0[0] * scale;
        double pressY = stage.getY() + f0[1] * scale;
        double baseX = pressX, baseY = pressY;

        System.out.printf("[drift] 起始态=%s 按下点=(%.1f,%.1f)%n", s0.id(), pressX, pressY);
        beginDrag(pressX, pressY, true);
        System.out.println("[drift] 按下后状态=" + player.current()
                + " 命中测试=" + player.hitTest(s0, 0, f0[0], f0[1]));

        double worst = 0;
        for (int i = 1; i <= 12; i++) {
            double cx = pressX + i * 13.7;
            double cy = pressY + i * 7.3;
            updateDrag(cx, cy);
            double[] fc = manifest.state(player.current()).faceCenter;
            double faceX = stage.getX() + fc[0] * scale;
            double faceY = stage.getY() + fc[1] * scale;
            double expX = baseX + (cx - pressX);
            double expY = baseY + (cy - pressY);
            double dx = faceX - expX;
            double dy = faceY - expY;
            worst = Math.max(worst, Math.hypot(dx, dy));
            System.out.printf("[drift] step%2d 光标=(%.1f,%.1f) 脸心=(%.1f,%.1f) 期望=(%.1f,%.1f) 漂移=(%+.2f,%+.2f)%n",
                    i, cx, cy, faceX, faceY, expX, expY, dx, dy);
        }
        endDrag();
        System.out.printf("[drift] 最大漂移 = %.3f px  %s%n", worst,
                worst < 0.5 ? "PASS" : "*** FAIL ***");
        System.out.flush();
        Platform.exit();
    }

    /**
     * DSH 交互自测（{@code --dshdrag}）：脚本化「抓起来 → 期间宿主改档位 → 松手」，
     * 验证 dsh-pet 的两条交互语义：
     * <ul>
     *   <li>拖拽期间宿主改档位 → **整拍丢弃**（不进状态机）；</li>
     *   <li>松手 → 恢复到当前档位（非终态）或回落待机（终态/无档位）。</li>
     * </ul>
     *
     * <p>用合成坐标，不碰真实鼠标；日志里打 READY / DRAG-START / DRAG-END 三个标记供外部脚本对表。
     */
    private void runDshDragProbe() {
        // 等宿主接上（不能用局部变量自引用：lambda 里引用尚未初始化的局部变量不合法）
        probeWait = new Timeline(new KeyFrame(Duration.millis(300), e -> {
            if (dsh != null) {
                probeWait.stop();
                probeScript();
            }
        }));
        probeWait.setCycleCount(Timeline.INDEFINITE);
        probeWait.play();
    }

    /** 探针脚本：抓起来 → 拖 3 秒 → 松手。 */
    private void probeScript() {
        System.out.println("[probe] READY 当前档位="
                + (dsh.currentTier() == null ? "null" : dsh.currentTier().name));
        Timeline t2 = new Timeline(new KeyFrame(Duration.seconds(2), e2 -> {
            PetState s0 = player.current();
            double[] f = manifest.state(s0).faceCenter;
            double px = stage.getX() + f[0] * scale;
            double py = stage.getY() + f[1] * scale;
            beginDrag(px, py, true);
            System.out.println("[probe] DRAG-START 状态=" + player.current().id());
            Timeline t3 = new Timeline(new KeyFrame(Duration.seconds(3), e3 -> {
                endDrag();
                System.out.println("[probe] DRAG-END 状态=" + player.current().id()
                        + " 档位="
                        + (dsh.currentTier() == null ? "null" : dsh.currentTier().name));
                Timeline t4 = new Timeline(new KeyFrame(Duration.seconds(2), e4 -> {
                    System.out.println("[probe] FINAL 状态=" + player.current().id());
                    System.out.flush();
                    Platform.exit();
                }));
                t4.play();
            }));
            t3.play();
        }));
        t2.play();
    }

    /**
     * 自检模式：逐态切过去，把场景渲染成 PNG，并校验「非透明像素 bbox」落在场景内。
     *
     * <p>这正是 v1 的回归点：缩放绕错了中心时，内容 bbox 会跑到窗口外，只剩几十个像素。
     */
    private void runSelfCheck(String dir) {
        final File outDir = new File(dir);
        if (!outDir.exists() && !outDir.mkdirs()) {
            System.out.println("[selfcheck] 无法创建目录: " + dir);
            Platform.exit();
            return;
        }
        final StringBuilder report = new StringBuilder();
        checkNext(PetState.values(), 0, outDir, report);
    }

    /** 自检递归推进：切态 → 等两帧 → 快照 → 下一态。 */
    private void checkNext(PetState[] order, int idx, File outDir, StringBuilder report) {
        if (idx >= order.length) {
            System.out.println("--- 舞台 ---");
            System.out.printf("scale=%.4f  角色高度=%.1fpx  舞台=%.0fx%.0f  位置=(%.0f,%.0f)%n",
                    scale, baseDisplayHeight, stage.getWidth(), stage.getHeight(),
                    stage.getX(), stage.getY());
            System.out.println("--- 汇总 ---");
            System.out.println(report.toString());
            System.out.flush();
            Platform.exit();
            return;
        }
        final PetState st = order[idx];
        player.switchTo(st, 0);
        Timeline wait = new Timeline(new KeyFrame(Duration.millis(300), e -> {
            try {
                String line = checkState(st, outDir);
                report.append(line).append('\n');
                System.out.println(line);
                System.out.flush();
            } catch (Exception ex) {
                report.append(st.id()).append(" 快照失败: ").append(ex).append('\n');
            }
            checkNext(order, idx + 1, outDir, report);
        }));
        wait.play();
    }

    private String checkState(PetState st, File outDir) throws IOException {
        // 用根 Pane + 显式 viewport 快照：Scene.snapshot() 的边界计算不可靠，会卡住
        javafx.scene.SnapshotParameters sp = new javafx.scene.SnapshotParameters();
        sp.setViewport(new Rectangle2D(0, 0, scene.getWidth(), scene.getHeight()));
        javafx.scene.image.WritableImage wi = root.snapshot(sp, null);
        int w = (int) wi.getWidth();
        int h = (int) wi.getHeight();
        javafx.scene.image.PixelReader pr = wi.getPixelReader();

        // ① 几何断言：视图在场景里的实际位置/尺寸必须等于「画布坐标 × scale」
        PetManifest.StateDef d = manifest.state(st);
        javafx.scene.image.ImageView v = player.viewOf(st);
        boolean geomOk = false;
        String geom = "view=null";
        if (v != null) {
            // Node 没有 getBoundsInScene()：用 localToScene 把本地包围盒映射到场景坐标
            javafx.geometry.Bounds b = v.localToScene(v.getBoundsInLocal());
            double ex = d.cellX * scale, ey = d.cellY * scale;
            double ew = d.fw * scale, eh = d.fh * scale;
            geomOk = Math.abs(b.getMinX() - ex) < 0.6
                    && Math.abs(b.getMinY() - ey) < 0.6
                    && Math.abs(b.getWidth() - ew) < 0.6
                    && Math.abs(b.getHeight() - eh) < 0.6
                    && b.getMinX() >= -0.6 && b.getMinY() >= -0.6
                    && b.getMaxX() <= scene.getWidth() + 0.6
                    && b.getMaxY() <= scene.getHeight() + 0.6;
            geom = String.format("scene(%.1f,%.1f %.1fx%.1f) expect(%.1f,%.1f %.1fx%.1f)",
                    b.getMinX(), b.getMinY(), b.getWidth(), b.getHeight(), ex, ey, ew, eh);
        }

        // ② 内容非空：统计不同颜色数（快照背景不透明，不能用 alpha 判空）
        java.util.Set<Integer> colors = new java.util.HashSet<>();
        for (int y = 0; y < h; y += 2) {
            for (int x = 0; x < w; x += 2) {
                colors.add(pr.getArgb(x, y));
            }
        }
        java.awt.image.BufferedImage bi =
                new java.awt.image.BufferedImage(w, h, java.awt.image.BufferedImage.TYPE_INT_ARGB);
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                bi.setRGB(x, y, pr.getArgb(x, y));
            }
        }
        File png = new File(outDir, "shot-" + st.id() + ".png");
        javax.imageio.ImageIO.write(bi, "png", png);

        boolean ok = geomOk && colors.size() > 50;
        return String.format("%-13s %s %s colors=%-6d %s",
                st.id(), geom, geomOk ? "GEOM-OK" : "*** GEOM-FAIL ***",
                colors.size(), ok ? "PASS" : "*** FAIL ***");
    }

    @Override
    public void stop() {
        if (player != null) {
            player.shutdown();
        }
    }

    /**
     * 事件模拟器：独立运行时（没有 DSH 会话事件源）也能看到状态机在工作。
     *
     * <p>序列刻意包含「低优先级打断高优先级」的组合，用来验证优先级判定：
     * thinking(1) → calling_tool(2) → waiting(1，低优先级不能打断 calling_tool) →
     * complete(3) → error(3) → 回落到随机链。
     */
    public static void startDemoDriver(StateMachine machine) {
        Timeline tl = new Timeline(
                new KeyFrame(Duration.seconds(0), e -> machine.request(
                        PetState.THINKING, StateMachine.Trigger.EVENT)),
                new KeyFrame(Duration.seconds(4), e -> machine.request(
                        PetState.CALLING_TOOL, StateMachine.Trigger.EVENT)),
                new KeyFrame(Duration.seconds(7), e -> machine.request(
                        PetState.WAITING, StateMachine.Trigger.EVENT)),
                new KeyFrame(Duration.seconds(10), e -> machine.request(
                        PetState.COMPLETE, StateMachine.Trigger.EVENT)),
                new KeyFrame(Duration.seconds(13), e -> machine.request(
                        PetState.ERROR, StateMachine.Trigger.EVENT)));
        tl.setCycleCount(Timeline.INDEFINITE);
        tl.play();
    }
}
