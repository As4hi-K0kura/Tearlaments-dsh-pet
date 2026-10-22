
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import numpy as np
from PIL import Image
import sys, os
sys.path.insert(0, str(_paths.TOOLS))
from detect_ipd import label_components, find_face_blob, content_mask

def eye_scale(path):
    img = Image.open(path).convert("RGBA")
    arr = np.asarray(img)
    rgb = arr[..., :3]
    alpha = arr[..., 3] if arr.shape[2] == 4 else None
    if alpha is not None and alpha.min() == 255:
        alpha = None
    op = content_mask(rgb, alpha)
    blob, skin = find_face_blob(rgb, op)
    if blob is None:
        return None
    area, x0, y0, x1, y1 = blob
    a = rgb.astype(np.int16)
    sr, sg, sb = a[..., 0], a[..., 1], a[..., 2]
    violet = (sb > sr + 45) & (sb > 130) & (sr < 200) & (sg < 210)
    violet &= op
    # 只取脸块内
    m = np.zeros_like(violet)
    m[y0:y1 + 1, x0:x1 + 1] = violet[y0:y1 + 1, x0:x1 + 1]
    comps = sorted(label_components(m), reverse=True)[:4]
    eyes = []
    for c in comps:
        size, ax0, ay0, ax1, ay1 = c
        w = ax1 - ax0 + 1
        h = ay1 - ay0 + 1
        if 12 <= w <= 90 and 12 <= h <= 90 and size >= 60:
            eyes.append((size, ax0, ay0, ax1, ay1, w, h))
    if len(eyes) < 2:
        return None
    # 取面积最大的两块作为双眼
    e1, e2 = eyes[0], eyes[1]
    c1 = ((e1[1] + e1[3]) / 2, (e1[2] + e1[4]) / 2)
    c2 = ((e2[1] + e2[3]) / 2, (e2[2] + e2[4]) / 2)
    dist = ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2) ** 0.5
    return {
        "eye1": (e1[5], e1[6]), "eye2": (e2[5], e2[6]),
        "iris_diag_avg": round((max(e1[5], e1[6]) + max(e2[5], e2[6])) / 2.0, 1),
        "ipd_2d": round(dist, 1),
        "ipd_x": round(abs(c1[0] - c2[0]), 1),
        "face_blob": (x0, y0, x1, y1),
        "face_w": x1 - x0 + 1,
    }

for p in sys.argv[1:]:
    r = eye_scale(p)
    name = os.path.basename(p)
    if r is None:
        print("%-26s  未取到双眼" % name)
    else:
        print("%-26s 虹膜直径=%5.1f  眼距2D=%6.1f  眼距X=%6.1f  脸块w=%3d" % (
            name, r["iris_diag_avg"], r["ipd_2d"], r["ipd_x"], r["face_w"]))
