/**
 * 桌宠进程托管 —— 让插件兼任桌宠的「进程宿主」。
 *
 * 为什么需要：
 *   dsh-pet-status 本身只提供**档位**（session/event → 档位 → 本地 HTTP），
 *   桌宠本体是独立的 JavaFX 程序。以前要手动起两个东西，这里把「起桌宠」也收进插件。
 *
 * 三个必须处理干净的问题：
 *   1. **不能双开**：用户可能已经手动起过桌宠，插件再拉一个就变成两只。
 *      → 判据是「最近有没有渲染端来取过档位」（/state 长轮询）。
 *   2. **不能留孤儿**：插件被卸载 / DSH 关掉时，它拉起的桌宠必须跟着走。
 *      → 归属用锁文件记录（谁拉起的、当前归谁管），换手时改写归属，
 *        旧实例 dispose 时发现归属已转移就不杀（否则热重载会把桌宠误杀）。
 *   3. **崩了要能回来**：桌宠异常退出后按退避重拉。
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const PLUGIN_DIR = dirname(fileURLToPath(import.meta.url));
/** 锁文件放在用户目录（不进仓库、不随插件目录搬家） */
const STATE_DIR = join(homedir(), '.dsh-pet');
const LOCK_FILE = join(STATE_DIR, 'pet.lock.json');
/** 启动配置文件：默认在插件目录，可用环境变量指到别处（自测用它隔离真实配置） */
const LAUNCH_FILE = process.env.DSH_PET_LAUNCH_FILE || join(PLUGIN_DIR, 'pet-launch.json');
/** 「渲染端最近取过档位」的时间戳落盘位置 —— 必须持久化：插件热重载会重新求值模块，
 *  只放内存的话每次重载都会以为「从没见过桌宠」→ 重复拉起 → 双开 */
const SEEN_FILE = join(STATE_DIR, 'pet-seen.json');

/** 渲染端最近一次取档位的时间戳（毫秒）；0 = 从没见过 */
let lastPetSeenAt = 0;
let lastSeenWriteAt = 0;

try {
  if (existsSync(SEEN_FILE)) {
    const raw = parseJsonLoose(readFileSync(SEEN_FILE, 'utf8'));
    if (raw && Number.isFinite(raw.at)) lastPetSeenAt = Number(raw.at);
  }
} catch {
  /* 读不到就当没见过 */
}

/** 记录一次渲染端取档位（只有渲染端会打 /state） */
export function notePetSeen() {
  const now = Date.now();
  lastPetSeenAt = now;
  if (now - lastSeenWriteAt < 1000) return; // 节流，别每次长轮询都落盘
  lastSeenWriteAt = now;
  try {
    mkdirSync(STATE_DIR, { recursive: true });
    writeFileSync(SEEN_FILE, JSON.stringify({ at: now }), 'utf8');
  } catch {
    /* 落盘失败不影响功能 */
  }
}

/** 距今多久内见过渲染端（毫秒）；0 = 从没见过 */
export function petSeenAgeMs() {
  return lastPetSeenAt === 0 ? Infinity : Date.now() - lastPetSeenAt;
}

function safeConfig(ctx) {
  try {
    const c = ctx && ctx.config;
    return c && typeof c === 'object' ? c : null;
  } catch {
    return null;
  }
}

/** 把一条命令字符串拆成 argv（支持双引号包裹的参数） */
function splitCommand(text) {
  const out = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(text)) !== null) out.push(m[1] ?? m[2] ?? m[3]);
  return out;
}

/** 解析 JSON：容忍 UTF-8 BOM（Windows 上很多工具写出来的文件都带 BOM） */
function parseJsonLoose(text) {
  return JSON.parse(String(text).replace(/^\uFEFF/, ''));
}

/** 归一化一份启动配置 → { command[], cwd, env, autoRestart, restartDelayMs } | null */
function normalizeLaunch(raw) {
  if (!raw) return null;
  let command = null;
  let cwd;
  let env;
  let autoRestart = true;
  let restartDelayMs = 3000;

  if (Array.isArray(raw)) {
    command = raw.map(String);
  } else if (typeof raw === 'string') {
    command = splitCommand(raw);
  } else if (typeof raw === 'object') {
    if (raw.command !== undefined) {
      command = Array.isArray(raw.command) ? raw.command.map(String) : splitCommand(String(raw.command));
    }
    if (raw.cwd) cwd = String(raw.cwd);
    if (raw.env && typeof raw.env === 'object') env = raw.env;
    if (raw.autoRestart === false) autoRestart = false;
    if (Number.isFinite(raw.restartDelayMs)) restartDelayMs = Math.max(500, Number(raw.restartDelayMs));
  }

  if (!command || command.length === 0) return null;
  return { command, cwd, env, autoRestart, restartDelayMs };
}

