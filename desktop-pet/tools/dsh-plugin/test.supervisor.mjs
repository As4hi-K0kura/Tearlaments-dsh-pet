// 桌宠进程托管的自测：拉起 / 接管 / 不碰外部桌宠 / 卸载收尾 / 看门狗接管
// 用法：node test.supervisor.mjs
import { mkdtempSync, readFileSync, existsSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// 必须在 import 之前改 USERPROFILE —— 锁文件路径在模块加载时就定下来了
const fakeHome = mkdtempSync(join(tmpdir(), 'dsh-pet-home-'));
process.env.USERPROFILE = fakeHome;
process.env.HOME = fakeHome;
// 启动配置也隔离到临时目录（别读到插件目录里真实的 pet-launch.json）
process.env.DSH_PET_LAUNCH_FILE = join(fakeHome, 'pet-launch.json');

const { startPetSupervisor, resolveLaunchConfig } = await import('./pet-supervisor.js');

const LOCK = join(fakeHome, '.dsh-pet', 'pet.lock.json');
let fails = 0;
const check = (name, cond, extra = '') => {
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (extra ? '   ' + extra : ''));
  if (!cond) fails++;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const alive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === 'EPERM';
  }
};

// 一个无害的长驻子进程
const CFG = {
  command: [process.execPath, '-e', 'setTimeout(()=>{}, 60000)'],
  cwd: fakeHome,
  autoRestart: false,
  restartDelayMs: 500,
};
const GRACE = 120; // 宽限期调短，测试不用等 5s

// ① 没有配置 → 不托管
check('无配置 → 不托管', resolveLaunchConfig({}) === null);

// ② 有活连接（用户手动起的桌宠）→ 不重复拉起、不接管生死
{
  const sup = startPetSupervisor(CFG, { petConnected: () => true, graceMs: GRACE });
  await sleep(GRACE + 300);
  check('外部桌宠 → 不重复拉起', sup.mode === 'external', `mode=${sup.mode}`);
  check('外部桌宠 → 不拥有生死', sup.owns === false);
  check('外部桌宠 → 不写锁文件', existsSync(LOCK) === false);
  sup.dispose();
  check('外部桌宠 → dispose 不杀进程', existsSync(LOCK) === false);
}

// ③ 没有活连接 + 无锁 → 宽限期后拉起自己
{
  const sup = startPetSupervisor(CFG, { petConnected: () => false, graceMs: GRACE });
  await sleep(GRACE + 500);
  check('无桌宠 → 拉起', sup.mode === 'spawned' && sup.childPid !== null, `pid=${sup.childPid}`);
  check('拉起后写锁文件', existsSync(LOCK) === true);
  const lock = JSON.parse(readFileSync(LOCK, 'utf8'));
  check('锁文件记着 pid', lock.pid === sup.childPid, `lock.pid=${lock.pid}`);
  const pid = sup.childPid;
  sup.dispose();
  await sleep(600);
  check('dispose → 收掉桌宠进程', alive(pid) === false, `pid=${pid}`);
  check('dispose → 清掉锁文件', existsSync(LOCK) === false);
}

// ④ 锁文件里有活进程 → 接管，不重复拉起
{
  const alivePid = process.pid; // 用当前 node 进程冒充「活着的桌宠」
  mkdirSync(join(fakeHome, '.dsh-pet'), { recursive: true });
  writeFileSync(LOCK, JSON.stringify({ pid: alivePid, owner: 'old-instance', startedAt: Date.now() }));
  const sup = startPetSupervisor(CFG, { petConnected: () => false, graceMs: GRACE });
  await sleep(GRACE + 300);
  check('锁文件有活进程 → 接管不重拉', sup.mode === 'adopted' && sup.childPid === null, `mode=${sup.mode}`);
  const lock = JSON.parse(readFileSync(LOCK, 'utf8'));
  check('接管后归属转移给新实例', lock.owner !== 'old-instance', `owner=${lock.owner}`);
  sup.dispose();
  check('归属已转移 → dispose 不杀进程', process.pid === alivePid);
  rmSync(LOCK, { force: true });
}

