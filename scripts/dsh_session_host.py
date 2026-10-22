#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DSH 会话档位宿主（sidecar）：把 DSH 的会话事件流聚合成 dsh-pet 的工作状态档位，
再按 dsh-pet 的 HTTP 契约对外提供 /state —— 桌宠（JavaFX）就能像接 dsh-pet 宿主一样接上。

为什么需要它：
  DSH 宿主里的 dsh-pet 插件（dsh-pet/src/host/work-status.ts）用 ctx.on('session/event')
  在**宿主进程内**监听会话事件，聚合成 workStatus 写进 /state。本机没装 dsh-pet 插件，
  装插件要重开 DSH（会打断正在跑的任务），而插件装好后它自己的 Electron 桌宠也会一起冒出来。
  ��� sidecar 直接读 DSH 落盘的会话事件流，复刻同一套聚合逻辑，对外仍说 dsh-pet 的契约
  —— 桌宠侧一行代码都不用改。

档位聚合（照抄 dsh-pet 的 reduceWorkStatus + pickDisplayed）：
  turn/start      → thinking
  tool/call       → working（工具名是 ask_user_question → waiting）
  tool/result     → result
  approval/asked  → waiting
  turn/end        → completed→success / error|max-tokens|timeout→error / blocked→waiting
                    其它（aborted 等）→ null＝该会话回空闲
  跨会话取最高：waiting 60 > error 50 > working 40 > thinking 30 > result 25 > success 20

用法：
  python dsh_session_host.py [--port=3080] [--sessions=<目录>] [--poll=0.5]
默认监听 3080 —— 正好落在桌宠的自动发现扫描范围（3080~3104）里，桌宠用 --dsh 就能自动接上。
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    import zstandard
except ImportError:
    print("缺少 zstandard：pip install zstandard", file=sys.stderr)
    sys.exit(2)

ROUTE = "/dsh-pet-7340"

# dsh 的档位序（host/src/host/work-status.ts 的 WORK_STATUS_PRIORITY）
PRIORITY = {
    "waiting": 60, "error": 50, "working": 40,
    "thinking": 30, "result": 25, "success": 20,
}
TERMINAL = {"success", "error"}

LOCK = threading.Lock()
CHANGED = threading.Condition()

# 对外的状态（形状与 dsh-pet 的 /state 一致）
STATE = {
    "counter": 1,
    "sections": {
        "balance": {"counter": 0, "data": None},
        "workStatus": {"counter": 0, "data": {"state": None, "task": None}},
        "notify": {"counter": 0, "data": None},
        "config": {"counter": 0, "data": {}},
    },
    "pets": {
        "main": {
            "say": {"counter": 0, "data": None},
            "anim": {"counter": 0, "data": None},
            "position": {"counter": 0, "data": None},
            "throw": {"counter": 0, "data": None},
            "lock": {"counter": 0, "data": None},
        }
    },
}
POSES = []

# 每个会话的聚合状态：sessionId -> {"tier": str|None, "task": str|None, "seq": int}
SESSIONS = {}
# 记住每个文件解到哪了：path -> (mtime, size, max_seq)
FILES = {}


def bump():
    with CHANGED:
        CHANGED.notify_all()


def set_state(tier, task):
    """更新对外的 workStatus 快照（变化才动 counter）。"""
    with LOCK:
        cur = STATE["sections"]["workStatus"]["data"]
        if cur.get("state") == tier and cur.get("task") == task:
            return False
        cur["state"] = tier
        cur["task"] = task
        STATE["sections"]["workStatus"]["counter"] += 1
        STATE["counter"] += 1
    bump()
    return True


def pick_displayed():
    """跨会话取优先级最高的那条（同级取最近更新的）。"""
    best = None
    best_key = None
    for sid, s in SESSIONS.items():
        tier = s.get("tier")
        if not tier:
            continue
        key = (PRIORITY.get(tier, 0), s.get("seq", 0))
        if best_key is None or key > best_key:
            best_key = key
            best = s
    if best is None:
        return None, None
    return best.get("tier"), best.get("task")


def apply_event(session_id, ev):
    """把一条 DSH 会话事件压成档位（对应 dsh-pet 的 reduceWorkStatus）。"""
    etype = ev.get("type")
    data = ev.get("data") or {}
    s = SESSIONS.setdefault(session_id, {"tier": None, "task": None, "seq": 0})
    s["seq"] = ev.get("seq", s["seq"])

    if etype == "turn/start":
        s["tier"] = "thinking"
        s["task"] = None
    elif etype == "tool/call":
        name = str(data.get("name") or "")
        s["tier"] = "waiting" if name == "ask_user_question" else "working"
    elif etype == "tool/result":
        s["tier"] = "result"
    elif etype == "approval/asked":
        s["tier"] = "waiting"
    elif etype == "turn/end":
        kind = str((data.get("reason") or {}).get("kind") or "")
        if kind == "completed":
            s["tier"] = "success"
        elif kind in ("error", "max-tokens", "timeout"):
            s["tier"] = "error"
        elif kind == "blocked":
            s["tier"] = "waiting"
        else:
            # aborted 等：回合已结束 → 清该会话，绝不残留上一档
            s["tier"] = None
            s["task"] = None
    elif etype == "todo/write":
        # 只更新任务详情文案，不切档位
        s["task"] = current_task_from_todo(data)
    else:
        return
    s["seq"] = ev.get("seq", s["seq"])


