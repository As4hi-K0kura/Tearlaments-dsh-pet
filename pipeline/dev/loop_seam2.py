#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用生产路径（convert）测全长视频的循环接缝 + 透明质量。"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import glob
import os
import shutil
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from green_to_alpha import FF, convert  # noqa

TMP = str(_paths.TMP_FMT)
SRC = str(_paths.VID_FINAL / "pet-idle.mp4")


def decode(out, tag):
    d = os.path.join(TMP, "sd_" + tag)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    subprocess.run([FF, "-y", "-c:v", "libvpx", "-i", out, "-vsync", "0", "-pix_fmt", "rgba",
                    os.path.join(d, "f%05d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(glob.glob(os.path.join(d, "*.png")))


import subprocess  # noqa: E402


def report(out, tag):
    fs = decode(out, tag)
    f0 = np.asarray(Image.open(fs[0]).convert("RGBA")).astype(np.int16)
    fl = np.asarray(Image.open(fs[-1]).convert("RGBA")).astype(np.int16)
    d = np.abs(f0 - fl)
    s0 = np.asarray(Image.fromarray(f0.astype(np.uint8)).resize((96, 96), Image.LANCZOS)).astype(np.int16)
    sL = np.asarray(Image.fromarray(fl.astype(np.uint8)).resize((96, 96), Image.LANCZOS)).astype(np.int16)
    ds = np.abs(s0 - sL)
    # 透明质量
    a0 = a255 = mid = 0
    edge_g = []
    tot = 0
    for p in fs[::4]:
        arr = np.asarray(Image.open(p).convert("RGBA")).astype(np.int16)
        a = arr[..., 3]
        rgb = arr[..., :3]
        ge = rgb[..., 1] - np.maximum(rgb[..., 0], rgb[..., 2])
        a0 += int((a == 0).sum())
        a255 += int((a == 255).sum())
        mid += int(((a > 8) & (a < 247)).sum())
        e = (a > 8) & (a < 247)
        if e.sum() > 20:
            edge_g.append(float(ge[e].mean()))
        tot += a.size
    print("  文件 %.2f MB  帧数 %d" % (os.path.getsize(out) / 1048576.0, len(fs)))
    print("  接缝 全分辨率: rgba_mean %.3f  rgb_max %d  alpha_max %d  px>8 %d" % (
        d.mean(), d[..., :3].max(), d[..., 3].max(), int((d.max(axis=2) > 8).sum())))
    print("  接缝 96px显示:  mean %.3f  max %d  px>8 %d / %d" % (
        ds.mean(), ds.max(), int((ds.max(axis=2) > 8).sum()), ds.shape[0] * ds.shape[1]))
    print("  alpha: ==0 %.2f%%  ==255 %.2f%%  过渡带 %.2f%%   边缘绿 excess 均值 %.2f" % (
        a0 / tot * 100, a255 / tot * 100, mid / tot * 100,
        float(np.mean(edge_g)) if edge_g else -999))


if __name__ == "__main__":
    out = os.path.join(TMP, "seam_test.webm")
    convert(SRC, out, "vp8", "24", keep=False, close_loop=True, bitrate="8M")
    report(out, "seam")
