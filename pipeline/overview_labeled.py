
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import os
from PIL import Image, ImageDraw, ImageFont

BASE = str(_paths.NORM_UNIFORM)
OUT = str(_paths.REPO / "八态归一化总览-统一画布.png")
BACKUP = str(_paths.REPO / "八态归一化总览-统一画布-v17.png")

if not os.path.exists(BACKUP):
    import shutil
    shutil.copyfile(OUT, BACKUP)

ORDER = ["pet-idle", "pet-think", "pet-tool", "pet-wait",
         "pet-done", "pet-error", "pet-drag", "pet-sleep"]
# 角色占画布（实测 bbox 宽 / 1978）
RATIO = {"pet-idle": "65%", "pet-think": "49%", "pet-tool": "63%", "pet-wait": "57%",
         "pet-done": "58%", "pet-error": "61%", "pet-drag": "49%", "pet-sleep": "52%"}

T = 480
sheet = Image.new("RGB", (T * 4, T * 2), (0, 0, 0))
draw = ImageDraw.Draw(sheet)
fnt = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 20)

for i, name in enumerate(ORDER):
    p = os.path.join(BASE, name + ".png")
    im = Image.open(p).convert("RGB").resize((T, T), Image.LANCZOS)
    c, r = i % 4, i // 4
    sheet.paste(im, (c * T, r * T))
    label = "%s\n脸宽165 角色占画布%s" % (name, RATIO[name])
    # 半透明黑底保证可读
    box = draw.textbbox((0, 0), label, font=fnt)
    w = box[2] - box[0]
    h = box[3] - box[1]
    x, y = c * T + 8, r * T + T - h - 20
    draw.rectangle([x - 6, y - 6, x + w + 6, y + h + 10], fill=(0, 0, 0))
    draw.text((x, y), label, font=fnt, fill=(255, 255, 255))

sheet.save(OUT)
print("saved", OUT, sheet.size)
