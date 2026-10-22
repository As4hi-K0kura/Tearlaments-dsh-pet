package app.pet;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * DSH 宿主桥接：把 dsh-pet 的真实工作状态接进桌宠状态机。
 *
 * <p>数据源：{@code GET http://127.0.0.1:<port>/dsh-pet-7340/state}（长轮询）。
 * 宿主把「会话槽」与「外部槽」按固定优先级合并成一个最终档位，我们读到的就是合并结果。
 *
 * <p><b>档位与优先级用 dsh 的那一套</b>（{@code openapi.yaml} 与
 * {@code host/src/host/work-status.ts} 明写）：
 * <pre>
 *   waiting 60 &gt; error 50 &gt; working 40 &gt; thinking 30 &gt; result 25 &gt; success 20
 * </pre>
 * 本项目 states.json 里那套 priority（complete/error=3、calling_tool=2…）是给
 * **随机链与菜单点播**用的；DSH 事件不套它，改用上面的档位序。
 *
 * <p><b>优先级在宿主侧生效，客户端只跟随</b>：宿主把「会话槽」与「外部槽」按上表合并成一个
 * 最终档位写进 {@code sections.workStatus}，客户端读到的就是合并结果。所以客户端不再自己
 * 比一遍 rank（同一会话的正常递进是档位序**下降**的：等待批准 waiting → 开始干活 working，
 * 再比一次会把宠物永远卡在「等待」上）。
 *
 * <p>状态映射：
 * <pre>
 *   thinking → thinking    working → calling_tool   waiting → waiting
 *   success  → complete    error   → error         result  → thinking（整理=消化结果）
 * </pre>
 *
 * <p>另外消费两样东西：
 * <ul>
 *   <li>{@code pets.<id>.lock} —— 占用锁（是状态不是指令；按 expiresAt 自己定时解除）</li>
 *   <li>{@code pets.<id>.position} —— 宿主下发的运动指令（读到就执行）</li>
 * </ul>
 */
public final class DshBridge {

    /** 一个 dsh 档位。 */
    public static final class TierInfo {
        public final String name;
        public final PetState state;
        /** dsh 档位序（越大越优先）。 */
        public final int rank;
        /** 终态：终态动画是「播一遍」的一次性演出，永远接管。 */
        public final boolean terminal;

        TierInfo(String name, PetState state, int rank, boolean terminal) {
            this.name = name;
            this.state = state;
            this.rank = rank;
            this.terminal = terminal;
        }
    }

    /**
     * 档位表 —— **dsh 宿主的优先级序**，数值与
     * {@code dsh-pet/src/host/work-status.ts} 的 {@code WORK_STATUS_PRIORITY} 逐字对齐：
     *
     * <pre>
     *   waiting 60  &gt;  error 50  &gt;  working 40  &gt;  thinking 30  &gt;  result 25  &gt;  success 20
     * </pre>
     *
     * <p>这张表是**宿主合并多会话时**用的（{@code pickDisplayed}：所有会话里取优先级最高的那条，
     * 同级取最近更新的）。我们从 {@code /state} 读到的 {@code sections.workStatus} 已经是
     * 合并后的唯一档位 —— 优先级在宿主侧生效过一次。
     *
     * <p>{@code result} 档（本项目没有「整理」态）映射到 {@code thinking}：
     * 工具结果回来了、她在消化/整理 —— 与「托腮思考」最接近。
     * 它的档位序(25)低于 thinking(30)与 working(40)，但高于 success(20)，
     * 正是 dsh 的原意：「进行中的过渡态不被别处已完成态压过，防中途庆祝」。
     */
    private static final Map<String, TierInfo> TIERS = new LinkedHashMap<>();

    static {
        TIERS.put("waiting", new TierInfo("waiting", PetState.WAITING, 60, false));
        TIERS.put("error", new TierInfo("error", PetState.ERROR, 50, true));
        TIERS.put("working", new TierInfo("working", PetState.CALLING_TOOL, 40, false));
        TIERS.put("thinking", new TierInfo("thinking", PetState.THINKING, 30, false));
        TIERS.put("result", new TierInfo("result", PetState.THINKING, 25, false));
        TIERS.put("success", new TierInfo("success", PetState.COMPLETE, 20, true));
    }

    public static TierInfo tierOf(String name) {
        return name == null ? null : TIERS.get(name);
    }

    /** 宿主回调（在 FX 线程上被调用）。 */
    public interface Listener {
        /** 工作状态档位变了（{@code tier} 为 null = 回空闲）。 */
        void onWorkStatus(TierInfo tier, String task);

        /** 占用锁状态变了。 */
        void onLock(boolean locked);

        /** 宿主下发位置指令（归一化坐标）。 */
        void onPosition(double x, double y, String mode, double durationMs);
    }

    private final DshClient client;
    private final Listener listener;
    private final java.util.concurrent.atomic.AtomicBoolean running = new java.util.concurrent.atomic.AtomicBoolean();
    private volatile Thread thread;

    /** 当前档位（宿主发来的最后���个），用于「交互结束后恢复」与「终态播完回落」。 */
    private volatile TierInfo currentTier;
    private volatile String currentTask;
    /** 占用锁到期时间（毫秒）；0 = 无锁。 */
    private volatile long lockExpiresAt;
    /** 上一次通知给渲染端的锁状态（只在变化时通知）。 */
    private volatile boolean lastLockState;

    public DshBridge(DshClient client, Listener listener) {
        this.client = client;
        this.listener = listener;
    }

    public DshClient client() {
        return client;
    }

    public TierInfo currentTier() {
        return currentTier;
    }

    /** 启动轮询线程。 */
    public void start() {
        if (!running.compareAndSet(false, true)) {
            return;
        }
        thread = new Thread(this::loop, "dsh-poll");
        thread.setDaemon(true);
        thread.start();
    }

    public void stop() {
        running.set(false);
        if (thread != null) {
            thread.interrupt();
        }
    }

    private void loop() {
        long since = 0;
        long lastWorkCounter = -1;
        long lastLockCounter = -1;
        long lastPosCounter = -1;
        int failures = 0;
        while (running.get()) {
            try {
                DshClient.State st = client.poll(since, 8000);
                failures = 0;
                since = st.counter;

                DshClient.Leaf work = st.sections.workStatus;
                if (work != null && work.counter != lastWorkCounter) {
                    lastWorkCounter = work.counter;
                    applyWorkStatus(work.data);
                }

                DshClient.Leaf lock = DshClient.leafOf(st, client.petId(), "lock");
                if (lock != null && lock.counter != lastLockCounter) {
                    lastLockCounter = lock.counter;
                    applyLock(lock.data);
                }

                DshClient.Leaf pos = DshClient.leafOf(st, client.petId(), "position");
                if (pos != null && pos.counter != lastPosCounter) {
                    lastPosCounter = pos.counter;
                    applyPosition(pos.data);
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            } catch (Exception e) {
                failures++;
                long backoff = Math.min(5000L, 300L * failures);
                try {
                    Thread.sleep(backoff);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }
    }

    private void applyWorkStatus(JsonElement data) {
        String name = null;
        String task = null;
        if (data != null && data.isJsonObject()) {
            JsonObject o = data.getAsJsonObject();
            if (o.has("state") && !o.get("state").isJsonNull()) {
                name = o.get("state").getAsString();
            }
            if (o.has("task") && !o.get("task").isJsonNull()) {
                task = o.get("task").getAsString();
            }
        }
        TierInfo tier = tierOf(name);
        currentTier = tier;
        currentTask = task;
        listener.onWorkStatus(tier, task);
    }

    /**
     * 占用锁：{@code {locked: true, expiresAt: <ms>}}。
     *
     * <p>{@code expiresAt} 是唯一真相 —— 宿主不会到点推一次「锁没了」，渲染端据此自己定时解除。
     */
    private void applyLock(JsonElement data) {
        boolean locked = false;
        long expires = 0;
        if (data != null && data.isJsonObject()) {
            JsonObject o = data.getAsJsonObject();
            if (o.has("locked") && o.get("locked").isJsonPrimitive()) {
                locked = o.get("locked").getAsBoolean();
            }
            if (o.has("expiresAt") && o.get("expiresAt").isJsonPrimitive()) {
                expires = o.get("expiresAt").getAsLong();
            }
        }
        if (locked && expires > 0 && expires <= System.currentTimeMillis()) {
            locked = false;   // 已过期 = 没有锁
        }
        lockExpiresAt = locked ? expires : 0;
        // 只在**状态变化**时通知：首拉拿到的是「从未写过」的 null（counter=0），
        // 那不是一次「解锁」事件，不该把渲染端从冻结态里叫醒。
        if (locked != lastLockState) {
            lastLockState = locked;
            listener.onLock(locked);
        }
    }

    /** 锁到期自动解除（宿主不推，渲染端自己定时）。 */
    public void checkLockExpiry() {
        long exp = lockExpiresAt;
        if (exp > 0 && exp <= System.currentTimeMillis()) {
            lockExpiresAt = 0;
            if (lastLockState) {
                lastLockState = false;
                listener.onLock(false);
            }
        }
    }

    private void applyPosition(JsonElement data) {
        if (data == null || !data.isJsonObject()) {
            return;
        }
        JsonObject o = data.getAsJsonObject();
        if (!o.has("x") || !o.has("y")) {
            return;
        }
        double x = o.get("x").getAsDouble();
        double y = o.get("y").getAsDouble();
        String mode = o.has("mode") && o.get("mode").isJsonPrimitive()
                ? o.get("mode").getAsString() : "instant";
        // duration 只有 mode=line 才有（行程时间，毫秒；0 = 立即到位）
        double duration = o.has("duration") && o.get("duration").isJsonPrimitive()
                ? o.get("duration").getAsDouble() : 0.0;
        listener.onPosition(x, y, mode, duration);
    }

    // ---------------------------------------------------------------- 端口发现

    /** 独立模式的默认端口（dsh-pet 的 DEFAULT_STANDALONE_PORT）。 */
    public static final int DEFAULT_STANDALONE_PORT = 3080;
    /** 独立模式连续扫描的端口数（与 dsh-pet 的 PORT_SCAN_LIMIT 一致）。 */
    public static final int PORT_SCAN_LIMIT = 25;

    /**
     * 发现宿主端口。顺序：
     * <ol>
     *   <li>{@code ~/.dsh-pet/standalone-runtime.json}（独立模式写的运行状态，含真实端口）</li>
     *   <li>扫描 3080..3080+24 找 {@code /dsh-pet-7340/state}（独立模式端口被占用时会顺延）</li>
     * </ol>
     * DSH 宿主模式（路由挂在 DSH 自己的 web 服务上）**端口不在这里**，需用 {@code --dsh-port=} 指定。
     *
     * @return 端口号；找不到返回 -1
     */
    public static int discoverPort(String host) {
        int fromFile = portFromRuntimeFile();
        if (fromFile > 0 && probe(host, fromFile)) {
            return fromFile;
        }
        for (int p = DEFAULT_STANDALONE_PORT; p < DEFAULT_STANDALONE_PORT + PORT_SCAN_LIMIT; p++) {
            if (probe(host, p)) {
                return p;
            }
        }
        return -1;
    }

    /** 读独立模式写的运行状态文件。 */
    public static int portFromRuntimeFile() {
        try {
            Path home = Paths.get(System.getProperty("user.home"));
            Path f = home.resolve(".dsh-pet").resolve("standalone-runtime.json");
            if (!Files.exists(f)) {
                return -1;
            }
            String txt = new String(Files.readAllBytes(f), StandardCharsets.UTF_8);
            JsonObject o = JsonParser.parseString(txt).getAsJsonObject();
            if (o.has("port")) {
                return o.get("port").getAsInt();
            }
        } catch (Exception e) {
            // 读不到就当没有
        }
        return -1;
    }

    /** 探活：某个端口上是否真的有 dsh-pet 路由。 */
    public static boolean probe(String host, int port) {
        try {
            DshClient c = new DshClient(host, port, "main");
            DshClient.State st = c.poll(0, 0);
            return st != null && st.sections != null;
        } catch (Exception e) {
            return false;
        }
    }
}
