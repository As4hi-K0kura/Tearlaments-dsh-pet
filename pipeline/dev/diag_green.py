#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断绿幕源帧的颜色分布：背景抖动、角色是否本身偏绿。"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import glob
import os
import subprocess

import numpy as np
from PIL import Image

FF = _paths.ffmpeg_bin()
TMP = str(_paths.TMP_FMT)
SRC_FRAMES = os.path.join(TMP, "src_frames")


def extract():
    if os.path.isdir(SRC_FRAMES):
        import shutil
        shutil.rmtree(SRC_FRAMES)
    os.makedirs(SRC_FRAMES)
    subprocess.run([FF, "-y", "-i", os.path.join(TMP, "clip24.mp4"), "-vsync", "0",
                    os.path.join(SRC_FRAMES, "s%05d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    extract()
    fs = sorted(glob.glob(os.path.join(SRC_FRAMES, "*.png")))
    print("帧数", len(fs))
    for idx in (0, 12):
        arr = np.asarray(Image.open(fs[idx]).convert("RGB")).astype(np.int16)
        r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
        mx = np.maximum(r, b)
        ge = g - mx

        greenish = (g > 150) & (r < 90) & (b < 90)
        print("\n--- frame%02d ---" % idx)
        print("绿色候选像素数 %d (%.1f%%)" % (greenish.sum(), greenish.sum() / r.size * 100))
        if greenish.sum():
            sel = arr[greenish]
            print("  绿色候选 RGB: R 中位 %d (p95 %d)  G 中位 %d (p5 %d/p95 %d)  B 中位 %d (p95 %d)" % (
                np.median(sel[:, 0]), np.percentile(sel[:, 0], 95),
                np.median(sel[:, 1]), np.percentile(sel[:, 1], 5), np.percentile(sel[:, 1], 95),
                np.median(sel[:, 2]), np.percentile(sel[:, 2], 95)))

        bg = np.median(arr[greenish], axis=0) if greenish.sum() else np.array([0, 242, 0])
        print("  bg 估计 =", bg)
        D = np.abs(arr - bg[None, None, :]).max(axis=2)
        for t in (20, 40, 60, 80, 100):
            print("  D>%3d 像素数 %8d (%.2f%%)  其中绿候选占 %.1f%%" % (
                t, (D > t).sum(), (D > t).sum() / r.size * 100,
                (greenish & (D > t)).sum() / max(1, (D > t).sum()) * 100))

        fgmask = D > 80
        if fgmask.sum():
            fgs = arr[fgmask]
            fge = fgs[:, 1] - np.maximum(fgs[:, 0], fgs[:, 2])
            print("  强前景(D>80) %d px, 绿 excess: 中位 %.1f, p95 %.1f, 绿占比(>30) %.1f%%" % (
                fgmask.sum(), np.median(fge), np.percentile(fge, 95), (fge > 30).mean() * 100))
            # 角色自身颜色里最绿的一批
            gi = np.argsort(fge)[-8:]
            print("  最绿的几个前景像素 RGB:", [tuple(int(v) for v in fgs[i]) for i in gi])


if __name__ == "__main__":
    main()
