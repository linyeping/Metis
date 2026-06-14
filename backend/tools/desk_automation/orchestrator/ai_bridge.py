# -*- coding: utf-8 -*-
"""多 AI 委派桥接：组装提示词、准备附件、路由到合适的 AI。

支持的委派目标:
- cursor: 通过 cursor_bridge 自动输入提示词
- clipboard: 组装好内容 → 放入剪贴板 → 用户可粘贴到任意 AI
- file: 将提示词写入文件，供用户手动上传
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .. import config
from ..input.file_ops import prepare_for_upload


# ─── 提示词组装 ───

def compose_prompt_a(
    background: str,
    problem: str,
    tried: str = "",
    need: str = "",
) -> str:
    """模板 A: 明确求助。"""
    parts = [f"## 背景\n{background}", f"## 我遇到的问题\n{problem}"]
    if tried:
        parts.append(f"## 我已经尝试过\n{tried}")
    if need:
        parts.append(f"## 我需要你做什么\n{need}")
    return "\n\n".join(parts)


def compose_prompt_b(
    goal: str,
    constraints: str = "",
) -> str:
    """模板 B: 开放探索（不带预设答案）。"""
    parts = [f"## 目标\n{goal}"]
    if constraints:
        parts.append(f"## 约束\n{constraints}")
    parts.append("## 请给出\n你认为最合适的实现方式，不限于我提到的方向。")
    return "\n\n".join(parts)


# ─── 附件准备 ───

def prepare_context_bundle(
    files: list[str] | None = None,
    screenshot: bool = False,
    log_tail: int = 50,
    log_path: str = "",
) -> dict[str, Any]:
    """准备委派所需附件包。

    Returns: {"prompt_attachments": [...paths], "clipboard_ready": bool}
    """
    attachments: list[str] = []

    if files:
        result = prepare_for_upload(*files)
        attachments.extend(result.get("copied", []))

    if screenshot:
        try:
            config.assert_automation_allowed()
            from ..capture.screenshot import grab_screen_png
            shot_dir = Path(config._config_path().parent) / "tmp"
            shot_dir.mkdir(parents=True, exist_ok=True)
            shot_path = shot_dir / f"delegate_{int(time.time())}.png"
            png_data = grab_screen_png()
            shot_path.write_bytes(png_data)
            attachments.append(str(shot_path))
        except (PermissionError, RuntimeError):
            pass

    if log_path and Path(log_path).is_file():
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-log_tail:])
        tail_path = Path(config._config_path().parent) / "tmp" / "log_tail.txt"
        tail_path.parent.mkdir(parents=True, exist_ok=True)
        tail_path.write_text(tail, encoding="utf-8")
        attachments.append(str(tail_path))

    return {"prompt_attachments": attachments, "clipboard_ready": len(attachments) > 0}


# ─── 委派路由 ───

def delegate_to_cursor(prompt: str) -> dict[str, Any]:
    """将任务委派给 Cursor（自动输入提示词）。"""
    from .cursor_bridge import send_prompt_to_cursor
    return send_prompt_to_cursor(prompt)


def delegate_to_clipboard(prompt: str, attachments: list[str] | None = None) -> dict[str, Any]:
    """将提示词复制到剪贴板。附件路径附在末尾供手动上传。"""
    full = prompt
    if attachments:
        full += "\n\n---\n附件路径（请手动上传/拖拽到目标 AI）:\n"
        for a in attachments:
            full += f"  - {a}\n"
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Set-Clipboard -Value {json.dumps(full)}"],
            timeout=5, capture_output=True,
        )
        return {"ok": True, "method": "clipboard", "length": len(full)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delegate_to_file(prompt: str, filename: str = "delegate_prompt.md") -> dict[str, Any]:
    """将提示词写入文件。"""
    out = Path(config._config_path().parent) / "tmp" / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt, encoding="utf-8")
    return {"ok": True, "path": str(out)}


# ─── 模型路由建议 ───

ROUTING_HINTS: dict[str, dict[str, str]] = {
    "code_generation": {"prefer": "cursor_auto", "fallback": "claude_api", "reason": "Cursor Auto 省 API，写码最顺手"},
    "code_review": {"prefer": "cursor_auto", "fallback": "gemini_web", "reason": "Cursor 上下文足；大文件可切 Gemini"},
    "search_docs": {"prefer": "cursor_web_search", "fallback": "gemini_web", "reason": "内置搜索最快"},
    "translation": {"prefer": "local_ollama", "fallback": "chatgpt_web", "reason": "本地模型零成本"},
    "image_analysis": {"prefer": "gemini_api", "fallback": "claude_api", "reason": "多模态强项"},
    "planning": {"prefer": "claude_api", "fallback": "cursor_auto", "reason": "深度推理"},
    "debug": {"prefer": "cursor_auto", "fallback": "claude_api", "reason": "有完整上下文"},
    "file_conversion": {"prefer": "local_cli", "fallback": "chatgpt_web", "reason": "pandoc/ffmpeg 本地搞定"},
}


def suggest_routing(task_type: str) -> dict[str, str]:
    """根据任务类型建议最佳 AI 路由。"""
    return ROUTING_HINTS.get(task_type, {
        "prefer": "cursor_auto",
        "fallback": "claude_api",
        "reason": "默认走 Cursor Auto",
    })
