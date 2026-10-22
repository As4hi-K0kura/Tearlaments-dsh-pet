#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全长视频的循环接缝检验 + 末帧强制关键帧方案对比。"""
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

TMP = str(_paths.TMP_FMT)
SRC = str(_paths.VID_FINAL / "pet-idle.mp4")
FULL = os.path.join(TMP, "full_frames")
KEY = os.path.join(TMP, "full_keyed")


def key_full():
    if os.path.isdir(FULL):
        shutil.rmtree(FULL)
    os.makedirs(FULL)
    subprocess.run([FF, "-y", "-i", SRC, "-vsync", "0", os.path.join(FULL, "s%05d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.isdir(KEY):
        shutil.rmtree(KEY)
    os.makedirs(KEY)
    fs = sorted(glob.glob(os.path.join(FULL, "*.png")))
    for i, p in enumerate(fs):
        rgb = np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
        a, c = key_frame(rgb)
        Image.fromarray(np.dstack([c.round().astype(np.uint8), a]), "RGBA").save(
            os.path.join(KEY, "f%05d.png" % (i + 1)))
    return len(fs)


def encode(name, extra):
    out = os.path.join(TMP, "f_%s.webm" % name)
    cmd = [FF, "-y", "-framerate", "24", "-i", os.path.join(KEY, "f%05d.png"),
           "-an", "-c:v", "libvpx", "-pix_fmt", "yuva420p"] + extra + [out]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def decode(out, tag):
    d = os.path.join(TMP, "fd_" + tag)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    subprocess.run([FF, "-y", "-c:v", "libvpx", "-i", out, "-vsync", "0", "-pix_fmt", "rgba",
                    os.path.join(d, "f%05d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(glob.glob(os.path.join(d, "*.png")))


def seam(fs):
    f0 = np.asarray(Image.open(fs[0]).convert("RGBA")).astype(np.int16)
    fl = np.asarray(Image.open(fs[-1]).convert("RGBA")).astype(np.int16)
    d = np.abs(f0 - fl)
    # 96px 显示尺寸下的差异（模拟桌宠实际显示）
    small0 = np.asarray(Image.fromarray(f0.astype(np.uint8)).resize((96, 96), Image.LANCZOS)).astype(np.int16)
    smallL = np.asarray(Image.fromarray(fl.astype(np.uint8)).resize((96, 96), Image.LANCZOS)).astype(np.int16)
    ds = np.abs(small0 - smallL)
    return {
        "rgba_mean": round(float(d.mean()), 3),
        "rgb_max": int(d[..., :3].max()),
        "alpha_max": int(d[..., 3].max()),
        "px_over_8": int((d.max(axis=2) > 8).sum()),
        "at96_mean": round(float(ds.mean()), 3),
        "at96_max": int(ds.max()),
        "at96_px_over_8": int((ds.max(axis=2) > 8).sum()),
    }


if __name__ == "__main__":
    n = key_full()
    print("帧数", n)
    # 先看抠像后（编码前）首尾是否逐像素相同
    kfs = sorted(glob.glob(os.path.join(KEY, "*.png")))
    f0 = np.asarray(Image.open(kfs[0]).convert("RGBA")).astype(np.int16)
    fl = np.asarray(Image.open(kfs[-1]).convert("RGBA")).astype(np.int16)
    print("抠像后 首尾帧差异: mean %.4f  max %d  (源 MP4 首尾已 loop-fix)" % (
        np.abs(f0 - fl).mean(), np.abs(f0 - fl).max()))

    cases = [
        ("br8M", ["-b:v", "8M", "-auto-alt-ref", "0", "-qmin", "4", "-qmax", "48", "-lag-in-frames", "0"]),
        ("br8M_lastkey", ["-b:v", "8M", "-auto-alt-ref", "0", "-qmin", "4", "-qmax", "48",
                          "-lag-in-frames", "0", "-force_key_frames", "expr:gte(n,%d)" % (n - 1)]),
    ]
    for name, extra in cases:
        out = encode(name, extra)
        fs = decode(out, name)
        print("%-16s 大小 %.2f MB  帧数 %d" % (name, os.path.getsize(out) / 1048576.0, len(fs)))
        print("   接缝:", seam(fs))
