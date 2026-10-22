#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图集预览：公共画布合成图 + 八态并排图。

合成图是最直观的验收证据：八态首帧按各自在公共画布里的格子位置叠到同一张画布上，
画一条脚底线（黄）与一条公共脸心线（青）——
八态若尺度/锚点统一，就能看到八个头一样大、八双脚落在同一条线上。

用法: python preview_atlas.py [图集目录]
"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

STATES = ["idle", "thinking", "calling_tool", "waiting",
          "complete", "error", "dragging", "sleeping"]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1
                else str(_paths.ATLAS_DIR))
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    cv = man["canvas"]
    W, H = cv["w"], cv["h"]
    by_id = {s["id"]: s for s in man["states"]}

    # ---------- 1) 公共画布合成图 ----------
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for sid in STATES:
        d = by_id[sid]
        atlas = Image.open(root / d["atlas"]).convert("RGBA")
        cell = atlas.crop((0, 0, d["fw"], d["fh"]))
        canvas.alpha_composite(cell, (d["cellX"], d["cellY"]))

    scale = max(1, int(760 / max(W, H)))
    big = canvas.resize((W * scale, H * scale), Image.LANCZOS)
    over = Image.new("RGBA", big.size, (26, 28, 34, 255))
    over.alpha_composite(big)
    dr = ImageDraw.Draw(over)
    gy = int(round(cv["groundY"] * scale))
    for x in range(over.width):
        dr.line([(x, gy), (x, gy + 1)], fill=(255, 198, 0, 255))
    fx = int(round(cv["faceCX"] * scale))
    for y in range(over.height):
        dr.line([(fx, y), (fx + 1, y)], fill=(80, 220, 255, 200))
    over.save(root / "preview-公共画布合成.png")
    print(f"合成图：{root / 'preview-公共画布合成.png'}  "
          f"(黄=公共脚底线 y={cv['groundY']}, 青=公共脸心线 x={cv['faceCX']})")

    # ---------- 2) 八态并排对齐对比（同一画布 8 列，脚底线/脸心线直接可比）----------
    tw, th = 200, 260
    sheet = Image.new("RGBA", (tw * 4, th * 2), (26, 28, 34, 255))
    for i, sid in enumerate(STATES):
        d = by_id[sid]
        atlas = Image.open(root / d["atlas"]).convert("RGBA")
        cell = atlas.crop((0, 0, d["fw"], d["fh"]))
        solo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        solo.alpha_composite(cell, (d["cellX"], d["cellY"]))
        s = min(tw / W, th / H)
        fr = solo.resize((int(W * s), int(H * s)), Image.LANCZOS)
        tile = Image.new("RGBA", (tw, th), (44, 48, 58, 255))
        tile.alpha_composite(fr, ((tw - fr.width) // 2, (th - fr.height) // 2))
        dd = ImageDraw.Draw(tile)
        gy = int(round(cv["groundY"] * s)) + (th - fr.height) // 2
        fx = int(round(cv["faceCX"] * s)) + (tw - fr.width) // 2
        for x in range(tile.width):
            dd.line([(x, gy), (x, gy + 1)], fill=(255, 198, 0, 255))
        for y in range(tile.height):
            dd.line([(fx, y), (fx + 1, y)], fill=(80, 220, 255, 170))
        dd.text((6, 4), f"{d['zh']}  {d['id']}", fill=(214, 218, 228, 255))
        dd.text((6, th - 15), f"face={d.get('faceWidthOut', 0):.1f}px  "
                              f"k={d.get('scale', 0):.3f}", fill=(150, 220, 160, 255))
        sheet.alpha_composite(tile, ((i % 4) * tw, (i // 4) * th))
    sheet.save(root / "preview-八态-对齐对比.png")
    print(f"对齐对比：{root / 'preview-八态-对齐对比.png'}")

    # ---------- 3) 八态并排图 ----------
    tw, th = 230, 300
    sheet = Image.new("RGBA", (tw * 4, th * 2), (26, 28, 34, 255))
    for i, sid in enumerate(STATES):
        d = by_id[sid]
        atlas = Image.open(root / d["atlas"]).convert("RGBA")
        cell = atlas.crop((0, 0, d["fw"], d["fh"]))
        s = min((tw - 10) / d["fw"], (th - 10) / d["fh"])
        fr = cell.resize((max(1, int(d["fw"] * s)), max(1, int(d["fh"] * s))),
                         Image.LANCZOS)
        tile = Image.new("RGBA", (tw, th), (44, 48, 58, 255))
        tile.alpha_composite(fr, ((tw - fr.width) // 2, (th - fr.height) // 2))
        dd = ImageDraw.Draw(tile)
        dd.text((6, 4), f"{d['zh']} {d['id']}", fill=(210, 214, 224, 255))
        dd.text((6, th - 14), f"face={d.get('faceWidthOut', 0):.1f}", fill=(150, 220, 160, 255))
        sheet.alpha_composite(tile, ((i % 4) * tw, (i // 4) * th))
    sheet.save(root / "preview-八态-图集.png")
    print(f"八态图：{root / 'preview-八态-图集.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
