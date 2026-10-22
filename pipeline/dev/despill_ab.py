#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SOLID_USE_FG / T_SEED 联合 A/B（最终定稿用）。"""
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
import green_to_alpha as G  # noqa

WORK = str(_paths.TMP_VERIFY)


def run(state, t_seed, use_fg, n_frames=6):
    fs = sorted(glob.glob(os.path.join(WORK, state + "_s", "*.png")))
    idxs = list(range(0, len(fs), max(1, len(fs) // n_frames)))[:n_frames]
    green = gmax = 0
    recon = []
    a255 = tot = 0
    for i in idxs:
        obs = np.asarray(Image.open(fs[i]).convert("RGB")).astype(np.float32)
        G.T_SEED = t_seed
        G.SOLID_USE_FG = use_fg
        a, c = G.key_frame(obs)
        ge = c[..., 1] - np.maximum(c[..., 0], c[..., 2])
        solid = a >= 250
        g = solid & (ge > 12)
        green += int(g.sum())
        if g.any():
            gmax = max(gmax, int(ge[g].max()))
        a255 += int((a == 255).sum())
        tot += a.size
        bg = np.array([0.0, 244.0, 0.0], np.float32)
        rr = (a[..., None] / 255.0) * c + (1 - a[..., None] / 255.0) * bg
        recon.append(float(np.abs(rr - obs).mean()))
    return green, gmax, round(float(np.mean(recon)), 3), round(a255 / tot * 100, 2)


if __name__ == "__main__":
    print("%-11s %6s %6s %9s %8s %10s %10s" % ("状态", "T_SEED", "用fg", "绿像素>12", "绿峰", "重建误差", "实心%"))
    for state in ("pet-done", "pet-idle", "pet-tool", "pet-wait", "pet-think"):
        for t, u in ((25, False), (25, True), (12, True), (6, True)):
            g, gm, rc, op = run(state, t, u)
            print("%-11s %6d %6s %9d %8d %10.3f %9.2f" % (state, t, u, g, gm, rc, op))
