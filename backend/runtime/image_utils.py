"""Image utilities for multimodal agent loop."""
from __future__ import annotations

import base64
import mimetypes
import os
import re
from typing import Any, Dict, List, Optional, Tuple

_IMAGE_EXTENSIONS = "png|jpg|jpeg|gif|webp|bmp"
_IMAGE_PATH_PATTERNS = [
    re.compile(
        rf"(?:saved|path|file|wrote|output)[:\s]+[\"']?"
        rf"([A-Za-z]:[\\/][^\r\n\"']+?\.({_IMAGE_EXTENSIONS}))",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:saved|path|file|wrote|output)[:\s]+[\"']?"
        rf"(/[^\r\n\"']+?\.({_IMAGE_EXTENSIONS}))",
        re.IGNORECASE,
    ),
    re.compile(
        rf"([A-Za-z]:[\\/][^\r\n\"']+?\.({_IMAGE_EXTENSIONS}))",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(/[^\r\n\"']+?\.({_IMAGE_EXTENSIONS}))",
        re.IGNORECASE,
    ),
]

MAX_DIMENSION = 1568
DEFAULT_SCREENSHOT_MAX_WIDTH = 1280
JPEG_QUALITY = 80
MAX_FILE_SIZE = 20 * 1024 * 1024


def extract_image_paths(text: str) -> List[str]:
    """Find existing absolute image paths in tool result text."""
    seen: set[str] = set()
    paths: List[str] = []
    for pattern in _IMAGE_PATH_PATTERNS:
        for match in pattern.finditer(text or ""):
            raw = match.group(1).strip().strip("\"'").rstrip(".,;:)")
            abs_path = os.path.abspath(raw)
            if abs_path not in seen and os.path.isfile(abs_path):
                seen.add(abs_path)
                paths.append(abs_path)
    return paths


def load_and_encode_image(
    path: str,
    max_dimension: int = MAX_DIMENSION,
) -> Optional[Dict[str, str]]:
    """Read an image file, resize if possible, and return base64 data."""
    if not os.path.isfile(path):
        return None
    try:
        size = os.path.getsize(path)
        if size == 0 or size > MAX_FILE_SIZE:
            return None
    except OSError:
        return None

    mime_type = mimetypes.guess_type(path)[0] or "image/png"
    try:
        with open(path, "rb") as handle:
            raw_bytes = handle.read()
    except OSError:
        return None

    resized = _try_resize(raw_bytes, max_dimension, mime_type)
    data_bytes = resized if resized is not None else raw_bytes
    if resized is not None:
        mime_type = "image/jpeg"
    encoded = base64.b64encode(data_bytes).decode("ascii")
    return {"data": encoded, "mime_type": mime_type}


def predict_display_dimensions(
    width: int,
    height: int,
    max_dimension: int = MAX_DIMENSION,
) -> Tuple[int, int]:
    """Predict the on-screen pixel size of an image after `_try_resize`.

    Single source of truth for the resize math: the model is shown the image at
    these dimensions, so desktop coordinate mapping must use the same numbers.
    Returns the unchanged (width, height) when no downscale would occur.
    """
    if width <= 0 or height <= 0:
        return width, height
    target_width = _screenshot_max_width(max_dimension)
    if width <= target_width and max(width, height) <= max_dimension:
        return width, height
    scale = min(target_width / width, max_dimension / max(width, height))
    return max(1, int(width * scale)), max(1, int(height * scale))


def _try_resize(data: bytes, max_dimension: int, mime_type: str) -> Optional[bytes]:
    """Best-effort resize using Pillow; return None when unavailable or unnecessary."""
    if "png" not in mime_type.lower() and "jpeg" not in mime_type.lower():
        return None
    try:
        import io

        from PIL import Image  # type: ignore[import-untyped]
    except ImportError:
        return None

    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        new_size = predict_display_dimensions(width, height, max_dimension)
        if new_size == (width, height):
            return None
        image = image.resize(new_size, Image.LANCZOS)

        output = io.BytesIO()
        fmt = "JPEG"
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")
        image.save(output, format=fmt, quality=JPEG_QUALITY, optimize=True)
        return output.getvalue()
    except Exception:
        return None


def _screenshot_max_width(default: int) -> int:
    raw = os.environ.get("METIS_SCREENSHOT_MAX_WIDTH", str(DEFAULT_SCREENSHOT_MAX_WIDTH)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SCREENSHOT_MAX_WIDTH
    return max(320, min(max(default, 320), value))


def build_image_content_block(path: str) -> Optional[Dict[str, Any]]:
    """Create an OpenAI-format image_url content block from an image file."""
    encoded = load_and_encode_image(path)
    if not encoded:
        return None
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{encoded['mime_type']};base64,{encoded['data']}",
            "detail": "auto",
        },
    }
