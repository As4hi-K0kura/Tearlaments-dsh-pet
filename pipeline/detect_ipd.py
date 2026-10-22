#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌宠脸部测量：先定位"脸部肤色块"，再在块内测睫毛眼距(IPD)。

两段式的原因：
  * 单靠肤色最大连通域会选到腿/手臂（细高块）
  * 单靠暗色扁宽块会选到头发描边/衣服线条
  * 先用"最靠画面中上部的近方形肤色块"锁住脸，再在里面找睫毛，最稳

脸块判据：肤色连通域中，bbox 宽高比 ∈ [0.8, 1.5]、面积 ≥ 1500、且最靠近画面中轴上部者。
"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import sys
import numpy as np
from PIL import Image


def is_skin(rgb):
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    return (r > 200) & (g > 175) & (b > 140) & ((r - b) > 8) & (np.abs(r - g) < 45)


def label_components(mask):
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    out = []
    cur = 0
    stack = []
    for sy in range(h):
        row = mask[sy]
        for sx in range(w):
            if not row[sx] or lab[sy, sx]:
                continue
            cur += 1
            size = 0
            minx = maxx = sx
            miny = maxy = sy
            stack.append((sy, sx))
            lab[sy, sx] = cur
            while stack:
                y, x = stack.pop()
                size += 1
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = cur
                        stack.append((ny, nx))
            out.append((size, minx, miny, maxx, maxy))
    return out


def dilate(mask, r):
    out = mask.copy()
    for _ in range(r):
        p = np.pad(out, 1, mode="constant")
        out = p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:] | out
    return out


def content_mask(rgb, alpha=None):
    if alpha is not None:
        return alpha > 40
    # 绿幕图：与纯绿有差异即内容
    return ~np.all(np.abs(rgb.astype(np.int16) - np.array([0, 255, 0])) < 14, axis=2)


def find_face_blob(rgb, op):
    skin = is_skin(rgb) & op
    comps = [c for c in label_components(skin) if c[0] >= 1500]
    ys, xs = np.nonzero(op)
    if len(xs) == 0:
        return None, skin
    cx0, cx1 = xs.min(), xs.max()
    cymid = (ys.min() + ys.max()) / 2.0
    best = None
    for area, x0, y0, x1, y1 in comps:
        w = x1 - x0 + 1
        h = y1 - y0 + 1
        ratio = w / float(h)
        if not (0.78 <= ratio <= 1.55):
            continue
        if y1 < ys.min() + 0.12 * (ys.max() - ys.min()):
            continue  # 太靠上（举着的手）
        ccx = (x0 + x1) / 2.0
        ccy = (y0 + y1) / 2.0
        if ccy > cymid:
            continue  # 腿/脚
        # 越靠画面中轴、越靠上，得分越高
        score = (w * h) * (1.0 - min(abs(ccx - (cx0 + cx1) / 2.0) / (0.5 * (cx1 - cx0 + 1)), 1.0))
        if best is None or score > best[0]:
            best = (score, (area, x0, y0, x1, y1))
    return (best[1] if best else None), skin


def measure(path, verbose=False):
    img = Image.open(path).convert("RGBA")
    arr = np.asarray(img)
    rgb = arr[..., :3]
    alpha = arr[..., 3] if arr.shape[2] == 4 else None
    if alpha is not None and alpha.min() == 255:
        alpha = None
    op = content_mask(rgb, alpha)

    blob, skin = find_face_blob(rgb, op)
    if blob is None:
        return {"path": path, "error": "未找到脸部肤色块"}
    area, fx0, fy0, fx1, fy1 = blob
    fw = fx1 - fx0 + 1
    fh = fy1 - fy0 + 1

    # 在脸块内找睫毛暗线
    pad = int(max(fw, fh) * 0.25)
    x0 = max(0, fx0 - pad); x1 = min(rgb.shape[1] - 1, fx1 + pad)
    y0 = max(0, fy0 - pad); y1 = min(rgb.shape[0] - 1, fy1 + pad)
    win = rgb[y0:y1 + 1, x0:x1 + 1]
    wskin = skin[y0:y1 + 1, x0:x1 + 1]
    dark = win.max(axis=2) < 135
    dark &= ~wskin
    cands = []
    for a2, ax0, ay0, ax1, ay1 in label_components(dark):
        w = ax1 - ax0 + 1
        h = ay1 - ay0 + 1
        if a2 < 20 or a2 > 8000 or w < 5 or h < 3:
            continue
        ratio = w / float(h)
        if not (1.4 <= ratio <= 8.0):
            continue
        cands.append((a2, ax0, ay0, ax1, ay1))
    if verbose:
        print("   face blob:", blob, "cands:", len(cands))

    best = None
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            a, b = cands[i], cands[j]
            acx, acy = (a[1] + a[3]) / 2.0, (a[2] + a[4]) / 2.0
            bcx, bcy = (b[1] + b[3]) / 2.0, (b[2] + b[4]) / 2.0
            if abs(acy - bcy) > 0.4 * max(a[4] - a[2], b[4] - b[2]) + 10:
                continue
            sep = abs(bcx - acx)
            if sep < 8:
                continue
            score = (a[0] + b[0]) * (1.0 - min(sep, 300) / 600.0)
            if best is None or score > best[0]:
                best = (score, a, b, sep)
    res = {
        "path": path,
        "face_blob": [fx0, fy0, fx1, fy1],
        "face_w": fw,
        "face_h": fh,
        "face_center": [(fx0 + fx1) / 2.0, (fy0 + fy1) / 2.0],
    }
    if best:
        _, a, b, sep = best
        res["ipd"] = round(sep, 1)
        res["eye_y"] = round((((a[2] + a[4]) / 2.0) + ((b[2] + b[4]) / 2.0)) / 2.0 + y0, 1)
    else:
        res["ipd"] = None
    return res


if __name__ == "__main__":
    verbose = "--dump" in sys.argv
    for p in [x for x in sys.argv[1:] if not x.startswith("--")]:
        r = measure(p, verbose)
        if "error" in r:
            print(p, "->", r["error"])
        else:
            print("%-58s faceblob w=%3d h=%3d center=(%.0f,%.0f) IPD=%s" % (
                p.split("\\")[-1], r["face_w"], r["face_h"], r["face_center"][0], r["face_center"][1], r["ipd"]))
