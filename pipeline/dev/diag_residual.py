#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定位残留绿像素：区分 edge 带 / solid 区，并看它们的空间形态。"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import glob
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from green_to_alpha import key_frame  # noqa

TMP = str(_paths.TMP_FMT)
SRC = os.path.join(TMP, "src_frames")
KEYED = os.path.join(TMP, "keyed_new")


def erode(mask, n=1):
    cur = mask.copy()
    for _ in range(n):
        p = np.pad(cur, 1, mode="constant")
        cur = (p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] &
               p[1:-1, :-2] & p[1:-1, 2:])
    return cur


def main():
    fs = sorted(glob.glob(os.path.join(KEYED, "*.png")))
    srcs = sorted(glob.glob(os.path.join(SRC, "*.png")))
    tot_edge = tot_solid = tot_all = 0
    worst = []
    for i, p in enumerate(fs[:6]):
        arr = np.asarray(Image.open(p).convert("RGBA")).astype(np.int16)
        a = arr[..., 3]
        rgb = arr[..., :3]
        ge = rgb[..., 1] - np.maximum(rgb[..., 0], rgb[..., 2])
        edge = (a > 8) & (a < 247)
        solid = a >= 247
        bad = (ge > 25) & (a > 8)
        tot_edge += int(edge.sum())
        tot_solid += int(solid.sum())
        tot_all += int(bad.sum())
        ys, xs = np.where(bad)
        if len(ys):
            worst.append((i, len(ys), int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())))
        # solid 里有多少带绿
        if solid.sum():
            sg = solid & (ge > 25)
            print("frame%d: solid 绿像素 %d / %d" % (i, sg.sum(), solid.sum()))
    print("合计: edge=%d solid=%d 绿像素(>25)=%d (占非透明 %.2f%%)" % (
        tot_edge, tot_solid, tot_all, tot_all / max(1, tot_edge + tot_solid) * 100))
    for w in worst:
        print("  frame%d 绿像素 %d bbox x %d..%d y %d..%d" % w)

    # 看一个具体像素的完整链路：源帧 obs / 抠像 fg / alpha
    print("\n=== 单像素链路（第0帧，最绿的一个）===")
    arr = np.asarray(Image.open(fs[0]).convert("RGBA")).astype(np.int16)
    a = arr[..., 3]
    rgb = arr[..., 3] * 0 + arr[..., :3]
    ge = arr[..., 1] - np.maximum(arr[..., 0], arr[..., 2])
    cand = np.argwhere((ge > 40) & (a > 8))
    if len(cand):
        y, x = cand[len(cand) // 2]
        print("位置 (%d,%d) 输出 rgba=%s" % (x, y, tuple(arr[y, x])))
        s = np.asarray(Image.open(srcs[0]).convert("RGB")).astype(np.int16)
        print("源帧 obs =", tuple(s[y, x]))
        a2, c2 = key_frame(s.astype(np.float32))
        print("重算 alpha=%d fg=%s" % (a2[y, x], tuple(c2[y, x].astype(int))))


if __name__ == "__main__":
    main()
