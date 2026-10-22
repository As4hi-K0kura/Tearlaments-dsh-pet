#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""腐蚀深度判定：绿染像素是薄边带（可被传播填充）还是实心色块（会打洞）。"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import glob
import os

import numpy as np
from PIL import Image

TMP = str(_paths.TMP_FMT)
SRC = os.path.join(TMP, "src_frames")


def erode(mask, n=1):
    cur = mask.copy()
    for _ in range(n):
        p = np.pad(cur, 1, mode="constant")
        cur = (p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] &
               p[1:-1, :-2] & p[1:-1, 2:])
    return cur


def main():
    fs = sorted(glob.glob(os.path.join(SRC, "*.png")))
    for idx in (0, 12):
        arr = np.asarray(Image.open(fs[idx]).convert("RGB")).astype(np.int16)
        r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
        bg = np.array([0.0, 244.0, 0.0])
        d = np.abs(arr - bg[None, None, :]).max(axis=2)
        green_dom = g - np.maximum(r, b)
        strong = d > 60
        gs = strong & (green_dom > 25)
        print("frame%02d: strong=%d  绿染强前景=%d (%.2f%%)" % (
            idx, strong.sum(), gs.sum(), gs.sum() / max(1, strong.sum()) * 100))
        m = gs
        for n in range(1, 7):
            m = erode(m)
            print("   腐蚀%d 像素后剩余 %d" % (n, m.sum()))
        # 绿染像素离最近的非绿染强前景有多远（决定传播半径够不够）
        print()
    # 目视：把绿染区域标红存图
    p = fs[0]
    arr = np.asarray(Image.open(p).convert("RGB")).astype(np.int16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    bg = np.array([0.0, 244.0, 0.0])
    d = np.abs(arr - bg[None, None, :]).max(axis=2)
    green_dom = g - np.maximum(r, b)
    strong = d > 60
    gs = strong & (green_dom > 25)
    vis = np.zeros(arr.shape, np.uint8)
    vis[strong] = (60, 60, 60)
    vis[gs] = (255, 0, 0)
    out = Image.fromarray(vis)
    out = out.crop((150, 220, 700, 780)).resize((550, 560), Image.NEAREST)
    out.save(os.path.join(TMP, "diag_green_overlay.png"))
    print("绿染区域标注图 ->", os.path.join(TMP, "diag_green_overlay.png"))


if __name__ == "__main__":
    main()
