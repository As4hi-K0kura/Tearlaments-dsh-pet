#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""8 条透明版视频的逐条像素级验收（终版）。

对每条输出（VP8+alpha WebM）：
  * 结构：帧数 / 分辨率 / alpha 轨 / 文件大小
  * alpha：全透占比、全不透占比、过渡带占比
  * 绿边：实心区绿 excess（>12 像素数与峰值）、过渡带绿 excess 均值
  * 破洞（两个独立判据）：
      A) 与旧版解码 alpha 对照：旧版 alpha>=250 而新版 alpha<128
      B) 源帧判据：源帧与背景色的差 >200（覆盖率必 >0.66）而输出 alpha<128
  * 循环接缝：末帧 vs 首帧（全分辨率 + 96px 显示尺寸）
  * 回合成：输出合回原绿底后与源绿幕帧的差异
  * 目视：合成到洋红底的对比图
"""
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
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import green_to_alpha as G  # noqa

SRC_DIR = str(_paths.VID_FINAL)
OUT_DIR = str(_paths.VID_ALPHA)
WORK = str(_paths.TMP_VERIFY)
PREVIEW = str(_paths.FRAMES_DIAG)
BG = np.array([0, 244, 0], np.int16)


def extract(video, outdir, decoder=None):
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir)
    cmd = [G.FF, "-y"]
    if decoder:
        cmd += ["-c:v", decoder]
    cmd += ["-i", video, "-vsync", "0", "-pix_fmt", "rgba", os.path.join(outdir, "f%05d.png")]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(glob.glob(os.path.join(outdir, "*.png")))


def probe(video):
    pr = subprocess.run([G.FF, "-hide_banner", "-i", video],
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8", errors="ignore")
    info = {"stream": "", "alpha_mode": False}
    for line in (pr.stderr or "").splitlines():
        s = line.strip()
        if s.startswith("Stream #0:0") and "Video:" in s:
            info["stream"] = s
        if "alpha_mode" in s:
            info["alpha_mode"] = True
    return info


def check(name, video, src_mp4):
    tag = os.path.splitext(name)[0]
    dec = extract(video, os.path.join(WORK, tag + "_d"), decoder="libvpx")
    src = extract(src_mp4, os.path.join(WORK, tag + "_s"))
    old = sorted(glob.glob(os.path.join(WORK, tag + "_d_v0", "*.png")))
    n = min(len(dec), len(src))
    step = max(1, n // 12)

    a0 = a255 = mid = 0
    tot = 0
    edge_g = []
    solid_green = 0
    solid_green_max = 0
    trans_colored = 0
    hole_a = 0
    hole_b = 0
    recon = []
    for i in range(0, n, step):
        d = np.asarray(Image.open(dec[i]).convert("RGBA")).astype(np.int16)
        s = np.asarray(Image.open(src[i]).convert("RGB")).astype(np.int16)
        a = d[..., 3]
        rgb = d[..., :3]
        a0 += int((a == 0).sum())
        a255 += int((a == 255).sum())
        mid += int(((a > 8) & (a < 247)).sum())
        tot += a.size
        ge = rgb[..., 1] - np.maximum(rgb[..., 0], rgb[..., 2])
        e = (a > 8) & (a < 247)
        if e.sum() > 20:
            edge_g.append(float(ge[e].mean()))
        solid = a >= 250
        bad = solid & (ge > 12)
        solid_green += int(bad.sum())
        if bad.any():
            solid_green_max = max(solid_green_max, int(ge[bad].max()))
        trans_colored += int(((a == 0) & (rgb.max(axis=2) > 12)).sum())
        # 破洞判据 B：源帧饱和度高（覆盖率必 >0.66）但输出 alpha 低
        cov = np.abs(s - BG[None, None, :]).max(axis=2) > 200
        hole_b += int((cov & (a < 128)).sum())
        # 破洞判据 A：与旧版对照
        if i < len(old):
            o = np.asarray(Image.open(old[i]).convert("RGBA")).astype(np.int16)
            hole_a += int(((o[..., 3] >= 250) & (a < 128)).sum())
        # 回合成
        bg = np.array([0.0, 244.0, 0.0], np.float32)
        rr = (a[..., None] / 255.0) * rgb.astype(np.float32) + (1 - a[..., None] / 255.0) * bg
        recon.append(float(np.abs(rr - s.astype(np.float32)).mean()))

    f0 = np.asarray(Image.open(dec[0]).convert("RGBA")).astype(np.int16)
    fl = np.asarray(Image.open(dec[-1]).convert("RGBA")).astype(np.int16)
    dd = np.abs(f0 - fl)
    s0 = np.asarray(Image.fromarray(f0.astype(np.uint8)).resize((96, 96), Image.LANCZOS)).astype(np.int16)
    sL = np.asarray(Image.fromarray(fl.astype(np.uint8)).resize((96, 96), Image.LANCZOS)).astype(np.int16)
    ds = np.abs(s0 - sL)

    os.makedirs(PREVIEW, exist_ok=True)
    picks = [dec[i] for i in range(0, len(dec), max(1, len(dec) // 4))][:4]
    tiles = []
    for p in picks:
        im = Image.open(p).convert("RGBA")
        bgm = Image.new("RGBA", im.size, (170, 0, 170, 255))
        bgm.alpha_composite(im)
        tiles.append(bgm.convert("RGB").resize((300, 300), Image.LANCZOS))
    sheet = Image.new("RGB", (300 * len(tiles), 300), (0, 0, 0))
    for i, t in enumerate(tiles):
        sheet.paste(t, (300 * i, 0))
    sheet.save(os.path.join(PREVIEW, "alpha-%s.png" % tag))

    pr = probe(video)
    return {
        "name": name,
        "size_mb": round(os.path.getsize(video) / 1048576.0, 2),
        "frames": len(dec),
        "alpha_mode": pr["alpha_mode"],
        "stream": pr["stream"][:110],
        "alpha0_pct": round(a0 / tot * 100, 2),
        "alpha255_pct": round(a255 / tot * 100, 2),
        "alpha_transition_pct": round(mid / tot * 100, 2),
        "edge_green_mean": round(float(np.mean(edge_g)), 2) if edge_g else None,
        "solid_green_px_over_12": solid_green,
        "solid_green_max": solid_green_max,
        "transparent_px_with_color": trans_colored,
        "holes_vs_old": hole_a,
        "holes_vs_source": hole_b,
        "recon_err_vs_source": round(float(np.mean(recon)), 3),
        "seam_rgba_mean": round(float(dd.mean()), 3),
        "seam_rgb_max": int(dd[..., :3].max()),
        "seam_alpha_max": int(dd[..., 3].max()),
        "seam_at96_mean": round(float(ds.mean()), 3),
        "seam_at96_max": int(ds.max()),
        "seam_at96_px_over_8": int((ds.max(axis=2) > 8).sum()),
        "preview": "frames-diag/alpha-%s.png" % tag,
    }


if __name__ == "__main__":
    os.makedirs(WORK, exist_ok=True)
    out = {}
    for v in sorted(glob.glob(os.path.join(OUT_DIR, "*.webm"))):
        name = os.path.basename(v)
        src = os.path.join(SRC_DIR, os.path.splitext(name)[0] + ".mp4")
        out[name] = check(name, v, src)
        print(name, json.dumps(out[name], ensure_ascii=False))
    with open(os.path.join(WORK, "alpha-verify.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("\n报告 ->", os.path.join(WORK, "alpha-verify.json"))
