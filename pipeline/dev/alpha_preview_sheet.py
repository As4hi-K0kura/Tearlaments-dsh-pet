#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""透明版八态预览图：WebM 解码 -> 合成到棋盘格底 -> 八宫格。"""
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

from PIL import Image, ImageDraw, ImageFont

FF = _paths.ffmpeg_bin()
SRC = str(_paths.VID_ALPHA)
OUT = str(_paths.DELIV / "预览-八态-透明版.png")
WORK = str(_paths.TMP_VERIFY / "preview")

ORDER = [
    ("待机", "pet-idle", "5.0s"),
    ("思考", "pet-think", "5.0s"),
    ("调用工具", "pet-tool", "5.0s"),
    ("等待", "pet-wait", "6.0s"),
    ("完成", "pet-done", "5.0s"),
    ("出错", "pet-error", "5.0s"),
    ("拖拽", "pet-drag", "5.0s"),
    ("休眠", "pet-sleep", "8.0s"),
]
CELL = 260
BAR = 54
COLS = 4


def checker(w, h, s=24):
    im = Image.new("RGB", (w, h), (58, 58, 66))
    d = ImageDraw.Draw(im)
    for y in range(0, h, s):
        for x in range(0, w, s):
            if ((x // s) + (y // s)) % 2 == 0:
                d.rectangle([x, y, x + s - 1, y + s - 1], fill=(96, 96, 104))
    return im


def main():
    os.makedirs(WORK, exist_ok=True)
    sheet = Image.new("RGB", (CELL * COLS, (CELL + BAR) * 2), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    f1 = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 17)
    f2 = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 14)

    for i, (zh, name, dur) in enumerate(ORDER):
        video = os.path.join(SRC, name + ".webm")
        d = os.path.join(WORK, name)
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)
        # 取中间帧
        subprocess.run([FF, "-y", "-c:v", "libvpx", "-ss", "2.0", "-i", video,
                        "-frames:v", "1", "-pix_fmt", "rgba", os.path.join(d, "f.png")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p = os.path.join(d, "f.png")
        base = checker(CELL, CELL).convert("RGBA")
        if os.path.exists(p):
            im = Image.open(p).convert("RGBA").resize((CELL, CELL), Image.LANCZOS)
            base.alpha_composite(im)
        c, r = i % COLS, i // COLS
        x, y = c * CELL, r * (CELL + BAR)
        sheet.paste(base.convert("RGB"), (x, y))
        draw.text((x + 8, y + CELL + 4), "%s %s" % (zh, name), font=f1, fill=(255, 255, 255))
        sz = os.path.getsize(video) / 1048576.0 if os.path.exists(video) else 0
        draw.text((x + 8, y + CELL + 27), "%s · 透明背景 · %.1f MB" % (dur, sz),
                  font=f2, fill=(150, 220, 255))

    sheet.save(OUT)
    print("saved", OUT, sheet.size)


if __name__ == "__main__":
    main()