/**
 * 解析启动配置，按优先级取第一个有效的：
 *   1) cordis patch 的 `config.launch`（DSH 原生配置方式）
 *   2) 插件目录下的 `pet-launch.json`（用户本地文件，最常用）
 *   3) 环境变量 `DSH_PET_LAUNCH`（JSON）
 */
export function resolveLaunchConfig(ctx) {
  // 自测/调试用：显式关掉托管，避免假 ctx 把真桌宠拉起来
  if (process.env.DSH_PET_STATUS_DISABLE_SUPERVISOR === '1') return null;
  const candidates = [];

  const patch = safeConfig(ctx);
  if (patch && patch.launch) candidates.push(['cordis.patch config.launch', patch.launch]);

  const file = LAUNCH_FILE;
  if (existsSync(file)) {
    try {
      candidates.push(['pet-launch.json', parseJsonLoose(readFileSync(file, 'utf8'))]);
    } catch (err) {
      console.error('[dsh-pet-status] pet-launch.json 解析失败：', err && err.message);
    }
  }

  const envRaw = process.env.DSH_PET_LAUNCH;
  if (envRaw) {
    try {
      candidates.push(['DSH_PET_LAUNCH', parseJsonLoose(envRaw)]);
    } catch {
      candidates.push(['DSH_PET_LAUNCH', envRaw]);
    }
  }

  for (const [source, raw] of candidates) {
    const cfg = normalizeLaunch(raw);
    if (cfg) return { source, ...cfg };
  }
  return null;
}

/** 进程是否还活着（Windows 下用 tasklist 判，跨平台用 kill(pid,0)） */
function isAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    // EPERM = 进程存在但本用户无权发信号
    return err && err.code === 'EPERM';
  }
}

function readLock() {
  try {
    if (!existsSync(LOCK_FILE)) return null;
    const raw = parseJsonLoose(readFileSync(LOCK_FILE, 'utf8'));
    return raw && typeof raw === 'object' ? raw : null;
  } catch {
    return null;
  }
}

function writeLock(pid, owner) {
  try {
    mkdirSync(STATE_DIR, { recursive: true });
    writeFileSync(LOCK_FILE, JSON.stringify({ pid, owner, startedAt: Date.now() }, null, 2), 'utf8');
  } catch (err) {
    console.error('[dsh-pet-status] 写锁文件失败：', err && err.message);
  }
}

function clearLock() {
  try {
    rmSync(LOCK_FILE, { force: true });
  } catch {
    /* 忽略 */
  }
}

/**
 * 启动桌宠进程监管。
 * @param cfg normalizeLaunch 的结果
 * @param opts.petSeenAgeMs 渲染端最近活动距今多久（用来判断是否已经有桌宠在跑）
 * @returns { dispose, state } dispose 用于插件卸载时收尾
 */
