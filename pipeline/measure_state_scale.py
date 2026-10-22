#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测量 8 态透明 WebM 里的「同口径脸宽」与画布边缘裁切。

脸宽口径与 normalized/ 管线完全一致（复用 detect_ipd.find_face_blob），
因此可直接算出把各态统一到同一脸宽所需的缩放系数。

用法: python measure_state_scale.py [透明背景版目录]
"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from detect_ipd import find_face_blob  # noqa: E402

try:
    import imageio_ffmpeg
except ImportError:
    print("need: pip install imageio-ffmpeg numpy pillow", file=sys.stderr)
    raise SystemExit(2)

try:
    from scipy import ndimage
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False

FF = imageio_ffmpeg.get_ffmpeg_exe()
W = H = 768
STATES = ["pet-idle", "pet-think", "pet-tool", "pet-wait",
          "pet-done", "pet-error", "pet-drag", "pet-sleep"]


def decode_rgba(path: Path) -> np.ndarray:
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-c:v", "libvpx",
           "-i", str(path), "-pix_fmt", "rgba", "-f", "rawvideo", "-"]
    out = subprocess.run(cmd, capture_output=True, check=False).stdout
    n = len(out) // (W * H * 4)
    return np.frombuffer(out[:n * W * H * 4], np.uint8).reshape(n, H, W, 4)


def fast_face_blob(rgb, op):
    """同 detect_ipd.find_face_blob 判据，但用 scipy 加速连通域。"""
    if not HAVE_SCIPY:
        blob, _ = find_face_blob(rgb, op)
        return blob
    from detect_ipd import is_skin
    skin = is_skin(rgb) & op
    lab, ncomp = ndimage.label(skin, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
    if ncomp == 0:
        return None
    objs = ndimage.find_objects(lab)
    ys, xs = np.nonzero(op)
    if len(xs) == 0:
        return None
    cx0, cx1 = xs.min(), xs.max()
    cymid = (ys.min() + ys.max()) / 2.0
    best = None
    for i, sl in enumerate(objs, start=1):
        area = int((lab[sl] == i).sum())
        if area < 1500:
            continue
        y0, y1 = sl[0].start, sl[0].stop - 1
        x0, x1 = sl[1].start, sl[1].stop - 1
        w = x1 - x0 + 1
        h = y1 - y0 + 1
        if not (0.78 <= w / float(h) <= 1.55):
            continue
        if y1 < ys.min() + 0.12 * (ys.max() - ys.min()):
            continue
        ccx = (x0 + x1) / 2.0
        ccy = (y0 + y1) / 2.0
        if ccy > cymid:
            continue
        score = (w * h) * (1.0 - min(abs(ccx - (cx0 + cx1) / 2.0) / (0.5 * (cx1 - cx0 + 1)), 1.0))
        if best is None or score > best[0]:
            best = (score, (area, x0, y0, x1, y1))
    return best[1] if best else None


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else str(_paths.SRC_ALPHA))
    print(f"scipy: {'yes' if HAVE_SCIPY else 'no (fallback to pure-python CCL)'}")
    print(f"{'state':<12}{'帧':>4}{'脸块(x0,y0,x1,y1)':>24}{'脸宽':>7}{'脸心x':>8}{'脸心y':>8}"
          f"{'触边像素':>10}{'触边行/列':>12}")
    print("-" * 100)
    widths = {}
    for fname in STATES:
        p = root / f"{fname}.webm"
        if not p.exists():
            print(f"{fname:<12} MISSING")
            continue
        arr = decode_rgba(p)
        n = arr.shape[0]
        f0 = arr[0]
        rgb = f0[..., :3]
        alpha = f0[..., 3]
        op = alpha > 40
        blob = fast_face_blob(rgb, op)
        if blob is None:
            print(f"{fname:<12} no face blob")
            continue
        area, fx0, fy0, fx1, fy1 = blob
        fw = fx1 - fx0 + 1
        widths[fname] = fw
        # 边缘裁切：最后一行/最后一列是否有实心内容
        edge = int((alpha[-1] > 40).sum() + (alpha[:, -1] > 40).sum()
                   + (alpha[0] > 40).sum() + (alpha[:, 0] > 40).sum())
        edge_desc = []
        if (alpha[-1] > 40).sum() > 0:
            edge_desc.append("底")
        if (alpha[:, -1] > 40).sum() > 0:
            edge_desc.append("右")
        if (alpha[0] > 40).sum() > 0:
            edge_desc.append("顶")
        if (alpha[:, 0] > 40).sum() > 0:
            edge_desc.append("左")
        print(f"{fname:<12}{n:>4}{str((fx0, fy0, fx1, fy1)):>24}{fw:>7}"
              f"{(fx0+fx1)/2:>8.1f}{(fy0+fy1)/2:>8.1f}{edge:>10}{'/'.join(edge_desc) or '-':>12}")
    if widths:
        print("-" * 100)
        base = widths.get("pet-idle")
        print("以 pet-idle 脸宽为基准的逐态缩放系数 k = base / face_w:")
        for k, v in widths.items():
            print(f"  {k:<12} face_w={v:>4}  k={base/float(v):.4f}")
        print(f"  脸宽极差 {max(widths.values())}/{min(widths.values())} = "
              f"{max(widths.values())/float(min(widths.values())):.2f}x")


if __name__ == "__main__":
    main()
