#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定位残留绿像素的来源：源帧 / 无损抠像帧 / VP8 解码帧 三方对照。"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import glob
import os
import subprocess
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from green_to_alpha import key_frame  # noqa

WORK = str(_paths.TMP_VERIFY)
SRC = os.path.join(WORK, "pet-done_s")
DEC = os.path.join(WORK, "pet-done_d")


def main():
    sf = sorted(glob.glob(os.path.join(SRC, "*.png")))
    df = sorted(glob.glob(os.path.join(DEC, "*.png")))
    for i in range(0, len(df), 8):
        d = np.asarray(Image.open(df[i]).convert("RGBA")).astype(np.int16)
        a = d[..., 3]
        rgb = d[..., :3]
        ge = rgb[..., 1] - np.maximum(rgb[..., 0], rgb[..., 2])
        m = (a >= 250) & (ge > 40)
        if not m.any():
            continue
        ys, xs = np.where(m)
        y, x = int(ys[0]), int(xs[0])
        print("帧%d 位置(%d,%d)" % (i, x, y))
        obs = np.asarray(Image.open(sf[i]).convert("RGB")).astype(np.float32)
        ref_a, ref_c = key_frame(obs)
        print("  源绿幕帧 RGB      :", tuple(int(v) for v in obs[y, x]))
        print("  源绿幕帧 绿 excess:", int(obs[y, x, 1] - max(obs[y, x, 0], obs[y, x, 2])))
        print("  无损抠像 alpha    :", int(ref_a[y, x]))
        print("  无损抠像 RGB      :", tuple(int(v) for v in ref_c[y, x]))
        print("  VP8 解码 alpha    :", int(a[y, x]))
        print("  VP8 解码 RGB      :", tuple(int(v) for v in rgb[y, x]))
        # 该点周围 5x5 的源帧与抠像
        print("  源帧 5x5:")
        for yy in range(max(0, y - 2), min(768, y + 3)):
            print("    ", " ".join("%3d,%3d,%3d" % tuple(obs[yy, xx].astype(int)) for xx in range(max(0, x - 2), min(768, x + 3))))
        # 裁图
        box = (max(0, x - 60), max(0, y - 60), min(768, x + 60), min(768, y + 60))
        Image.open(sf[i]).convert("RGB").crop(box).resize((480, 480), Image.NEAREST).save(
            os.path.join(WORK, "green_src_crop.png"))
        Image.open(df[i]).convert("RGBA").crop(box).resize((480, 480), Image.NEAREST).save(
            os.path.join(WORK, "green_dec_crop.png"))
        print("  裁图 -> green_src_crop.png / green_dec_crop.png")
        return


if __name__ == "__main__":
    main()
