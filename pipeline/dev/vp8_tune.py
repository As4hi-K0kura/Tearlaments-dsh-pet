#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""VP8+alpha 编码参数定稿：码率模式对比 + 循环接缝检验。"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import glob
import json
import os
import shutil
import subprocess

import numpy as np
from PIL import Image

FF = r"C:\Users\KSO\AppData\Local\Programs\Python\Python312\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
TMP = str(_paths.TMP_FMT)
REF = os.path.join(TMP, "keyed_new")      # 无损抠像真值（24 帧）
SRC_MP4 = os.path.join(TMP, "clip24.mp4")


def enc(name, extra):
    out = os.path.join(TMP, "p_%s.webm" % name)
    cmd = [FF, "-y", "-framerate", "24", "-i", os.path.join(REF, "f%05d.png"),
           "-an", "-c:v", "libvpx", "-pix_fmt", "yuva420p"] + extra + [out]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def decode(out, tag):
    d = os.path.join(TMP, "pd_" + tag)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    subprocess.run([FF, "-y", "-c:v", "libvpx", "-i", out, "-vsync", "0", "-pix_fmt", "rgba",
                    os.path.join(d, "f%05d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(glob.glob(os.path.join(d, "*.png")))


def metrics(out, tag):
    fs = decode(out, tag)
    refs = sorted(glob.glob(os.path.join(REF, "*.png")))
    n = min(len(fs), len(refs))
    se = 0.0
    cnt = 0
    alpha_d = []
    edge_g = []
    for i in range(n):
        dec = np.asarray(Image.open(fs[i]).convert("RGBA")).astype(np.float32)
        ref = np.asarray(Image.open(refs[i]).convert("RGBA")).astype(np.float32)
        d2 = ((dec[..., :3] - ref[..., :3]) ** 2).sum(axis=2)
        se += float(d2.sum())
        cnt += d2.size
        alpha_d.append(float(np.abs(dec[..., 3] - ref[..., 3]).mean()))
        a = dec[..., 3]
        rgb = dec[..., :3]
        ge = rgb[..., 1] - np.maximum(rgb[..., 0], rgb[..., 2])
        e = (a > 8) & (a < 247)
        if e.sum() > 20:
            edge_g.append(float(ge[e].mean()))
    psnr = 10 * np.log10(255.0 ** 2 / max(se / cnt, 1e-9))
    # 循环接缝：末帧 vs 首帧
    f0 = np.asarray(Image.open(fs[0]).convert("RGBA")).astype(np.int16)
    fl = np.asarray(Image.open(fs[-1]).convert("RGBA")).astype(np.int16)
    diff = np.abs(f0 - fl)
    seam = {
        "rgba_mean": round(float(diff.mean()), 3),
        "rgb_max": int(diff[..., :3].max()),
        "alpha_max": int(diff[..., 3].max()),
        "diff_px_over_8": int((diff.max(axis=2) > 8).sum()),
    }
    size = os.path.getsize(out)
    return {
        "tag": tag,
        "size_kb": round(size / 1024, 1),
        "est_124f_mb": round(size / 1024.0 / n * 124 / 1024.0, 2),
        "rgb_psnr_db": round(psnr, 2),
        "alpha_absdiff_mean": round(float(np.mean(alpha_d)), 3),
        "edge_green_mean": round(float(np.mean(edge_g)), 2) if edge_g else None,
        "loop_seam": seam,
    }


if __name__ == "__main__":
    cands = [
        ("br8M", ["-b:v", "8M", "-auto-alt-ref", "0", "-qmin", "4", "-qmax", "48", "-lag-in-frames", "0"]),
        ("crf4", ["-crf", "4", "-b:v", "0", "-auto-alt-ref", "0", "-lag-in-frames", "0"]),
        ("crf10", ["-crf", "10", "-b:v", "0", "-auto-alt-ref", "0", "-lag-in-frames", "0"]),
        ("crf16", ["-crf", "16", "-b:v", "0", "-auto-alt-ref", "0", "-lag-in-frames", "0"]),
        ("crf10_g1", ["-crf", "10", "-b:v", "0", "-auto-alt-ref", "0", "-lag-in-frames", "0", "-g", "1"]),
    ]
    rows = []
    for name, extra in cands:
        out = enc(name, extra)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            rows.append(metrics(out, name))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
