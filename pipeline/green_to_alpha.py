#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""绿幕视频 -> 透明背景视频（带 alpha 轨）。

抠像方法（避免边缘绿边残留）：
  1. 逐帧估计背景绿（取"绿意像素"的中位数，抗噪）
  2. D = 与背景色的最大通道差；D > T_hi 判为"确定的前景内部"
  3. 用迭代膨胀把内部颜色向外传播 12 像素，得到每个像素的"干净前景色" fg
  4. 按合成公式 obs = a*fg + (1-a)*bg 解出真实覆盖率 a：
        a = <obs-bg, fg-bg> / |fg-bg|^2
  5. 透明区 RGB 置 0（避免色度下采样把绿边渗进来），边缘 RGB 用 fg（无绿边）
  6. 内部像素做轻微 despill（仅当 G 明显高于 R/B 时）

输出格式（--format）：
  apng    单文件动画 PNG，RGBA 无损，浏览器/Electron 原生支持
  qtrle   MOV，argb 无损
  prores  MOV，ProRes 4444（yuva444p10le），视觉无损、10bit
  pngseq  PNG 序列（目录），最稳但占空间

用法：
  python green_to_alpha.py <in.mp4> <out.ext> [--format apng] [--fps 24] [--keep]
  python green_to_alpha.py --batch --indir <in_dir> --outdir <out_dir> [--format apng]
