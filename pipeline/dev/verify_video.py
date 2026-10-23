#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌宠视频像素级验收：绿幕纯净度 / 钟摆摆幅 / 首尾帧差异 / 头顶无上飘头发 / 发光像素。"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import subprocess
import sys
import os
import json
import numpy as np
from PIL import Image

FF = _paths.ffmpeg_bin()


def extract(video, outdir, size=None):
    os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(outdir):
        os.remove(os.path.join(outdir, f))
    vf = "fps=24" + (",scale=%d:%d" % (size, size) if size else "")
    subprocess.run([FF, "-y", "-i", video, "-vf", vf, os.path.join(outdir, "f%04d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(os.path.join(outdir, f) for f in os.listdir(outdir))


def content_mask(a):
    return ~np.all(np.abs(a.astype(np.int16) - np.array([0, 255, 0])) < 14, axis=2)


def analyze(video, workdir, head_box=None):
    frames = extract(video, workdir)
    data = []
    for p in frames:
        a = np.asarray(Image.open(p).convert("RGB"))
        m = content_mask(a)
        ys, xs = np.nonzero(m)
        if len(xs) == 0:
            data.append(None)
            continue
        data.append({
            "cx": float(xs.mean()), "x0": int(xs.min()), "x1": int(xs.max()),
            "y0": int(ys.min()), "y1": int(ys.max()), "w": int(xs.max() - xs.min() + 1),
            "h": int(ys.max() - ys.min() + 1), "area": int(m.sum()),
        })
    res = {"frames": len(frames)}

    # 绿幕纯净度
    tot_ng = 0
    for p in frames:
        a = np.asarray(Image.open(p).convert("RGB"))
        tot_ng += int(content_mask(a).sum())
    res["non_green_ratio"] = round(tot_ng / (len(frames) * a.shape[0] * a.shape[1]) * 100, 3)

    # 钟摆摆幅
    cxs = [d["cx"] for d in data if d]
    res["centroid_x_min"] = round(min(cxs), 1)
    res["centroid_x_max"] = round(max(cxs), 1)
    res["swing_amplitude_px"] = round(max(cxs) - min(cxs), 1)
    res["centroid_x_series"] = [round(c, 1) for c in cxs]
    hs = [d["h"] for d in data if d]
    res["bbox_h_min_max"] = [min(hs), max(hs)]

    # 首尾帧差异
    if len(frames) >= 2:
        a = np.asarray(Image.open(frames[0]).convert("RGB")).astype(np.int16)
        b = np.asarray(Image.open(frames[-1]).convert("RGB")).astype(np.int16)
        diff = np.abs(a - b).mean(axis=2)
        res["last_vs_first_mean_768"] = round(float(diff.mean()), 3)
        res["last_vs_first_pct_768"] = round(float((diff > 12).sum()) / diff.size * 100, 3)
        small_a = np.asarray(Image.open(frames[0]).convert("RGB").resize((96, 96), Image.LANCZOS)).astype(np.int16)
        small_b = np.asarray(Image.open(frames[-1]).convert("RGB").resize((96, 96), Image.LANCZOS)).astype(np.int16)
        sd = np.abs(small_a - small_b).mean(axis=2)
        res["last_vs_first_mean_96"] = round(float(sd.mean()), 3)
        res["last_vs_first_pct_96"] = round(float((sd > 12).sum()) / sd.size * 100, 3)
        res["last_vs_first_max_96"] = int(sd.max())

    # 头顶区域是否有内容（头发上飘检测）
    if head_box:
        hx0, hy0, hx1, hy1 = head_box
        above = []
        for p in frames:
            a = np.asarray(Image.open(p).convert("RGB"))
            band = a[0:hy0, hx0:hx1]
            above.append(int(content_mask(band).sum()))
        res["above_head_px_max"] = max(above)
        res["above_head_px_series_sample"] = above[::6]
    return res


if __name__ == "__main__":
    video = sys.argv[1]
    work = sys.argv[2]
    hb = None
    if len(sys.argv) > 3:
        hb = json.loads(sys.argv[3])
    print(json.dumps(analyze(video, work, hb), ensure_ascii=False, indent=2))
