#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""透明格式横向实测：体积 + alpha 往返保真。"""
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
from green_to_alpha import FF, convert, key_frame  # noqa

TMP = str(_paths.TMP_FMT)
SRC = str(_paths.VID_FINAL / "pet-idle.mp4")
CLIP = os.path.join(TMP, "clip24.mp4")
N = 24


def cut():
    subprocess.run([FF, "-y", "-i", SRC, "-frames:v", str(N), "-an", CLIP],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def alpha_roundtrip(video, tag):
    d = os.path.join(TMP, "dec_" + tag)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    subprocess.run([FF, "-y", "-i", video, "-vsync", "0", "-pix_fmt", "rgba",
                    os.path.join(d, "f%05d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fs = sorted(glob.glob(os.path.join(d, "*.png")))
    a0 = a255 = mid = 0
    tot = 768 * 768 * max(1, len(fs))
    for p in fs:
        arr = np.asarray(Image.open(p).convert("RGBA"))
        a = arr[..., 3]
        a0 += int((a == 0).sum())
        a255 += int((a == 255).sum())
        mid += int(((a > 8) & (a < 247)).sum())
    # 容器声明的像素格式
    pr = subprocess.run([FF, "-i", video], stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore")
    fmt = ""
    for line in (pr.stderr or "").splitlines():
        if "Stream #" in line and "Video:" in line:
            fmt = line.strip()
    return {
        "tag": tag,
        "frames": len(fs),
        "alpha0_pct": round(a0 / tot * 100, 2),
        "alpha255_pct": round(a255 / tot * 100, 2),
        "alpha_mid_pct": round(mid / tot * 100, 3),
        "stream": fmt[:160],
    }


def vp9_variants(rgba_dir):
    """VP9 alpha 再验证：普通 / -auto-alt-ref 0。"""
    out = []
    seq = os.path.join(rgba_dir, "f%05d.png")
    for tag, extra in (("vp9", []), ("vp9_alt", ["-auto-alt-ref", "0"])):
        dst = os.path.join(TMP, "t_%s.webm" % tag)
        cmd = [FF, "-y", "-framerate", "24", "-i", seq, "-an", "-c:v", "libvpx-vp9",
               "-pix_fmt", "yuva420p", "-crf", "22", "-b:v", "0"] + extra + [dst]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out.append((dst, tag))
    return out


def main():
    os.makedirs(TMP, exist_ok=True)
    cut()
    rows = []
    for fmt, ext in (("apng", ".apng"), ("qtrle", ".mov"), ("prores", ".mov")):
        dst = os.path.join(TMP, "t_%s%s" % (fmt, ext))
        convert(CLIP, dst, fmt, "24", keep=True)
        size = os.path.getsize(dst)
        r = alpha_roundtrip(dst, fmt)
        r["size_kb"] = round(size / 1024, 1)
        r["kb_per_frame"] = round(size / 1024.0 / N, 1)
        r["est_124f_mb"] = round(size / 1024.0 / N * 124 / 1024.0, 2)
        rows.append(r)

    # VP9 变体：复用抠像好的帧
    rgba_dir = os.path.join(TMP, "t_apng_rgba")
    if not os.path.isdir(rgba_dir):
        rgba_dir = os.path.join(TMP, "t_qtrle_rgba")
    for dst, tag in vp9_variants(rgba_dir):
        if not os.path.exists(dst):
            continue
        r = alpha_roundtrip(dst, tag)
        r["size_kb"] = round(os.path.getsize(dst) / 1024, 1)
        r["kb_per_frame"] = round(os.path.getsize(dst) / 1024.0 / N, 1)
        r["est_124f_mb"] = round(os.path.getsize(dst) / 1024.0 / N * 124 / 1024.0, 2)
        rows.append(r)

    import json
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
