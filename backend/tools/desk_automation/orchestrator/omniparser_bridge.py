# -*- coding: utf-8 -*-
"""OmniParser（microsoft/OmniParser）桥接：专用图标检测 + OCR 融合 + Florence2 描述 → 元素列表。

使用方式见同目录文档 `OMNIPARSER_SETUP.md`，或在 `~/.miro/desk_automation.json` 中设置：

  "som_parser": {
    "backend": "omniparser",
    "omni_parser_root": "C:/dev/OmniParser",
    ...
  }

环境变量 `OMNI_PARSER_ROOT` 可与配置同时存在；**配置项优先**。
"""

from __future__ import annotations

import base64
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# 懒加载缓存（避免每帧重载 Florence + YOLO）
_omni_cache: dict[str, Any] = {
    "root": None,
    "yolo": None,
    "caption": None,
}


@dataclass
class OmniUiElement:
    element_id: str
    bbox_xyxy: tuple[int, int, int, int]
    type: str
    content: str
    source: str = "omniparser"


def reset_omni_model_cache() -> None:
    """释放缓存（切换权重目录或调试时调用）。"""
    global _omni_cache
    _omni_cache = {"root": None, "yolo": None, "caption": None}


def _ensure_omni_on_path(omni_root: Path) -> None:
    root = str(omni_root.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)


