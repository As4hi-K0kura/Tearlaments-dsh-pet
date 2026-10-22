
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import sys
from PIL import Image

# 顺序与 八态归一化总览-统一画布.png 一致：idle think tool wait / done error drag sleep
base = str(_paths.NORM_UNIFORM)
drag = sys.argv[1] if len(sys.argv) > 1 else base + r"\pet-drag.png"
out_path = sys.argv[2] if len(sys.argv) > 2 else str(_paths.TMP_NORM / "overview-new.png")

order = ["pet-idle", "pet-think", "pet-tool", "pet-wait", "pet-done", "pet-error", "pet-drag", "pet-sleep"]
T = 480
GAP = 8
sheet = Image.new("RGB", (T * 4 + GAP * 5, T * 2 + GAP * 3), (24, 24, 32))
for i, name in enumerate(order):
    p = drag if name == "pet-drag" else base + "\\" + name + ".png"
    im = Image.open(p).convert("RGB").resize((T, T), Image.LANCZOS)
    c, r = i % 4, i // 4
    sheet.paste(im, (GAP + c * (T + GAP), GAP + r * (T + GAP)))
sheet.save(out_path)
print(out_path, sheet.size)
