# -*- coding: utf-8 -*-
"""本地 OCR 定位：双引擎（Tesseract + PaddleOCR）自动探测 → 匹配目标关键词 → 返回点击坐标。

引擎探测策略（按可用性自动选择）:

  **Tesseract** 探测顺序:
    1. ~/.miro/desk_automation.json → ocr.tesseract_cmd
    2. E:\\apps\\AOCR\\tesseract.exe（工作区 translation/ 实际使用路径）
    3. PATH 里的 tesseract（where tesseract）
    4. 常见安装目录扫描
    Python 绑定: pytesseract（优先当前环境，其次 HAPPY conda 环境）

  **PaddleOCR** 探测:
    优先当前 Python 环境 import paddleocr
    其次注入 HAPPY conda 环境 site-packages 后重试
    参考: agent/translation/英译中/1.py 和 中译英/1.py 的调用方式
    用法: PaddleOCR(lang="ch"/"en") + ocr.ocr(numpy_array, cls=False)

  **环境感知**:
    translation/ 脚本运行在 D:\\Users\\Serein\\anaconda3\\envs\\HAPPY 环境中，
    里面已装好 pytesseract + paddleocr。如果当前 Python 不是 HAPPY 环境
    （比如 Cursor 默认用 VS 自带的 Python），会自动把 HAPPY 环境的
    site-packages 注入 sys.path 来复用已安装的包。
    PaddleOCR 已知 protobuf 版本冲突自动处理。

  优先级（可通过 ~/.miro/desk_automation.json 的 ocr.prefer 配置）:
    默认 "tesseract" → 先试 Tesseract，不可用则试 PaddleOCR
    设为 "paddle"     → 先试 PaddleOCR，不可用则试 Tesseract
    设为 "both"       → 两个都跑，取 score 更高的结果

  都找不到 → 返回 None，上层走多模态 API

参考: agent/translation/提取区域文本/1.py (Tesseract)
      agent/translation/英译中/1.py, 中译英/1.py (PaddleOCR)
"""

from __future__ import annotations

import ctypes
import io
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .. import config
from .screen_reader import ActionType, ScreenAction
from . import desk_log

# DPI 感知（与 translation/ 保持一致，避免截图坐标偏移）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
    except Exception:
        pass


# ═══════════════════════════════════════════
# Tesseract 引擎探测
# ═══════════════════════════════════════════

_tess_cmd: str | None = None
_tess_checked = False


