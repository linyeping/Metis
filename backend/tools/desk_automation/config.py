# -*- coding: utf-8 -*-
"""持久化配置：总开关、暂停标志路径、操作模式、Python 环境探测。

操作模式 (exec_mode):
- "auto"  : 先 skill（API/CLI），不行再 human 智能视觉链
- "human" : 类人键鼠——本地 OCR 优先、帧差节流、局部/全图再调多模态 API（省费）
- "skill" : OpenClaw 式——只走程序化/技能链（CLI、Win32、后续可接 browser 扩展），不跑 human 视觉链

human_policy（可选，desk_automation.json 内）示例见 get_human_policy() 默认值。
默认 human_core 为 **som**（ROI + YOLO/OCR + 双图 SoM）；可改回 llm / multimodal。

视觉产物目录：`~/.miro/tmp/vision/`（`write_vision_artifacts`），供 HTML 展示最新原图与 SoM 图。

路径约定：`mine/miro/var/logs` 为 desk 运行日志（见 `get_logs_root()`）；`mine/miro/var/rely` 为权重与克隆仓库（见 `get_rely_dir()`）。可在 `~/.miro/desk_automation.json` 的 `paths` 段覆盖。

Python 环境探测:
  用户可能有多个 Python（如 VS 自带的、Anaconda HAPPY env 等）。
  translation/ 脚本跑在 HAPPY conda 环境里，里面有 pytesseract、paddleocr 等包。
  本模块提供 find_happy_site_packages() 让 ocr_locate.py 在当前环境缺包时
  自动注入 HAPPY 环境的 site-packages 到 sys.path。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_PORT = 8765
VALID_EXEC_MODES = ("auto", "human", "skill")


def _config_path() -> Path:
    base = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~")) / ".miro"
    base.mkdir(parents=True, exist_ok=True)
    return base / "desk_automation.json"


def _pause_flag_path() -> Path:
    return _config_path().parent / "desk_automation.pause"


def load_config() -> dict[str, Any]:
    p = _config_path()
    if not p.is_file():
        return {"enabled": False, "http_port": DEFAULT_PORT, "exec_mode": "auto"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"enabled": False, "http_port": DEFAULT_PORT, "exec_mode": "auto"}
    if "enabled" not in data:
        data["enabled"] = False
    data.setdefault("http_port", DEFAULT_PORT)
    data.setdefault("exec_mode", "auto")
    # 迁移旧名 program → skill
    if data.get("exec_mode") == "program":
        data["exec_mode"] = "skill"
    if data["exec_mode"] not in VALID_EXEC_MODES:
        data["exec_mode"] = "auto"
    return data


def get_exec_mode() -> str:
    """当前操作模式: auto / human / skill。"""
    m = load_config().get("exec_mode", "auto")
    return "skill" if m == "program" else m


def get_human_policy() -> dict[str, Any]:
    """human 模式策略（可在 ~/.miro/desk_automation.json 的 human_policy 覆盖）。
    human_core:
      "som"        — 默认：ROI + L2 + 双图 SoM + element_id 执行（交接 v2 主路径）
      "llm"        — 文本 LLM + OCR + 帧差节流 + 多模态
      "multimodal" — 多模态批量规划 → 逐步执行
    """
    cfg = load_config()
    default: dict[str, Any] = {
        "human_core": "som",
        "min_api_interval_sec": 2.0,
        "throttle_diff_max": 0.035,
        "throttle_tiny_diff": 0.008,
        "throttle_local_min": 0.12,
        "throttle_flicker_min": 4.0,
        "throttle_sleep_sec": 0.45,
        "full_screen_diff_min": 0.08,
        "stable_frames_for_skill": 3,
        "ocr_first": True,
        "max_idle_continues": 80,
        "step_warn_ratio": 0.8,
    }
    merged = {**default, **(cfg.get("human_policy") or {})}
    return merged


_VISION_POLICY_KEYS = frozenset({
    "enforce_min_interval_sec",
    "max_calls_per_goal",
    "frozen_diff_max",
    "frozen_max_skips",
    "frozen_wait_sec",
})


def get_vision_policy() -> dict[str, Any]:
    """多模态节流策略；仅从 desk JSON 的 vision 段读取**白名单键**（不含密钥与 API 地址）。"""
    cfg = load_config()
    default: dict[str, Any] = {
        # 两次成功多模态请求之间的硬间隔（秒），防止 OCR 失败后连续刷接口
        "enforce_min_interval_sec": 1.0,
        # 单任务最大多模态次数，0 表示不限制
        "max_calls_per_goal": 0,
        # 全屏几乎不变（与上一帧差分极小）时，先短等待跳过若干轮再允许调 API
        "frozen_diff_max": 0.005,
        "frozen_max_skips": 2,
        "frozen_wait_sec": 0.4,
    }
    raw = cfg.get("vision") or {}
    overrides = {k: v for k, v in raw.items() if k in _VISION_POLICY_KEYS}
    return {**default, **overrides}


def get_som_parser_policy() -> dict[str, Any]:
    """ROI → SoM 解析后端（screen_parser.parse_roi_l2）。

    backend:
      - \"yolo_ocr\"   — 仅 Ultralytics YOLOv8(COCO) + OCR 融合。
      - \"omniparser\" — 仅 Microsoft OmniParser。
      - \"hybrid\" / \"both\" — **两条同时跑**，按 IOU 合并去重、内容互补（推荐）。

    配置写在 ~/.miro/desk_automation.json 的 som_parser 段；omni_parser_root 优先于环境变量 OMNI_PARSER_ROOT。
    """
    cfg = load_config()
    default: dict[str, Any] = {
        "backend": "yolo_ocr",
        "omni_parser_root": "",
        "omni_box_threshold": 0.05,
        "omni_iou_threshold": 0.7,
        "omni_imgsz": 640,
        "omni_batch_size": 12,
        "omni_use_paddleocr": True,
        "omni_use_local_semantics": True,
        "omni_fallback_yolo": True,
        # hybrid：两路框 IOU≥此阈值则合并为一条（框取并集，content 拼接）
        "hybrid_cross_iou": 0.45,
    }
    return {**default, **(cfg.get("som_parser") or {})}


def get_miro_root() -> Path:
    """Backend root directory, kept under the legacy function name for compatibility."""
    return Path(__file__).resolve().parent.parent.parent


def get_logs_root() -> Path:
    """运行日志根目录。默认 `<backend>/var/logs`。

    优先级：环境变量 `MIRO_LOGS_DIR` → `desk_automation.json` 的 `paths.logs_dir` → 默认 `var/logs`。
    """
    env = (os.environ.get("MIRO_LOGS_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    cfg = load_config()
    raw = str((cfg.get("paths") or {}).get("logs_dir") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return get_miro_root() / "var" / "logs"


def get_rely_dir() -> Path:
    """本机大文件依赖根目录（权重、克隆仓库等）。默认 `<backend>/var/rely`。

    优先级：环境变量 `MIRO_RELY_DIR` → `desk_automation.json` 的 `paths.rely_dir` → 默认 `var/rely`。
    """
    env = (os.environ.get("MIRO_RELY_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    cfg = load_config()
    raw = str((cfg.get("paths") or {}).get("rely_dir") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return get_miro_root() / "var" / "rely"


def get_input_timing() -> dict[str, Any]:
    """键鼠后摇的额外等待（秒），写在 ~/.miro/desk_automation.json 的 input 段。

    用于 Electron / WebView（如 Gemini 桌面端）：点击后焦点切换慢，或 UIPI 下偶发丢事件时，
    可适当加大 extra_settle_after_click_sec / extra_settle_before_type_sec（建议 0.3～0.8）。
    """
    cfg = load_config()
    default: dict[str, Any] = {
        "extra_settle_after_click_sec": 0.0,
        "extra_settle_before_type_sec": 0.0,
    }
    out = {**default}
    for k in default:
        raw = (cfg.get("input") or {}).get(k)
        if raw is None:
            continue
        try:
            v = float(raw)
            out[k] = min(3.0, max(0.0, v))
        except (TypeError, ValueError):
            pass
    return out


def save_config(data: dict[str, Any]) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    merged = load_config()
    merged.update(data)
    p.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")


def is_enabled() -> bool:
    return bool(load_config().get("enabled"))


def is_paused() -> bool:
    return _pause_flag_path().is_file()


def set_paused(paused: bool) -> None:
    flag = _pause_flag_path()
    if paused:
        flag.write_text("1", encoding="utf-8")
    elif flag.is_file():
        flag.unlink()


def assert_automation_allowed() -> None:
    """键鼠/截图前调用；失败抛 PermissionError。"""
    if not is_enabled():
        raise PermissionError("desk_automation 总开关为关（HTML 或 POST /api/enabled 打开）")
    if is_paused():
        raise PermissionError("desk_automation 已暂停（ESC 钩子或 POST /api/pause）")


def vision_artifacts_dir() -> Path:
    """SoM / 原图落盘目录：`~/.miro/tmp/vision/`（供前端静态读取或轮询）。"""
    d = _config_path().parent / "tmp" / "vision"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_vision_artifacts(
    raw_roi_png: bytes,
    som_png: bytes,
    *,
    step: int | None = None,
) -> dict[str, str]:
    """写入最新 ROI 原图与 SoM 标注图；可选按步数归档副本。

    返回绝对路径字符串，便于 SSE/WebSocket 推给前端。
    """
    d = vision_artifacts_dir()
    raw_latest = d / "vision_raw_latest.png"
    som_latest = d / "vision_som_latest.png"
    raw_latest.write_bytes(raw_roi_png)
    som_latest.write_bytes(som_png)
    meta_path = d / "vision_latest_meta.json"
    try:
        import time as _time
        import json as _json

        meta_path.write_text(
            _json.dumps(
                {
                    "updated_at": _time.time(),
                    "step": step,
                    "raw": str(raw_latest.resolve()),
                    "som": str(som_latest.resolve()),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    if step is not None:
        try:
            (d / f"raw_step_{int(step):04d}.png").write_bytes(raw_roi_png)
            (d / f"som_step_{int(step):04d}.png").write_bytes(som_png)
        except OSError:
            pass
    return {
        "vision_dir": str(d.resolve()),
        "vision_raw_latest": str(raw_latest.resolve()),
        "vision_som_latest": str(som_latest.resolve()),
        "meta": str(meta_path.resolve()),
    }


# ═══════════════════════════════════════════
# Python 环境探测（可选 HAPPY conda env）
# ═══════════════════════════════════════════

_happy_site_packages: str | None = None
_happy_checked = False
_happy_python: str | None = None

_KNOWN_HAPPY_PYTHONS = [
    os.environ.get("METIS_HAPPY_PYTHON", ""),
]


def find_happy_python() -> str | None:
    """找到配置或 conda 中的 HAPPY 环境 python.exe 路径。"""
    global _happy_python, _happy_checked
    if _happy_checked:
        return _happy_python

    cfg = load_config()
    user_python = cfg.get("python_path", "")
    if user_python and Path(user_python).is_file():
        _happy_python = user_python
        _happy_checked = True
        return _happy_python

    for p in _KNOWN_HAPPY_PYTHONS:
        if p and Path(p).is_file():
            _happy_python = p
            _happy_checked = True
            return _happy_python

    try:
        r = subprocess.run(
            ["conda", "run", "-n", "HAPPY", "python", "-c", "import sys; print(sys.executable)"],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode == 0:
            exe = r.stdout.strip()
            if exe and Path(exe).is_file():
                _happy_python = exe
                _happy_checked = True
                return _happy_python
    except Exception:
        pass

    _happy_checked = True
    return None


def find_happy_site_packages() -> str | None:
    """返回 HAPPY conda 环境的 site-packages 路径（供 sys.path 注入）。"""
    global _happy_site_packages
    if _happy_site_packages is not None:
        return _happy_site_packages

    hp = find_happy_python()
    if hp is None:
        return None

    sp = Path(hp).parent / "lib" / "site-packages"
    if sp.is_dir():
        _happy_site_packages = str(sp)
        return _happy_site_packages

    try:
        r = subprocess.run(
            [hp, "-c", "import site; print(site.getsitepackages()[-1])"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode == 0:
            p = r.stdout.strip()
            if p and Path(p).is_dir():
                _happy_site_packages = p
                return _happy_site_packages
    except Exception:
        pass

    return None


def ensure_happy_packages_importable() -> bool:
    """将 HAPPY 环境的 site-packages 注入当前 sys.path（如果尚未注入）。
    返回 True 表示注入成功或不需要注入。"""
    sp = find_happy_site_packages()
    if sp is None:
        return False
    if sp not in sys.path:
        sys.path.insert(0, sp)
    return True


def get_python_env_info() -> dict[str, Any]:
    """返回当前 Python 环境信息和 HAPPY 环境探测结果，供诊断。"""
    hp = find_happy_python()
    sp = find_happy_site_packages()
    return {
        "current_python": sys.executable,
        "current_version": sys.version.split()[0],
        "happy_python": hp or "",
        "happy_site_packages": sp or "",
        "happy_injected": sp is not None and sp in sys.path,
        "is_happy_env": hp is not None and os.path.normcase(sys.executable) == os.path.normcase(hp),
    }
