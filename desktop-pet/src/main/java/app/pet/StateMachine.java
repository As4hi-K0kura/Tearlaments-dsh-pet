package app.pet;

import java.util.Map;

/**
 * 八态状态机：**优先级判定**在这里落地。
 *
 * <p>参考实现 dsh-pet 给的两条铁律，本项目照搬：
 * ① 事件动画**不进随机链**——只有 idle / sleeping 进链，其余五态由事件显式触发；
 * ② 用户交互永远优先于自动来源（dsh-pet 的 "用户交互优先" 与占用锁语义）。
 *
 * <p>优先级判定规则：
 * <pre>
 *   USER      用户主动（点播 / 点击 / 拖拽）→ 永远生效，抢占一切。
 *   EVENT     DSH 工作状态档位 → 按 dsh 的档位序抢占（见 request 的注释）。
 *              非 DSH 来源（演示器）→ 用 states.json 的 priority 表判。
 *   CHAIN     随机链 → 用 states.json 的 priority 表判，且只能抽到 chainEligible 的状态。
 *   FALLBACK  事件态播完回落 idle → 不算抢占，无视优先级。
 * </pre>
 *
 * <p>抢占优先级（来自 states.json，**只用于随机链与菜单点播**）：
 * <pre>
 *   9 dragging        物理优先，硬切，任何时候都能打断
 *   3 complete/error  终态任务 —— 不让位给非终态（对齐 dsh-pet 的气泡优先级）
 *   2 calling_tool    工作中
 *   1 thinking/waiting 非终态任务
 *   0 idle/sleeping   底座
 * </pre>
 *
 * <p>DSH 事件**不**用上面这张表，改用 dsh 宿主的档位序
 * （{@code waiting 60 > error 50 > working 40 > thinking 30 > result 25 > success 20}）。
 */
public final class StateMachine {

    /** 触发来源，决定是否受优先级约束。 */
    public enum Trigger {
        /** 用户主动 —— 永远生效。 */
        USER,
        /** 事件驱动（会话状态档位）。 */
        EVENT,
        /** 随机链自播。 */
        CHAIN,
        /** 事件态播完回落 —— 不算抢占。 */
        FALLBACK
    }

    public interface Listener {
        void onStateChanged(PetState from, PetState to, Trigger trigger);
    }

    /**
     * 事件源自带的优先级（DSH 档位序）。
     *
     * <p>DSH 宿主把「会话槽」与「外部槽」按固定优先级合并成一个最终档位
     * （{@code waiting 60 > error 50 > working 40 > thinking 30 > result 25 > success 20}），
     * 我们读到的就是合并后的结果。**抢占判定就用这一套序**，不再用本项目
     * states.json 里那套表（那套表是给随机链/菜单点播用的）。
     */
    public static final class EventPriority {
        /** dsh 档位序：waiting 60 > error 50 > working 40 > thinking 30 > result 25 > success 20。 */
        public final int rank;
        /** 终态（success/error）：终态动画是「播一遍」的一次性演出，**永远接管**。 */
        public final boolean terminal;

        public EventPriority(int rank, boolean terminal) {
            this.rank = rank;
            this.terminal = terminal;
        }
    }

    private final PetManifest manifest;
    private final Listener listener;

    private PetState current = PetState.IDLE;
    /** 当前态的 dsh 档位序（null = 当前态不是 DSH 事件来的）。 */
    private Integer currentEventRank;
    /** 当前态是否为 DSH 终态。 */
    private boolean currentTerminal;
    /** 占用锁：冻结自播动画、忽略自动来源的事件表现（dsh-pet 的 lock 语义）。 */
    private boolean locked;
    /** 连续播了几轮 idle —— 达到阈值后自动进休眠。 */
    private int idleLoops;

    public StateMachine(PetManifest manifest, Listener listener) {
        this.manifest = manifest;
        this.listener = listener;
    }

    public PetState current() {
        return current;
    }

    public boolean locked() {
        return locked;
    }

    public void setLocked(boolean locked) {
        this.locked = locked;
    }

    /**
     * 请求切换状态。
     *
     * @return true = 已切换；false = 被优先级挡下（当前态不动）
     */
    public boolean request(PetState next, Trigger trigger) {
        return request(next, trigger, null);
    }