def _find_tesseract() -> str | None:
    """按优先级找到可用的 tesseract 可执行文件路径。"""
    global _tess_cmd, _tess_checked
    if _tess_checked:
        return _tess_cmd
    _tess_checked = True

    cfg = config.load_config()
    user_cmd = (cfg.get("ocr") or {}).get("tesseract_cmd", "")
    if user_cmd and Path(user_cmd).is_file():
        _tess_cmd = user_cmd
        return _tess_cmd

    known = [
        r"E:\apps\AOCR\tesseract.exe",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for p in known:
        if Path(p).is_file():
            _tess_cmd = p
            return _tess_cmd

    try:
        r = subprocess.run(
            ["where", "tesseract"], capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            first = r.stdout.strip().splitlines()[0]
            if Path(first).is_file():
                _tess_cmd = first
                return _tess_cmd
    except Exception:
        pass

    for base in [os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")]:
        if not base:
            continue
        candidate = Path(base) / "Tesseract-OCR" / "tesseract.exe"
        if candidate.is_file():
            _tess_cmd = str(candidate)
            return _tess_cmd

    return None


def _get_pytesseract() -> Any:
    """返回配置好路径的 pytesseract 模块，找不到引擎则返回 None。
    当前环境没装 pytesseract 时，会尝试注入 HAPPY conda 环境的 site-packages。"""
    cmd = _find_tesseract()
    if cmd is None:
        return None
    try:
        import pytesseract
    except ImportError:
        config.ensure_happy_packages_importable()
        try:
            import pytesseract
        except ImportError:
            return None
    pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


# ═══════════════════════════════════════════
# PaddleOCR 引擎探测
# ═══════════════════════════════════════════

_paddle_ocr_instance: Any = None
_paddle_checked = False


def _get_paddle_ocr() -> Any:
    """返回 PaddleOCR 实例；未安装 paddleocr 包则返回 None。
    参考 translation/英译中/1.py: PaddleOCR(lang="en", use_angle_cls=True, show_log=False)
    参考 translation/中译英/1.py: PaddleOCR(lang="ch", use_angle_cls=False, show_log=False)
    这里用 ch 支持中英混合识别。

    当前环境没装 paddleocr 时自动注入 HAPPY conda 环境 site-packages。
    已知 protobuf 版本冲突自动通过环境变量解决。
    """
    global _paddle_ocr_instance, _paddle_checked
    if _paddle_checked:
        return _paddle_ocr_instance
    _paddle_checked = True

    os.environ.setdefault("FLAGS_use_onednn", "0")
    # HAPPY 环境 paddle 依赖的 protobuf 版本冲突 workaround
    os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

    PaddleOCR = _try_import_paddleocr()
    if PaddleOCR is None:
        return None

    try:
        _paddle_ocr_instance = PaddleOCR(
            lang="ch", use_angle_cls=False, show_log=False,
        )
        return _paddle_ocr_instance
    except Exception:
        return None


def _try_import_paddleocr() -> Any:
    """尝试 import PaddleOCR 类，当前环境失败则注入 HAPPY site-packages 重试。"""
    try:
        from paddleocr import PaddleOCR
        return PaddleOCR
    except ImportError:
        pass

    config.ensure_happy_packages_importable()
    try:
        from paddleocr import PaddleOCR
        return PaddleOCR
    except ImportError:
        return None
    except Exception:
        return None


# ═══════════════════════════════════════════
# 关键词提取 + 文字清洗
# ═══════════════════════════════════════════

_STOP_WORDS = {"请", "把", "在", "要", "去", "帮我", "帮", "然后", "一下", "的", "了", "吗", "呢", "啊"}
_OPEN_KEYWORDS = {"打开", "启动", "运行", "开", "open", "launch", "run", "start"}


def _extract_keywords(goal: str) -> list[str]:
    """从自然语言目标里拆出可能出现在屏幕上的短词/按钮文字。"""
    parts = re.split(r"[\s，,。、；;：:！!？?\"\'\(\)\[\]]+", goal.strip())
    kws: list[str] = []
    for p in parts:
        p = p.strip()
        if len(p) < 1 or p in _STOP_WORDS:
            continue
        if len(p) <= 20:
            kws.append(p)
    seen: set[str] = set()
    out: list[str] = []
    for k in kws:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out[:12]


def _clean_ocr_text(raw: str) -> str:
    """与 translation/提取区域文本/1.py 保持一致的清洗逻辑。"""
    text = raw.replace("\n", " ").replace("|", "I")
    text = re.sub(r"(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ═══════════════════════════════════════════
# 核心匹配逻辑
# ═══════════════════════════════════════════

@dataclass
class OcrMatch:
    x: int
    y: int
    text: str
    score: float
    engine: str


def _match_keywords(text: str, keywords: list[str], conf: float) -> float | None:
    """返回匹配分数，不匹配则返回 None。"""
    for kw in keywords:
        kw_low = kw.lower()
        text_low = text.lower()
        if kw_low == text_low:
            return conf + 20.0
        if kw_low in text_low:
            return conf + 10.0
        if text_low in kw_low and len(text) >= 2:
            return conf + 5.0
    return None


# ─── Tesseract OCR 定位 ───

def _find_by_tesseract(img: Image.Image, keywords: list[str]) -> OcrMatch | None:
    pytesseract = _get_pytesseract()
    if pytesseract is None:
        return None

    try:
        data = pytesseract.image_to_data(
            img, lang="chi_sim+eng", config="--oem 3 --psm 6",
            output_type=pytesseract.Output.DICT,
        )
    except Exception:
        try:
            data = pytesseract.image_to_data(
                img, lang="eng", config="--oem 3 --psm 6",
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            return None

    n = len(data.get("text", []))
    best: OcrMatch | None = None

    for i in range(n):
        raw_text = (data["text"][i] or "").strip()
        if not raw_text:
            continue
        text = _clean_ocr_text(raw_text)
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = 0.0
        if conf < 25:
            continue
        x = data["left"][i] + data["width"][i] // 2
        y = data["top"][i] + data["height"][i] // 2

        score = _match_keywords(text, keywords, conf)
        if score is not None and (best is None or score > best.score):
            best = OcrMatch(x=x, y=y, text=text, score=score, engine="tesseract")

    return best


# ─── PaddleOCR 定位 ───

def _find_by_paddle(img: Image.Image, keywords: list[str]) -> OcrMatch | None:
    """参考 translation/英译中/1.py: ocr.ocr(img_cv, cls=True) → [[box, (text, conf)], ...]"""
    paddle = _get_paddle_ocr()
    if paddle is None:
        return None

    try:
        import numpy as np
        img_np = np.array(img)
        if len(img_np.shape) == 3 and img_np.shape[2] == 3:
            img_bgr = img_np[:, :, ::-1]
        else:
            img_bgr = img_np
    except Exception:
        return None

    try:
        result = paddle.ocr(img_bgr, cls=False)
    except Exception:
        return None

    if not result or not result[0]:
        return None

    best: OcrMatch | None = None

    for line in result[0]:
        try:
            box = line[0]          # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            raw_text = line[1][0]  # 文字
            conf = float(line[1][1]) * 100.0  # PaddleOCR 置信度 0~1 → 0~100 对齐 Tesseract
        except (IndexError, TypeError, ValueError):
            continue

        text = _clean_ocr_text(raw_text)
        if not text:
            continue
        if conf < 25:
            continue

        # box 中心
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        cx = int((min(xs) + max(xs)) / 2)
        cy = int((min(ys) + max(ys)) / 2)

        score = _match_keywords(text, keywords, conf)
        if score is not None and (best is None or score > best.score):
            best = OcrMatch(x=cx, y=cy, text=text, score=score, engine="paddle")

    return best


# ═══════════════════════════════════════════
# 对外接口
# ═══════════════════════════════════════════

def _get_prefer() -> str:
    """读取 ocr.prefer 配置: "tesseract"(默认) / "paddle" / "both"。"""
    cfg = config.load_config()
    return (cfg.get("ocr") or {}).get("prefer", "tesseract")


def _goal_is_open_intent(goal: str) -> bool:
    """判断目标是否是"打开/启动"类意图。"""
    goal_low = goal.lower()
    return any(kw in goal_low for kw in _OPEN_KEYWORDS)


def _is_likely_desktop_icon(y: int, img_height: int) -> bool:
    """桌面图标通常在屏幕中上部区域（排除底部任务栏约 50px）。"""
    return y < img_height - 60


_DESKTOP_ICON_Y_OFFSET = 40


def find_click_by_ocr(png_bytes: bytes, goal: str) -> ScreenAction | None:
    """在截图上 OCR，若找到与目标匹配的文字则返回点击动作，否则 None。
    双引擎按 ocr.prefer 配置决定优先级。
    「打开」类目标：坐标上移到图标区域 + 使用 double_click。"""
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception:
        return None

    keywords = _extract_keywords(goal)
    if not keywords:
        return None

    prefer = _get_prefer()

    if prefer == "both":
        t = _find_by_tesseract(img, keywords)
        p = _find_by_paddle(img, keywords)
        best = max([x for x in (t, p) if x is not None], key=lambda m: m.score, default=None)
    elif prefer == "paddle":
        best = _find_by_paddle(img, keywords) or _find_by_tesseract(img, keywords)
    else:
        best = _find_by_tesseract(img, keywords) or _find_by_paddle(img, keywords)

    if best is None:
        return None

    click_x, click_y = best.x, best.y
    is_open = _goal_is_open_intent(goal)
    is_desktop = _is_likely_desktop_icon(best.y, img.height)

    if is_open and is_desktop:
        click_y = max(0, best.y - _DESKTOP_ICON_Y_OFFSET)
        action_type = ActionType.DOUBLE_CLICK
        note = f"打开意图 → 上移{_DESKTOP_ICON_Y_OFFSET}px到图标区域 + double_click"
    elif is_open:
        action_type = ActionType.DOUBLE_CLICK
        note = "打开意图 → double_click"
    else:
        action_type = ActionType.CLICK
        note = "click"

    return ScreenAction(
        action_type,
        {"x": click_x, "y": click_y},
        reasoning=f"OCR[{best.engine}] 匹配「{best.text}」(score={best.score:.0f}) → ({click_x},{click_y}) [{note}]",
    )


def ocr_scan_all(png_bytes: bytes) -> list[OcrMatch]:
    """一次 OCR 扫描，返回屏幕上所有识别到的文字块及其中心坐标。"""
    t0 = time.time()
    desk_log.log("INFO", "ocr", f"ocr_scan_all: png_size={len(png_bytes)}")
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception as e:
        desk_log.log("ERROR", "ocr", f"无法打开截图: {e}")
        return []

    results: list[OcrMatch] = []
    prefer = _get_prefer()
    desk_log.log("DEBUG", "ocr", f"OCR prefer={prefer}")

    if prefer in ("tesseract", "both"):
        tess_items = _scan_all_tesseract(img)
        desk_log.log("INFO", "ocr", f"tesseract: {len(tess_items)} items")
        results.extend(tess_items)
    if prefer in ("paddle", "both") or not results:
        paddle_items = _scan_all_paddle(img)
        desk_log.log("INFO", "ocr", f"paddle: {len(paddle_items)} items")
        results.extend(paddle_items)

    elapsed = time.time() - t0
    desk_log.log("INFO", "ocr", f"ocr_scan_all 完成: {len(results)} items, {elapsed:.2f}s")
    desk_log.log_ocr_results(results, elapsed)
    return results


def _scan_all_tesseract(img: Image.Image) -> list[OcrMatch]:
    pytesseract = _get_pytesseract()
    if pytesseract is None:
        return []
    try:
        data = pytesseract.image_to_data(
            img, lang="chi_sim+eng", config="--oem 3 --psm 6",
            output_type=pytesseract.Output.DICT,
        )
    except Exception:
        try:
            data = pytesseract.image_to_data(
                img, lang="eng", config="--oem 3 --psm 6",
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            return []

    results: list[OcrMatch] = []
    n = len(data.get("text", []))
    for i in range(n):
        raw = (data["text"][i] or "").strip()
        if not raw:
            continue
        text = _clean_ocr_text(raw)
        if not text or len(text) < 1:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = 0.0
        if conf < 20:
            continue
        x = data["left"][i] + data["width"][i] // 2
        y = data["top"][i] + data["height"][i] // 2
        results.append(OcrMatch(x=x, y=y, text=text, score=conf, engine="tesseract"))
    return results


def _scan_all_paddle(img: Image.Image) -> list[OcrMatch]:
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

    results: list[OcrMatch] = []
    for line in result[0]:
        try:
            box = line[0]
            raw_text = line[1][0]
            conf = float(line[1][1]) * 100.0
        except (IndexError, TypeError, ValueError):
            continue
        text = _clean_ocr_text(raw_text)
        if not text:
            continue
        if conf < 20:
            continue
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        cx = int((min(xs) + max(xs)) / 2)
        cy = int((min(ys) + max(ys)) / 2)
        results.append(OcrMatch(x=cx, y=cy, text=text, score=conf, engine="paddle"))
    return results


def ocr_calibrate_click(
    target_text: str,
    api_x: int,
    api_y: int,
    ocr_items: list[OcrMatch],
    is_desktop_icon: bool = False,
    *,
    nearest_when_no_text_match: bool = True,
) -> tuple[int, int, str]:
    """用 OCR 结果校准 API 返回的点击坐标。

    nearest_when_no_text_match: 无文字匹配时是否在 80px 内吸附到最近 OCR 块。
    对 **桌面空白处右键** 应设为 False：否则常吸附到壁纸/水印上的随机汉字，菜单弹不出来。
    """
    desk_log.log("DEBUG", "calibrate",
        f"target='{target_text}' api=({api_x},{api_y}) ocr_count={len(ocr_items)} desktop={is_desktop_icon}")

    if not target_text or not ocr_items:
        note = "无OCR数据,使用API坐标" if not ocr_items else "无target_text,使用API坐标"
        desk_log.log_calibration(target_text, api_x, api_y, api_x, api_y, note)
        return api_x, api_y, note

    target_low = target_text.lower().strip()
    best_item: OcrMatch | None = None
    best_score = -1.0

    for item in ocr_items:
        item_low = item.text.lower().strip()
        if not item_low:
            continue
        score = 0.0
        if item_low == target_low:
            score = 100.0 + item.score
        elif target_low in item_low:
            score = 60.0 + item.score
        elif item_low in target_low and len(item.text) >= 2:
            score = 40.0 + item.score
        else:
            continue
        if score > best_score:
            best_score = score
            best_item = item

    if best_item is None:
        if not nearest_when_no_text_match:
            note = "无OCR文字匹配,禁用就近吸附(如桌面空白右键),保留API坐标"
            desk_log.log_calibration(target_text, api_x, api_y, api_x, api_y, note)
            return api_x, api_y, note
        dist_items = []
        for item in ocr_items:
            d = ((item.x - api_x) ** 2 + (item.y - api_y) ** 2) ** 0.5
            if d < 80:
                dist_items.append((d, item))
        if dist_items:
            dist_items.sort(key=lambda t: t[0])
            nearest = dist_items[0][1]
            nx, ny = nearest.x, nearest.y
            if is_desktop_icon:
                ny = max(0, ny - 35)
            note = f"文字不匹配,就近OCR校准「{nearest.text}」({nx},{ny})"
            desk_log.log_calibration(target_text, api_x, api_y, nx, ny, note)
            return nx, ny, note
        note = "无匹配OCR文字,使用API坐标"
        desk_log.log_calibration(target_text, api_x, api_y, api_x, api_y, note)
        return api_x, api_y, note

    cx, cy = best_item.x, best_item.y
    if is_desktop_icon:
        cy = max(0, cy - 35)
        note = f"OCR匹配「{best_item.text}」→图标上移35px({cx},{cy})"
    else:
        note = f"OCR匹配「{best_item.text}」({cx},{cy})"
    desk_log.log_calibration(target_text, api_x, api_y, cx, cy, note)
    return cx, cy, note


def ocr_full_text(png_bytes: bytes, lang: str = "chi_sim+eng") -> str | None:
    """全屏 OCR 返回文本。先试 Tesseract，再试 PaddleOCR。找不到引擎返回 None。"""
    # Tesseract
    pytesseract = _get_pytesseract()
    if pytesseract is not None:
        try:
            img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            raw = pytesseract.image_to_string(img, lang=lang, config="--oem 3 --psm 6")
            return _clean_ocr_text(raw)
        except Exception:
            pass

    # PaddleOCR fallback
    paddle = _get_paddle_ocr()
    if paddle is not None:
        try:
            import numpy as np
            img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            img_np = np.array(img)[:, :, ::-1]
            result = paddle.ocr(img_np, cls=False)
            if result and result[0]:
                return _clean_ocr_text(
                    " ".join(line[1][0] for line in result[0])
                )
        except Exception:
            pass

    return None


def get_ocr_info() -> dict[str, Any]:
    """返回双引擎探测结果 + Python 环境信息，供 HTTP /api/ocr/status 用。"""
    tess_cmd = _find_tesseract()
    tess_pkg = _check_pytesseract()
    paddle_ok = _check_paddle()
    env_info = config.get_python_env_info()
    return {
        "tesseract": {
            "available": tess_cmd is not None and tess_pkg,
            "engine_path": tess_cmd or "",
            "pytesseract_installed": tess_pkg,
        },
        "paddle": {
            "available": paddle_ok,
            "package_installed": paddle_ok,
        },
        "prefer": _get_prefer(),
        "any_available": (tess_cmd is not None and tess_pkg) or paddle_ok,
        "python_env": env_info,
    }


def _check_pytesseract() -> bool:
    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        config.ensure_happy_packages_importable()
        try:
            import pytesseract  # noqa: F401
            return True
        except ImportError:
            return False


def _check_paddle() -> bool:
    os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
    try:
        import paddleocr  # noqa: F401
        return True
    except ImportError:
        config.ensure_happy_packages_importable()
        try:
            import paddleocr  # noqa: F401
            return True
        except ImportError:
            return False
    except Exception:
        return False