"""
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
T_HI = 50.0      # 与背景色的最大通道差超过此值 -> 可能是前景
T_SEED = 12.0    # 绿 excess(G - max(R,B)) 高于此值 -> 判为"绿染像素"，不作种子
SOLID_USE_FG = True  # 实心区用传播色（干净）而不是观测色
PROP_ITERS = 14  # 前景色向外传播半径（像素）
T_LO = 0.002     # 小于这个 alpha 视为全透明
A_LO = 0.04      # 投影值低于此值 -> alpha 直接归 0（去掉角色周围的"薄雾"）
A_HI = 0.96      # 投影值高于此值 -> alpha 直接归 1
DESPILL_T = 30.0  # 实心区绿 excess 超过此值就压
DESPILL_K = 0.25  # 压到 excess*K 的保留比例


# ---------------------------------------------------------------- 抠像核心

def shift(a, dy, dx):
    out = np.zeros_like(a)
    h, w = a.shape[:2]
    ys, yd = slice(max(0, dy), h + min(0, dy)), slice(max(0, -dy), h + min(0, -dy))
    xs, xd = slice(max(0, dx), w + min(0, dx)), slice(max(0, -dx), w + min(0, -dx))
    out[yd, xd] = a[ys, xs]
    return out


def estimate_bg(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cand = (g > 150) & (r < 90) & (b < 90)
    if cand.sum() < 100:
        return np.array([0.0, 242.0, 0.0])
    sel = rgb[cand]
    return np.median(sel, axis=0)


def propagate(rgb, known, iters=12):
    """把 known 区域的颜色向外扩散 iters 像素（迭代膨胀求平均）。"""
    color = rgb.copy()
    cnt = known.astype(np.float32)
    filled = known.copy()
    for _ in range(iters):
        s = np.zeros_like(color)
        c = np.zeros(cnt.shape, np.float32)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            sc = shift(cnt, dy, dx)
            s += shift(color, dy, dx) * sc[..., None]
            c += sc
        newly = (~filled) & (c > 0)
        if not newly.any():
            break
        color[newly] = s[newly] / c[newly][..., None]
        cnt[newly] = 1.0
        filled |= newly
    return color, filled


def key_frame(rgb):
    """单帧抠像 -> (alpha uint8, 前景 RGB uint8 数组)。

    关键点：H.264 压缩会在角色轮廓上留下 1~2px 的"绿染"抗锯齿像素
    (实测绿 excess 中位 ~160)，它们与背景色的最大通道差 > 50，
    若直接当种子会把绿渗进整条边带。这里用"绿 dominance"把它们剔除出种子，
    再靠前景色向外传播把这条带子填成干净的内部颜色。
    """
    bg = estimate_bg(rgb)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    green_dom = g - np.maximum(r, b)
    d = np.abs(rgb - bg[None, None, :]).max(axis=2)

    # 种子 = 明确是角色、且不是绿染像素
    interior = (d > T_HI) & (green_dom < T_SEED)
    if not interior.any():
        return np.zeros(rgb.shape[:2], np.uint8), np.zeros_like(rgb)
    fg, filled = propagate(rgb, interior, iters=PROP_ITERS)

    diff_obs = rgb - bg[None, None, :]
    diff_fg = fg - bg[None, None, :]
    den = (diff_fg ** 2).sum(axis=2)
    num = (diff_obs * diff_fg).sum(axis=2)
    # 投影值 t：t=1 表示与前景色完全一致，t=0 表示与背景色完全一致
    t = num / np.maximum(den, 1e-6)
    # 软阈值：把 H.264 噪声造成的微弱投影压成 0，消除角色周围的"薄雾"
    a = np.clip((t - A_LO) / (A_HI - A_LO), 0.0, 1.0)
    a[~filled] = 0.0

    out = np.zeros_like(rgb)
    solid = a >= 0.999
    # 实心区优先用传播出来的干净前景色：
    # 种子像素 fg == rgb（无差别）；非种子但被判实心的像素（轮廓上带一点绿意、
    # 因 green_dom 超过 T_SEED 被排除出种子）用 fg 就能把绿味换掉。
    if SOLID_USE_FG:
        out[solid] = fg[solid]
    else:
        out[solid] = rgb[solid]
    edge = (a > T_LO) & (~solid)
    # 边缘用传播出来的干净前景色，彻底没有绿边
    out[edge] = fg[edge]

    # 实心区轻微 despill：角色本身没有偏绿像素（绿 excess 中位 -34），
    # 这里只压 H.264 压缩打进角色内部的绿噪
    r, g, b = out[..., 0], out[..., 1], out[..., 2]
    mx = np.maximum(r, b)
    excess = g - mx
    strong = solid & (excess > DESPILL_T)
    out[..., 1] = np.where(strong, mx + DESPILL_K * excess, g)
    return (a * 255.0).round().astype(np.uint8), out


# ---------------------------------------------------------------- 封装

def encode(rgba_dir, dst, fmt, fps, bitrate="8M", nframes=None):
    seq = os.path.join(rgba_dir, "f%05d.png")
    common = [FF, "-y", "-framerate", fps, "-i", seq, "-an"]
    if fmt == "apng":
        cmd = common + ["-f", "apng", "-pix_fmt", "rgba", dst]
    elif fmt == "qtrle":
        cmd = common + ["-c:v", "qtrle", "-pix_fmt", "argb", dst]
    elif fmt == "prores":
        cmd = common + ["-c:v", "prores_ks", "-profile:v", "4444",
                        "-pix_fmt", "yuva444p10le", "-vendor", "apl0", dst]
    elif fmt == "vp8":
        # VP8 是 ffmpeg 的 libvpx 里唯一支持 alpha 的编码（VP9/AV1 都会丢 alpha 轨）。
        # -lag-in-frames 0：不做前瞻，末帧不受后续帧影响，保证循环接缝
        cmd = common + ["-c:v", "libvpx", "-pix_fmt", "yuva420p",
                        "-b:v", bitrate, "-auto-alt-ref", "0",
                        "-qmin", "4", "-qmax", "48", "-lag-in-frames", "0"]
        if nframes:
            cmd += ["-force_key_frames", "expr:gte(n,%d)" % (nframes - 1)]
        cmd += [dst]
    elif fmt == "pngseq":
        outdir = dst
        if os.path.isdir(outdir):
            shutil.rmtree(outdir)
        os.makedirs(outdir)
        cmd = common + ["-pix_fmt", "rgba", os.path.join(outdir, "f%05d.png")]
    else:
        raise SystemExit("未知格式: %s" % fmt)
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def convert(src, dst, fmt, fps, keep=False, close_loop=True, bitrate="8M"):
    work = dst + "_frames"
    rgba_dir = dst + "_rgba"
    for d in (work, rgba_dir):
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)

    subprocess.run([FF, "-y", "-i", src, "-vsync", "0", os.path.join(work, "s%05d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    srcs = sorted(f for f in os.listdir(work) if f.startswith("s"))
    if not srcs:
        raise SystemExit("未抽到帧: %s" % src)

    # 逐帧抠像
    for i, name in enumerate(srcs):
        im = Image.open(os.path.join(work, name)).convert("RGB")
        rgb = np.asarray(im).astype(np.float32)
        alpha, color = key_frame(rgb)
        out = np.dstack([color.round().astype(np.uint8), alpha])
        Image.fromarray(out, "RGBA").save(os.path.join(rgba_dir, "f%05d.png" % (i + 1)))
    shutil.rmtree(work)

    nframes = len(srcs)
    if close_loop and nframes > 1:
        # 源 MP4 末帧是首帧的"重编码副本"，像素并不完全相同；
        # 抠像会把这点差异在轮廓边缘放大成 alpha 跳变。这里让末帧直接用首帧的像素。
        first = os.path.join(rgba_dir, "f00001.png")
        shutil.copyfile(first, os.path.join(rgba_dir, "f%05d.png" % nframes))

    encode(rgba_dir, dst, fmt, fps, bitrate=bitrate, nframes=nframes if close_loop else None)
    if not keep:
        shutil.rmtree(rgba_dir)

    if os.path.isdir(dst):
        size = sum(os.path.getsize(p) for p in glob.glob(os.path.join(dst, "*.png")))
    else:
        size = os.path.getsize(dst)
    print("%s -> %s [%s] %d frames, %.2f MB" % (
        os.path.basename(src), os.path.basename(dst), fmt, len(srcs), size / 1048576.0))


def main():
    argv = sys.argv[1:]
    if "--batch" in argv:
        indir = argv[argv.index("--indir") + 1]
        outdir = argv[argv.index("--outdir") + 1]
        fmt = argv[argv.index("--format") + 1] if "--format" in argv else "vp8"
        fps = argv[argv.index("--fps") + 1] if "--fps" in argv else "24"
        bitrate = argv[argv.index("--bitrate") + 1] if "--bitrate" in argv else "8M"
        keep = "--keep" in argv
        close_loop = "--no-close-loop" not in argv
        os.makedirs(outdir, exist_ok=True)
        ext = {"apng": ".apng", "qtrle": ".mov", "prores": ".mov",
               "vp8": ".webm", "pngseq": ""}[fmt]
        for src in sorted(glob.glob(os.path.join(indir, "*.mp4"))):
            base = os.path.splitext(os.path.basename(src))[0]
            dst = os.path.join(outdir, base + ext)
            convert(src, dst, fmt, fps, keep, close_loop, bitrate)
        return

    src, dst = argv[0], argv[1]
    fmt = argv[argv.index("--format") + 1] if "--format" in argv else "vp8"
    fps = argv[argv.index("--fps") + 1] if "--fps" in argv else "24"
    bitrate = argv[argv.index("--bitrate") + 1] if "--bitrate" in argv else "8M"
    convert(src, dst, fmt, fps, "--keep" in argv, "--no-close-loop" not in argv, bitrate)


if __name__ == "__main__":
    main()
