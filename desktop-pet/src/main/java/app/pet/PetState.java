package app.pet;

/**
 * 八态枚举。
 *
 * <p>档位（tier）沿用参考实现 dsh-pet 的 events.workStatus 语义：<b>索引即档位，顺序即语义，
 * 新档只能追加到末尾</b>（往中间插会让已生效配置的档位含义错位）。
 *
 * <p>抢占优先级（priority）另有一套：它回答「谁能打断谁」，与档位顺序无关。
 * 例如 dsh-pet 的档位里 error(5) 在 success(4) 之后，但两者优先级相同（都是终态 3）。
 *
 * <p>这里的默认值与 {@code states.json} 一致；运行时以 manifest.json 为准。
 */
public enum PetState {
    /** 待机：随机链的底座，任何时刻可被打断。 */
    IDLE("idle", "待机", 0, Tier.NONE),
    /** 思考：事件档位 0（对应 turn/start 回合开）。 */
    THINKING("thinking", "思考", 1, Tier.THINKING),
    /** 调用工具：事件档位 1（对应 tool/call 工具调用）。 */
    CALLING_TOOL("calling_tool", "调用工具", 2, Tier.WORKING),
    /** 等待：事件档位 3（对应 approval/asked 等待批准）。 */
    WAITING("waiting", "等待", 1, Tier.WAITING),
    /** 完成：事件档位 4（对应 turn/end completed）。终态。 */
    COMPLETE("complete", "完成", 3, Tier.SUCCESS),
    /** 出错：事件档位 5（对应 turn/end error/max-tokens）。终态。 */
    ERROR("error", "出错", 3, Tier.ERROR),
    /** 拖拽：用户物理交互，硬切，不做过渡，永远抢占。 */
    DRAGGING("dragging", "拖拽", 9, Tier.NONE),
    /** 休眠：随机链里最低的一档，被任意任务态抢占。 */
    SLEEPING("sleeping", "休眠", 0, Tier.NONE);

    /** dsh-pet 的工作状态档位（索引即档位，勿在中间插入）。 */
    public enum Tier {
        NONE(-1),
        THINKING(0),
        WORKING(1),
        /** 本项目没有「整理」态（dsh-pet 的 result 档），保留占位以对齐档位编号。 */
        RESULT(2),
        WAITING(3),
        SUCCESS(4),
        ERROR(5);

        private final int index;

        Tier(int index) {
            this.index = index;
        }

        public int index() {
            return index;
        }
    }

    private final String id;
    private final String zh;
    private final int priority;
    private final Tier tier;

    PetState(String id, String zh, int priority, Tier tier) {
        this.id = id;
        this.zh = zh;
        this.priority = priority;
        this.tier = tier;
    }

    public String id() {
        return id;
    }

    public String zh() {
        return zh;
    }

    public int priority() {
        return priority;
    }

    public Tier tier() {
        return tier;
    }

    /** 是否为「事件态」——由外部事件显式触发，不进随机链（对齐 dsh-pet 的事件动画规则）。 */
    public boolean isEventState() {
        return tier != Tier.NONE;
    }

    /** 是否为终态——终态的气泡/表现不该被非终态顶掉（对齐 dsh-pet 的气泡优先级规则）。 */
    public boolean isTerminal() {
        return this == COMPLETE || this == ERROR;
    }

    public static PetState byId(String id) {
        for (PetState s : values()) {
            if (s.id.equals(id)) {
                return s;
            }
        }
        throw new IllegalArgumentException("未知状态 id: " + id);
    }
}