// ⑤ 看门狗：外部桌宠后来死了 → 插件接管拉起
{
  let connected = true; // 先假装外部桌宠活着
  const sup = startPetSupervisor(CFG, {
    petConnected: () => connected,
    graceMs: GRACE,
    idleThresholdMs: 600, // 测试里把空阈值调小
    watchdogIntervalMs: 200,
  });
  await sleep(GRACE + 300);
  check('看门狗前：判为外部桌宠', sup.mode === 'external', `mode=${sup.mode}`);
  connected = false; // 外部桌宠死了
  await sleep(1500);
  check('外部桌宠死了 → 看门狗接管拉起', sup.mode === 'spawned' && sup.childPid !== null, `mode=${sup.mode}`);
  sup.dispose();
}

// ⑥ 退出语义：正常退出（code=0，用户主动退出）**不**重拉；异常退出才重拉
{
  const sup = startPetSupervisor(
    { command: [process.execPath, '-e', 'process.exit(0)'], cwd: fakeHome, autoRestart: true, restartDelayMs: 200 },
    { petConnected: () => false, graceMs: GRACE },
  );
  await sleep(GRACE + 1500);
  check('正常退出(code=0) → 不自动拉起', sup.childPid === null && sup.mode === 'exited', `mode=${sup.mode} pid=${sup.childPid}`);
  sup.dispose();
}
{
  // 子进程每次起来就写一行标记再异常退出 —— 数标记行数就知道重拉了几次
  // （不能直接看 childPid：秒退的子进程在采样瞬间正好落在重启间隙里，pid 恒为 null）
  const marker = join(fakeHome, 'spawn-marker.txt');
  const sup = startPetSupervisor(
    {
      command: [
        process.execPath,
        '-e',
        `require('fs').appendFileSync(${JSON.stringify(marker)}, 'x'); process.exit(3);`,
      ],
      cwd: fakeHome,
      autoRestart: true,
      restartDelayMs: 200,
    },
    { petConnected: () => false, graceMs: GRACE },
  );
  await sleep(GRACE + 1500);
  const marks = existsSync(marker) ? readFileSync(marker, 'utf8').length : 0;
  check('异常退出(code!=0) → 自动重拉', marks >= 2, `重拉次数=${marks}`);
  sup.dispose();
  await sleep(400);
}

// ⑦ stop() / start()：用户主动停 → 不再自动拉；再 start() → 又拉起来
{
  const sup = startPetSupervisor(
    { ...CFG, autoRestart: true, restartDelayMs: 200 },
    { petConnected: () => false, graceMs: GRACE },
  );
  await sleep(GRACE + 500);
  const first = sup.childPid;
  check('已拉起桌宠', first !== null, `pid=${first}`);
  sup.stop();
  await sleep(500);
  check('stop() → 进程已收掉', first === null || alive(first) === false, `pid=${first}`);
  check('stop() → 标记为用户主动停', sup.userStopped === true);
  check('stop() → 清掉锁文件', existsSync(LOCK) === false);
  await sleep(600);
  check('stop() 之后不会被自动重拉', sup.childPid === null, `pid=${sup.childPid}`);
  sup.start();
  await sleep(600);
  check('start() → 又拉起一只', sup.childPid !== null && sup.childPid !== first, `pid=${sup.childPid}`);
  sup.dispose();
  await sleep(400);
}

// 清理：Windows 上刚被杀的子进程可能还占着句柄，rmSync 会 EPERM —— 重试几次，仍失败也不该让自测红掉
try {
  rmSync(fakeHome, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
} catch {
  console.log('  WARN  临时目录清理失败（不影响断言结果）');
}

console.log(fails === 0 ? '\n全部通过' : `\n${fails} 项失败`);
process.exit(fails === 0 ? 0 : 1);
