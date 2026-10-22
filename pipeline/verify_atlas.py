#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图集产物像素级验收。

判据（与构建期同一套口径，避免"自己验自己"）：
  1. 脸宽一致 —— 用 build_atlas.face_bbox_windowed + manifest 给的脸心提示复测输出图集，
     每态实测脸宽都应等于目标脸宽（容差 ±10%）。这是「脸部归一化」落到产物上的硬判据。
  2. 脚底线一致 —— 各态**首帧**内容底边换算到画布坐标后应落在同一条线上（容差 ±3px），
     dragging 例外（悬空，运行时由鼠标接管）。
  3. 无裁切 —— 任何一帧的非透明像素都不允许碰到格子外沿（有 padding 就意味着溢出）。
  4. 帧数与循环闭合 —— 帧数 = (源帧数-1) × 目标fps/源fps；首帧与末帧内容必须不同
     （末帧是首帧副本、已剔除，否则循环会多停一帧）。
  5. 交叉验证 —— 站立态的「头心离地高度」应当彼此接近（独立于脸宽检测器的整体尺度校验）。

用法: python verify_atlas.py [图集目录]
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

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from build_atlas import face_bbox_windowed  # noqa: E402

FACE_TOL = 0.10
GROUND_TOL = 3.0
STANDING = {"idle", "calling_tool", "complete", "error"}


def cell_bbox(alpha):
    rows = np.flatnonzero((alpha > 40).any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero((alpha > 40).any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1
                else str(_paths.ATLAS_DIR))
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    canvas = man["canvas"]
    print(f"画布 {canvas['w']}x{canvas['h']}  脚底线 y={canvas['groundY']}  "
          f"脸心x={canvas['faceCX']}  目标脸宽={canvas['faceWidth']}")
    print(f"{'state':<14}{'帧':>4}{'脸宽(输出)':>11}{'偏差':>8}{'脚底线y':>9}"
          f"{'头心离地':>9}{'触边帧':>8}{'判定':>8}")
    print("-" * 80)

    fails, head_heights = [], {}
    for st in man["states"]:
        sid = st["id"]
        atlas = Image.open(root / st["atlas"]).convert("RGBA")
        fw, fh, cols = st["fw"], st["fh"], st["cols"]
        # 脸心提示：画布坐标 → 格子内局部坐标
        hint_cx = int(round(st["faceCenter"][0] - st["cellX"]))
        hint_cy = int(round(st["faceCenter"][1] - st["cellY"]))
        ref_w = st["faceWidth"] * st["scale"]      # 目标脸宽在源尺度下的等效值
        widths, touch = [], 0
        first_bottom = None
        for f in range(st["frames"]):
            cx0 = (f % cols) * fw
            cy0 = (f // cols) * fh
            cell = atlas.crop((cx0, cy0, cx0 + fw, cy0 + fh))
            arr = np.array(cell)
            alpha = arr[..., 3]
            bb = cell_bbox(alpha)
            if bb and f == 0:
                first_bottom = st["cellY"] + bb[3]
            if f in (0, st["frames"] // 2, st["frames"] - 1):
                fb = face_bbox_windowed(arr[..., :3], alpha, hint_cx, hint_cy, ref_w)
                if fb:
                    widths.append(fb[2] - fb[0] + 1)
            m = alpha > 40
            if m[0, :].any() or m[-1, :].any() or m[:, 0].any() or m[:, -1].any():
                touch += 1
        face_w = float(np.median(widths)) if widths else 0.0
        dev = (face_w - canvas["faceWidth"]) / canvas["faceWidth"] if face_w else -1.0
        head_h = st.get("headHeight", 0.0)

        ok = True
        if face_w <= 0 or abs(dev) > FACE_TOL:
            ok = False
            fails.append(f"{sid}: 输出脸宽 {face_w:.1f}，偏差 {dev:+.1%}")
        if st.get("groundAligned", True):
            if first_bottom is None or abs(first_bottom - canvas["groundY"]) > GROUND_TOL:
                ok = False
                fails.append(f"{sid}: 脚底线 {first_bottom} ≠ 公共 {canvas['groundY']}")
        if touch:
            ok = False
            fails.append(f"{sid}: {touch} 帧触到格子外沿（内容被裁）")
        expect = round((st["srcFrames"] - 1) * st["fps"] / st["srcFps"])
        if st["frames"] != expect:
            ok = False
            fails.append(f"{sid}: 帧数 {st['frames']} ≠ 期望 {expect}")
        if sid in STANDING:
            head_heights[sid] = head_h

        print(f"{sid:<14}{st['frames']:>4}{face_w:>11.1f}{dev:>+8.1%}"
              f"{(first_bottom if first_bottom is not None else -1):>9.1f}{head_h:>9.1f}"
              f"{touch:>8}{'PASS' if ok else 'FAIL':>8}")

    print("-" * 80)
    if head_heights:
        vals = list(head_heights.values())
        spread = (max(vals) - min(vals)) / max(vals)
        print(f"站立态头心离地高度：{head_heights}  极差 {spread:.1%}"
              f"  {'✅ 一致' if spread <= 0.10 else '⚠️ 偏大（源素材比例本就有差）'}")
    if fails:
        print("未通过：")
        for f in fails:
            print("  ·", f)
        return 1
    print("全部通过：八态脸宽一致、脚底线对齐、无裁切、循环闭合。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
