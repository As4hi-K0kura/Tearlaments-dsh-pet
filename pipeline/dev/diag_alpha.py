#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断：①边缘带绿像素来源 ②VP8 alpha 实际写入情况。"""
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
REF = os.path.join(TMP, "t_apng.apng_rgba")
SRC_FRAMES = os.path.join(TMP, "clip24_frames")


def edge_diag():
    print("=== 边缘带绿像素诊断（参考 RGBA 帧）===")
    refs = sorted(glob.glob(os.path.join(REF, "*.png")))
    for idx in (0, 8, 16):
        p = refs[idx]
        arr = np.asarray(Image.open(p).convert("RGBA")).astype(np.int16)
        a = arr[..., 3]
        rgb = arr[..., :3]
        mx = np.maximum(rgb[..., 0], rgb[..., 2])
        ge = rgb[..., 1] - mx
        edge = (a > 8) & (a < 247)
        bad = edge & (ge > 60)
        print("frame%02d  edge_px=%d  bad(green>60)=%d (%.1f%%)  ge_mean=%.1f" % (
            idx, edge.sum(), bad.sum(), bad.sum() / max(1, edge.sum()) * 100, ge[edge].mean()))
        if bad.sum() > 30:
            ys, xs = np.where(bad)
            print("   bad bbox: x %d..%d  y %d..%d" % (xs.min(), xs.max(), ys.min(), ys.max()))
            # 聚类看是否集中在某一小块
            print("   bad x 分布 min/med/max: %d/%d/%d" % (xs.min(), int(np.median(xs)), xs.max()))
            print("   bad y 分布 min/med/max: %d/%d/%d" % (ys.min(), int(np.median(ys)), ys.max()))
            # 采样几个坏点
            for k in range(0, min(6, len(ys)), max(1, len(ys) // 6)):
                y, x = ys[k], xs[k]
                print("   sample (%d,%d) rgba=%s" % (x, y, tuple(arr[y, x])))


def vp8_diag():
    print("\n=== VP8 alpha 诊断 ===")
    v = os.path.join(TMP, "t_vp8.webm")
    d = os.path.join(TMP, "dec_vp8probe")
    subprocess.run([FF, "-y", "-i", v, "-vsync", "0", "-pix_fmt", "rgba",
                    os.path.join(d, "f%05d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fs = sorted(glob.glob(os.path.join(d, "*.png")))
    arr = np.asarray(Image.open(fs[0]).convert("RGBA")).astype(np.int16)
    a = arr[..., 3]
    print("decoded alpha: min=%d max=%d  唯一值数=%d" % (a.min(), a.max(), len(np.unique(a))))
    print("  ==0 占比 %.2f%%   ==255 占比 %.2f%%" % (
        (a == 0).sum() / a.size * 100, (a == 255).sum() / a.size * 100))
    # 参考真值
    ref = np.asarray(Image.open(sorted(glob.glob(os.path.join(REF, "*.png")))[0]).convert("RGBA")).astype(np.int16)
    print("参考 alpha: min=%d max=%d  ==0 占比 %.2f%%  ==255 占比 %.2f%%" % (
        ref[..., 3].min(), ref[..., 3].max(),
        (ref[..., 3] == 0).sum() / ref[..., 3].size * 100,
        (ref[..., 3] == 255).sum() / ref[..., 3].size * 100))
    # 抽原始 mp4 帧（绿幕）看背景与角色交界
    print("\n=== 绿幕源帧交界采样 ===")
    srcs = sorted(glob.glob(os.path.join(SRC_FRAMES, "*.png")))
    if srcs:
        s = np.asarray(Image.open(srcs[0]).convert("RGB")).astype(np.int16)
        print("源帧 size", s.shape)
        # 找一个绿色像素样本
        g = (s[..., 1] > 150) & (s[..., 0] < 90) & (s[..., 2] < 90)
        print("绿像素数 %d, 取样 RGB:" % g.sum(), [tuple(s[g][i]) for i in range(0, min(5, g.sum()), max(1, g.sum() // 5))])


if __name__ == "__main__":
    edge_diag()
    vp8_diag()