def current_task_from_todo(data):
    """取最后一个 in_progress（没有就取第一个 pending），截断 40 码点。"""
    todos = data.get("todos")
    if not isinstance(todos, list):
        return None
    item = None
    for t in todos:
        if isinstance(t, dict) and t.get("status") == "in_progress":
            item = t
    if item is None:
        for t in todos:
            if isinstance(t, dict) and t.get("status") == "pending":
                item = t
                break
    if item is None:
        return None
    text = str(item.get("content") or "").strip()
    if not text:
        return None
    if len(text) > 40:
        text = text[:40] + "…"
    return text


def scan_once(sessions_dir, dec, max_files=8, max_age=600.0):
    """扫一遍会话目录，聚合各会话档位。返回 (tier, task) 或 None（无变化时）。"""
    files = []
    try:
        for ws in os.listdir(sessions_dir):
            d = os.path.join(sessions_dir, ws)
            if not os.path.isdir(d):
                continue
            for sid in os.listdir(d):
                p = os.path.join(d, sid, "session.v4.jsonl.zstd")
                if os.path.isfile(p):
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    files.append((st.st_mtime, st.st_size, p, sid))
    except OSError:
        return None

    files.sort(reverse=True)
    now = time.time()
    for mtime, size, path, sid in files[:max_files]:
        if now - mtime > max_age:
            continue
        prev = FILES.get(path)
        if prev and prev[0] == mtime and prev[1] == size:
            continue   # 没变化，跳过（省解压）
        try:
            with open(path, "rb") as f:
                raw = f.read()
            text = dec.stream_reader(raw).read().decode("utf-8", "replace")
        except Exception:
            continue
        max_seq = prev[2] if prev else -1
        new_max = max_seq
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            seq = ev.get("seq")
            if isinstance(seq, int) and seq <= max_seq:
                continue
            if isinstance(seq, int):
                new_max = max(new_max, seq)
            apply_event(sid, ev)
        FILES[path] = (mtime, size, new_max)

    # 丢掉不再活跃的会话条目（文件太久没动且没有未结束的回合）
    for path in list(FILES.keys()):
        if not os.path.isfile(path):
            FILES.pop(path, None)
    return pick_displayed()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == ROUTE + "/state":
            since = int(q.get("since", ["0"])[0])
            timeout = int(q.get("timeout", ["0"])[0])
            deadline = time.time() + min(max(timeout, 0), 10000) / 1000.0
            while True:
                with LOCK:
                    cur = json.loads(json.dumps(STATE))
                if cur["counter"] != since:
                    self._json(200, cur)
                    return
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._json(200, cur)
                    return
                with CHANGED:
                    CHANGED.wait(min(remaining, 0.5))
        elif u.path == "/_ctl/log":
            self._json(200, {"poses": POSES, "sessions": SESSIONS})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if u.path == ROUTE + "/pose/report":
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                self._json(400, {"ok": False, "reason": "bad-request"})
                return
            POSES.append({"t": round(time.time(), 3), **body})
            if len(POSES) > 200:
                del POSES[:-200]
            print("[host] pose=%s" % body, flush=True)
            self._json(200, {"ok": True})
            return
        if u.path == "/_ctl/refresh":
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not found"})


def main():
    # Windows 上 stdout 重定向到文件时用系统 ANSI 编码（GBK），这里统一成 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3080)
    ap.add_argument("--sessions", default=os.path.join(os.path.expanduser("~"), ".dsh", "sessions"))
    ap.add_argument("--poll", type=float, default=0.5)
    args = ap.parse_args()

    dec = zstandard.ZstdDecompressor()

    def poller():
        last = None
        while True:
            try:
                r = scan_once(args.sessions, dec)
                if r is not None and r != last:
                    tier, task = r
                    changed = set_state(tier, task)
                    if changed:
                        print("[host] 档位 %s  %s" % (tier, task or ""), flush=True)
                    last = r
            except Exception as e:
                print("[host] 轮询异常:", e, flush=True)
            time.sleep(args.poll)

    t = threading.Thread(target=poller, name="poll", daemon=True)
    t.start()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    print("[host] DSH 会话档位宿主已启动：http://127.0.0.1:%d%s/state" % (args.port, ROUTE), flush=True)
    print("[host] 会话目录：%s" % args.sessions, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
