#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mock DSH 宿主：实现 dsh-pet 的 /state 长轮询契约 + /pose/report 回报通道。

只用于本地端到端验收（没有真的 dsh-pet 宿主时也能验证桌宠的 DSH 接入）。

实现的部分：
  GET  /dsh-pet-7340/state?since=<counter>&timeout=<ms>   长轮询（counter 变了立刻返回）
  POST /dsh-pet-7340/pose/report?pet=<id>                 渲染端上报实况（记进日志）

测试用的私有控制口（不是 dsh 契约的一部分）：
  POST /_ctl/tier?state=<档位>&task=<文案>                设置工作状态档位（空 state = 空闲）
  POST /_ctl/lock?locked=true|false&ttl=<毫秒>            设置占用锁
  POST /_ctl/position?x=<0~1>&y=<0~1>&mode=<instant|spring|line>&duration=<毫秒>
  GET  /_ctl/log                                         取回 pose 上报与事件日志
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROUTE = "/dsh-pet-7340"
LOCK = threading.Lock()

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

LOG = []
CHANGED = threading.Condition()


def bump():
    with CHANGED:
        CHANGED.notify_all()


def snapshot():
    with LOCK:
        return json.loads(json.dumps(STATE))


def set_tier(state, task):
    with LOCK:
        STATE["sections"]["workStatus"]["data"] = {
            "state": state if state else None,
            "task": task if task else None,
        }
        STATE["sections"]["workStatus"]["counter"] += 1
        STATE["counter"] += 1
    LOG.append({"t": round(time.time(), 3), "kind": "tier", "state": state, "task": task})
    print("[mock] tier=%s task=%s" % (state, task), flush=True)
    bump()


def set_lock(locked, ttl):
    with LOCK:
        pet = STATE["pets"]["main"]
        if locked:
            pet["lock"]["data"] = {"locked": True, "expiresAt": int(time.time() * 1000) + int(ttl)}
        else:
            pet["lock"]["data"] = {"locked": False}
        pet["lock"]["counter"] += 1
        STATE["counter"] += 1
    LOG.append({"t": round(time.time(), 3), "kind": "lock", "locked": bool(locked), "ttl": ttl})
    print("[mock] lock=%s ttl=%s" % (locked, ttl), flush=True)
    bump()


def set_position(x, y, mode, duration):
    with LOCK:
        pet = STATE["pets"]["main"]
        data = {"x": float(x), "y": float(y), "mode": mode}
        if mode == "line":
            data["duration"] = float(duration)
        pet["position"]["data"] = data
        pet["position"]["counter"] += 1
        STATE["counter"] += 1
    LOG.append({"t": round(time.time(), 3), "kind": "position", "x": x, "y": y, "mode": mode})
    print("[mock] position=(%s,%s) mode=%s" % (x, y, mode), flush=True)
    bump()


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
                cur = snapshot()
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
            self._json(200, {"log": LOG})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if u.path == ROUTE + "/pose/report":
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                self._json(400, {"ok": False, "reason": "bad-request"})
                return
            LOG.append({"t": round(time.time(), 3), "kind": "pose", **body})
            print("[mock] pose=%s" % body, flush=True)
            self._json(200, {"ok": True})
            return
        if u.path == "/_ctl/tier":
            set_tier(q.get("state", [""])[0] or None, q.get("task", [""])[0] or None)
            self._json(200, {"ok": True})
            return
        if u.path == "/_ctl/lock":
            set_lock(q.get("locked", ["true"])[0] == "true", int(q.get("ttl", ["5000"])[0]))
            self._json(200, {"ok": True})
            return
        if u.path == "/_ctl/position":
            set_position(q.get("x", ["0.5"])[0], q.get("y", ["0.5"])[0],
                          q.get("mode", ["instant"])[0], q.get("duration", ["400"])[0])
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not found"})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3099
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    print("[mock] dsh-pet mock listening on 127.0.0.1:%d" % port, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
