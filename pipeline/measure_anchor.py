"""测各态逐帧的**顶部锚点**（最高处内容的水平中心）稳定性 —— 拖拽锚的候选。

用法: python measure_anchor.py
"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import json
import os

import numpy as np
from PIL import Image

RES = str(_paths.ATLAS_DIR)

m = json.load(open(os.path.join(RES, "manifest.json"), encoding="utf-8"))

print(f"{'state':<14}{'顶部锚x极差':>12}{'顶部锚y极差':>12}{'顶部行宽极差':>12}  备注")
for s in m["states"]:
    p = os.path.join(RES, s["atlas"])
    im = Image.open(p).convert("RGBA")
    fw, fh, cols = s["fw"], s["fh"], s["cols"]
    ox, oy = s["contentOrigin"]
    anchors = []
    widths = []
    for f in range(s["frames"]):
        cx0 = (f % cols) * fw
        cy0 = (f // cols) * fh
        a = np.array(im.crop((cx0, cy0, cx0 + fw, cy0 + fh)))[..., 3]
        mask = a > 40
        rows = np.flatnonzero(mask.any(axis=1))
        if rows.size == 0:
            anchors.append(None)
            continue
        r0 = rows[0]
        xs = np.flatnonzero(mask[r0])
        anchors.append((ox + (xs.min() + xs.max()) / 2.0, oy + r0))
        widths.append(xs.max() - xs.min() + 1)
    got = [a for a in anchors if a]
    ax = np.array([a[0] for a in got])
    ay = np.array([a[1] for a in got])
    print(f"{s['id']:<14}{ax.max()-ax.min():>12.1f}{ay.max()-ay.min():>12.1f}"
          f"{max(widths)-min(widths):>12}  检出 {len(got)}/{s['frames']}")
