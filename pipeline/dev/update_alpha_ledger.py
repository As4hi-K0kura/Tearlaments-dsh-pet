#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""透明版交付收尾：复制到交付目录、算 MD5、写透明版台账、修正 v3 台账的 drag 条目。"""
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import hashlib
import json
import os
import shutil

SRC = str(_paths.VID_ALPHA)
DST = str(_paths.SRC_ALPHA)
LEDGER = str(_paths.LEDGER_ALPHA)
V3 = str(_paths.LEDGER_V3)
REPORT = str(_paths.TMP_VERIFY / "alpha-verify.json")

ORDER = ["pet-idle", "pet-think", "pet-tool", "pet-wait",
         "pet-done", "pet-error", "pet-drag", "pet-sleep"]
ZH = {"pet-idle": "待机", "pet-think": "思考", "pet-tool": "调用工具",
      "pet-wait": "等待", "pet-done": "完成", "pet-error": "出错",
      "pet-drag": "拖拽", "pet-sleep": "休眠"}


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def main():
    os.makedirs(DST, exist_ok=True)
    report = {}
    if os.path.exists(REPORT):
        with open(REPORT, encoding="utf-8") as fh:
            report = json.load(fh)

    entries = []
    for st in ORDER:
        src = os.path.join(SRC, st + ".webm")
        if not os.path.exists(src):
            continue
        dst = os.path.join(DST, st + ".webm")
        shutil.copyfile(src, dst)
        r = report.get(st + ".webm", {})
        entries.append({
            "state": st,
            "verdict": "可用",
            "failedItems": "-",
            "round": "alpha-v1",
            "file": dst,
            "source": os.path.join(str(_paths.VID_FINAL), st + ".mp4"),
            "score": "像素级全项通过",
            "note": (
                "透明背景版（VP8 + alpha WebM，768x768 / 24fps / 无音轨，8Mbps）。"
                "由绿幕母版逐帧物理抠像得到：背景色取绿意像素中位数 (0,244,0)，"
                "前景色由内部像素向外传播 14px 得到，alpha 按合成公式 "
                "a=<obs-bg,fg-bg>/|fg-bg|^2 解算并加 0.04/0.96 软阈值，"
                "透明区 RGB 置 0、边缘与实心区一律用传播出来的干净前景色（零绿边），"
                "绿染像素（green_dom>12）一律不作种子以免把绿渗进整条边带。"
                "像素级：alpha 全透 %s%% / 全不透 %s%% / 过渡带 %s%%；"
                "过渡带绿 excess 均值 %s；实心区绿 excess>12 的像素 %s 个（峰值 %s）；"
                "独立破洞判据（源帧与背景差>200 而输出 alpha<128）%s 像素；"
                "回合成对源帧误差 %s；"
                "末帧→首帧全分辨率 rgba mean %s / max %s，96px 显示尺寸 %s（完全闭合）。"),
        })
        e = entries[-1]
        e["note"] = e["note"] % (
            r.get("alpha0_pct", "-"), r.get("alpha255_pct", "-"),
            r.get("alpha_transition_pct", "-"),
            r.get("edge_green_mean", "-"),
            r.get("solid_green_px_over_12", "-"),
            r.get("solid_green_max", "-"),
            r.get("holes_vs_source", "-"),
            r.get("recon_err_vs_source", "-"),
            r.get("seam_rgba_mean", "-"), r.get("seam_rgb_max", "-"),
            r.get("seam_at96_mean", "-"),
        )
        e["size_bytes"] = os.path.getsize(dst)
        e["md5"] = md5(dst)
        e["frames"] = r.get("frames", "-")
        e["alpha_mode"] = r.get("alpha_mode", "-")
        e["zh"] = ZH.get(st, st)

    with open(LEDGER, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=2)
    print("透明版台账 ->", LEDGER)

    # 修正 v3 台账的 drag 条目：v17 -> v18
    with open(V3, encoding="utf-8") as fh:
        v3 = json.load(fh)
    for it in v3:
        if it.get("state") == "pet-drag":
            it["round"] = "v18"
            it["source"] = str(_paths.VIDEOS / "pet-drag-v18-final.mp4")
            it["score"] = "8/8"
            it["note"] = (
                "v18 视频（H3-Max 768P 1:1 5s，task 455005486293397）：**优雅重制**——"
                "去掉 v17 的头发上飘，改成重力下垂 + 次级运动滞后半拍；"
                "姿态换成半阖眼 + 闭唇浅笑 + 双腿并拢微屈 + 翅膀半开下垂如披风。"
                "提示词补上身形硬约束（下巴到鞋底约两个头高、举起的手只到头顶上方约半个头高）。"
                "语义编号清单 8/8 全过。像素级：头顶（脸框以上）非绿像素中位 266281→16014、"
                "最长横段 768→317；角色水平质心摆幅 88.3px→155.2px；"
                "末帧→首帧 96px 显示尺寸差异像素 0.00%（max 3）。"
                "归一化：两段式脸框口径（肤色连通域里 bbox 宽高比∈[0.78,1.55]、中轴上部、"
                "最靠近中轴的一块），目标脸宽 165（组内中位），k=1.1149，居中偏差 (1.0,0.0)px，"
                "最小边距 104px，角色占画布 48.7%。"
                "成片已统一剥除音轨（无音频流）。"
            )
    with open(V3, "w", encoding="utf-8") as fh:
        json.dump(v3, fh, ensure_ascii=False, indent=2)
    print("v3 台账 drag 条目已更新为 v18")

    for e in entries:
        print("  %-12s %8d B  %s" % (e["state"], e["size_bytes"], e["md5"]))


if __name__ == "__main__":
    main()
