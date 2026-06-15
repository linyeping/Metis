"""编辑后快速诊断（反馈回路 / correction vector）。

对刚改动的**单个 Python 文件**跑一次 ruff，把真实报错直接回灌进工具结果——模型当轮就能看到
自己引入的问题并修，不必等下一轮被动发现。原则：
- 只对 .py、只在有错时追加（干净则静默，不加噪音）；
- ruff-only、短超时、异常全吞——绝不拖慢或拖垮编辑路径；
- ruff 不可用（如未装/打包环境无 PATH）时优雅降级为空，调用方回退到通用提醒；
- 可用环境变量 ``METIS_EDIT_DIAGNOSTICS=0`` 关闭。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, Iterable

_PATH_KEYS = ("file_path", "path", "target_file")
_MAX_LINES = 20
_TIMEOUT_S = 8


def _enabled() -> bool:
    return os.environ.get("METIS_EDIT_DIAGNOSTICS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _extract_path(kwargs: Dict[str, Any]) -> str:
    for key in _PATH_KEYS:
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def quick_python_diagnostics(file_path: str) -> str:
    """对单个 .py 文件跑 ruff，返回精简报错文本；无错/不适用/ruff 缺失则返回空。"""
    if not file_path.endswith(".py") or not os.path.isfile(file_path):
        return ""
    exe = shutil.which("ruff")
    if not exe:
        return ""
    try:
        result = subprocess.run(
            [exe, "check", "--quiet", file_path],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            cwd=os.path.dirname(os.path.abspath(file_path)) or ".",
        )
    except Exception:
        return ""
    if result.returncode == 0:
        return ""
    out = (result.stdout or result.stderr or "").strip()
    if not out:
        return ""
    lines = out.splitlines()
    if len(lines) > _MAX_LINES:
        out = "\n".join(lines[:_MAX_LINES]) + f"\n... (+{len(lines) - _MAX_LINES} more)"
    return out


def edit_diagnostics_feedback(
    tool_name: str, kwargs: Dict[str, Any], file_modify_tools: Iterable[str]
) -> str:
    """改文件类工具执行后返回应追加到结果末尾的诊断块；无可回灌内容时返回空。"""
    if not _enabled() or tool_name not in set(file_modify_tools):
        return ""
    path = _extract_path(kwargs)
    if not path:
        return ""
    diag = quick_python_diagnostics(path)
    if not diag:
        return ""
    return (
        f"\n\n⚠️ [auto-diagnostics] ruff found issues in {os.path.basename(path)} "
        f"after this edit — fix them before moving on:\n{diag}"
    )
