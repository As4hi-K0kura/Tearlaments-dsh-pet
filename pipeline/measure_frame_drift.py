"""测量各态**逐帧**的脸心位移与内容位移（画布像素），判断"拖动时漂移"是素材自带还是代码造成。

用法: python measure_frame_drift.py
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
from detect_ipd import find_face_blob  # noqa: E402
from build_atlas import face_bbox_windowed  # noqa: E402

RES = str(_paths.ATLAS_DIR)
OUT = sys.argv[1] if len(sys.argv) > 1 else str(_paths.TMP / "drift")

m = json.load(open(os.path.join(RES, "manifest.json"), encoding="utf-8"))
pad = m["grid"]["padding"]
canvas = m["canvas"]

os.makedirs(OUT, exist_ok=True)
summary = []
for s in m["states"]:
    p = os.path.join(RES, s["atlas"])
    im = Image.open(p).convert("RGBA")
    W, H = im.size
    fw, fh, cols = s["fw"], s["fh"], s["cols"]
    # 该态内容在画布里的左上角（= 格子原点 + padding）
    ox, oy = s["contentOrigin"]
    faces = []
    boxes = []
    for f in range(s["frames"]):
        cx0 = (f % cols) * fw
        cy0 = (f // cols) * fh
        cell = im.crop((cx0, cy0, cx0 + fw, cy0 + fh))
        arr = np.array(cell)
        a = arr[..., 3]
        rgb = arr[..., :3]
        ys, xs = np.nonzero(a > 40)
        if xs.size:
            boxes.append((ox + xs.min(), oy + ys.min(), ox + xs.max(), oy + ys.max()))
        else:
            boxes.append(None)
        # 窗口化判据（build_atlas 同款）：以该态的脸心为提示在局部窗口里找脸
        hint = (int(round(s["faceCenter"][0] - s["cellX"])),
                int(round(s["faceCenter"][1] - s["cellY"])))
        fb = face_bbox_windowed(rgb, a, hint[0], hint[1], ref_w=41.0)
        if fb:
            faces.append((ox + (fb[0] + fb[2]) / 2.0, oy + (fb[1] + fb[3]) / 2.0))
        else:
            faces.append(None)
    got = [(i, f) for i, f in enumerate(faces) if f]
    if len(got) < 2:
        summary.append((s["id"], None, None, None))
        continue
    fx = np.array([f[0] for _, f in got])
    fy = np.array([f[1] for _, f in got])
    bb = [b for b in boxes if b]
    bx = np.array([(b[0] + b[2]) / 2.0 for b in bb])
    by = np.array([(b[1] + b[3]) / 2.0 for b in bb])
    summary.append((s["id"],
                    fx.max() - fx.min(), fy.max() - fy.min(),
                    bx.max() - bx.min(), by.max() - by.min(), len(got), s["frames"]))

print(f"{'state':<14}{'脸心x极差':>10}{'脸心y极差':>10}{'内容x极差':>10}{'内容y极差':>10}{'检出帧':>10}")
for row in summary:
    if row[1] is None:
        print(f"{row[0]:<14}  脸未检出")
        continue
    sid, fx, fy, bx, by, n, tot = row
    print(f"{sid:<14}{fx:>10.1f}{fy:>10.1f}{bx:>10.1f}{by:>10.1f}{str(n)+'/'+str(tot):>10}")
print()
print("画布 %dx%d  脚底线 y=%.2f" % (canvas["w"], canvas["h"], canvas["groundY"]))
