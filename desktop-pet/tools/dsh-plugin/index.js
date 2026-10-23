/**
 * dsh-pet-status —— DSH 插件：把会话事件流聚合成「工作状态档位」，对外按 dsh-pet 的契约提供。
 *
 * 为什么是插件而不是外部 sidecar：
 *   宿主侧能直接 `ctx.on('session/event', …)` 拿到**实时**事件（不必去读 ~/.dsh/sessions 落盘文件、
 *   也不必自己解压 zstd），聚合同步发生在宿主进程内。
 *
 * 对外契约（与 dsh-pet 完全一致，桌宠客户端零改动）：
 *   GET  /dsh-pet-7340/state?since=<counter>&timeout=<ms>   长轮询（counter 变了立刻返回）
 *   POST /dsh-pet-7340/pose/report?pet=<id>                 渲染端上报实况（存内存，不写 /state）
 *   POST /dsh-pet-7340/work-status                          外部槽写入档位（与 DSH 会话一起按序合并）
 *
 * 为什么自己起 HTTP 端口而不挂 DSH 的 webServer：
 *   DSH 桌面版的 webServer 有「浏览器访问」闸门（`decideDesktopBrowserAccess`）：
 *   关闭时**任何**路径都回 403 "Browser access is disabled" —— 跨进程客户端读不到。
 *   本插件自起一个仅监听 127.0.0.1 的端口（默认 3080，被占则顺延），不受该闸门影响。
 */
import { createServer } from 'node:http';
import { notePetSeen, petSeenAgeMs, resolveLaunchConfig, startPetSupervisor } from './pet-supervisor.js';

const ROUTE = '/dsh-pet-7340';
const DEFAULT_PORT = 3080;
const PORT_SCAN_LIMIT = 25;
const MAX_STATE_WAIT_MS = 10 * 1000;

/**
 * 展示优先级：waiting > error > working > thinking > result > success
 * （result 高于 success：任何会话的进行中过渡态都不被别处已完成态压过，防中途庆祝；同档按最近更新优先）
 */
const PRIORITY = {
  waiting: 60,
  error: 50,
  working: 40,
  thinking: 30,
  result: 25,
  success: 20,
};
const TERMINAL = new Set(['success', 'error']);
const TASK_TEXT_MAX = 40;

/** turn/end 的 reason.kind → 档位；其余（aborted 等）→ null＝该会话回空闲 */
function turnEndState(kind) {
  if (kind === 'completed') return 'success';
  if (kind === 'error' || kind === 'max-tokens' || kind === 'timeout') return 'error';
  if (kind === 'blocked') return 'waiting';
  return null;
}

/** 取最后一个 in_progress（没有就取第一个 pending），截断 40 码点 */
function currentTaskFromTodo(data) {
  const todos = data && data.todos;
  if (!Array.isArray(todos)) return null;
  let item = null;
  for (const t of todos) {
    if (t && t.status === 'in_progress') item = t;
  }
  if (!item) {
    for (const t of todos) {
      if (t && t.status === 'pending') {
        item = t;
        break;
      }
    }
  }
  if (!item) return null;
  const text = String(item.content || '').trim();
  if (!text) return null;
  const chars = Array.from(text);
  return chars.length > TASK_TEXT_MAX ? chars.slice(0, TASK_TEXT_MAX).join('') + '…' : text;
}

