#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断：逐帧脸部宽度分布 —— 确认脸部测量没被「手/胸」等肤色块污染。

用的是 build_atlas 里同一套判据（离脸心最近 + 尺寸与首帧脸宽相符）。
"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from detect_ipd import find_face_blob  # noqa: E402
from build_atlas import decode_frames, face_bbox_windowed  # noqa: E402

SRC = Path(str(_paths.SRC_ALPHA))
STATES = ["pet-idle", "pet-think", "pet-tool", "pet-wait",
          "pet-done", "pet-error", "pet-drag", "pet-sleep"]


def main():
    for name in STATES:
        p = SRC / f"{name}.webm"
        hint, ref_w = None, 0.0
        widths, centers, skipped, n = [], [], 0, 0
        for idx, arr in decode_frames(p):
            n = idx + 1
            rgb, alpha = arr[..., :3], arr[..., 3]
            if hint is None:
                blob, _ = find_face_blob(rgb, alpha > 40)
                if blob:
                    _, fx0, fy0, fx1, fy1 = blob
                    hint = ((fx0 + fx1) // 2, (fy0 + fy1) // 2)
                    ref_w = fx1 - fx0 + 1
                    widths.append(ref_w)
                    centers.append(((fx0 + fx1) / 2, (fy0 + fy1) / 2))
                continue
            if idx % 3:
                continue
            fb = face_bbox_windowed(rgb, alpha, hint[0], hint[1], ref_w)
            if fb is None:
                skipped += 1
                continue
            fx0, fy0, fx1, fy1 = fb
            widths.append(fx1 - fx0 + 1)
            centers.append(((fx0 + fx1) / 2, (fy0 + fy1) / 2))
        if not widths:
            print(f"{name:<12} 无有效测量")
            continue
        w = np.array(widths, float)
        c = np.array(centers, float)
        q = np.percentile(w, [5, 25, 50, 75, 95])
        print(f"{name:<12} n={len(w):<4} 跳过={skipped:<3} "
              f"脸宽 p5={q[0]:5.1f} p25={q[1]:5.1f} 中位={q[2]:5.1f} p75={q[3]:5.1f} p95={q[4]:5.1f} "
              f"| 极差={w.max()-w.min():4.1f} ({w.max()/max(w.min(),1):.2f}x) "
              f"| 脸心x 中位={np.median(c[:,0]):6.1f} y 中位={np.median(c[:,1]):6.1f}")


if __name__ == "__main__":
    main()
