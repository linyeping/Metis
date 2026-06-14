# -*- coding: utf-8 -*-
"""L2 本地视觉管线：YOLOv8 图标/区域 + OCR 文字框融合 → SoM 编号图 + UIElementList。

约束（交接 part_07）：
  · YOLO 权重：**优先**用 ultralytics Hub/内置名（如 `yolov8s.pt`，可自动缓存）；失败再用 pathlib 加载包内
    `models/weights/yolov8s.pt`（禁止写死盘符）。
  · YOLO 与 OCR 框 IOU 重叠时合并去重，再统一打 ~1、~2…
  · SoM 仅在 ROI 图像上绘制红框 + 编号；原图与标注图尺寸一致。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .. import config as _da_config

from PIL import Image, ImageDraw, ImageFont

from .ocr_locate import (
    _clean_ocr_text,
    _get_paddle_ocr,
    _get_prefer,
    _get_pytesseract,
)

# 可选日志
try:
    from . import desk_log
except Exception:
    desk_log = None  # type: ignore


def _log(level: str, msg: str) -> None:
    if desk_log is not None:
        try:
            desk_log.log(level, "screen_parser", msg)
        except Exception:
            pass


def _yolo_weights_path() -> Path:
    """Hub 不可用时的本地回退：优先 ``<miro>/var/rely/models/weights/yolov8s.pt``，其次包内 models。"""
    try:
        r = _da_config.get_rely_dir() / "models" / "weights" / "yolov8s.pt"
        if r.is_file():
            return r
    except Exception:
        pass
    pkg_root = Path(__file__).resolve().parent.parent
    return pkg_root / "models" / "weights" / "yolov8s.pt"


_yolo_model: Any = None

# 优先尝试的 Hub / 内置预训练名（与交接约定 yolov8s 一致；无需仓库内必有 .pt 文件）
_YOLO_HUB_NAMES: tuple[str, ...] = ("yolov8s.pt", "yolov8s")


def _get_yolo_model() -> Any:
    """懒加载 YOLO：先 `YOLO("yolov8s.pt")` 等 Hub 名；均失败再加载 pathlib 本地 weights。"""
    global _yolo_model
    if _yolo_model is not None:
        return _yolo_model
    from backend.runtime.pip_helper import ensure_import
    ultralytics = ensure_import("ultralytics", pip="ultralytics")
    YOLO = ultralytics.YOLO

    last_err: BaseException | None = None
    for name in _YOLO_HUB_NAMES:
        try:
            _yolo_model = YOLO(name)
            _log("INFO", f"YOLO 已从 Hub/内置名加载: {name}")
            return _yolo_model
        except BaseException as e:
            last_err = e
            continue

    w = _yolo_weights_path()
    if w.is_file():
        try:
            _yolo_model = YOLO(str(w))
            _log("INFO", f"YOLO 已从本地加载: {w}")
            return _yolo_model
        except BaseException as e:
            last_err = e

    hint = (
        f"无法加载 YOLO：已尝试 Hub 名 {_YOLO_HUB_NAMES}，本地路径 {w} "
        f"{'存在但加载失败' if w.is_file() else '不存在'}"
    )
    if last_err is not None:
        raise RuntimeError(f"{hint}. 最后错误: {last_err}") from last_err
    raise RuntimeError(hint)


# ─── 几何：IOU / 合并 ───


def _bbox_xyxy_from_xywh(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    return x, y, x + max(0, w), y + max(0, h)


def _iou_xyxy(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / float(union)


def _union_xyxy(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    return (
        min(a[0], b[0]),
        min(a[1], b[1]),
        max(a[2], b[2]),
        max(a[3], b[3]),
    )


def _area_xyxy(b: tuple[int, int, int, int]) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


# ─── OCR：带 bbox 的扫描（与 ocr_locate 引擎一致） ───


@dataclass
class _OcrBox:
    xyxy: tuple[int, int, int, int]
    text: str
    confidence: float
    engine: str


def _scan_tesseract_boxes(img: Image.Image) -> list[_OcrBox]:
    pytesseract = _get_pytesseract()
    if pytesseract is None:
        return []
    try:
        data = pytesseract.image_to_data(
            img,
            lang="chi_sim+eng",
            config="--oem 3 --psm 6",
            output_type=pytesseract.Output.DICT,
        )
    except Exception:
        try:
            data = pytesseract.image_to_data(
                img,
                lang="eng",
                config="--oem 3 --psm 6",
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            return []

    out: list[_OcrBox] = []
    n = len(data.get("text", []))
    for i in range(n):
        raw = (data["text"][i] or "").strip()
        if not raw:
            continue
        text = _clean_ocr_text(raw)
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = 0.0
        if conf < 20:
            continue
        left = int(data["left"][i])
        top = int(data["top"][i])
        w = int(data["width"][i])
        h = int(data["height"][i])
        xyxy = _bbox_xyxy_from_xywh(left, top, w, h)
        out.append(_OcrBox(xyxy=xyxy, text=text, confidence=conf, engine="tesseract"))
    return out


def _scan_paddle_boxes(img: Image.Image) -> list[_OcrBox]:
    paddle = _get_paddle_ocr()
    if paddle is None:
        return []
    try:
        import numpy as np

        img_np = np.array(img)
        if len(img_np.shape) == 3 and img_np.shape[2] == 3:
            img_bgr = img_np[:, :, ::-1]
        else:
            img_bgr = img_np
    except Exception:
        return []
    try:
        result = paddle.ocr(img_bgr, cls=False)
    except Exception:
        return []
    if not result or not result[0]:
        return []

    out: list[_OcrBox] = []
    for line in result[0]:
        try:
            box = line[0]
            raw_text = line[1][0]
            conf = float(line[1][1]) * 100.0
        except (IndexError, TypeError, ValueError):
            continue
        text = _clean_ocr_text(raw_text)
        if not text or conf < 20:
            continue
        xs = [float(pt[0]) for pt in box]
        ys = [float(pt[1]) for pt in box]
        x1, y1 = int(min(xs)), int(min(ys))
        x2, y2 = int(max(xs)), int(max(ys))
        out.append(
            _OcrBox(xyxy=(x1, y1, x2, y2), text=text, confidence=conf, engine="paddle")
        )
    return out


def _dedupe_ocr_boxes(boxes: list[_OcrBox], iou_t: float = 0.55) -> list[_OcrBox]:
    """both 模式或重复扫描时，去掉几乎重合的文字框。"""
    if len(boxes) <= 1:
        return boxes
    sorted_b = sorted(boxes, key=lambda b: b.confidence, reverse=True)
    kept: list[_OcrBox] = []
    for b in sorted_b:
        if all(_iou_xyxy(b.xyxy, k.xyxy) < iou_t for k in kept):
            kept.append(b)
    return kept


def _collect_ocr_boxes(img: Image.Image) -> list[_OcrBox]:
    prefer = _get_prefer()
    boxes: list[_OcrBox] = []
    if prefer in ("tesseract", "both"):
        boxes.extend(_scan_tesseract_boxes(img))
    if prefer in ("paddle", "both"):
        boxes.extend(_scan_paddle_boxes(img))
    if not boxes and prefer == "tesseract":
        boxes.extend(_scan_paddle_boxes(img))
    if not boxes and prefer == "paddle":
        boxes.extend(_scan_tesseract_boxes(img))
    if prefer == "both":
        boxes = _dedupe_ocr_boxes(boxes)
    return boxes


# ─── YOLO ───


@dataclass
class _YoloDet:
    xyxy: tuple[int, int, int, int]
    confidence: float
    class_name: str


def _yolo_predict_np(img_bgr: Any, conf: float) -> list[_YoloDet]:
    model = _get_yolo_model()
    # ultralytics 接受 BGR np.ndarray (H,W,3)
    res = model.predict(img_bgr, conf=conf, verbose=False)
    dets: list[_YoloDet] = []
    names = getattr(model, "names", None)
    for r in res:
        if r.boxes is None or len(r.boxes) == 0:
            continue
        for b in r.boxes:
            try:
                xy = b.xyxy[0].cpu().numpy().tolist()
                x1, y1, x2, y2 = int(xy[0]), int(xy[1]), int(xy[2]), int(xy[3])
                cf = float(b.conf[0].cpu().item())
                ci = int(b.cls[0].cpu().item())
                if isinstance(names, (list, tuple)) and 0 <= ci < len(names):
                    cname = str(names[ci])
                elif isinstance(names, dict):
                    cname = str(names.get(ci, names.get(str(ci), ci)))
                else:
                    cname = str(ci)
            except Exception:
                continue
            if x2 <= x1 or y2 <= y1:
                continue
            dets.append(_YoloDet(xyxy=(x1, y1, x2, y2), confidence=cf, class_name=cname))
    return dets


# ─── 融合：IOU 去重 + 阅读顺序 ───


@dataclass
class _MergedCand:
    xyxy: tuple[int, int, int, int]
    type: Literal["icon", "text", "mixed"]
    content: str
    confidence: float
    sources: str = ""


def _merge_yolo_ocr(
    yolo: list[_YoloDet],
    ocr: list[_OcrBox],
    iou_threshold: float,
) -> list[_MergedCand]:
    """重叠策略：若 OCR 与某 YOLO 框 IOU ≥ 阈值，合并为一条（框取并集，正文优先 OCR）。"""
    used_y: set[int] = set()
    merged: list[_MergedCand] = []

    for ob in ocr:
        best_i = -1
        best_iou = 0.0
        for i, yd in enumerate(yolo):
            if i in used_y:
                continue
            t = _iou_xyxy(yd.xyxy, ob.xyxy)
            if t > best_iou:
                best_iou = t
                best_i = i

        if best_i >= 0 and best_iou >= iou_threshold:
            used_y.add(best_i)
            yd = yolo[best_i]
            u = _union_xyxy(yd.xyxy, ob.xyxy)
            conf = max(yd.confidence, ob.confidence / 100.0 if ob.confidence > 1.0 else ob.confidence)
            text_part = ob.text.strip()
            icon_part = yd.class_name.strip()
            if text_part and icon_part:
                content = f"{text_part} ({icon_part})"
                typ: Literal["icon", "text", "mixed"] = "mixed"
            elif text_part:
                content = text_part
                typ = "text"
            else:
                content = icon_part or "icon"
                typ = "icon"
            merged.append(
                _MergedCand(
                    xyxy=u,
                    type=typ,
                    content=content[:500],
                    confidence=float(conf),
                    sources=f"yolo+ocr[{best_iou:.2f}]",
                )
            )
        else:
            merged.append(
                _MergedCand(
                    xyxy=ob.xyxy,
                    type="text",
                    content=ob.text[:500],
                    confidence=float(ob.confidence / 100.0 if ob.confidence > 1.0 else ob.confidence),
                    sources=ob.engine,
                )
            )

    for i, yd in enumerate(yolo):
        if i in used_y:
            continue
        merged.append(
            _MergedCand(
                xyxy=yd.xyxy,
                type="icon",
                content=yd.class_name or "icon",
                confidence=yd.confidence,
                sources="yolo",
            )
        )

    # 阅读顺序：自上而下、从左到右（以框中心排序）
    def sort_key(c: _MergedCand) -> tuple[int, int]:
        x1, y1, x2, y2 = c.xyxy
        return (y1 + y2) // 2, (x1 + x2) // 2

    merged.sort(key=sort_key)
    return merged


def _nms_light(
    cands: list[_MergedCand],
    iou_threshold: float,
) -> list[_MergedCand]:
    """同一管线内轻度 NMS：高置信优先，抑制与其 IOU 过高的后续框（防止重复元素）。"""
    if len(cands) <= 1:
        return cands
    sorted_c = sorted(cands, key=lambda c: c.confidence, reverse=True)
    kept: list[_MergedCand] = []
    for c in sorted_c:
        ok = True
        for k in kept:
            if _iou_xyxy(c.xyxy, k.xyxy) >= iou_threshold:
                ok = False
                break
        if ok:
            kept.append(c)
    kept.sort(key=lambda c: ((c.xyxy[1] + c.xyxy[3]) // 2, (c.xyxy[0] + c.xyxy[2]) // 2))
    return kept


@dataclass
class UIElement:
    """统一 UI 元素描述（bbox 为 ROI 像素系 xyxy）。"""

    element_id: str
    bbox_roi_xyxy: tuple[int, int, int, int]
    type: str
    content: str
    confidence: float


@dataclass
class ScreenParseResult:
    """解析结果；extra 可含 backend、omniparser_raw 等。"""

    elements: list[UIElement]
    som_png_bytes: bytes
    som_image_rgb: Image.Image
    yolo_detections: int = 0
    ocr_boxes: int = 0
    merge_iou_threshold: float = 0.45
    extra: dict[str, Any] = field(default_factory=dict)


def _default_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size=size)
    except Exception:
        try:
            return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size=size)
        except Exception:
            return ImageFont.load_default()


def _draw_som(
    base_rgb: Image.Image,
    elements: list[UIElement],
) -> Image.Image:
    """在 ROI 上绘制红框与 ~n 编号（交接：仅 SoM 图用于选 element_id）。"""
    img = base_rgb.copy()
    draw = ImageDraw.Draw(img)
    font = _default_font(max(12, min(base_rgb.size) // 40))
    for el in elements:
        x1, y1, x2, y2 = el.bbox_roi_xyxy
        pad = 2
        draw.rectangle(
            [x1 - pad, y1 - pad, x2 + pad, y2 + pad],
            outline="red",
            width=2,
        )
        label = el.element_id
        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = len(label) * 7, 12
        tx = max(0, x1)
        ty = max(0, y1 - th - 2)
        draw.rectangle([tx, ty, tx + tw + 4, ty + th + 2], fill="red")
        draw.text((tx + 2, ty + 1), label, fill="white", font=font)
    return img


def _merge_two_merged_cands(a: _MergedCand, b: _MergedCand) -> _MergedCand:
    """两路检出同一控件时：框取并集，正文去重拼接，类型不一致则 mixed。"""
    u = _union_xyxy(a.xyxy, b.xyxy)
    parts: list[str] = []
    for t in (a.content, b.content):
        t = (t or "").strip()
        if t and t not in parts:
            parts.append(t)
    content = (" | ".join(parts))[:500] if parts else ""
    if a.type != b.type and a.type in ("icon", "text", "mixed") and b.type in ("icon", "text", "mixed"):
        typ: Literal["icon", "text", "mixed"] = "mixed"
    else:
        typ = a.type if a.type in ("icon", "text", "mixed") else "icon"
    sa, sb = a.sources or "", b.sources or ""
    sources = f"{sa}+{sb}" if sa and sb and sa != sb else (sa or sb or "fused")
    return _MergedCand(
        xyxy=u,
        type=typ,
        content=content,
        confidence=max(a.confidence, b.confidence),
        sources=sources,
    )


def _fuse_merged_streams(
    stream_a: list[_MergedCand],
    stream_b: list[_MergedCand],
    cross_iou: float,
) -> list[_MergedCand]:
    """双流互补：高置信优先入表；后续若与已有框 IOU≥阈值则合并而非丢弃。"""
    pool = sorted(stream_a + stream_b, key=lambda c: c.confidence, reverse=True)
    out: list[_MergedCand] = []
    for c in pool:
        hit = -1
        best_iou = 0.0
        for i, k in enumerate(out):
            t = _iou_xyxy(c.xyxy, k.xyxy)
            if t >= cross_iou and t > best_iou:
                best_iou = t
                hit = i
        if hit >= 0:
            out[hit] = _merge_two_merged_cands(out[hit], c)
        else:
            out.append(c)
    out.sort(
        key=lambda c: ((c.xyxy[1] + c.xyxy[3]) // 2, (c.xyxy[0] + c.xyxy[2]) // 2)
    )
    return out


def _collect_yolo_ocr_merged(
    img: Image.Image,
    *,
    yolo_conf: float,
    merge_iou_threshold: float,
    post_nms_iou: float,
) -> tuple[list[_MergedCand], int, int]:
    """原 YOLO+OCR 管线 → _MergedCand 列表（不含 max_elements 截断）。"""
    import numpy as np

    img_bgr = np.array(img)[:, :, ::-1]
    yolo_dets: list[_YoloDet] = []
    try:
        yolo_dets = _yolo_predict_np(img_bgr, conf=yolo_conf)
    except FileNotFoundError:
        raise
    except Exception as e:
        _log("WARNING", f"YOLO 推理失败，降级为纯 OCR: {e}")
        yolo_dets = []

    ocr_boxes = _collect_ocr_boxes(img)
    _log(
        "INFO",
        f"yolo_ocr branch: yolo={len(yolo_dets)} ocr={len(ocr_boxes)} merge_iou={merge_iou_threshold}",
    )

    merged = _merge_yolo_ocr(yolo_dets, ocr_boxes, merge_iou_threshold)
    merged = _nms_light(merged, post_nms_iou)
    for c in merged:
        c.sources = "yolo_ocr"
    return merged, len(yolo_dets), len(ocr_boxes)


def _collect_omniparser_merged(
    img: Image.Image,
    policy: dict[str, Any],
    *,
    post_nms_iou: float,
) -> tuple[list[_MergedCand], dict[str, Any]]:
    """OmniParser → _MergedCand；返回 (列表, raw 元数据)。"""
    from . import omniparser_bridge

    root = str(policy.get("omni_parser_root") or "").strip()
    root_path = Path(root).expanduser() if root else None

    _, omni_elems, raw = omniparser_bridge.run_omniparser_on_pil(
        img,
        omni_root=root_path,
        box_threshold=float(policy.get("omni_box_threshold", 0.05)),
        iou_threshold=float(policy.get("omni_iou_threshold", 0.7)),
        imgsz=int(policy.get("omni_imgsz", 640)),
        use_paddleocr=bool(policy.get("omni_use_paddleocr", True)),
        use_local_semantics=bool(policy.get("omni_use_local_semantics", True)),
        batch_size=int(policy.get("omni_batch_size", 12)),
    )

    merged: list[_MergedCand] = []
    for oe in omni_elems:
        typ: Literal["icon", "text", "mixed"] = (
            oe.type if oe.type in ("icon", "text", "mixed") else "icon"
        )
        merged.append(
            _MergedCand(
                xyxy=oe.bbox_xyxy,
                type=typ,
                content=oe.content,
                confidence=0.95,
                sources="omniparser",
            )
        )
    merged = _nms_light(merged, post_nms_iou)
    return merged, raw


def _screen_result_from_merged(
    img: Image.Image,
    merged: list[_MergedCand],
    *,
    max_elements: int,
    merge_iou_report: float,
    yolo_detections: int,
    ocr_boxes: int,
    extra: dict[str, Any],
) -> ScreenParseResult:
    """统一：截断数量、阅读序、画 SoM、打包 ScreenParseResult。"""
    work = list(merged)
    if len(work) > max_elements:
        work.sort(key=lambda c: _area_xyxy(c.xyxy), reverse=True)
        work = work[:max_elements]
        work.sort(
            key=lambda c: ((c.xyxy[1] + c.xyxy[3]) // 2, (c.xyxy[0] + c.xyxy[2]) // 2)
        )

    elements: list[UIElement] = []
    for i, c in enumerate(work, start=1):
        elements.append(
            UIElement(
                element_id=f"~{i}",
                bbox_roi_xyxy=c.xyxy,
                type=c.type,
                content=c.content,
                confidence=c.confidence,
            )
        )

    som_img = _draw_som(img, elements)
    buf = io.BytesIO()
    som_img.save(buf, format="PNG")

    n_icon = sum(1 for e in elements if e.type == "icon")
    n_text = sum(1 for e in elements if e.type == "text")
    _log(
        "INFO",
        f"SoM 输出 elements={len(elements)} icon={n_icon} text={n_text} backend={extra.get('backend')}",
    )

    return ScreenParseResult(
        elements=elements,
        som_png_bytes=buf.getvalue(),
        som_image_rgb=som_img,
        yolo_detections=yolo_detections,
        ocr_boxes=ocr_boxes,
        merge_iou_threshold=merge_iou_report,
        extra=extra,
    )


def _parse_roi_omniparser(
    img: Image.Image,
    policy: dict[str, Any],
    *,
    post_nms_iou: float,
    max_elements: int,
) -> ScreenParseResult:
    """仅 OmniParser。"""
    merged, raw = _collect_omniparser_merged(img, policy, post_nms_iou=post_nms_iou)
    return _screen_result_from_merged(
        img,
        merged,
        max_elements=max_elements,
        merge_iou_report=float(policy.get("omni_iou_threshold", 0.7)),
        yolo_detections=sum(1 for m in merged if m.type == "icon"),
        ocr_boxes=sum(1 for m in merged if m.type == "text"),
        extra={
            "backend": "omniparser",
            "post_nms_iou": post_nms_iou,
            "omniparser_device": raw.get("device"),
        },
    )


def _parse_roi_hybrid(
    img: Image.Image,
    policy: dict[str, Any],
    *,
    yolo_conf: float,
    merge_iou_threshold: float,
    post_nms_iou: float,
    max_elements: int,
) -> ScreenParseResult:
    """YOLO+OCR 与 OmniParser 同时运行，再按 hybrid_cross_iou 合并互补。"""
    y_merged, n_yolo, n_ocr = _collect_yolo_ocr_merged(
        img,
        yolo_conf=yolo_conf,
        merge_iou_threshold=merge_iou_threshold,
        post_nms_iou=post_nms_iou,
    )
    o_merged: list[_MergedCand] = []
    raw: dict[str, Any] = {}
    omni_ok = False
    try:
        o_merged, raw = _collect_omniparser_merged(img, policy, post_nms_iou=post_nms_iou)
        omni_ok = True
    except Exception as e:
        _log("WARN", f"hybrid: OmniParser 失败，本帧仅用 YOLO+OCR: {e}")
        if not bool(policy.get("omni_fallback_yolo", True)):
            raise

    cross = float(policy.get("hybrid_cross_iou", 0.45))
    if omni_ok and o_merged:
        fused = _fuse_merged_streams(y_merged, o_merged, cross)
    else:
        fused = y_merged

    _log(
        "INFO",
        f"hybrid: yolo_ocr_cands={len(y_merged)} omni_cands={len(o_merged)} "
        f"fused={len(fused)} cross_iou={cross}",
    )

    return _screen_result_from_merged(
        img,
        fused,
        max_elements=max_elements,
        merge_iou_report=merge_iou_threshold,
        yolo_detections=n_yolo,
        ocr_boxes=n_ocr,
        extra={
            "backend": "hybrid",
            "post_nms_iou": post_nms_iou,
            "hybrid_cross_iou": cross,
            "yolo_ocr_candidates": len(y_merged),
            "omniparser_candidates": len(o_merged),
            "omniparser_used": omni_ok,
            "omniparser_device": raw.get("device"),
        },
    )


def parse_roi_l2(
    png_bytes: bytes,
    *,
    yolo_conf: float = 0.25,
    merge_iou_threshold: float = 0.45,
    post_nms_iou: float = 0.65,
    max_elements: int = 80,
) -> ScreenParseResult:
    """对 ROI PNG 生成 SoM 图与 UIElementList。

    ``som_parser.backend``（``~/.miro/desk_automation.json``）：
      - ``yolo_ocr`` — 仅原 YOLO+OCR；
      - ``omniparser`` — 仅 OmniParser；
      - ``hybrid`` / ``both`` — **两路同时跑**再 IOU 融合（见 ``hybrid_cross_iou``）。

    - merge_iou_threshold: YOLO 与 OCR 合并阈值（yolo_ocr / hybrid 中的第一路）。
    - post_nms_iou: 各路内部及融合后再截断前的 NMS。
    """
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception as e:
        raise ValueError(f"无法解析 ROI PNG: {e}") from e

    policy = _da_config.get_som_parser_policy()
    backend = str(policy.get("backend", "yolo_ocr")).strip().lower()

    if backend in ("hybrid", "both"):
        return _parse_roi_hybrid(
            img,
            policy,
            yolo_conf=yolo_conf,
            merge_iou_threshold=merge_iou_threshold,
            post_nms_iou=post_nms_iou,
            max_elements=max_elements,
        )

    if backend == "omniparser":
        try:
            return _parse_roi_omniparser(
                img,
                policy,
                post_nms_iou=post_nms_iou,
                max_elements=max_elements,
            )
        except Exception as e:
            _log("WARN", f"OmniParser 失败，回退 YOLO+OCR: {e}")
            if not bool(policy.get("omni_fallback_yolo", True)):
                raise

    merged, n_yolo, n_ocr = _collect_yolo_ocr_merged(
        img,
        yolo_conf=yolo_conf,
        merge_iou_threshold=merge_iou_threshold,
        post_nms_iou=post_nms_iou,
    )
    return _screen_result_from_merged(
        img,
        merged,
        max_elements=max_elements,
        merge_iou_report=merge_iou_threshold,
        yolo_detections=n_yolo,
        ocr_boxes=n_ocr,
        extra={"backend": "yolo_ocr", "post_nms_iou": post_nms_iou},
    )


def uielement_list_to_prompt_lines(
    elements: list[UIElement],
    scale_factor: float = 1.0,
) -> list[str]:
    """生成 User Message 里「编号 | 类型 | 内容 | 中心(API)」行（scale_factor 与 capture.scaling 一致）。"""
    lines: list[str] = []
    s = scale_factor if scale_factor > 0 else 1.0
    for el in elements:
        x1, y1, x2, y2 = el.bbox_roi_xyxy
        cx = int(round(((x1 + x2) / 2) * s))
        cy = int(round(((y1 + y2) / 2) * s))
        lines.append(
            f"  {el.element_id}  | {el.type:6} | {el.content[:80]} | 中心({cx},{cy})"
        )
    return lines
