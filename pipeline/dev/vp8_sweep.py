#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""VP8+alpha WebM 的码率-画质扫描，与无损 APNG 对照。"""
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
REF = os.path.join(TMP, "keyed_new")          # 无损抠像真值
SRC = os.path.join(TMP, "src_frames")          # 绿幕源帧


def encode(bitrate, out):
    cmd = [FF, "-y", "-framerate", "24", "-i", os.path.join(REF, "f%05d.png"),
           "-an", "-c:v", "libvpx", "-pix_fmt", "yuva420p",
           "-b:v", bitrate, "-auto-alt-ref", "0", "-qmin", "4", "-qmax", "48",
           "-lag-in-frames", "0", "-auto-alt-ref", "0", out]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def decode(out, tag):
    d = os.path.join(TMP, "sw_" + tag)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    cmd = [FF, "-y"]
    if out.endswith(".webm"):
        cmd += ["-c:v", "libvpx"]      # VP8 alpha 只有 libvpx 解码器会读
    cmd += ["-i", out, "-vsync", "0", "-pix_fmt", "rgba", os.path.join(d, "f%05d.png")]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(glob.glob(os.path.join(d, "*.png")))


def evaluate(video, tag):
    fs = decode(video, tag)
    refs = sorted(glob.glob(os.path.join(REF, "*.png")))
    srcs = sorted(glob.glob(os.path.join(SRC, "*.png")))
    n = min(len(fs), len(refs))
    adiff = []
    rgb_sq = []
    rgb_n = 0
    rgb_sum = 0.0
    edge_g = []
    recon_err = []
    holes = 0
    trans_colored = 0
    for i in range(n):
        dec = np.asarray(Image.open(fs[i]).convert("RGBA")).astype(np.float32)
        ref = np.asarray(Image.open(refs[i]).convert("RGBA")).astype(np.float32)
        src = np.asarray(Image.open(srcs[i]).convert("RGB")).astype(np.float32)
        a = dec[..., 3]
        ar = ref[..., 3]
        adiff.append(np.abs(a - ar).mean())
        d2 = ((dec[..., :3] - ref[..., :3]) ** 2).sum(axis=2)
        rgb_sq.append(float((d2.sum()) ** 0.5))
        rgb_n += d2.size
        rgb_sum += float(d2.sum())
        # 回合成误差（对源帧）
        bg = np.array([0.0, 244.0, 0.0], np.float32)
        recon = (a[..., None] / 255.0) * dec[..., :3] + (1 - a[..., None] / 255.0) * bg
        recon_err.append(np.abs(recon - src).mean())
        # 角色内部是否有被打洞（参考为不透明但结果为透明）
        holes += int(((ar > 250) & (a < 128)).sum())
        mx = np.maximum(dec[..., 0], dec[..., 2])
        ge = dec[..., 1] - mx
        e = (a > 8) & (a < 247)
        if e.sum() > 20:
            edge_g.append(float(ge[e].mean()))
        trans_colored += int(((a == 0) & (dec[..., :3].max(axis=2) > 12)).sum())
    mse = rgb_sum / rgb_n
    psnr = 10 * np.log10(255.0 ** 2 / max(mse, 1e-9))
    size = os.path.getsize(video)
    return {
        "tag": tag,
        "size_kb": round(size / 1024, 1),
        "est_124f_mb": round(size / 1024.0 / n * 124 / 1024.0, 2),
        "alpha_absdiff_mean": round(float(np.mean(adiff)), 3),
        "rgb_psnr_db": round(psnr, 2),
        "recon_err_vs_src": round(float(np.mean(recon_err)), 3),
        "edge_green_mean": round(float(np.mean(edge_g)), 2) if edge_g else None,
        "holes_inside_char": holes,
        "transparent_px_with_color": trans_colored,
    }


if __name__ == "__main__":
    rows = []
    for br in ("1M", "2M", "4M", "8M", "16M"):
        out = os.path.join(TMP, "sw_%s.webm" % br)
        encode(br, out)
        if os.path.exists(out):
            rows.append(evaluate(out, br))
    # 无损基准
    apng = os.path.join(TMP, "t_apng.apng")
    if os.path.exists(apng):
        rows.append(evaluate(apng, "apng_lossless"))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
