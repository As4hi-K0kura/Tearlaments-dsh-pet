#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""透明背景视频验收：alpha 分布 / 绿边残留 / 合成目视图。"""
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

FF = r"C:\Users\KSO\AppData\Local\Programs\Python\Python312\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"


def frames_rgba(video, workdir):
    if os.path.isdir(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir)
    # 必须显式 -pix_fmt rgba：PNG 默认 rgb24 会静默丢掉 alpha 轨
    subprocess.run([FF, "-y", "-i", video, "-vsync", "0", "-pix_fmt", "rgba",
                    os.path.join(workdir, "f%05d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(glob.glob(os.path.join(workdir, "*.png")))


def check(video, workdir, comp_out=None):
    fs = frames_rgba(video, workdir)
    a0 = a255 = 0
    edge_green = []
    opaque_green = []
    transparent_nonzero = 0
    for p in fs:
        im = Image.open(p).convert("RGBA")
        arr = np.asarray(im).astype(np.int16)
        a = arr[..., 3]
        rgb = arr[..., :3].astype(np.int16)
        a0 += int((a == 0).sum())
        a255 += int((a == 255).sum())
        edge = (a > 8) & (a < 247)
        solid = a >= 247
        mx = np.maximum(rgb[..., 0], rgb[..., 2])
        g_excess = rgb[..., 1] - mx
        if edge.sum() > 20:
            edge_green.append(float(g_excess[edge].mean()))
        if solid.sum() > 20:
            opaque_green.append(float(g_excess[solid].mean()))
        transparent_nonzero += int(((a == 0) & (rgb.max(axis=2) > 12)).sum())
    n = len(fs)
    tot = 768 * 768 * n
    res = {
        "frames": n,
        "alpha0_pct": round(a0 / tot * 100, 2),
        "alpha255_pct": round(a255 / tot * 100, 2),
        "edge_green_excess_mean": round(float(np.mean(edge_green)), 2) if edge_green else None,
        "edge_green_excess_max": round(float(np.max(edge_green)), 2) if edge_green else None,
        "opaque_green_excess_mean": round(float(np.mean(opaque_green)), 2) if opaque_green else None,
        "transparent_px_with_color": transparent_nonzero,
    }
    if comp_out:
        # 合成到深色棋盘 + 洋红底，检查有无绿边
        tiles = []
        for p in fs[::max(1, len(fs) // 4)][:4]:
            im = Image.open(p).convert("RGBA")
            bg = Image.new("RGBA", im.size, (60, 0, 60, 255))
            bg.alpha_composite(im)
            tiles.append(bg.convert("RGB").resize((320, 320), Image.LANCZOS))
        sheet = Image.new("RGB", (320 * len(tiles), 320), (0, 0, 0))
        for i, t in enumerate(tiles):
            sheet.paste(t, (320 * i, 0))
        sheet.save(comp_out)
        res["composite"] = comp_out
    return res


if __name__ == "__main__":
    video = sys.argv[1]
    work = sys.argv[2]
    comp = sys.argv[3] if len(sys.argv) > 3 else None
    import json
    print(json.dumps(check(video, work, comp), ensure_ascii=False, indent=2))
