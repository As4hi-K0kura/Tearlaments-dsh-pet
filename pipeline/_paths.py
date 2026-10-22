#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""管线默认路径：全部相对仓库根目录解析，可用环境变量覆盖。

仓库里只放代码、状态定义与图集产物；**源素材（八个透明背景版 webm）与
中间产物不入库**。要跑完整管线，先把源视频放到 SRC_ALPHA 指向的目录
（默认 assets/alpha-webm），或用环境变量指到你自己的位置：

    PET_SRC_ALPHA=/path/to/alpha-webm python pipeline/build_atlas.py
"""
import os
from pathlib import Path

# 仓库根（pipeline/ 的上一级）
REPO = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent          # 本目录
APP_DIR = REPO / "desktop-pet"                    # JavaFX 桌宠


def _p(env, rel):
    """环境变量优先，其次相对仓库根的默认位置。"""
    v = os.environ.get(env)
    return Path(v).expanduser() if v else REPO / rel


# 源素材：八个透明背景版 webm（不入库）
SRC_ALPHA = _p("PET_SRC_ALPHA", "assets/alpha-webm")
# 图集产物目录
ATLAS_DIR = _p("PET_ATLAS_DIR", "desktop-pet/src/main/resources/pet")
# 状态定义（八态优先级 / 转场表的事实来源）
STATES = _p("PET_STATES", "pipeline/states.json")
# 视频生成中间产物（不入库）
VIDEOS = _p("PET_VIDEOS", "work/videos")
VID_FINAL = VIDEOS / "final"
VID_ALPHA = VIDEOS / "final-alpha"
DELIV = _p("PET_DELIV", "work/deliverables")
TMP = _p("PET_TMP", "work/tmp")
TMP_VERIFY = TMP / "verify"
TMP_FMT = TMP / "fmt"
TMP_NORM = TMP / "norm"
NORM = _p("PET_NORM", "work/normalized")
NORM_UNIFORM = _p("PET_NORM_UNIFORM", "work/normalized-uniform")
FRAMES = _p("PET_FRAMES", "work/frames")
FRAMES_DIAG = _p("PET_FRAMES_DIAG", "work/frames-diag")
LEDGER = _p("PET_LEDGER", "work/video-deliverables.json")
LEDGER_ALPHA = _p("PET_LEDGER_ALPHA", "work/video-deliverables-alpha.json")
LEDGER_V3 = _p("PET_LEDGER_V3", "work/video-deliverables-v3.json")
