# -*- coding: utf-8 -*-
"""boke — 波士顿圆脸采访视频中文配音流水线。

stage 顺序: extract → ocr → diarize → attribute → synthesize → mix
约定: 每个模块提供 run(...) 返回产物路径 dict;pipeline 按产物存在性断点续跑。
"""
__version__ = "0.1.0"