export function startPetSupervisor(cfg, opts = {}) {
  const owner = `dsh-pet-status:${process.pid}:${Date.now()}`;
  /** 此刻是否有桌宠连着（活连接判据） */
  const petConnected = typeof opts.petConnected === 'function' ? opts.petConnected : () => false;
  /** 宽限期：等一会儿看有没有桌宠自己接上来（重载/重装时，旧桌宠会立刻重连） */
  const graceMs = Number.isFinite(opts.graceMs) ? opts.graceMs : 5000;
  const seenAge = typeof opts.petSeenAgeMs === 'function' ? opts.petSeenAgeMs() : Infinity;

  let child = null;
  let stopping = false;
  let restartTimer = null;
  let decisionTimer = null;
  let watchdog = null;
  let owns = false; // 本实例是否拥有桌宠的生死
  let mode = 'none';
  const lock = readLock();
  const lockAlive = lock && isAlive(Number(lock.pid));

  console.log(
    `[dsh-pet-status] 托管判定：锁文件 ${lock ? `pid=${lock.pid}(${lockAlive ? '存活' : '已死'})` : '无'}` +
      `，渲染端最近活跃 ${Number.isFinite(seenAge) ? Math.round(seenAge / 1000) + 's 前' : '从没见过'}` +
      `，此刻有活连接 ${petConnected() ? '有' : '无'}`,
  );

  if (lockAlive) {
    // 之前拉起过、桌宠还活着（典型场景：插件热重载 / DSH 重启后旧进程遗留）→ 接管，不重复拉
    mode = 'adopted';
    owns = true;
    writeLock(Number(lock.pid), owner);
    console.log(`[dsh-pet-status] 检测到已在运行的桌宠（pid ${lock.pid}），接管，不再重复拉起`);
  } else {
    // 没有可接管的锁 → 等一小段宽限期，看有没有桌宠自己接上来。
    //   · 旧实例刚被换掉 → 它的桌宠会立刻重连（活连接出现）→ 判定为「外部桌宠」，不重复拉
    //   · 旧桌宠已经死了 → 没人连 → 宽限期结束 → 由插件拉起
    // 判据用**活连接**而不是时间戳：时间戳分不清「18 秒前还活着」和「18 秒前就死了」。
    mode = 'pending';
    decisionTimer = setTimeout(() => {
      if (stopping) return;
      decisionTimer = null;
      if (petConnected()) {
        mode = 'external';
        console.log('[dsh-pet-status] 检测到已有桌宠连着（活连接），不重复拉起');
        startWatchdog();
      } else {
        mode = 'spawned';
        owns = true;
        console.log('[dsh-pet-status] 没有桌宠在跑 → 由插件拉起');
        spawnPet();
      }
    }, graceMs);
  }

  const spawnPet = () => {
    if (stopping) return;
    console.log(`[dsh-pet-status] 拉起桌宠：${cfg.command[0]} ${cfg.command.slice(1).join(' ')}`);
    try {
      child = spawn(cfg.command[0], cfg.command.slice(1), {
        cwd: cfg.cwd,
        env: { ...process.env, ...(cfg.env || {}) },
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
      });
    } catch (err) {
      console.error('[dsh-pet-status] 拉起桌宠失败：', err && err.message);
      child = null;
      return;
    }
    if (child.pid) writeLock(child.pid, owner);
    child.stdout.on('data', (buf) => {
      const text = buf.toString('utf8').trimEnd();
      if (text) console.log('[dsh-pet-status][桌宠]', text);
    });
    child.stderr.on('data', (buf) => {
      const text = buf.toString('utf8').trimEnd();
      if (text) console.error('[dsh-pet-status][桌宠]', text);
    });
    child.on('error', (err) => {
      console.error('[dsh-pet-status] 桌宠进程错误：', err && err.message);
      child = null;
    });
    child.on('exit', (code, signal) => {
      console.log(`[dsh-pet-status] 桌宠退出（code=${code} signal=${signal}）`);
      const current = readLock();
      if (current && Number(current.pid) === (child && child.pid ? child.pid : -1)) clearLock();
      child = null;
      if (!stopping && cfg.autoRestart) {
        console.log(`[dsh-pet-status] ${cfg.restartDelayMs}ms 后重拉桌宠`);
        restartTimer = setTimeout(spawnPet, cfg.restartDelayMs);
      }
    });
  };

  // 看门狗：处于「外部桌宠」模式时，如果那个桌宠不再连着了（连续 30s 没有活连接），
  // 说明它已经没了 → 转为「自己拉起」。桌宠 8s 轮询一次，30s 足够宽松。
  function startWatchdog() {
    if (watchdog) return;
    const idleThreshold = Number.isFinite(opts.idleThresholdMs) ? opts.idleThresholdMs : 30000;
    const interval = Number.isFinite(opts.watchdogIntervalMs) ? opts.watchdogIntervalMs : 10000;
    let idleSince = Date.now();
    watchdog = setInterval(() => {
      if (stopping || mode !== 'external') return;
      if (petConnected()) {
        idleSince = Date.now();
        return;
      }
      const idle = Date.now() - idleSince;
      if (idle < idleThreshold) return;
      console.log(`[dsh-pet-status] 外部桌宠已 ${Math.round(idle / 1000)}s 没有活连接，判定为已退出 → 改为插件拉起`);
      clearInterval(watchdog);
      watchdog = null;
      mode = 'spawned';
      owns = true;
      spawnPet();
    }, interval);
    if (watchdog.unref) watchdog.unref();
  }

  return {
    get mode() {
      return mode;
    },
    get childPid() {
      return child ? child.pid : null;
    },
    get owns() {
      return owns;
    },
    /** 插件卸载 / DSH 关闭时收尾：只收「归我管」的桌宠 */
    dispose() {
      stopping = true;
      if (decisionTimer) {
        clearTimeout(decisionTimer);
        decisionTimer = null;
      }
      if (watchdog) {
        clearInterval(watchdog);
        watchdog = null;
      }
      if (restartTimer) {
        clearTimeout(restartTimer);
        restartTimer = null;
      }
      const current = readLock();
      const stillMine = current && current.owner === owner;
      if (!owns || !stillMine) {
        // 归属已转移（被新实例接管）或本来就不是我拉起的 → 不杀，避免误杀
        console.log('[dsh-pet-status] 不接管桌宠的生死，保留运行');
        return;
      }
      if (child) {
        console.log(`[dsh-pet-status] 收掉桌宠进程（pid ${child.pid}）`);
        try {
          child.kill();
        } catch (err) {
          console.error('[dsh-pet-status] 结束桌宠进程失败：', err && err.message);
        }
      }
      clearLock();
    },
  };
}
