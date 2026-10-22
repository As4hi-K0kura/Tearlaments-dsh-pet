#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回合成检验：抠像结果合回原背景色，应逐像素还原源帧。
同时输出关键帧与源帧的并排对比（判断"发丝泛绿"是原画还是抠像伪影）。
"""
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
from green_to_alpha import key_frame  # noqa

TMP = str(_paths.TMP_FMT)
SRC = os.path.join(TMP, "src_frames")
KEYED = os.path.join(TMP, "keyed_new")


def main():
    srcs = sorted(glob.glob(os.path.join(SRC, "*.png")))
    keys = sorted(glob.glob(os.path.join(KEYED, "*.png")))
    errs = []
    for p in srcs[:6]:
        obs = np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
        k = np.asarray(Image.open(keys[0 if False else srcs.index(p)]).convert("RGBA")).astype(np.float32)
        a = (k[..., 3:4] / 255.0)
        fg = k[..., :3]
        bg = np.array([0.0, 244.0, 0.0], np.float32)
        recon = a * fg + (1 - a) * bg[None, None, :]
        diff = np.abs(recon - obs)
        errs.append(diff.reshape(-1, 3).max(axis=1))
        print("frame%02d  回合成误差: mean %.2f  p95 %.2f  max %.2f" % (
            srcs.index(p), diff.mean(), np.percentile(diff, 95), diff.max()))
    allerr = np.concatenate(errs)
    print("合计: mean %.2f  p95 %.2f  p99 %.2f  max %.2f  | 误差>8 的像素占比 %.3f%%" % (
        allerr.mean(), np.percentile(allerr, 95), np.percentile(allerr, 99), allerr.max(),
        (allerr > 8).mean() * 100))

    # 并排对比：源帧 vs 抠像后合成到洋红底
    p0 = srcs[0]
    obs = Image.open(p0).convert("RGB")
    k = Image.open(keys[0]).convert("RGBA")
    bgm = Image.new("RGBA", obs.size, (170, 0, 170, 255))
    bgm.alpha_composite(k)
    box = (190, 250, 470, 530)
    a1 = obs.crop(box).resize((840, 840), Image.NEAREST)
    a2 = bgm.convert("RGB").crop(box).resize((840, 840), Image.NEAREST)
    sheet = Image.new("RGB", (840 * 2 + 20, 840), (0, 0, 0))
    sheet.paste(a1, (0, 0))
    sheet.paste(a2, (860, 0))
    sheet.save(os.path.join(TMP, "cmp_src_vs_keyed.png"))
    print("并排对比(左=源帧绿幕, 右=抠像后合洋红) -> cmp_src_vs_keyed.png")


if __name__ == "__main__":
    main()
