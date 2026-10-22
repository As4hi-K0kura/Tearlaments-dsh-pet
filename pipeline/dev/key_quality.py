#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""新抠像算法的质量体检：边缘绿 / alpha 分布 / 单帧体积 / 合成目视。"""
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
SRC = os.path.join(TMP, "src_frames")
OUT = os.path.join(TMP, "keyed_new")


def key_all():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    fs = sorted(glob.glob(os.path.join(SRC, "*.png")))
    for i, p in enumerate(fs):
        im = Image.open(p).convert("RGB")
        rgb = np.asarray(im).astype(np.float32)
        a, c = key_frame(rgb)
        Image.fromarray(np.dstack([c.round().astype(np.uint8), a]), "RGBA").save(
            os.path.join(OUT, "f%05d.png" % (i + 1)))
    return fs


def report():
    fs = sorted(glob.glob(os.path.join(OUT, "*.png")))
    edge_g, solid_g, a0, a255, mid = [], [], 0, 0, 0
    tot = 0
    trans_colored = 0
    for p in fs:
        arr = np.asarray(Image.open(p).convert("RGBA")).astype(np.int16)
        a = arr[..., 3]
        rgb = arr[..., :3]
        ge = rgb[..., 1] - np.maximum(rgb[..., 0], rgb[..., 2])
        e = (a > 8) & (a < 247)
        s = a >= 247
        if e.sum() > 20:
            edge_g.append((float(ge[e].mean()), float(ge[e].max()), int(e.sum())))
        if s.sum() > 20:
            solid_g.append(float(ge[s].mean()))
        a0 += int((a == 0).sum())
        a255 += int((a == 255).sum())
        mid += int(e.sum())
        trans_colored += int(((a == 0) & (rgb.max(axis=2) > 12)).sum())
        tot += a.size
    sz = [os.path.getsize(p) for p in fs]
    print("帧数 %d" % len(fs))
    print("alpha: ==0 %.2f%%  ==255 %.2f%%  过渡带 %.2f%%" % (
        a0 / tot * 100, a255 / tot * 100, mid / tot * 100))
    print("边缘带绿 excess: mean %.2f  max %.2f  (帧均 %.2f)" % (
        np.mean([x[0] for x in edge_g]), np.max([x[1] for x in edge_g]),
        np.mean([x[0] for x in edge_g])))
    print("实心区绿 excess: mean %.2f" % np.mean(solid_g))
    print("透明区残留彩色像素: %d" % trans_colored)
    print("单帧 PNG: mean %.1f KB  max %.1f KB" % (np.mean(sz) / 1024, np.max(sz) / 1024))
    # 合成：洋红底 + 蓝格底
    picks = fs[:: max(1, len(fs) // 3)][:3]
    for tag, bgc in (("magenta", (170, 0, 170)), ("blue", (20, 40, 160))):
        tiles = []
        for p in picks:
            im = Image.open(p).convert("RGBA")
            bg = Image.new("RGBA", im.size, bgc + (255,))
            bg.alpha_composite(im)
            tiles.append(bg.convert("RGB").resize((340, 340), Image.LANCZOS))
        sheet = Image.new("RGB", (340 * len(tiles), 340), (0, 0, 0))
        for i, t in enumerate(tiles):
            sheet.paste(t, (340 * i, 0))
        sheet.save(os.path.join(TMP, "new_%s.png" % tag))
    print("合成图 -> new_magenta.png / new_blue.png")
    # 边缘放大（4x 局部）
    p = picks[0]
    im = Image.open(p).convert("RGBA")
    bg = Image.new("RGBA", im.size, (170, 0, 170, 255))
    bg.alpha_composite(im)
    bg.convert("RGB").crop((180, 240, 480, 540)).resize((900, 900), Image.NEAREST).save(
        os.path.join(TMP, "new_edgezoom.png"))
    print("边缘放大 -> new_edgezoom.png")


if __name__ == "__main__":
    key_all()
    report()
