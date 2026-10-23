#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌宠 DSH 接入端到端验收。

流程：起 mock 宿主 → 起桌宠（--dsh-port）→ 按脚本推档位/锁/位置 → 逐条断言桌宠日志。

断言口径（对齐 dsh-pet 的真实语义）：
  · 进行中档位（thinking/working/result/waiting）→ 循环播，**不**回落待机
  · 档位序下降（working → result）也要跟随宿主（宿主的合并结果就是最终答案）
  · 终态（success/error）→ 播一遍后回落待机
  · 宿主回空闲 → 当前段播完即停 → 回落待机
  · 占用锁生效 → 档位事件不响应；解锁 → 重新对齐当前档位
  · 位置指令 → 窗口挪动 + 上报 pose
"""
import os
import re
import subprocess
import sys
import time
import urllib.request

# 路径：默认相对仓库根，环境变量可覆盖（PET_JDK / PET_LIB / PET_CLASSES / PET_MOCK / PET_OUT / PET_ERR）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# JDK / LIB 没有可用的仓库内默认值 —— 必须显式给（便携 JDK 目录、javafx+gson+jna 的 jar 目录）
JDK = os.environ.get("PET_JDK", "")
LIB = os.environ.get("PET_LIB", "")
CLASSES = os.environ.get("PET_CLASSES", os.path.join(ROOT, "desktop-pet", "target", "classes"))
MOCK = os.environ.get("PET_MOCK", os.path.join(ROOT, "scripts", "mock_dsh.py"))
PORT = int(os.environ.get("PET_PORT", "3099"))
OUT = os.environ.get("PET_OUT", os.path.join(ROOT, "work", "pet-dsh.log"))
ERR = os.environ.get("PET_ERR", os.path.join(ROOT, "work", "pet-dsh.err"))
if not JDK or not LIB:
    print("需要先设 PET_JDK（便携 JDK 21 目录）和 PET_LIB（javafx/gson/jna 的 jar 目录），例如：")
    print("  set PET_JDK=C:\\path\\to\\jdk-21")
    print("  set PET_LIB=C:\\path\\to\\lib")
    sys.exit(2)

os.makedirs(os.path.join(ROOT, 'work'), exist_ok=True)

RESULTS = []


def http(path, method="GET"):
    # 查询串里的中文要百分号编码
    if "?" in path:
        base, qs = path.split("?", 1)
        pairs = urllib.parse.parse_qsl(qs, keep_blank_values=True)
        path = base + "?" + urllib.parse.urlencode(pairs)
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read().decode("utf-8")


def log_text():
    try:
        with open(OUT, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("   " + detail if detail else ""), flush=True)


def since(marker):
    """取日志里 marker 之后的部分（按出现次数切片）。"""
    t = log_text()
    i = t.rfind(marker)
    return t[i:] if i >= 0 else ""


def wait_for(pattern, timeout, start_index=0):
    """等日志里出现 pattern，返回 (命中?, 该次出现的片段)。"""
    end = time.time() + timeout
    while time.time() < end:
        t = log_text()[start_index:]
        m = re.search(pattern, t)
        if m:
            return True, t[max(0, m.start() - 60):m.end() + 80]
        time.sleep(0.25)
    return False, ""


def drag_probe():
    """拖拽交互自测：--dshdrag 脚本化「抓起来 → 期间宿主改档位 → 松手」。"""
    mp = ";".join([os.path.join(LIB, n) for n in
                   ["javafx-base-21.0.4-win.jar", "javafx-graphics-21.0.4-win.jar",
                    "javafx-controls-21.0.4-win.jar"]])
    cp = ";".join([CLASSES] + [os.path.join(LIB, n) for n in os.listdir(LIB)
                               if n.endswith(".jar")
                               and not re.match(r"^javafx-(base|graphics|controls)-21\.0\.4\.jar$", n)])
    out2 = os.environ.get("PET_DRAG_LOG", os.path.join(ROOT, "work", "pet-dsh-drag.log"))
    open(out2, "w", encoding="utf-8").close()
    p = subprocess.Popen(
        [os.path.join(JDK, "bin", "java.exe"), "-Dstdout.encoding=UTF-8", "-Dfile.encoding=UTF-8",
         "--module-path", mp, "--add-modules", "javafx.controls",
         "-cp", cp, "app.pet.PetApp", "--dsh-port=%d" % PORT, "--dshdrag"],
        stdout=open(out2, "a", encoding="utf-8"), stderr=open(ERR, "a", encoding="utf-8"))
    try:
        def read():
            try:
                return open(out2, "r", encoding="utf-8", errors="replace").read()
            except Exception:
                return ""

        # 等 READY（宿主接上）
        t = time.time() + 25
        while time.time() < t and "[probe] READY" not in read():
            time.sleep(0.3)
        check("拖拽探针就绪", "[probe] READY" in read())

        # 拖拽前：档位 working
        http("/_ctl/tier?state=working", "POST")
        time.sleep(1.0)
        # 等 DRAG-START
        t = time.time() + 15
        while time.time() < t and "[probe] DRAG-START" not in read():
            time.sleep(0.2)
        check("拖拽已开始", "[probe] DRAG-START" in read())
        drag_start_idx = read().find("[probe] DRAG-START")
        # 拖拽中改档位 → 应当被丢弃
        http("/_ctl/tier?state=waiting", "POST")
        time.sleep(1.2)
        during = read()[drag_start_idx:]
        dropped = re.search(r"\[state [^\]]*\] *\S+ → waiting", during) is None
        check("拖拽期间的档位更新被丢弃", dropped,
              during.strip().replace("\n", " | ")[:200])
        # 等 FINAL
        t = time.time() + 15
        while time.time() < t and "[probe] FINAL" not in read():
            time.sleep(0.3)
        final = read()
        m = re.search(r"\[probe\] FINAL 状态=(\S+)", final)
        check("松手后恢复到拖拽期间的档位（waiting）",
              m is not None and m.group(1) == "waiting",
              m.group(0) if m else final[-200:])
    finally:
        try:
            p.terminate()
            p.wait(timeout=6)
        except Exception:
            p.kill()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    py = sys.executable
    # 1) mock 宿主
    mock = subprocess.Popen([py, MOCK, str(PORT)],
                            stdout=open(os.environ.get("PET_MOCK_LOG",
                                                     os.path.join(ROOT, "work", "mock_dsh.log")),
                                        "w", encoding="utf-8"),
                            stderr=subprocess.STDOUT)
    time.sleep(1.5)

    # 2) 桌宠
    mp = ";".join([os.path.join(LIB, n) for n in
                   ["javafx-base-21.0.4-win.jar", "javafx-graphics-21.0.4-win.jar",
                    "javafx-controls-21.0.4-win.jar"]])
    cp = ";".join([CLASSES] + [os.path.join(LIB, n) for n in os.listdir(LIB)
                               if n.endswith(".jar")
                               and not re.match(r"^javafx-(base|graphics|controls)-21\.0\.4\.jar$", n)])
    open(OUT, "w", encoding="utf-8").close()
    pet = subprocess.Popen(
        [os.path.join(JDK, "bin", "java.exe"), "-Dstdout.encoding=UTF-8", "-Dfile.encoding=UTF-8",
         "--module-path", mp, "--add-modules", "javafx.controls",
         "-cp", cp, "app.pet.PetApp", "--dsh-port=%d" % PORT],
        stdout=open(OUT, "a", encoding="utf-8"), stderr=open(ERR, "a", encoding="utf-8"))
    time.sleep(5)
    check("桌宠启动并接入宿主", "已接入" in log_text(), log_text().strip().splitlines()[0] if log_text() else "")

    try:
        # ---- ① 进行中档位：thinking，循环播，不回落待机 ----
        http("/_ctl/tier?state=thinking&task=整理需求", "POST")
        ok, _ = wait_for(r"\[state [^\]]*\] *\S+ → thinking", 8)
        check("档位 thinking → 进入思考态", ok)
        idx = len(log_text())
        time.sleep(7)   # 思考一段 62 帧 @12fps = 5.2s，够走完一轮
        seg = log_text()[idx:]
        check("进行中档位循环播（未回落待机）",
              "thinking" in seg and not re.search(r"\[state [^\]]*\] *\S+ → idle", seg),
              seg.strip().replace("\n", " | ")[:160])

        # ---- ② 档位序下降也要跟随宿主（working → result）----
        http("/_ctl/tier?state=working", "POST")
        ok, _ = wait_for(r"\[state [^\]]*\] *\S+ → calling_tool", 8)
        check("档位 working → 进入调用工具态", ok)
        http("/_ctl/tier?state=result", "POST")
        ok, seg = wait_for(r"\[state [^\]]*\] *\S+ → thinking", 8)
        check("档位序下降 working(40)→result(25) 仍跟随宿主", ok, seg.replace("\n", " | ")[:140])

        # ---- ③ 更高档位抢占（result → waiting）----
        http("/_ctl/tier?state=waiting", "POST")
        ok, _ = wait_for(r"\[state [^\]]*\] *\S+ → waiting", 8)
        check("档位 waiting → 进入等待态", ok)

        # ---- ④ 终态 success：播一遍后回落待机 ----
        http("/_ctl/tier?state=success", "POST")
        ok, _ = wait_for(r"\[state [^\]]*\] *\S+ → complete", 8)
        check("档位 success → 进入完成态", ok)
        idx = len(log_text())
        ok, seg = wait_for(r"\[state [^\]]*\] *\S+ → idle", 12, idx)
        check("终态播一遍后回落待机", ok, seg.replace("\n", " | ")[:140])

        # ---- ⑤ 终态 error ----
        http("/_ctl/tier?state=error", "POST")
        ok, _ = wait_for(r"\[state [^\]]*\] *\S+ → error", 8)
        check("档位 error → 进入出错态", ok)
        idx = len(log_text())
        ok, seg = wait_for(r"\[state [^\]]*\] *\S+ → idle", 12, idx)
        check("出错态播一遍后回落待机", ok, seg.replace("\n", " | ")[:140])

        # ---- ⑥ 宿主回空闲：当前段播完即停 → 待机 ----
        http("/_ctl/tier?state=thinking", "POST")
        wait_for(r"\[state [^\]]*\] *\S+ → thinking", 8)
        http("/_ctl/tier?state=", "POST")
        idx = len(log_text())
        ok, seg = wait_for(r"\[state [^\]]*\] *\S+ → idle", 14, idx)
        check("宿主回空闲 → 当前段播完后回落待机", ok, seg.replace("\n", " | ")[:140])

        # ---- ⑦ 占用锁：冻结自动事件；解锁后重新对齐 ----
        http("/_ctl/lock?locked=true&ttl=25000", "POST")
        time.sleep(1.5)
        idx = len(log_text())
        http("/_ctl/tier?state=working", "POST")
        time.sleep(4)
        seg = log_text()[idx:]
        # 判据：锁生效期间**没有状态切换行**（[state ...] → xxx）。
        # 注意别只找 "→ calling_tool"：档位日志本身也含这个箭头。
        switched = re.search(r"\[state [^\]]*\] *\S+ → calling_tool", seg)
        check("占用锁生效时不响应档位事件", switched is None,
              seg.strip().replace("\n", " | ")[:200])
        http("/_ctl/lock?locked=false", "POST")
        ok, seg = wait_for(r"\[state [^\]]*\] *\S+ → calling_tool", 8)
        check("解锁后重新对齐当前档位", ok, seg.replace("\n", " | ")[:140])

        # ---- ⑧ 位置指令 + pose 上报 ----
        before = len(log_text())
        http("/_ctl/position?x=0.25&y=0.35&mode=instant", "POST")
        ok, seg = wait_for(r"位置指令", 8, before)
        check("位置指令被接收并执行", ok, seg.replace("\n", " | ")[:140])
        time.sleep(2.5)
        mocklog = http("/_ctl/log")
        check("实况 pose 已上报宿主", '"kind": "pose"' in mocklog or '"kind":"pose"' in mocklog,
              mocklog[-220:] if '"pose"' not in mocklog else "")
    finally:
        try:
            pet.terminate()
            pet.wait(timeout=6)
        except Exception:
            pet.kill()

    # ---- ⑨ 拖拽交互：拖拽期间丢档位、松手恢复（独立进程跑 --dshdrag）----
    try:
        drag_probe()
    finally:
        mock.terminate()

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n==== 汇总 ====")
    print("PASS %d / %d" % (len(RESULTS) - len(bad), len(RESULTS)))
    if bad:
        print("失败项：")
        for n in bad:
            print("  - " + n)
    print("\n==== 桌宠日志 ====")
    print(log_text())
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

