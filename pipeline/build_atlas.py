#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""人鱼桌宠八态：脸部归一化 → 图集打包 → manifest 产出。

参考实现：PC2005-cloud/dsh-pet（MIT）
  · scripts/normalize_step03.py —— 整段统一 scale + 平移到公共画布；首尾帧测「站立基准」；
    用 alphaextract,bbox 扫全帧并集校验内容未被裁；水平居中取**非透明像素 x 的中位数**
    （防手/道具把 bbox 中心拉偏），垂直基准取 bbox 下边界。
  · src/client/pet.ts —— 脚底垫高：画布固定 FEET_Y，宠物始终站在同一条脚底线上。
  · src/shared/work-status.ts + assets/config.jsonc —— 事件档位（索引即档位，勿在中间插入）
    与动画池 / 权重模型。

与参考实现的两处差异（本项目八态姿势差异大，必须改）：
  1) 尺度锚用**脸宽**而不是「站立高度」：thinking 盘腿 / sleeping 躺下 / dragging 悬空，
     站立高度在这三态根本不成立。脸宽口径与本项目 normalized/ 管线完全一致
     （复用 detect_ipd.find_face_blob），与设计稿「八态脸宽一律 165」同源。
  2) 垂直锚用**脚底线**而不是脸心：盘腿 / 躺下时「脸心到脚底」的距离本就该变，
     强行对齐脸心会把身体拉长或让角色浮空。对齐脚底线 → 坐就是坐、躺就是躺、跳就是跳。
     dragging 是唯一例外：它被用户拎着悬空，垂直位置运行时由鼠标接管，
     构建期只对齐水平与尺度。

用法：
  python build_atlas.py [--src 透明背景版目录] [--out 输出目录] [--face-width 41]
                        [--fps 12] [--cols 12] [--padding 4] [--no-preview]
