
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import os
import subprocess
from PIL import Image, ImageDraw, ImageFont

FF = r"C:\Users\KSO\AppData\Local\Programs\Python\Python312\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
SRC = str(_paths.DELIV)
OUT = str(_paths.DELIV / "预览-八态.png")
WORK = str(_paths.TMP_NORM / "preview")

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

os.makedirs(WORK, exist_ok=True)
sheet = Image.new("RGB", (CELL * COLS, (CELL + BAR) * 2), (0, 0, 0))
draw = ImageDraw.Draw(sheet)
f1 = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 17)
f2 = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 14)

for i, (zh, name, dur) in enumerate(ORDER):
    video = os.path.join(SRC, name + ".mp4")
    png = os.path.join(WORK, name + ".png")
    # 取 40% 处的一帧
    subprocess.run([FF, "-y", "-ss", "2.0", "-i", video, "-frames:v", "1", png],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    im = Image.open(png).convert("RGB").resize((CELL, CELL), Image.LANCZOS)
    c, r = i % COLS, i // COLS
    x, y = c * CELL, r * (CELL + BAR)
    sheet.paste(im, (x, y))
    draw.text((x + 8, y + CELL + 4), "%s %s" % (zh, name), font=f1, fill=(255, 255, 255))
    draw.text((x + 8, y + CELL + 27), "%s · 可用" % dur, font=f2, fill=(180, 255, 180))

sheet.save(OUT)
print("saved", OUT, sheet.size)
