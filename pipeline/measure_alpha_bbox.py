#!/usr/bin/env python
"""测量 8 态透明 WebM 的 alpha 占位（union bbox / 帧间漂移 / 重心）。

用途：判断 JavaFX 桌宠运行时能否把八态画在同一个固定窗口里，
还是必须逐态做位置与尺度对齐。

用法: python measure_alpha_bbox.py [透明背景版目录]
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

try:
    import imageio_ffmpeg
except ImportError:
    print("need: pip install imageio-ffmpeg numpy pillow", file=sys.stderr)
    raise SystemExit(2)

FF = imageio_ffmpeg.get_ffmpeg_exe()
W = H = 768
STATES = [
    ("pet-idle", "待机"), ("pet-think", "思考"), ("pet-tool", "调用工具"),
    ("pet-wait", "等待"), ("pet-done", "完成"), ("pet-error", "出错"),
    ("pet-drag", "拖拽"), ("pet-sleep", "休眠"),
]


def decode_rgba(path: Path) -> np.ndarray:
    # VP8 alpha 必须显式指定 libvpx 解码器，且像素格式要 rgba，否则 alpha 轨被丢弃
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-c:v", "libvpx",
           "-i", str(path), "-pix_fmt", "rgba", "-f", "rawvideo", "-"]
    out = subprocess.run(cmd, capture_output=True, check=False).stdout
    n = len(out) // (W * H * 4)
    return np.frombuffer(out[:n * W * H * 4], np.uint8).reshape(n, H, W, 4)


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else str(_paths.SRC_ALPHA))
    print(f"{'state':<12}{'帧':>4}{'并集bbox(x0,y0,x1,y1)':>28}{'内容高%':>8}"
          f"{'内容宽%':>8}{'帧间y漂移':>11}{'帧间x漂移':>11}{'重心x':>8}{'重心y':>8}")
    print("-" * 108)
    global_bbox = None
    for fname, zh in STATES:
        p = root / f"{fname}.webm"
        if not p.exists():
            print(f"{fname:<12} MISSING {p}")
            continue
        arr = decode_rgba(p)
        n = arr.shape[0]
        a = arr[..., 3]
        xs0 = xs1 = ys0 = ys1 = None
        frame_h, frame_w, cx, cy = [], [], [], []
        for i in range(n):
            al = a[i]
            rows = np.flatnonzero(al.any(axis=1))
            cols = np.flatnonzero(al.any(axis=0))
            if rows.size == 0:
                continue
            y0, y1, x0, x1 = rows[0], rows[-1], cols[0], cols[-1]
            ys0 = y0 if ys0 is None else min(ys0, y0)
            ys1 = y1 if ys1 is None else max(ys1, y1)
            xs0 = x0 if xs0 is None else min(xs0, x0)
            xs1 = x1 if xs1 is None else max(xs1, x1)
            frame_h.append(y1 - y0 + 1)
            frame_w.append(x1 - x0 + 1)
            m = al > 32
            yy, xx = np.nonzero(m)
            cx.append(xx.mean())
            cy.append(yy.mean())
        bbox = (xs0, ys0, xs1, ys1)
        if global_bbox is None:
            global_bbox = list(bbox)
        else:
            global_bbox = [min(global_bbox[0], xs0), min(global_bbox[1], ys0),
                           max(global_bbox[2], xs1), max(global_bbox[3], ys1)]
        ch = (ys1 - ys0 + 1) / H * 100
        cw = (xs1 - xs0 + 1) / W * 100
        dy = max(cy) - min(cy)
        dx = max(cx) - min(cx)
        print(f"{fname:<12}{n:>4}{str(bbox):>28}{ch:>8.1f}{cw:>8.1f}"
              f"{dy:>11.1f}{dx:>11.1f}{np.mean(cx):>8.1f}{np.mean(cy):>8.1f}")
    if global_bbox:
        gx0, gy0, gx1, gy1 = global_bbox
        print("-" * 108)
        print(f"八态全局并集 bbox: {tuple(global_bbox)}  "
              f"内容高 {gy1-gy0+1}px ({(gy1-gy0+1)/H*100:.1f}%)  "
              f"内容宽 {gx1-gx0+1}px ({(gx1-gx0+1)/W*100:.1f}%)")


if __name__ == "__main__":
    main()
