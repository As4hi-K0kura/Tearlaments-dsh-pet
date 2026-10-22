"""评估拖拽态逐帧脸心轨迹的质量（能否用于锚点锁定）。

用法: python probe_drag_face.py
"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_atlas import face_bbox_windowed  # noqa: E402

RES = str(_paths.ATLAS_DIR)
m = json.load(open(os.path.join(RES, "manifest.json"), encoding="utf-8"))
want = sys.argv[1] if len(sys.argv) > 1 else "dragging"
s = [x for x in m["states"] if x["id"] == want][0]

im = Image.open(os.path.join(RES, s["atlas"])).convert("RGBA")
fw, fh, cols = s["fw"], s["fh"], s["cols"]
ox, oy = s["contentOrigin"]
hint = (int(round(s["faceCenter"][0] - s["cellX"])),
        int(round(s["faceCenter"][1] - s["cellY"])))

print("hint(local) =", hint, " cell =", (s["cellX"], s["cellY"]))
for half, lo, hi in ((58, 0.65, 1.45), (70, 0.45, 2.2), (80, 0.40, 2.6)):
    xs, ys, ws, miss = [], [], [], 0
    track = []
    for f in range(s["frames"]):
        cx0 = (f % cols) * fw
        cy0 = (f // cols) * fh
        arr = np.array(im.crop((cx0, cy0, cx0 + fw, cy0 + fh)))
        fb = face_bbox_windowed(arr[..., :3], arr[..., 3], hint[0], hint[1],
                                41.0, half=half, lo=lo, hi=hi)
        if fb:
            x0, y0, x1, y1 = fb
            track.append((f, ox + (x0 + x1) / 2.0, ox + (x1 - x0 + 1)))
        else:
            miss += 1
    t = np.array([(a[1], a[2]) for a in track])
    steps = np.abs(np.diff(t[:, 0])) if len(t) > 1 else np.array([0.0])
    print(f"half={half} lo={lo} hi={hi}: 检出 {len(track)}/{s['frames']} 缺 {miss}"
          f"  x极差 {t[:,0].max()-t[:,0].min():.1f}"
          f"  相邻帧跳变 max {steps.max():.1f} 中位 {np.median(steps):.1f}"
          f"  脸宽中位 {np.median(t[:,1]):.1f}")
    if len(track) >= s["frames"] * 0.9:
        print("   前 12 帧 x:", [round(a[1], 1) for a in track[:12]])
