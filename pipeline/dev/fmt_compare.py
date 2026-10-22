#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""透明格式终选对比：alpha 保真 / 绿边 / 体积 / 合成目视。"""
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
REF = os.path.join(TMP, "t_apng.apng_rgba")   # 抠像后的真值 RGBA 帧


def decode(video, tag):
    d = os.path.join(TMP, "dec_" + tag)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    subprocess.run([FF, "-y", "-i", video, "-vsync", "0", "-pix_fmt", "rgba",
                    os.path.join(d, "f%05d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(glob.glob(os.path.join(d, "*.png")))


def report(video, tag):
    fs = decode(video, tag)
    refs = sorted(glob.glob(os.path.join(REF, "*.png")))
    n = min(len(fs), len(refs))
    a_diffs, a_max = [], []
    edge_g, edge_g_max = [], 0.0
    solid_g = []
    trans_colored = 0
    tot = 0
    for i in range(n):
        dec = np.asarray(Image.open(fs[i]).convert("RGBA")).astype(np.int16)
        ref = np.asarray(Image.open(refs[i]).convert("RGBA")).astype(np.int16)
        a = dec[..., 3]
        ar = ref[..., 3]
        d = np.abs(a - ar)
        a_diffs.append(float(d.mean()))
        a_max.append(int(d.max()))
        rgb = dec[..., :3]
        mx = np.maximum(rgb[..., 0], rgb[..., 2])
        ge = rgb[..., 1] - mx
        edge = (a > 8) & (a < 247)
        if edge.sum() > 20:
            edge_g.append(float(ge[edge].mean()))
            edge_g_max = max(edge_g_max, float(ge[edge].max()))
        solid = a >= 247
        if solid.sum() > 20:
            solid_g.append(float(ge[solid].mean()))
        trans_colored += int(((a == 0) & (rgb.max(axis=2) > 12)).sum())
        tot += a.size
    # 合成目视：洋红底 + 棋盘底，各取 4 帧
    tiles = []
    for p in fs[:: max(1, n // 4)][:4]:
        im = Image.open(p).convert("RGBA")
        bg = Image.new("RGBA", im.size, (160, 0, 160, 255))
        bg.alpha_composite(im)
        tiles.append(bg.convert("RGB").resize((300, 300), Image.LANCZOS))
    sheet = Image.new("RGB", (300 * len(tiles), 300), (0, 0, 0))
    for i, t in enumerate(tiles):
        sheet.paste(t, (300 * i, 0))
    sheet.save(os.path.join(TMP, "comp_%s.png" % tag))

    size = os.path.getsize(video)
    return {
        "tag": tag,
        "frames": n,
        "size_kb": round(size / 1024, 1),
        "kb_per_frame": round(size / 1024.0 / n, 1),
        "est_124f_mb": round(size / 1024.0 / n * 124 / 1024.0, 2),
        "alpha_absdiff_mean": round(float(np.mean(a_diffs)), 2),
        "alpha_absdiff_max": int(max(a_max)),
        "edge_green_excess_mean": round(float(np.mean(edge_g)), 2) if edge_g else None,
        "edge_green_excess_max": round(edge_g_max, 2),
        "opaque_green_excess_mean": round(float(np.mean(solid_g)), 2) if solid_g else None,
        "transparent_px_with_color": trans_colored,
        "total_px": tot,
    }


if __name__ == "__main__":
    cands = [
        (os.path.join(TMP, "t_apng.apng"), "apng"),
        (os.path.join(TMP, "t_qtrle.mov"), "qtrle"),
        (os.path.join(TMP, "t_prores.mov"), "prores"),
        (os.path.join(TMP, "t_vp8.webm"), "vp8webm"),
    ]
    rows = []
    for path, tag in cands:
        if os.path.exists(path):
            rows.append(report(path, tag))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
