// 用假的 ctx 验证插件逻辑（不依赖 DSH 宿主）
import { apply } from './index.js';

// 自测不托管桌宠进程：假 ctx 里没有真宿主，起真桌宠会干扰断言
process.env.DSH_PET_STATUS_DISABLE_SUPERVISOR = '1';

const PORT = Number(process.env.DSH_PET_STATUS_PORT || 3085);
const BASE = `http://127.0.0.1:${PORT}`;
const handlers = [];
const cleanups = [];
const ctx = {
  on(evt, cb) {
    handlers.push({ evt, cb });
    return () => {};
  },
  effect(fn) {
    const c = fn();
    if (typeof c === 'function') cleanups.push(c);
  },
};

apply(ctx);

// 找到 session/event 的监听器
const h = handlers.find((x) => x.evt === 'session/event');
if (!h) {
  console.error('FAIL: 没注册 session/event 监听');
  process.exit(1);
}

const ev = (type, seq, data = {}) => ({ type, seq, time: Date.now(), data });
const sid = { id: 'session-test-1' };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function readState() {
  const r = await fetch(`${BASE}/dsh-pet-7340/state?since=0&timeout=0`);
  return (await r.json()).sections.workStatus.data;
}

let fails = 0;
function check(name, cond, extra = '') {
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (extra ? '   ' + extra : ''));
  if (!cond) fails++;
}

h.cb(sid, ev('turn/start', 1));
await sleep(100);
check('turn/start → thinking', (await readState()).state === 'thinking');

h.cb(sid, ev('tool/call', 2, { name: 'Read' }));
await sleep(100);
check('tool/call → working', (await readState()).state === 'working');

h.cb(sid, ev('tool/result', 3));
await sleep(100);
check('tool/result → result', (await readState()).state === 'result');

h.cb(sid, ev('approval/asked', 4));
await sleep(100);
check('approval/asked → waiting', (await readState()).state === 'waiting');

h.cb(sid, ev('turn/end', 5, { reason: { kind: 'completed' } }));
await sleep(100);
check('turn/end completed → success', (await readState()).state === 'success');

h.cb(sid, ev('turn/end', 6, { reason: { kind: 'aborted' } }));
await sleep(100);
check('turn/end aborted → 回空闲(null)', (await readState()).state === null);

// 多会话：waiting(60) 应盖过 success(20)
const sid2 = { id: 'session-test-2' };
h.cb(sid2, ev('turn/start', 1));
h.cb(sid2, ev('approval/asked', 2));
await sleep(100);
check('跨会话 waiting(60) 盖过 success(20)', (await readState()).state === 'waiting');

// task 文案
h.cb(sid2, ev('todo/write', 3, { todos: [{ status: 'in_progress', content: '正在写代码' }] }));
await sleep(100);
check('todo/write → 任务文案', (await readState()).task === '正在写代码');

// 外部槽
await fetch(`${BASE}/dsh-pet-7340/work-status`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ state: 'working' }),
});
await sleep(100);
const st = await readState();
check('外部槽 POST /work-status → 参与合并', st.state === 'working' || st.state === 'waiting', JSON.stringify(st));

// pose 上报
await fetch(`${BASE}/dsh-pet-7340/pose/report?pet=main`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ x: 0.5, y: 0.9, facing: 'right' }),
});
const dbg = await (await fetch(`${BASE}/dsh-pet-7340/debug`)).json();
check('pose 上报已记录', Array.isArray(dbg.poses) && dbg.poses.length > 0);

// 长轮询：counter 不变时挂起，变化时返回
const before = await (await fetch(`${BASE}/dsh-pet-7340/state?since=0&timeout=0`)).json();
const t0 = Date.now();
const pending = fetch(
  `${BASE}/dsh-pet-7340/state?since=${before.counter}&timeout=5000`,
).then((r) => r.json());
await sleep(300);
h.cb(sid2, ev('tool/call', 9, { name: 'Bash' }));
const woke = await pending;
check('长轮询：counter 变化即返回', Date.now() - t0 < 3000 && woke.counter !== before.counter);

for (const c of cleanups) {
  try {
    c();
  } catch {}
}
console.log(fails === 0 ? '\n全部通过' : `\n${fails} 项失败`);
process.exit(fails === 0 ? 0 : 1);

