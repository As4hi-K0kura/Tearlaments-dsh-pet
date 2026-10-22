package app.pet;

import com.google.gson.Gson;

import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 图集清单（构建期由 tools/build_atlas.py 产出）。
 *
 * <p>清单是**唯一的事实来源**：运行时不再知道「脸宽该是多少」「脚底线在哪」，
 * 只按清单里的格子坐标与视口切帧。图集与清单必须成对使用。
 */
public final class PetManifest {

    /** 公共画布：所有状态共用同一套画布坐标，切换状态时角色不发生位移。 */
    public static final class Canvas {
        /** 画布宽高（图集像素）。 */
        public int w;
        public int h;
        /** 公共脚底线（画布 y 坐标）—— 宠物始终站在这条线上。 */
        public double groundY;
        /** 公共脸心 x（画布坐标）—— 水平方向所有状态对齐到这里。 */
        public double faceCX;
        /** 归一化后的目标脸宽（图集像素）。 */
        public double faceWidth;
    }

    public static final class Grid {
        public int cols;
        public int padding;
    }

    /** 随机链：只有 chainEligible 的状态进链；事件态不进链。 */
    public static final class Chain {
        public Map<String, Integer> weights = new LinkedHashMap<>();
        public int sleepAfterIdleLoops = 3;
        public String note;
    }

    public static final class StateDef {
        public String id;
        public String zh;
        public String semantic;
        public String atlas;
        public int cols;
        public int rows;
        /** 单帧格子宽高（图集像素）。 */
        public int fw;
        public int fh;
        /** 该状态格子在公共画布里的左上角。 */
        public int cellX;
        public int cellY;
        public int frames;
        public double fps;
        public int srcFrames;
        public boolean loop;
        public String closure;
        /** 抢占优先级：数字越大越能打断别人。 */
        public int priority;
        public double faceWidth;
        public double faceWidthMedian;
        /** 在产物图集上复测到的脸宽 —— 归一化的最终判据。 */
        public double faceWidthOut;
        public double[] faceCenter;
        public int[] contentOrigin;
        /** 头心离地高度（输出像素），站立态之间应接近。 */
        public double headHeight;
        public double scale;
        public double srcFps;
        public boolean groundAligned;
        public boolean chainEligible;
        public double[] bbox;
    }

    /** 转场表：from→to 的时长与遮蔽策略（strategy 决定切换是否瞬时）。 */
    public static final class Transition {
        public String from;
        public String to;
        public int ms;
        /** continue / bodyMask / arrayMask / hard。 */
        public String strategy;
        public String effect;
    }

    public Canvas canvas = new Canvas();
    public Grid grid = new Grid();
    public Chain chain = new Chain();
    public List<StateDef> states = new ArrayList<>();
    public List<Transition> transitions = new ArrayList<>();
    public List<String> priorityOrder = new ArrayList<>();

    private final Map<PetState, StateDef> byState = new EnumMap<>(PetState.class);

    public static PetManifest load() {
        try (InputStream in = PetManifest.class.getResourceAsStream("/pet/manifest.json")) {
            if (in == null) {
                throw new IllegalStateException("找不到 /pet/manifest.json");
            }
            PetManifest m = new Gson().fromJson(
                    new InputStreamReader(in, StandardCharsets.UTF_8), PetManifest.class);
            for (StateDef d : m.states) {
                m.byState.put(PetState.byId(d.id), d);
            }
            return m;
        } catch (IOException e) {
            throw new IllegalStateException("读取 manifest 失败", e);
        }
    }

    public StateDef state(PetState s) {
        StateDef d = byState.get(s);
        if (d == null) {
            throw new IllegalStateException("清单里没有状态: " + s.id());
        }
        return d;
    }

    /** 查转场；没有就返回 null（调用方按默认处理）。 */
    public Transition transition(PetState from, PetState to) {
        for (Transition t : transitions) {
            if (t.to == null || !t.to.equals(to.id())) {
                continue;
            }
            if (t.from == null) {
                continue;
            }
            if (t.from.equals(from.id()) || t.from.equals("any")) {
                return t;
            }
        }
        return null;
    }
}