    /**
     * 请求切换状态。
     *
     * @param ep 事件源自带的优先级（DSH 档位序）；null = 用状态自身的 priority。
     * @return true = 已切换；false = 被优先级挡下（当前态不动）
     */
    public boolean request(PetState next, Trigger trigger, EventPriority ep) {
        if (next == null || next == current) {
            return false;
        }
        // 占用锁生效时，**自动来源**（事件 / 随机链）一律不响应；用户主动仍然生效。
        if (locked && trigger != Trigger.USER) {
            return false;
        }
        if (trigger == Trigger.EVENT && ep != null) {
            // ---- DSH 事件：按 **dsh 的档位序** 判抢占 ----
            //
            // dsh 的档位序（waiting 60 > error 50 > working 40 > thinking 30 > result 25 > success 20）
            // 是**宿主合并多会话时**用的：host/src/host/work-status.ts 的 WORK_STATUS_PRIORITY +
            // pickDisplayed（所有会话里取优先级最高的那条）。我们从 /state 读到的
            // sections.workStatus 已经是合并后的唯一档位 —— 优先级在宿主侧生效过一次。
            //
            // 客户端这一层的判定：
            //  ① 当前态不是 DSH 档位（随机链 / 菜单点播）→ DSH 事件**直接抢占**。
            //     绝不能拿 states.json 那张表判：它把 waiting 排在 calling_tool 之后，
            //     用它判的话「等待批准」就盖不住「正在干活」——这正是要用 dsh 的序的原因。
            //  ② 当前态也是 DSH 档位 → **跟随宿主**（宿主的合并结果就是最终答案）。
            //  ③ 终态（success / error）→ 永远接管，且是「播一遍」的一次性演出。
            //
            // 为什么 ② 不再比一次 rank：同一会话的正常递进是**档位序下降**的
            // （等待批准 waiting → 用户批准 → 开始干活 working；工具调用 working → 工具返回 result），
            // 客户端按序再拦一次，宠物会永远卡在「等待」上。宿主侧已经比过了。
            apply(next, trigger, ep);
            return true;
        }
        if (trigger == Trigger.EVENT || trigger == Trigger.CHAIN) {
            // 非 DSH 来源的事件（演示器 / 菜单）：用 states.json 自带的 priority 表判
            if (next.priority() < current.priority()) {
                return false;   // 抢占失败：低优先级不许打断高优先级
            }
        }
        apply(next, trigger, ep);
        return true;
    }

    private void apply(PetState next, Trigger trigger, EventPriority ep) {
        PetState from = current;
        current = next;
        currentEventRank = ep != null ? ep.rank : null;
        currentTerminal = ep != null && ep.terminal;
        if (next == PetState.IDLE) {
            idleLoops++;
        } else if (next != PetState.SLEEPING) {
            idleLoops = 0;
        }
        listener.onStateChanged(from, next, trigger);
    }

    /**
     * 随机链抽下一段：按清单里的权重抽，只抽 chainEligible 的状态。
     *
     * <p>与 dsh-pet 的 pickCategoryAction 同构：权重求和后掷骰，命中即返回。
     */
    public PetState pickChainNext() {
        Map<String, Integer> weights = manifest.chain.weights;
        int total = 0;
        for (Map.Entry<String, Integer> e : weights.entrySet()) {
            if (manifest.state(PetState.byId(e.getKey())).chainEligible) {
                total += e.getValue();
            }
        }
        if (total <= 0) {
            return PetState.IDLE;
        }
        int r = (int) (Math.random() * total);
        for (Map.Entry<String, Integer> e : weights.entrySet()) {
            PetState s = PetState.byId(e.getKey());
            if (!manifest.state(s).chainEligible) {
                continue;
            }
            r -= e.getValue();
            if (r < 0) {
                return s;
            }
        }
        return PetState.IDLE;
    }

    /**
     * 一轮播完之后决定去哪。
     *
     * <p>规则（对齐 dsh-pet）：事件态播完即回落 idle 继续随机链；
     * idle 连播若干轮后自动进休眠；其它情况按权重抽。
     */
    public PetState nextAfterLoop(PetState finished) {
        if (finished.isEventState()) {
            return PetState.IDLE;
        }
        if (finished == PetState.IDLE
                && manifest.chain.sleepAfterIdleLoops > 0
                && idleLoops >= manifest.chain.sleepAfterIdleLoops) {
            return PetState.SLEEPING;
        }
        return pickChainNext();
    }

    /** 当前态是否由 DSH 事件驱动（带 dsh 档位序）。 */
    public boolean isEventDriven() {
        return currentEventRank != null;
    }

    /** 当前态的 dsh 档位序（null = 当前态不是 DSH 事件来的）。 */
    public Integer currentEventRank() {
        return currentEventRank;
    }

    /** 当前态是否为 DSH 终态（success/error）。 */
    public boolean isCurrentTerminalEvent() {
        return currentTerminal;
    }

    /** 复位（例如用户松手后回到默认链）。 */
    public void reset() {
        idleLoops = 0;
    }
}
