#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T_SEED 阈值 A/B：绿残留 / 破洞 / 重建误差 三项对照。"""
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
import green_to_alpha as G  # noqa

WORK = str(_paths.TMP_VERIFY)


def load(state):
    d = os.path.join(WORK, state + "_s")
    if not os.path.isdir(d):
        subprocess.run([G.FF, "-y", "-i", os.path.join(str(_paths.VID_FINAL), state + ".mp4"),
                        "-vsync", "0", os.path.join(d, "f%05d.png")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(glob.glob(os.path.join(d, "*.png")))


def run(state, t_seed, n_frames=6):
    fs = load(state)
    idxs = list(range(0, len(fs), max(1, len(fs) // n_frames)))[:n_frames]
    green = 0
    green_max = 0
    holes = 0
    recon = []
    alpha0 = alpha255 = 0
    tot = 0
    for i in idxs:
        obs = np.asarray(Image.open(fs[i]).convert("RGB")).astype(np.float32)
        G.T_SEED = t_seed
        a, c = G.key_frame(obs)
        # 用一份严格参考（极严阈值 + 不做 despill）算"真·角色"掩码
        ge = c[..., 1] - np.maximum(c[..., 0], c[..., 2])
        solid = a >= 250
        g = solid & (ge > 25)
        green += int(g.sum())
        if g.any():
            green_max = max(green_max, int(ge[g].max()))
        alpha0 += int((a == 0).sum())
        alpha255 += int((a == 255).sum())
        tot += a.size
        # 重建误差
        bg = np.array([0.0, 244.0, 0.0], np.float32)
        rr = (a[..., None] / 255.0) * c + (1 - a[..., None] / 255.0) * bg
        recon.append(float(np.abs(rr - obs).mean()))
    return {
        "T_SEED": t_seed,
        "green_px": green,
        "green_max": green_max,
        "alpha0_pct": round(alpha0 / tot * 100, 2),
        "alpha255_pct": round(alpha255 / tot * 100, 2),
        "recon_err": round(float(np.mean(recon)), 3),
    }


if __name__ == "__main__":
    for state in ("pet-done", "pet-idle", "pet-tool"):
        print("=== %s ===" % state)
        for t in (25, 12, 6, 0, -100):
            r = run(state, t)
            print("  T_SEED=%4d  绿像素 %5d (峰 %3d)  全透 %.2f%%  实心 %.2f%%  重建误差 %.3f" % (
                r["T_SEED"], r["green_px"], r["green_max"], r["alpha0_pct"],
                r["alpha255_pct"], r["recon_err"]))