/** 宿主插件主体 */
export function apply(ctx) {
  /** sessionId → { tier, task, seq } */
  const sessions = new Map();
  /** 外部槽（POST /work-status 写入） */
  const external = { tier: null, task: null, seq: 0 };
  /** 对外的 S：顶层 counter + workStatus 叶子 */
  const state = {
    counter: 1,
    workStatus: { counter: 0, data: { state: null, task: null } },
  };
  /** 挂起的 /state 等待者 */
  const waiters = new Set();
  /** 渲染端上报的实况（内存快照，不写 /state） */
  const poses = [];
  /** 诊断：收到的事件总数与首个事件的形状（/debug 里可见） */
  const diag = { events: 0, firstEvent: null, supervisor: { mode: 'pending', source: null, childPid: null } };
  /** 此刻挂着的 /state 长轮询数量（> 0 表示有桌宠正连着） */
  let openStateRequests = 0;
  /** 托管器实例（/debug 里实时读它的状态） */
  let supervisorRef = null;

  function nextCounter() {
    // 严格递增：Date.now() 与「上一个 +1」取大（同一毫秒连写两次也不重复）
    state.counter = Math.max(Date.now(), state.counter + 1);
    return state.counter;
  }

  function publish() {
    // 跨会话取优先级最高的那条（同级取最近更新的）；外部槽也参与
    let best = null;
    let bestKey = null;
    for (const s of sessions.values()) {
      if (!s.tier) continue;
      const key = [PRIORITY[s.tier] || 0, s.seq || 0];
      if (!bestKey || key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] > bestKey[1])) {
        bestKey = key;
        best = s;
      }
    }
    if (external.tier) {
      const key = [PRIORITY[external.tier] || 0, external.seq || 0];
      if (!bestKey || key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] > bestKey[1])) {
        bestKey = key;
        best = external;
      }
    }
    const tier = best ? best.tier : null;
    const task = best ? best.task || null : null;
    const cur = state.workStatus.data;
    if (cur.state === tier && cur.task === task) return;
    cur.state = tier;
    cur.task = task;
    state.workStatus.counter = nextCounter();
    state.counter = Math.max(state.counter, state.workStatus.counter);
    // 唤醒所有等待者（全局集合：一次写入唤醒所有人）
    for (const w of Array.from(waiters)) w();
  }

  function snapshot() {
    return {
      counter: state.counter,
      sections: {
        balance: { counter: 0, data: null },
        workStatus: { counter: state.workStatus.counter, data: { ...state.workStatus.data } },
        notify: { counter: 0, data: null },
        config: { counter: 0, data: {} },
      },
      pets: {
        main: {
          say: { counter: 0, data: null },
          anim: { counter: 0, data: null },
          position: { counter: 0, data: null },
          throw: { counter: 0, data: null },
          lock: { counter: 0, data: null },
        },
      },
    };
  }

  // ① 监听 DSH 会话事件 → 聚合成档位
  //
  // 用 `{ global: true }`：DSH 的会话事件按**隔离作用域**派发（Cordis 在 dispatch 时
  // 按 `ctx[symbols.isolate][serviceName]` 过滤监听器）。运行期才挂进配置树的插件
  // （安装 / 热加载），它所在作用域与会话服务所在作用域未必一致，默认监听可能被过滤掉，
  // 表现为「插件在跑、端口也通，但一个事件都收不到」。global 监听绕过该过滤 ——
  // 会话事件本就是全局广播，跨作用域收是对的。
  ctx.effect(() => {
    const dispose = ctx.on('session/event', (session, event) => {
      diag.events += 1;
      if (diag.events === 1) {
        diag.firstEvent = {
          eventKeys: event && typeof event === 'object' ? Object.keys(event) : null,
          eventType: event && event.type,
          sessionKeys: session && typeof session === 'object' ? Object.keys(session) : null,
          sessionId: session && (session.id || (session.header && session.header.id)),
        };
        console.log('[dsh-pet-status] 收到首个 session/event:', JSON.stringify(diag.firstEvent));
      }
      const type = event && event.type;
      if (!type) return;
      const sessionId = String(
        (session && session.header && session.header.id) || (session && session.id) || 'unknown',
      );
      const data = (event && event.data) || {};
      const s = sessions.get(sessionId) || { tier: null, task: null, seq: 0 };
      s.seq = Number(event.seq) || s.seq;

      if (type === 'todo/write') {
        // 只更新任务详情文案，不切档位
        s.task = currentTaskFromTodo(data);
      } else if (type === 'turn/start') {
        s.tier = 'thinking';
        s.task = null;
      } else if (type === 'tool/call') {
        // 问用户问题的工具（选择题弹窗）＝等用户答复，不是普通干活
        s.tier = String(data.name || '') === 'ask_user_question' ? 'waiting' : 'working';
      } else if (type === 'tool/result') {
        s.tier = 'result';
      } else if (type === 'approval/asked') {
        s.tier = 'waiting';
      } else if (type === 'turn/end') {
        const kind = String((data.reason && data.reason.kind) || '');
        const next = turnEndState(kind);
        s.tier = next;
        if (next === null) s.task = null;
      } else {
        return;
      }
      sessions.set(sessionId, s);
      publish();
    }, { global: true });
    return () => dispose();
  });

  // ② 自起 HTTP 服务（仅监听 127.0.0.1；不受 DSH 的 web 访问闸门影响）
  ctx.effect(() => {
    const server = createServer(async (req, res) => {
      const url = new URL(req.url || '/', 'http://127.0.0.1');
      const path = url.pathname;
      if (!path.startsWith(ROUTE)) {
        res.writeHead(404, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ error: 'not found' }));
        return;
      }
      const sub = path.slice(ROUTE.length) || '/';

      if (sub === '/state' && req.method === 'GET') {
        // 只有渲染端（桌宠）会打 /state → 记一笔「桌宠还活着」
        notePetSeen();
        // 活连接计数：长轮询期间这个请求是**挂着的**，所以计数 > 0 就是「此刻有桌宠连着」。
        // 比时间戳可靠：时间戳分不清「18 秒前活着」和「18 秒前死了」。
        openStateRequests += 1;
        let released = false;
        const release = () => {
          if (released) return;
          released = true;
          openStateRequests -= 1;
        };
        res.on('close', release);
        res.on('finish', release);
        const sinceRaw = url.searchParams.get('since');
        const timeoutRaw = url.searchParams.get('timeout');
        if ((sinceRaw === null) !== (timeoutRaw === null)) {
          res.writeHead(400, { 'content-type': 'application/json' });
          res.end(JSON.stringify({ error: 'since and timeout must be used together' }));
          return;
        }
        if (sinceRaw !== null && (!/^\d+$/.test(sinceRaw) || !/^\d+$/.test(timeoutRaw))) {
          res.writeHead(400, { 'content-type': 'application/json' });
          res.end(JSON.stringify({ error: 'invalid since/timeout' }));
          return;
        }
        const since = sinceRaw === null ? null : Number(sinceRaw);
        const timeoutMs = timeoutRaw === null ? 0 : Math.min(Number(timeoutRaw), MAX_STATE_WAIT_MS);
        const send = () => {
          const body = JSON.stringify(snapshot());
          res.writeHead(200, {
            'content-type': 'application/json; charset=utf-8',
            'cache-control': 'no-cache, no-store',
          });
          res.end(body);
        };
        if (since === null || since !== state.counter) {
          send();
          return;
        }
        // 挂起等待：counter 变了就返回
        let done = false;
        const wake = () => {
          if (done) return;
          done = true;
          clearTimeout(timer);
          waiters.delete(wake);
          if (res.writableEnded || res.destroyed) return;
          send();
        };
        const timer = setTimeout(wake, timeoutMs);
        waiters.add(wake);
        res.on('close', () => {
          if (done) return;
          done = true;
          clearTimeout(timer);
          waiters.delete(wake);
        });
        return;
      }

      if (sub === '/pose/report' && req.method === 'POST') {
        const chunks = [];
        req.on('data', (c) => chunks.push(c));
        req.on('end', () => {
          try {
            const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
            if (typeof body.x !== 'number' || typeof body.y !== 'number') {
              res.writeHead(400, { 'content-type': 'application/json' });
              res.end(JSON.stringify({ ok: false, reason: 'bad-request' }));
              return;
            }
            poses.push({ t: Date.now(), ...body });
            if (poses.length > 200) poses.splice(0, poses.length - 200);
            res.writeHead(200, { 'content-type': 'application/json' });
            res.end(JSON.stringify({ ok: true }));
          } catch {
            res.writeHead(400, { 'content-type': 'application/json' });
            res.end(JSON.stringify({ ok: false, reason: 'bad-request' }));
          }
        });
        return;
      }

      if (sub === '/work-status' && req.method === 'POST') {
        const chunks = [];
        req.on('data', (c) => chunks.push(c));
        req.on('end', () => {
          try {
            const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
            const st = body.state;
            if (st !== null && st !== undefined && !(st in PRIORITY)) {
              res.writeHead(400, { 'content-type': 'application/json' });
              res.end(JSON.stringify({ ok: false, reason: 'bad-request' }));
              return;
            }
            external.tier = st || null;
            external.task = body.task === undefined ? external.task : body.task || null;
            external.seq = Date.now();
            publish();
            res.writeHead(200, { 'content-type': 'application/json' });
            res.end(JSON.stringify({ ok: true, state: external.tier, task: external.task }));
          } catch {
            res.writeHead(400, { 'content-type': 'application/json' });
            res.end(JSON.stringify({ ok: false, reason: 'bad-request' }));
          }
        });
        return;
      }

      if (sub === '/debug' && req.method === 'GET') {
        const seenAge = petSeenAgeMs();
        res.writeHead(200, { 'content-type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          sessions: Object.fromEntries(sessions),
          poses,
          diag: {
            ...diag,
            petSeenAgeMs: Number.isFinite(seenAge) ? seenAge : null,
            openStateRequests,
            // 实时读托管器状态（mode 会随宽限期/看门狗变化，不能用挂载时的快照）
            supervisor: supervisorRef
              ? {
                  mode: supervisorRef.mode,
                  source: diag.supervisor.source,
                  childPid: supervisorRef.childPid,
                  owns: supervisorRef.owns,
                  command: diag.supervisor.command,
                  autoRestart: diag.supervisor.autoRestart,
                }
              : diag.supervisor,
          },
          state: snapshot(),
        }, null, 2));
        return;
      }

      res.writeHead(404, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ error: 'not found' }));
    });

    let port = Number(process.env.DSH_PET_STATUS_PORT || DEFAULT_PORT) || DEFAULT_PORT;
    let tries = 0;
    // 注意：error 监听器必须用 once —— 用 on 会在每次重试时**累积**监听器，
    // 一次 EADDRINUSE 会触发多个顺延分支（端口乱跳、甚至同时起多个服务）。
    const tryListen = (p) => {
      server.once('error', (err) => {
        if (err && err.code === 'EADDRINUSE' && tries < PORT_SCAN_LIMIT) {
          tries += 1;
          console.log(`[dsh-pet-status] 端口 ${p} 被占用，顺延到 ${p + 1}`);
          tryListen(p + 1);
          return;
        }
        console.error('[dsh-pet-status] HTTP 服务启动失败:', err);
      });
      server.listen(p, '127.0.0.1', () => {
        console.log(`[dsh-pet-status] listening on 127.0.0.1:${p}${ROUTE}/state`);
      });
    };
    tryListen(port);

    return () => {
      for (const w of Array.from(waiters)) w();
      waiters.clear();
      if (server) server.close();
    };
  });

  // ③ 桌宠进程托管：插件兼任桌宠的「进程宿主」
  //
  // 以前要手动起两样东西（插件 + JavaFX 桌宠），这里把桌宠也收进插件的生命周期：
  // 插件装载 → 拉起桌宠；插件卸载 / DSH 关闭 → 收掉桌宠；桌宠崩了 → 按退避重拉。
  //
  // 启动配置来源（按优先级）：
  //   1) cordis patch 的 `config.launch`（DSH 原生配置）
  //   2) 插件目录下的 `pet-launch.json`（用户本地文件，最常用）
  //   3) 环境变量 `DSH_PET_LAUNCH`（JSON 或整条命令）
  // 一个都没有 → 只当「档位宿主」，不托管进程（保持向后兼容）。
  ctx.effect(() => {
    const cfg = resolveLaunchConfig(ctx);
    if (!cfg) {
      console.log('[dsh-pet-status] 未配置桌宠启动命令（pet-launch.json / config.launch / DSH_PET_LAUNCH），只提供档位');
      diag.supervisor = { mode: 'disabled', source: null, childPid: null };
      return () => {};
    }
    const supervisor = startPetSupervisor(cfg, {
      petConnected: () => openStateRequests > 0,
      petSeenAgeMs,
    });
    supervisorRef = supervisor;
    diag.supervisor = {
      mode: supervisor.mode,
      source: cfg.source,
      childPid: supervisor.childPid,
      command: cfg.command.join(' '),
      autoRestart: cfg.autoRestart,
    };
    console.log(`[dsh-pet-status] 桌宠托管已启用（配置来源：${cfg.source}，模式：${supervisor.mode}）`);
    return () => {
      supervisor.dispose();
      supervisorRef = null;
      diag.supervisor = { mode: 'disposed', source: cfg.source, childPid: null };
    };
  });
}
