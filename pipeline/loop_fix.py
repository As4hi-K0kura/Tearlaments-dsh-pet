#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""末帧硬对齐首帧：抽帧 -> 用首帧覆盖末帧 -> 重新编码（同时剥音轨）。

比 ffmpeg overlay enable 表达式可靠（本机 ffmpeg 7.1 上 enable 未生效）。
用法： python loop_fix.py <in.mp4> <out.mp4> [--crf 16]
"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import os
import shutil
import subprocess
import sys

FF = r"C:\Users\KSO\AppData\Local\Programs\Python\Python312\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"


def main():
    src, dst = sys.argv[1], sys.argv[2]
    crf = "16"
    if "--crf" in sys.argv:
        crf = sys.argv[sys.argv.index("--crf") + 1]
    work = os.path.join(os.path.dirname(dst), "_loopfix_frames")
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)
    # 1. 抽帧（保留原分辨率）
    subprocess.run([FF, "-y", "-i", src, "-vsync", "0", os.path.join(work, "f%05d.png")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    frames = sorted(f for f in os.listdir(work) if f.endswith(".png"))
    if not frames:
        raise SystemExit("未抽到帧")
    # 2. 末帧 <- 首帧
    shutil.copyfile(os.path.join(work, frames[0]), os.path.join(work, frames[-1]))
    # 3. 重编码
    subprocess.run([FF, "-y", "-framerate", "24", "-i", os.path.join(work, "f%05d.png"),
                    "-an", "-c:v", "libx264", "-preset", "slow", "-crf", crf,
                    "-pix_fmt", "yuv420p", "-r", "24", dst],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    shutil.rmtree(work)
    print("frames=%d -> %s" % (len(frames), dst))


if __name__ == "__main__":
    main()