"""
from __future__ import annotations
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from detect_ipd import find_face_blob, is_skin, label_components  # noqa: E402

try:
    import imageio_ffmpeg
except ImportError:  # pragma: no cover
    print("need: pip install imageio-ffmpeg numpy pillow", file=sys.stderr)
    raise SystemExit(2)

FF = imageio_ffmpeg.get_ffmpeg_exe()
SRC_W = SRC_H = 768

# states.json 的 id → 透明版文件名（无扩展名）
STATE_FILES = {
    "idle": "pet-idle",
    "thinking": "pet-think",
    "calling_tool": "pet-tool",
    "waiting": "pet-wait",
    "complete": "pet-done",
    "error": "pet-error",
    "dragging": "pet-drag",
    "sleeping": "pet-sleep",
}

# 垂直不对齐到脚底线的态（运行时由外部决定垂直位置）
NO_GROUND_ALIGN = {"dragging"}


# ---------------------------------------------------------------- 解码


def decode_frames(path: Path):
    """逐帧产出 (index, rgba uint8 [H,W,4])。VP8 alpha 必须显式 libvpx + rgba。"""
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-c:v", "libvpx",
           "-i", str(path), "-pix_fmt", "rgba", "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size = SRC_W * SRC_H * 4
    idx = 0
    try:
        while True:
            buf = bytearray()
            while len(buf) < size:
                chunk = proc.stdout.read(size - len(buf))
                if not chunk:
                    break
                buf.extend(chunk)
            if len(buf) < size:
                break
            arr = np.frombuffer(bytes(buf), np.uint8).reshape(SRC_H, SRC_W, 4)
            yield idx, arr
            idx += 1
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait()


# ---------------------------------------------------------------- 测量


def face_bbox_windowed(rgb: np.ndarray, alpha: np.ndarray, cx: int, cy: int,
                       ref_w: float, half: int = 58, lo: float = 0.65,
                       hi: float = 1.45):
    """在以 (cx,cy) 为中心的窗口内找脸部肤色块（复用 is_skin + label_components，口径同源）。

    判据不是「窗口里最大的肤色块」——那会抓到**手**（手也是肤色，且有时比脸还大/更完整）：
      ① 肤色块宽高比 ∈ [0.78,1.55]（排除细长的手臂/腿）
      ② 块中心离已知脸心 ≤ 30px（脸不会乱跑，手会）
      ③ 块宽与基准脸宽 ref_w 相差在 [lo, hi] 倍内（排除指节等小块）
    三条同时满足才算脸；都不满足就跳过该帧（宁缺毋滥）。
    """
    h, w = alpha.shape
    x0, x1 = max(0, cx - half), min(w, cx + half + 1)
    y0, y1 = max(0, cy - half), min(h, cy + half + 1)
    op = alpha[y0:y1, x0:x1] > 40
    skin = is_skin(rgb[y0:y1, x0:x1]) & op
    best = None
    for area, ax0, ay0, ax1, ay1 in label_components(skin):
        bw, bh = ax1 - ax0 + 1, ay1 - ay0 + 1
        if area < 120 or not (0.78 <= bw / float(bh) <= 1.55):
            continue
        ccx = (ax0 + ax1) / 2.0 + x0 - cx      # 换算回全局坐标再与脸心比
        ccy = (ay0 + ay1) / 2.0 + y0 - cy
        dist = (ccx * ccx + ccy * ccy) ** 0.5
        if dist > 30.0:
            continue
        if not (lo * ref_w <= bw <= hi * ref_w):
            continue
        score = dist - 0.001 * bw
        if best is None or score < best[0]:
            best = (score, ax0, ay0, ax1, ay1)
    if best is None:
        return None
    _, ax0, ay0, ax1, ay1 = best
    return (ax0 + x0, ay0 + y0, ax1 + x0, ay1 + y0)


def content_bbox(alpha: np.ndarray):
    """非透明像素的并集 bbox；空则 None。"""
    rows = np.flatnonzero((alpha > 40).any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero((alpha > 40).any(axis=0))
    return (int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1]))


@dataclass
class StateMetrics:
    sid: str
    file: str
    n_frames: int = 0
    src_fps: float = 24.0
    face_w: float = 0.0
    face_cx: float = 0.0
    face_cy: float = 0.0
    face_w_median: float = 0.0
    face_cx_median: float = 0.0
    face_cy_median: float = 0.0
    ground_y: float = 0.0          # 首帧 bbox 下边界（规范姿态的脚底线）
    ground_y_median: float = 0.0   # 首 5 帧下边界中位数（对照用）
    union: tuple = (0, 0, 0, 0)    # 全帧内容并集 bbox（源坐标）
    per_frame_bbox: list = field(default_factory=list)


def measure_state(path: Path, sid: str) -> StateMetrics:
    """量一态：帧数、脸宽/脸心、脚底线（首尾站立帧）、全帧内容并集。"""
    m = StateMetrics(sid=sid, file=path.stem)
    head, tail = [], []          # 首尾各 5 帧（dsh-pet 的 HEAD_FRAMES 口径）
    face_w, face_cx, face_cy = [], [], []
    hint = None
    ref_w = 0.0                  # 首帧实测脸宽 = 后续帧的尺寸合理性基准
    ux0 = uy0 = 10 ** 9
    ux1 = uy1 = -1
    for idx, arr in decode_frames(path):
        m.n_frames = idx + 1
        rgb, alpha = arr[..., :3], arr[..., 3]
        bb = content_bbox(alpha)
        m.per_frame_bbox.append(bb)
        if bb:
            ux0 = min(ux0, bb[0]); uy0 = min(uy0, bb[1])
            ux1 = max(ux1, bb[2]); uy1 = max(uy1, bb[3])
        if idx < 5:
            head.append((idx, arr, bb))
        if idx >= m.n_frames - 5:  # 占位，真实尾部在下面统一处理
            pass
        if hint is None and bb:
            blob, _ = find_face_blob(rgb, alpha > 40)
            if blob:
                _, fx0, fy0, fx1, fy1 = blob
                hint = ((fx0 + fx1) // 2, (fy0 + fy1) // 2)
                ref_w = fx1 - fx0 + 1
                fb = (fx0, fy0, fx1, fy1)
                face_w.append(ref_w)
                face_cx.append((fx0 + fx1) / 2.0)
                face_cy.append((fy0 + fy1) / 2.0)
        elif hint is not None and (idx < 5 or idx % 3 == 0):
            fb = face_bbox_windowed(rgb, alpha, hint[0], hint[1], ref_w)
            if fb:
                fx0, fy0, fx1, fy1 = fb
                face_w.append(fx1 - fx0 + 1)
                face_cx.append((fx0 + fx1) / 2.0)
                face_cy.append((fy0 + fy1) / 2.0)
    m.union = (ux0, uy0, ux1, uy1)
    # 尺度锚与垂直锚都取**首帧**（= 该态的规范姿态 = 原始 still 的姿态）：
    #   · still 管线是用 find_face_blob 在**单张**图上量出「脸宽 165」的，
    #     视频侧必须用同一口径（同一函数、同一帧）才可比；
    #   · 动画中段的头会转/被头发挡，多帧中位数会被这些**姿态变化**系统性带偏。
    # 多帧中位数仍会记进 manifest 作对照，不参与计算。
    m.face_w = float(face_w[0]) if face_w else 0.0
    m.face_cx = float(face_cx[0]) if face_cx else 0.0
    m.face_cy = float(face_cy[0]) if face_cy else 0.0
    m.face_w_median = float(np.median(face_w)) if face_w else 0.0
    m.face_cx_median = float(np.median(face_cx)) if face_cx else 0.0
    m.face_cy_median = float(np.median(face_cy)) if face_cy else 0.0
    m.ground_y = float(m.per_frame_bbox[0][3]) if m.per_frame_bbox and m.per_frame_bbox[0] \
        else float(m.union[3])
    m.ground_y_median = float(np.median([bb[3] for bb in m.per_frame_bbox[:5]
                                         if bb])) if m.per_frame_bbox else 0.0
    return m


# ---------------------------------------------------------------- 变换


@dataclass
class Transform:
    k: float
    dx: float
    dy: float


def plan_transforms(metrics: dict, target_face: float, corrections=None):
    """算出每态的 (k, dx, dy)。

    k  = (目标脸宽 / 该态实测脸宽) × 修正系数   （尺度锚：脸）
    dx = 公共脸心x - 脸心x*k                   （水平锚：脸心）
    dy = 公共脚底线 - 脚底线*k                 （垂直锚：脚底线；dragging 例外 → dy=0）

    修正系数来自**闭式迭代**：在源帧上量到的脸宽与「归一化后应该有的脸宽」之间总有偏差
    （脸部被头发/手掌遮挡时尤其明显），所以每次构建后都在**输出图集上**复测一次脸宽，
    用 目标/实测 反推修正系数再重建。这样「脸部归一化」这个判据不是靠源帧估计满足的，
    而是靠产物本身满足的 —— 构建口径与验收口径严格同一套。
    """
    ref = metrics["idle"]
    k_ref = (target_face / ref.face_w) * (1.0 if corrections is None
                                           else corrections.get("idle", 1.0))
    common_face_cx = ref.face_cx * k_ref
    common_ground_y = ref.ground_y * k_ref
    out = {}
    for sid, m in metrics.items():
        c = 1.0 if corrections is None else corrections.get(sid, 1.0)
        k = (target_face / m.face_w) * c
        dx = common_face_cx - m.face_cx * k
        dy = 0.0 if sid in NO_GROUND_ALIGN else common_ground_y - m.ground_y * k
        out[sid] = Transform(k=k, dx=dx, dy=dy)
    return out, common_face_cx, common_ground_y


def measure_atlas_face(atlas: Image.Image, fw: int, fh: int, cols: int,
                       frames: int, hint_x: int, hint_y: int, ref_w: float):
    """在**已构建的图集**上复测脸宽（与 verify_atlas.py 同一套判据）。

    为什么要复测产物而不是源帧：源帧上量到的「可见肤色块宽度」与「归一化后应有的宽度」
    之间有系统性偏差（头发遮挡、手掌贴脸时块会变形）。以产物为准反推修正系数，
    才能让「八态脸宽一致」这条判据真正落在交付物上。
    """
    widths = []
    # 多点采样（7 帧等距）：头部在动画里会转，测 3 帧的噪声能到 ±5%，
    # 采样点够密才能把「姿态噪声」和「真实尺度偏差」分开。
    probes = sorted({int(i * (frames - 1) / 6) for i in range(7)})
    for f in probes:
        if f < 0 or f >= frames:
            continue
        cx0 = (f % cols) * fw
        cy0 = (f // cols) * fh
        cell = atlas.crop((cx0, cy0, cx0 + fw, cy0 + fh))
        arr = np.array(cell)
        fb = face_bbox_windowed(arr[..., :3], arr[..., 3], hint_x, hint_y, ref_w,
                                lo=0.45, hi=2.2)
        if fb:
            widths.append(fb[2] - fb[0] + 1)
    return float(np.median(widths)) if widths else 0.0


def transformed_union(m: StateMetrics, t: Transform):
    """整态变换后的内容并集（输出坐标）。"""
    ux0, uy0, ux1, uy1 = m.union
    return (ux0 * t.k + t.dx, uy0 * t.k + t.dy, ux1 * t.k + t.dx, uy1 * t.k + t.dy)


# ---------------------------------------------------------------- 图集


def build_atlas(path: Path, t: Transform, state_union, pad: int, fps: float,
                cols: int, n_total: int, src_fps: float = 24.0):
    """把一态重采样 + 变换 + 打包成网格图集。

    格子尺寸 = 该态变换后内容并集 + 2*pad；帧内容按状态并集原点对齐放进格子。
    """
    ux0, uy0, ux1, uy1 = state_union
    fw = int(round(ux1 - ux0)) + 1 + 2 * pad
    fh = int(round(uy1 - uy0)) + 1 + 2 * pad

    # 末帧是首帧副本（为锁循环而替换），一轮播 n_total-1 帧即可无缝闭合
    play = n_total - 1
    n_out = max(1, int(round(play * fps / src_fps)))
    picks = [min(play - 1, int(round(i * play / n_out))) for i in range(n_out)]

    rows = (n_out + cols - 1) // cols
    atlas = Image.new("RGBA", (cols * fw, rows * fh), (0, 0, 0, 0))
    offsets = []
    want = {p: i for i, p in enumerate(picks)}
    for idx, arr in decode_frames(path):
        if idx not in want:
            continue
        rgb, alpha = arr[..., :3], arr[..., 3]
        bb = content_bbox(alpha)
        if bb is None:
            offsets.append((0, 0))
            continue
        sx0, sy0, sx1, sy1 = bb
        src = Image.fromarray(arr, "RGBA").crop((sx0, sy0, sx1 + 1, sy1 + 1))
        nw = max(1, int(round(src.width * t.k)))
        nh = max(1, int(round(src.height * t.k)))
        resample = Image.LANCZOS if t.k < 1 else Image.LANCZOS
        src = src.resize((nw, nh), resample)
        # 变换后的全局位置 → 格子内位置
        gx = sx0 * t.k + t.dx
        gy = sy0 * t.k + t.dy
        ox = int(round(gx - ux0)) + pad
        oy = int(round(gy - uy0)) + pad
        si = want[idx]
        cx = (si % cols) * fw
        cy = (si // cols) * fh
        atlas.paste(src, (cx + ox, cy + oy), src)
        offsets.append((ox, oy))
    return atlas, fw, fh, cols, rows, picks


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(_paths.SRC_ALPHA))
    ap.add_argument("--out", default=str(_paths.ATLAS_DIR))
    ap.add_argument("--states", default=str(_paths.STATES))
    ap.add_argument("--face-width", type=float, default=41.0, help="图集里的目标脸宽(px)")
    ap.add_argument("--fps", type=float, default=12.0, help="输出帧率")
    ap.add_argument("--cols", type=int, default=12)
    ap.add_argument("--padding", type=int, default=4)
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("== 测量 ==", flush=True)
    metrics = {}
    for sid, fname in STATE_FILES.items():
        p = src / f"{fname}.webm"
        if not p.exists():
            print(f"!! 缺少 {p}", file=sys.stderr)
            return 2
        m = measure_state(p, sid)
        metrics[sid] = m
        print(f"  {sid:<13} 帧={m.n_frames:<4} 脸宽={m.face_w:5.1f} "
              f"脸心=({m.face_cx:6.1f},{m.face_cy:6.1f}) 脚底线={m.ground_y:6.1f} "
              f"并集={m.union}", flush=True)

    # 状态元数据（来自 states.json：优先级 / 闭合类型 / 转场）
    meta = {}
    transitions = []
    if Path(args.states).exists():
        sj = json.loads(Path(args.states).read_text(encoding="utf-8"))
        for s in sj.get("states", []):
            meta[s["id"]] = s
        transitions = sj.get("transitions", [])

    # ---- 闭式迭代：构建 → 在产物上复测脸宽 → 回修 k → 重建 ----
    # 为什么要迭代：源帧上量到的「可见肤色块宽度」与「归一化后应有的宽度」之间有系统性偏差
    # （头发遮脸、手掌贴脸时块会变形），所以尺度必须以**产物**为准回修，
    # 否则「八态脸宽一致」只是估算满足，验收时会对不上。
    corrections = {sid: 1.0 for sid in STATE_FILES}
    built, unions, transforms = {}, {}, {}
    gx0 = gy0 = gx1 = gy1 = 0
    canvas_w = canvas_h = 0
    common_face_cx = common_ground_y = 0.0
    measured = {}
    TOL = 0.06
    best = None            # 最优迭代的记录（收敛有测量噪声，取偏差最小的那一轮）
    import shutil
    tmp_root = out / ".atlas-iter"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    for it in range(1, 6):
        transforms, common_face_cx, common_ground_y = plan_transforms(
            metrics, args.face_width, corrections)
        # 全局画布 = 八态变换后并集 + padding
        gx0 = gy0 = 10 ** 9
        gx1 = gy1 = -10 ** 9
        unions = {}
        for sid, m in metrics.items():
            u = transformed_union(m, transforms[sid])
            unions[sid] = u
            gx0 = min(gx0, u[0]); gy0 = min(gy0, u[1])
            gx1 = max(gx1, u[2]); gy1 = max(gy1, u[3])
        canvas_w = int(round(gx1 - gx0)) + 1 + 2 * args.padding
        canvas_h = int(round(gy1 - gy0)) + 1 + 2 * args.padding

        iter_dir = tmp_root / f"iter{it}"
        iter_dir.mkdir(parents=True, exist_ok=True)
        built = {}
        measured = {}
        for sid, fname in STATE_FILES.items():
            m = metrics[sid]
            t = transforms[sid]
            u = unions[sid]
            atlas, fw, fh, cols, rows, picks = build_atlas(
                src / f"{fname}.webm", t, u, args.padding,
                args.fps, args.cols, m.n_frames)
            atlas.save(iter_dir / f"{fname}.atlas.png")
            built[sid] = (fw, fh, cols, rows, picks)
            cell_org_x = u[0] - args.padding          # 格子原点在全局输出坐标里的位置
            cell_org_y = u[1] - args.padding
            hint_x = int(round(m.face_cx * t.k + t.dx - cell_org_x))
            hint_y = int(round(m.face_cy * t.k + t.dy - cell_org_y))
            measured[sid] = measure_atlas_face(atlas, fw, fh, cols, len(picks),
                                               hint_x, hint_y, args.face_width)
            del atlas
        devs = {s: (measured[s] - args.face_width) / args.face_width
                for s in STATE_FILES if measured[s] > 0}
        worst = max(abs(v) for v in devs.values()) if devs else 1.0
        detail = " ".join(f"{s[:4]}:{measured[s]:.0f}" for s in STATE_FILES
                          if measured[s] > 0)
        print(f"  迭代 {it}: 画布 {canvas_w}x{canvas_h}  产物脸宽 {detail}"
              f"  最差偏差 {worst:.1%}", flush=True)
        if best is None or worst < best["worst"]:
            best = {
                "worst": worst, "it": it, "dir": iter_dir,
                "canvas_w": canvas_w, "canvas_h": canvas_h,
                "gx0": gx0, "gy0": gy0,
                "common_face_cx": common_face_cx,
                "common_ground_y": common_ground_y,
                "unions": dict(unions),
                "transforms": {k: Transform(v.k, v.dx, v.dy)
                               for k, v in transforms.items()},
                "measured": dict(measured),
                "built": dict(built),
            }
        if worst <= TOL:
            print("  → 归一化收敛", flush=True)
            break
        for sid in STATE_FILES:
            if measured[sid] > 0:
                # 阻尼回修：一次只修正 70%，避免在测量噪声上来回震荡
                corrections[sid] *= (args.face_width / measured[sid]) ** 0.7

    # 采用最优迭代的产物
    print(f"\n采用第 {best['it']} 迭代（最差偏差 {best['worst']:.1%}）", flush=True)
    for fname in STATE_FILES.values():
        shutil.copyfile(best["dir"] / f"{fname}.atlas.png", out / f"{fname}.atlas.png")
    canvas_w, canvas_h = best["canvas_w"], best["canvas_h"]
    gx0, gy0 = best["gx0"], best["gy0"]
    common_face_cx, common_ground_y = best["common_face_cx"], best["common_ground_y"]
    unions, transforms, measured, built = (best["unions"], best["transforms"],
                                           best["measured"], best["built"])
    shutil.rmtree(tmp_root, ignore_errors=True)

    print("\n== 打包图集 ==", flush=True)
    manifest_states = []
    for sid, fname in STATE_FILES.items():
        m = metrics[sid]
        t = transforms[sid]
        fw, fh, cols, rows, picks = built[sid]
        name = f"{fname}.atlas.png"
        u = unions[sid]
        cell_x = int(round(u[0] - gx0))
        cell_y = int(round(u[1] - gy0))
        smeta = meta.get(sid, {})
        entry = {
            "id": sid,
            "zh": smeta.get("label", sid),
            "semantic": smeta.get("semantic", ""),
            "atlas": name,
            "cols": cols, "rows": rows, "fw": fw, "fh": fh,
            "cellX": cell_x, "cellY": cell_y,
            # 该态内容原点（画布坐标）= 格子原点 + padding；拖拽时用它当抓取锚点
            "contentOrigin": [cell_x + args.padding, cell_y + args.padding],
            "chainEligible": sid in ("idle", "sleeping"),
            "frames": len(picks),
            "fps": args.fps,
            "srcFrames": m.n_frames,
            "loop": bool(smeta.get("loop", True)),
            "closure": smeta.get("closure", "silent"),
            "priority": int(smeta.get("priority", 0)),
            "faceWidth": round(m.face_w, 2),
            "faceWidthMedian": round(m.face_w_median, 2),
            # 在**产物图集**上复测到的脸宽 —— 归一化的最终判据
            "faceWidthOut": round(measured.get(sid, 0.0), 2),
            # 脸心（画布坐标）—— 验收脚本用同一套判据复测时作定位提示
            "faceCenter": [round(m.face_cx * t.k + t.dx - gx0 + args.padding, 1),
                           round(m.face_cy * t.k + t.dy - gy0 + args.padding, 1)],
            # 头心离地高度（输出 px）：站立态之间应当一致，是「整体大小是否统一」的独立交叉验证
            "headHeight": round((m.ground_y - m.face_cy) * t.k, 1),
            "scale": round(t.k, 6),
            "srcFps": 24.0,
            "groundAligned": sid not in NO_GROUND_ALIGN,
            "bbox": [round(v, 1) for v in u],
        }
        manifest_states.append(entry)
        print(f"  {sid:<13} {name}  {cols}x{rows} 格 {fw}x{fh}  "
              f"帧={len(picks)}/{m.n_frames}  k={t.k:.4f}  cell=({cell_x},{cell_y})  "
              f"脸宽(产物)={measured.get(sid, 0):.1f}  {cols * fw}x{rows * fh}",
              flush=True)

    manifest = {
        "$schema": "internal://mermaid-desktop-pet/atlas-manifest/v1",
        "canvas": {"w": canvas_w, "h": canvas_h,
                   "groundY": round(common_ground_y - gy0 + args.padding, 2),
                   "faceCX": round(common_face_cx - gx0 + args.padding, 2),
                   "faceWidth": args.face_width},
        "grid": {"cols": args.cols, "padding": args.padding},
        # 随机链：只有 idle / sleeping 进链（对齐 dsh-pet「事件动画不进随机链」）。
        # 权重同 dsh-pet 的 animationWeights 口径：各项权重按百分比抽，总和 100。
        "chain": {
            "weights": {"idle": 65, "sleeping": 35},
            "sleepAfterIdleLoops": 3,
            "note": "一轮播完按权重抽下一段；sleeping 也可由 idle 连播若干轮后自动进入。",
        },
        "note": ("脸部为尺度锚（八态脸宽统一）；脚底线为垂直锚（dragging 例外，运行时由鼠标接管）；"
                 "水平以脸心对齐。末帧为首帧副本，故每态帧数 = 源帧数 - 1，一轮无缝闭合。"),
        "states": manifest_states,
        "transitions": transitions,
        "priorityOrder": [s["id"] for s in sorted(manifest_states,
                                                  key=lambda x: -x["priority"])],
    }

    # 预览：八态首帧（保持纵横比）+ 公共脚底线
    if not args.no_preview:
        tile_w, tile_h = 240, 300
        sheet = Image.new("RGBA", (tile_w * 4, tile_h * 2 + 34), (28, 30, 36, 255))
        ground_canvas = manifest["canvas"]["groundY"]
        for i, e in enumerate(manifest_states):
            a = Image.open(out / e["atlas"]).convert("RGBA")
            cell = a.crop((0, 0, e["fw"], e["fh"]))
            # 该态内容在画布里的原点（格子原点 + padding）
            ox = e["cellX"] + args.padding
            oy = e["cellY"] + args.padding
            scale = min((tile_w - 8) / e["fw"], (tile_h - 8) / e["fh"])
            fr = cell.resize((max(1, int(e["fw"] * scale)), max(1, int(e["fh"] * scale))),
                             Image.LANCZOS)
            # 画布 → 预览平面的偏移（脚底线落在同一条横线上）
            px = (i % 4) * tile_w + 4
            py = (i // 4) * tile_h + 4
            bg = Image.new("RGBA", (tile_w, tile_h), (60, 64, 74, 255))
            # 脚底线
            gy = py + int(round((ground_canvas - oy) * scale)) + 4
            if 0 <= gy < tile_h:
                for x in range(tile_w):
                    bg.putpixel((x, gy), (255, 196, 0, 255))
            bg.alpha_composite(fr, (4, 4))
            sheet.alpha_composite(bg, ((i % 4) * tile_w, (i // 4) * tile_h))
        sheet.save(out / "preview-八态-图集.png")
        print(f"\n预览：{out / 'preview-八态-图集.png'}（黄线 = 公共脚底线）")

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"清单：{out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
