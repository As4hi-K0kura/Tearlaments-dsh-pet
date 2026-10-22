#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""正确口径复核：
  1. 破洞 = 无损抠像真值里 alpha>=250 的像素，在解码输出里却 alpha<128
  2. 定位残留绿像素（按空间位置 + 是否有绿色元素）
"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import glob
import os
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from green_to_alpha import FF, key_frame  # noqa

SRC_DIR = str(_paths.VID_FINAL)
OUT_DIR = str(_paths.VID_ALPHA)
WORK = str(_paths.TMP_VERIFY)


def dec(video, d, decoder=None):
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    cmd = [FF, "-y"]
    if decoder:
        cmd += ["-c:v", decoder]
    cmd += ["-i", video, "-vsync", "0", "-pix_fmt", "rgba", os.path.join(d, "f%05d.png")]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(glob.glob(os.path.join(d, "*.png")))


def main():
    for v in sorted(glob.glob(os.path.join(OUT_DIR, "*.webm"))):
        st = os.path.splitext(os.path.basename(v))[0]
        src = os.path.join(SRC_DIR, st + ".mp4")
        sf = dec(src, os.path.join(WORK, st + "_s"))
        df = dec(v, os.path.join(WORK, st + "_d"), decoder="libvpx")
        n = min(len(sf), len(df))
        idxs = list(range(0, n, max(1, n // 4)))[:4]
        hard = soft = 0
        green_solid = []
        for i in idxs:
            obs = np.asarray(Image.open(sf[i]).convert("RGB")).astype(np.float32)
            ref_a, ref_c = key_frame(obs)
            d = np.asarray(Image.open(df[i]).convert("RGBA")).astype(np.int16)
            a = d[..., 3]
            hard += int(((ref_a >= 250) & (a < 128)).sum())
            soft += int(((ref_a >= 250) & (a < 250)).sum())
            rgb = d[..., :3]
            ge = rgb[..., 1] - np.maximum(rgb[..., 0], rgb[..., 2])
            solid = a >= 250
            bad = solid & (ge > 25)
            if bad.sum():
                ys, xs = np.where(bad)
                green_solid.append((int(bad.sum()), int(xs.min()), int(xs.max()),
                                    int(ys.min()), int(ys.max()),
                                    float(ge[bad].mean()), float(ge[bad].max())))
        gs = "; ".join("n=%d x%d..%d y%d..%d 均%.1f 峰%.0f" % g for g in green_solid) or "无"
        print("%-11s 硬破洞 %5d  实心区被啃 %6d  | 实心区绿像素: %s" % (st, hard, soft, gs))


if __name__ == "__main__":
    main()