def resolve_omni_root(explicit: Path | str | None) -> Path:
    if explicit is not None and str(explicit).strip():
        p = Path(str(explicit).strip()).expanduser()
        if p.is_dir():
            return p
    env = os.environ.get("OMNI_PARSER_ROOT", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p
    raise RuntimeError(
        "未配置 OmniParser 路径：请在 desk_automation.json 的 som_parser.omni_parser_root "
        "或环境变量 OMNI_PARSER_ROOT 中设置为克隆仓库的根目录。"
    )


def _ratio_xyxy_to_pixel(
    box: Any,
    w: int,
    h: int,
) -> tuple[int, int, int, int]:
    if isinstance(box, (list, tuple)) and len(box) >= 4:
        a, b, c, d = float(box[0]), float(box[1]), float(box[2]), float(box[3])
        if max(abs(a), abs(b), abs(c), abs(d)) <= 1.5:
            return int(a * w), int(b * h), int(c * w), int(d * h)
        return int(a), int(b), int(c), int(d)
    if isinstance(box, dict):
        keys = {k.lower(): k for k in box}
        for xa, ya, xb, yb in (
            ("xmin", "ymin", "xmax", "ymax"),
            ("x1", "y1", "x2", "y2"),
        ):
            if all(k in keys for k in (xa, ya, xb, yb)):
                a, b, c, d = (
                    float(box[keys[xa]]),
                    float(box[keys[ya]]),
                    float(box[keys[xb]]),
                    float(box[keys[yb]]),
                )
                if max(abs(a), abs(b), abs(c), abs(d)) <= 1.5:
                    return int(a * w), int(b * h), int(c * w), int(d * h)
                return int(a), int(b), int(c), int(d)
    raise ValueError(f"无法解析 bbox: {box!r}")


def _get_cached_omni_models(omni_root: Path) -> tuple[Any, Any]:
    """各进程内只加载一次 YOLO(icon_detect) + Florence2。"""
    global _omni_cache
    key = str(omni_root.resolve())
    if _omni_cache["root"] == key and _omni_cache["yolo"] is not None and _omni_cache["caption"] is not None:
        return _omni_cache["yolo"], _omni_cache["caption"]

    weights = omni_root / "weights"
    som_pt = weights / "icon_detect" / "model.pt"
    cap_dir = weights / "icon_caption_florence"
    if not som_pt.is_file():
        raise FileNotFoundError(
            f"缺少 icon 检测权重: {som_pt}\n"
            "从 Hugging Face microsoft/OmniParser-v2.0 下载到 OmniParser/weights/（见 OMNIPARSER_SETUP.md）。"
        )
    if not cap_dir.is_dir():
        raise FileNotFoundError(
            f"缺少 Florence 权重目录: {cap_dir}\n"
            "下载 icon_caption 后重命名为 icon_caption_florence。"
        )

    _ensure_omni_on_path(omni_root)
    import torch
    from util.utils import get_caption_model_processor, get_yolo_model  # type: ignore  # noqa: E402

    device = "cuda" if torch.cuda.is_available() else "cpu"
    yolo_model = get_yolo_model(model_path=str(som_pt))
    caption_model_processor = get_caption_model_processor(
        model_name="florence2",
        model_name_or_path=str(cap_dir),
        device=device,
    )
    _omni_cache = {"root": key, "yolo": yolo_model, "caption": caption_model_processor}
    return yolo_model, caption_model_processor


def run_omniparser_on_pil(
    image_rgb: Image.Image,
    *,
    omni_root: Path | str | None = None,
    box_threshold: float | None = None,
    iou_threshold: float | None = None,
    imgsz: int | None = None,
    use_paddleocr: bool = True,
    use_local_semantics: bool = True,
    batch_size: int = 12,
) -> tuple[Image.Image, list[OmniUiElement], dict[str, Any]]:
    """运行 OmniParser，返回（官方标注图 PIL, 元素列表, 原始字段）。

    batch_size：Florence 批大小；**RTX 4060 8GB 建议 8～16**，显存不够可再降或设 use_local_semantics=false（无图标描述）。
    """
    root = resolve_omni_root(omni_root)
    yolo_model, caption_model_processor = _get_cached_omni_models(root)

    import torch
    from util.utils import check_ocr_box, get_som_labeled_img  # type: ignore  # noqa: E402

    box_threshold = float(
        box_threshold if box_threshold is not None else os.environ.get("OMNI_BOX_THRESHOLD", "0.05")
    )
    iou_threshold = float(
        iou_threshold if iou_threshold is not None else os.environ.get("OMNI_IOU_THRESHOLD", "0.7")
    )
    imgsz = int(imgsz if imgsz is not None else os.environ.get("OMNI_IMGSZ", "640"))

    if image_rgb.mode != "RGB":
        image_rgb = image_rgb.convert("RGB")
    w, h = image_rgb.size
    box_overlay_ratio = max(w, h) / 3200.0
    draw_bbox_config = {
        "text_scale": 0.8 * box_overlay_ratio,
        "text_thickness": max(int(2 * box_overlay_ratio), 1),
        "text_padding": max(int(3 * box_overlay_ratio), 1),
        "thickness": max(int(3 * box_overlay_ratio), 1),
    }

    ocr_bbox_rslt, _ = check_ocr_box(
        image_rgb,
        display_img=False,
        output_bb_format="xyxy",
        easyocr_args={"paragraph": False, "text_threshold": 0.9},
        use_paddleocr=use_paddleocr,
    )
    text, ocr_bbox = ocr_bbox_rslt

    dino_b64, label_coordinates, filtered_boxes_elem = get_som_labeled_img(
        image_rgb,
        yolo_model,
        BOX_TRESHOLD=box_threshold,
        output_coord_in_ratio=True,
        ocr_bbox=ocr_bbox,
        draw_bbox_config=draw_bbox_config,
        caption_model_processor=caption_model_processor,
        ocr_text=text,
        iou_threshold=iou_threshold,
        imgsz=imgsz,
        use_local_semantics=use_local_semantics,
        batch_size=batch_size,
        scale_img=False,
    )

    raw_bytes = base64.b64decode(dino_b64)
    som_image = Image.open(io.BytesIO(raw_bytes)).convert("RGB")

    elements: list[OmniUiElement] = []
    if isinstance(filtered_boxes_elem, (list, tuple)):
        for i, elem in enumerate(filtered_boxes_elem, start=1):
            if not isinstance(elem, dict):
                continue
            box = elem.get("bbox")
            if box is None:
                continue
            try:
                xyxy = _ratio_xyxy_to_pixel(box, w, h)
            except Exception:
                continue
            x1, y1, x2, y2 = xyxy
            if x2 <= x1 or y2 <= y1:
                continue
            ct = str(elem.get("content") or "")
            typ = str(elem.get("type") or "icon")
            elements.append(
                OmniUiElement(
                    element_id=f"~{i}",
                    bbox_xyxy=xyxy,
                    type=typ,
                    content=ct[:500],
                    source="omniparser",
                )
            )

    raw: dict[str, Any] = {
        "label_coordinates": label_coordinates,
        "filtered_boxes_elem": filtered_boxes_elem,
        "ocr_text": text,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
    }
    return som_image, elements, raw


def draw_som_tilde_style(
    base_rgb: Image.Image,
    elements: list[OmniUiElement],
) -> Image.Image:
    """简易红框 + ~n（与 screen_parser._draw_som 视觉弱一致时的备用）。"""
    img = base_rgb.copy()
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    for el in elements:
        x1, y1, x2, y2 = el.bbox_xyxy
        draw.rectangle([x1, y1, x2, y2], outline="red", width=2)
        draw.text((x1 + 2, max(0, y1 - 12)), el.element_id, fill="red", font=font)
    return img
