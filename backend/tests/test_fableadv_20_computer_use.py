# -*- coding: utf-8 -*-
"""FABLEADV-20: computer-use 坐标映射可靠性。

核心 bug：模型在被缩放到 <=1280 宽的截图上选点，但 desktop_action 把坐标当
物理像素直接点 → 高分屏偏 1.5~2 倍。这里验证缩放预测与坐标映射对齐。
"""
from __future__ import annotations

import pytest

from backend.runtime.image_utils import (
    DEFAULT_SCREENSHOT_MAX_WIDTH,
    predict_display_dimensions,
)
from backend.runtime.tool_registry import _png_dimensions
from backend.tools.desk_automation.input import actions


@pytest.fixture(autouse=True)
def _reset_frame():
    actions.clear_screenshot_frame()
    yield
    actions.clear_screenshot_frame()


def test_predict_display_dimensions_downscales_hidpi():
    # 1920x1080 → 宽被压到 1280，等比 → 720 高
    assert predict_display_dimensions(1920, 1080) == (1280, 720)
    # 2560x1440 → 1280x720
    assert predict_display_dimensions(2560, 1440) == (1280, 720)


def test_predict_display_dimensions_no_upscale_for_small():
    # 已不超过阈值，原样返回（不放大、不缩小）
    assert predict_display_dimensions(1024, 768) == (1024, 768)
    assert predict_display_dimensions(DEFAULT_SCREENSHOT_MAX_WIDTH, 800) == (
        DEFAULT_SCREENSHOT_MAX_WIDTH,
        800,
    )


def test_predict_display_dimensions_guards_bad_input():
    assert predict_display_dimensions(0, 0) == (0, 0)
    assert predict_display_dimensions(-5, 10) == (-5, 10)


def test_map_model_point_identity_without_frame():
    # 没有记录帧时恒等映射，安全降级
    assert actions.map_model_point(640, 360) == (640, 360)


def test_map_model_point_scales_to_physical():
    # 模型看到 1280x720（实际 1920x1080）→ 中心点 (640,360) 映射回 (960,540)
    actions.record_screenshot_frame(1920, 1080, 1280, 720)
    assert actions.map_model_point(640, 360) == (960, 540)
    # 2x 屏：(640,360) → (1280,720)
    actions.record_screenshot_frame(2560, 1440, 1280, 720)
    assert actions.map_model_point(640, 360) == (1280, 720)


def test_map_model_point_identity_when_no_resize():
    # 小屏未缩放：disp==phys → 恒等
    actions.record_screenshot_frame(1024, 768, 1024, 768)
    assert actions.map_model_point(500, 400) == (500, 400)


def test_clear_frame_restores_identity():
    actions.record_screenshot_frame(1920, 1080, 1280, 720)
    actions.clear_screenshot_frame()
    assert actions.map_model_point(640, 360) == (640, 360)


def test_png_dimensions_reads_ihdr():
    # 构造仅含 PNG 签名 + IHDR 头的前 24 字节（_png_dimensions 只读这段）
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = b"\x00\x00\x00\x0d" + b"IHDR" + (200).to_bytes(4, "big") + (100).to_bytes(4, "big")
    assert _png_dimensions(sig + ihdr) == (200, 100)


def test_png_dimensions_rejects_non_png():
    assert _png_dimensions(b"not a png") == (0, 0)
    assert _png_dimensions(b"") == (0, 0)


def test_coord_space_normalized(monkeypatch):
    # 归一化 0-1000：500 → 物理中心
    monkeypatch.setenv("METIS_CUA_COORD_SPACE", "normalized")
    actions.record_screenshot_frame(2560, 1440, 1280, 720)
    assert actions.map_model_point(500, 500) == (1280, 720)
    assert actions.map_model_point(1000, 1000) == (2560, 1440)


def test_coord_space_fraction(monkeypatch):
    # 0-1 比例
    monkeypatch.setenv("METIS_CUA_COORD_SPACE", "fraction")
    actions.record_screenshot_frame(2560, 1440, 1280, 720)
    assert actions.map_model_point(0.5, 0.5) == (1280, 720)


def test_coord_space_default_image_unaffected(monkeypatch):
    # 默认 image：仍按缩放图像素 ×scale（不受新逻辑影响）
    monkeypatch.delenv("METIS_CUA_COORD_SPACE", raising=False)
    actions.record_screenshot_frame(2560, 1440, 1280, 720)
    assert actions.map_model_point(640, 360) == (1280, 720)


def test_full_chain_hidpi_click_lands_on_target():
    """端到端逻辑：模型在 1280 图上点 (1000, 200)，实际 1920 屏应落在 (1500, 300)。"""
    phys_w, phys_h = 1920, 1080
    disp_w, disp_h = predict_display_dimensions(phys_w, phys_h)
    actions.record_screenshot_frame(phys_w, phys_h, disp_w, disp_h)
    # 模型读到的搜索框在缩放图的 (1000, 200)
    px, py = actions.map_model_point(1000, 200)
    assert (px, py) == (1500, 300)
