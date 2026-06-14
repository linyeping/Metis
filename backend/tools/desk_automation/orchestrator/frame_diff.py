# -*- coding: utf-8 -*-
"""帧差分析：判断全屏变化幅度、局部热点、是否「流式小范围快变」（适合节流不调 API）。"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageChops


@dataclass
class FrameDiffResult:
    """两帧对比结果。"""
    diff_ratio: float  # 0~1，全图平均变化强度
    max_cell_ratio: float  # 网格内最大单元格平均变化（局部剧变）
    flicker_ratio: float  # max_cell / (diff_ratio+eps)，大表示「整体变化不大但局部跳变」
    hotspots: list[tuple[int, int, int, int]]  # 全分辨率下的热点 bbox (x,y,w,h)，最多 3 个
    raw_size: tuple[int, int]

    def to_log(self) -> str:
        return (
            f"diff={self.diff_ratio:.4f} max_cell={self.max_cell_ratio:.4f} "
            f"flicker={self.flicker_ratio:.2f} hotspots={len(self.hotspots)}"
        )


def compare_frames(prev_png: bytes, curr_png: bytes, grid: int = 8) -> FrameDiffResult:
    """对比两帧 PNG。热点基于差分图 + 网格能量选取。"""
    prev = Image.open(io.BytesIO(prev_png)).convert("RGB")
    curr = Image.open(io.BytesIO(curr_png)).convert("RGB")
    if prev.size != curr.size:
        curr = curr.resize(prev.size, Image.Resampling.LANCZOS)

    w, h = prev.size
    diff = ImageChops.difference(prev, curr).convert("L")
    arr = diff.load()
    total = w * h * 255.0
    s = 0.0
    for y in range(h):
        for x in range(w):
            s += arr[x, y]
    diff_ratio = (s / total) if total else 0.0

    gw, gh = grid, grid
    cw, ch = max(1, w // gw), max(1, h // gh)
    cell_avgs: list[tuple[int, int, float]] = []
    max_cell = 0.0
    for gy in range(gh):
        for gx in range(gw):
            x0, y0 = gx * cw, gy * ch
            x1, y1 = min(w, x0 + cw), min(h, y0 + ch)
            sub = 0.0
            cnt = 0
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    sub += arr[xx, yy]
                    cnt += 1
            avg = (sub / (cnt * 255.0)) if cnt else 0.0
            cell_avgs.append((gx, gy, avg))
            max_cell = max(max_cell, avg)

    eps = 1e-6
    flicker = max_cell / (diff_ratio + eps)

    sorted_cells = sorted(cell_avgs, key=lambda t: -t[2])
    hotspots: list[tuple[int, int, int, int]] = []
    pad = 24
    for gx, gy, energy in sorted_cells[:3]:
        if energy < 0.02:
            continue
        x0, y0 = gx * cw, gy * ch
        x1, y1 = min(w, x0 + cw), min(h, y0 + ch)
        bx0 = max(0, x0 - pad)
        by0 = max(0, y0 - pad)
        bx1 = min(w, x1 + pad)
        by1 = min(h, y1 + pad)
        hotspots.append((bx0, by0, bx1 - bx0, by1 - by0))

    return FrameDiffResult(
        diff_ratio=diff_ratio,
        max_cell_ratio=max_cell,
        flicker_ratio=flicker,
        hotspots=hotspots,
        raw_size=(w, h),
    )


def should_throttle_api(
    fd: FrameDiffResult,
    policy: dict[str, Any],
    time_since_last_api: float,
) -> bool:
    """是否因「流式输出 / 局部快变」跳过本轮多模态 API。"""
    if time_since_last_api >= float(policy.get("min_api_interval_sec", 2.0)):
        return False
    dr = fd.diff_ratio
    mx = fd.max_cell_ratio
    fl = fd.flicker_ratio
    if dr < float(policy.get("throttle_diff_max", 0.035)) and mx > float(
        policy.get("throttle_local_min", 0.12)
    ):
        return True
    if dr < float(policy.get("throttle_tiny_diff", 0.008)):
        return True
    if dr < float(policy.get("throttle_diff_max", 0.035)) and fl > float(
        policy.get("throttle_flicker_min", 4.0)
    ):
        return True
    return False


def crop_hotspots_to_png(full_png: bytes, hotspots: list[tuple[int, int, int, int]], max_side: int = 640) -> list[tuple[int, int, bytes]]:
    """裁剪热点区域为 PNG 列表，每项 (offset_x, offset_y, png_bytes)。"""
    img = Image.open(io.BytesIO(full_png)).convert("RGB")
    out: list[tuple[int, int, bytes]] = []
    for x, y, bw, bh in hotspots[:3]:
        box = (x, y, min(img.width, x + bw), min(img.height, y + bh))
        crop = img.crop(box)
        if max(crop.size) > max_side:
            crop.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        out.append((x, y, buf.getvalue()))
    return out


def compose_rois_for_prompt(rois: list[tuple[int, int, bytes]]) -> tuple[bytes, str]:
    """将多块 ROI 横向拼接成一张图，并生成说明文字（供 LLM 用全屏绝对坐标）。"""
    if not rois:
        return b"", ""
    imgs = [Image.open(io.BytesIO(p)).convert("RGB") for _, _, p in rois]
    h = max(im.height for im in imgs)
    padded = []
    for im in imgs:
        if im.height < h:
            bg = Image.new("RGB", (im.width, h), (20, 20, 20))
            bg.paste(im, (0, (h - im.height) // 2))
            padded.append(bg)
        else:
            padded.append(im)
    total_w = sum(im.width for im in padded) + 10 * (len(padded) - 1)
    canvas = Image.new("RGB", (total_w, h), (30, 30, 30))
    x = 0
    lines = []
    for i, ((ox, oy, _), im) in enumerate(zip(rois, padded)):
        canvas.paste(im, (x, 0))
        lines.append(f"子图{i+1} 对应全屏左上角偏移=({ox},{oy})，子图尺寸=({im.width},{im.height})")
        x += im.width + 10
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue(), "\n".join(lines)
