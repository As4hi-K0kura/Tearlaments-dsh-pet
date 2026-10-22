package app.pet;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;

/**
 * dsh-pet 宿主 HTTP 客户端（只读为主）。
 *
 * <p>契约来源：dsh-pet 的 {@code openapi.yaml} / {@code API.md}。
 * 基址 {@code http://127.0.0.1:<port>/dsh-pet-7340}，只监听 127.0.0.1，无鉴权。
 *
 * <p>我们只读三样东西：
 * <ul>
 *   <li>{@code sections.workStatus} —— 真实工作状态档位（全局段）</li>
 *   <li>{@code pets.<id>.lock} —— 占用锁状态（是状态不是指令，渲染端据此冻结自己）</li>
 *   <li>{@code pets.<id>.position} —— 宿主下发的运动指令（读到就执行，不往回写）</li>
 * </ul>
 * 另外把���们的实况（位置/朝向）回报给宿主：{@code POST /pose/report}。
 *
 * <p>长轮询：{@code GET /state?since=<counter>&timeout=<ms>}（成对必填，上限 10000ms）。
 * {@code since} 传上一拍的顶层 {@code counter}，不同就立刻返回，相同就挂起到 timeout。
 */
public final class DshClient {

    /** 路由前缀。 */
    public static final String ROUTE = "/dsh-pet-7340";

    /** 叶子：{@code {counter, data}}，counter 变了就表示该叶子更新了。 */
    public static final class Leaf {
        public long counter;
        public JsonElement data;
    }

    /** 全局段。 */
    public static final class Sections {
        public Leaf balance;
        public Leaf workStatus;
        public Leaf notify;
        public Leaf config;
    }

    /** 每只宠物的五个槽位（一起建，没写过的叶子是 {@code {counter:0,data:null}}）。 */
    public static final class PetEntry {
        public Leaf say;
        public Leaf anim;
        public Leaf position;
        /** JSON 键是 "throw"（Java 关键字，用 SerializedName 映射）。 */
        @com.google.gson.annotations.SerializedName("throw")
        public Leaf throwCue;
        public Leaf lock;
    }

    /** 统一状态 S。 */
    public static final class State {
        public long counter;
        public Sections sections = new Sections();
        public Map<String, PetEntry> pets;
    }

    private final String base;
    private final String petId;
    private final HttpClient http;

    public DshClient(String host, int port, String petId) {
        this.base = "http://" + host + ":" + port + ROUTE;
        this.petId = petId;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    public String base() {
        return base;
    }

    public String petId() {
        return petId;
    }

    /** 长轮询一次。返回 null 表示连接失败（宿主没起 / 端口不对），调用方负责重试。 */
    public State poll(long since, int timeoutMs) throws IOException, InterruptedException {
        URI uri = URI.create(base + "/state?since=" + since + "&timeout=" + timeoutMs);
        HttpRequest req = HttpRequest.newBuilder(uri)
                .timeout(Duration.ofMillis(timeoutMs + 5000L))
                .header("Accept", "application/json")
                .GET()
                .build();
        HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        if (resp.statusCode() / 100 != 2) {
            throw new IOException("GET /state 返回 HTTP " + resp.statusCode());
        }
        return parse(resp.body());
    }

    /** 解析 /state 的响应体。 */
    public static State parse(String json) {
        JsonObject root = JsonParser.parseString(json).getAsJsonObject();
        State s = new State();
        s.counter = root.has("counter") ? root.get("counter").getAsLong() : 0L;

        JsonObject sections = root.has("sections") && root.get("sections").isJsonObject()
                ? root.getAsJsonObject("sections") : new JsonObject();
        s.sections.balance = leaf(sections, "balance");
        s.sections.workStatus = leaf(sections, "workStatus");
        s.sections.notify = leaf(sections, "notify");
        s.sections.config = leaf(sections, "config");

        s.pets = new java.util.LinkedHashMap<>();
        if (root.has("pets") && root.get("pets").isJsonObject()) {
            JsonObject pets = root.getAsJsonObject("pets");
            for (Map.Entry<String, JsonElement> e : pets.entrySet()) {
                if (!e.getValue().isJsonObject()) {
                    continue;
                }
                JsonObject p = e.getValue().getAsJsonObject();
                PetEntry pe = new PetEntry();
                pe.say = leaf(p, "say");
                pe.anim = leaf(p, "anim");
                pe.position = leaf(p, "position");
                pe.throwCue = leaf(p, "throw");
                pe.lock = leaf(p, "lock");
                s.pets.put(e.getKey(), pe);
            }
        }
        return s;
    }

    private static Leaf leaf(JsonObject o, String key) {
        Leaf l = new Leaf();
        if (!o.has(key) || !o.get(key).isJsonObject()) {
            return l;
        }
        JsonObject lo = o.getAsJsonObject(key);
        l.counter = lo.has("counter") ? lo.get("counter").getAsLong() : 0L;
        l.data = lo.has("data") ? lo.get("data") : null;
        return l;
    }

    /** 读某宠物的某个叶子（没有该宠物时返回空叶子）。 */
    public static Leaf leafOf(State s, String petId, String slot) {
        if (s == null || s.pets == null) {
            return new Leaf();
        }
        PetEntry pe = s.pets.get(petId);
        if (pe == null) {
            return new Leaf();
        }
        switch (slot) {
            case "say": return pe.say;
            case "anim": return pe.anim;
            case "position": return pe.position;
            case "throw": return pe.throwCue;
            case "lock": return pe.lock;
            default: return new Leaf();
        }
    }

    /**
     * 上报实况（位置 + 朝向）—— 渲染端 → 宿主的内部通道。
     *
     * <p>宿主只存内存快照，不写 /state。失败静默：位置上报失败不该影响桌宠。
     */
    public void reportPose(double x, double y, String facing) {
        try {
            JsonObject body = new JsonObject();
            body.addProperty("x", x);
            body.addProperty("y", y);
            body.addProperty("facing", facing);
            URI uri = URI.create(base + "/pose/report?pet=" + petId);
            HttpRequest req = HttpRequest.newBuilder(uri)
                    .timeout(Duration.ofSeconds(3))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(body.toString(), StandardCharsets.UTF_8))
                    .build();
            http.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (Exception e) {
            // 静默：上报是尽力而为
        }
    }
}
