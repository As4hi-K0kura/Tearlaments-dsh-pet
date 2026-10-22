#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌宠初始帧归一化：透明 RGBA -> 统一画布纯绿幕 RGB。

口径（与 normalized-uniform/README.md 对齐，且用旧八态帧反推校验过）：
  * 脸 = "脸部肤色块"：肤色连通域里 bbox 宽高比 ∈ [0.78,1.55]、面积 ≥1500、
    位于画面中轴上部、且最靠近中轴的那一块（排除腿/手臂/举着的手）
  * 缩放：按目标脸块宽缩放，预乘 alpha 的 LANCZOS（不预乘边缘发黑）
  * 锚点：脸块中心 -> 画布几何中心
  * 输出：按原 alpha 合成纯绿 #00FF00，存 RGB（无 alpha）

用法：
    python normalize_frame.py <src_rgba.png> <dst_green.png> [--face-width 146] [--canvas 1978]
"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import sys
import json
import numpy as np
from PIL import Image

sys.path.insert(0, str(_paths.TOOLS))
from detect_ipd import find_face_blob, content_mask


def premultiply_resize(img, k):
    a = np.asarray(img).astype(np.float32) / 255.0
    rgb = a[..., :3]
    al = a[..., 3:4]
    pre = rgb * al
    out = np.empty_like(a)
    out[..., :3] = pre
    out[..., 3:4] = al
    pm = Image.fromarray((out * 255.0).round().astype(np.uint8), "RGBA")
    nw = max(1, int(round(img.width * k)))
    nh = max(1, int(round(img.height * k)))
    small = pm.resize((nw, nh), Image.LANCZOS)
    arr = np.asarray(small).astype(np.float32) / 255.0
    al2 = arr[..., 3:4]
    rgb2 = np.where(al2 > 0, arr[..., :3] / np.maximum(al2, 1e-6), 0.0)
    rgb2 = np.clip(rgb2, 0, 1) * al2
    final = np.concatenate([rgb2, al2], axis=2)
    return Image.fromarray((final * 255.0).round().astype(np.uint8), "RGBA")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    src, dst = sys.argv[1], sys.argv[2]
    face_width = 146.0
    canvas = 1978
    a3 = sys.argv[3:]
    for i, a in enumerate(a3):
        if a == "--face-width":
            face_width = float(a3[i + 1])
        if a == "--canvas":
            canvas = int(a3[i + 1])

    img = Image.open(src).convert("RGBA")
    arr = np.asarray(img)
    rgb = arr[..., :3]
    alpha = arr[..., 3] if arr.shape[2] == 4 else None
    if alpha is not None and alpha.min() == 255:
        alpha = None
    op = content_mask(rgb, alpha)

    blob, skin = find_face_blob(rgb, op)
    if blob is None:
        raise SystemExit("未找到脸部肤色块")
    area, fx0, fy0, fx1, fy1 = blob
    fw = fx1 - fx0 + 1
    fh = fy1 - fy0 + 1
    k = face_width / float(fw)

    fc_x = (fx0 + fx1) / 2.0
    fc_y = (fy0 + fy1) / 2.0

    scaled = premultiply_resize(img, k)
    cx = cy = canvas / 2.0
    ox = int(round(cx - fc_x * k))
    oy = int(round(cy - fc_y * k))
    canvas_img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    canvas_img.alpha_composite(scaled, dest=(ox, oy))

    carr = np.asarray(canvas_img).astype(np.float32) / 255.0
    calpha = carr[..., 3:4]
    green = np.zeros_like(carr)
    green[..., 1] = 1.0
    comp_rgb = carr[..., :3] * calpha + green[..., :3] * (1.0 - calpha)
    out = Image.fromarray((np.clip(comp_rgb, 0, 1) * 255.0).round().astype(np.uint8), "RGB")
    out.save(dst)

    out_arr = np.asarray(out)
    content = ~np.all(np.abs(out_arr.astype(np.int16) - np.array([0, 255, 0])) < 14, axis=2)
    ys, xs = np.nonzero(content)
    cb = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    marg = min(cb[0], cb[1], canvas - 1 - cb[2], canvas - 1 - cb[3])
    green_ratio = float((~content).sum()) / content.size

    # 复核：归一化后脸块中心是否落在画布中心
    blob2, _ = find_face_blob(out_arr, content)
    off = None
    if blob2:
        b2 = blob2[1:]
        off = [round((b2[0] + b2[2]) / 2.0 - canvas / 2.0, 1), round((b2[1] + b2[3]) / 2.0 - canvas / 2.0, 1)]
        fw2 = b2[2] - b2[0] + 1
    else:
        fw2 = None

    print(json.dumps({
        "src": src, "dst": dst,
        "src_size": [img.width, img.height],
        "src_face_blob": [fx0, fy0, fx1, fy1],
        "src_face_w": fw, "src_face_h": fh,
        "k": round(k, 4), "canvas": canvas,
        "out_face_blob": list(blob2[1:]) if blob2 else None,
        "out_face_w": fw2,
        "out_face_center_offset": off,
        "char_bbox": list(cb),
        "char_size": [cb[2] - cb[0] + 1, cb[3] - cb[1] + 1],
        "char_ratio_of_canvas": round(((cb[2] - cb[0] + 1) / canvas) * 100, 1),
        "min_margin_px": int(marg),
        "green_ratio": round(green_ratio, 4),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
