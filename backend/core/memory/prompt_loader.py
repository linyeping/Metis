"""系统 Prompt 加载（与 core/prompts/ + others/scripts/build_prompt.py 对齐）。"""
import importlib.util
import os
from pathlib import Path
from typing import Optional

from backend.tools.coding.foundation.core_mechanisms.colored_logger import colored_log

# 工作区根：agent/mine/miro（与 app.py 同级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_agent_txt_fallback(root: str) -> Optional[str]:
    path = os.path.join(root, "agent.txt")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def get_default_prompt() -> str:
    """默认 Prompt（简化版）"""
    return """You are k, an autonomous AI agent. 
Core principles: Ownership, Proactivity, Self-Healing, Relentless Iteration.
Always output <thought_process> before actions."""


def load_kiro_soul() -> str:
    """
    加载系统 Prompt。默认与模块化仓库一致（others/scripts/build_prompt.py 拼接结果）；
    回退链：MIRO_PROMPT_FILE -> agent.txt（仅当 MIRO_USE_LEGACY_AGENT_TXT=1）-> 模块化构建 -> agent.txt -> 内置默认。
    """
    script_dir = str(PROJECT_ROOT)
    override = os.environ.get("MIRO_PROMPT_FILE", "").strip()
    if override and os.path.isfile(override):
        with open(override, "r", encoding="utf-8") as f:
            soul = f.read()
        colored_log.success(f"System Prompt 已加载 (MIRO_PROMPT_FILE, {len(soul)} 字符)")
        return soul

    legacy_only = os.environ.get("MIRO_USE_LEGACY_AGENT_TXT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if legacy_only:
        soul = _load_agent_txt_fallback(script_dir)
        if soul is not None:
            colored_log.success(f"System Prompt 已加载 (agent.txt 兼容模式, {len(soul)} 字符)")
            return soul
        colored_log.fallback("System Prompt", "MIRO_USE_LEGACY_AGENT_TXT=1 但未找到 agent.txt")
        return get_default_prompt()

    build_script = os.path.join(script_dir, "others", "scripts", "build_prompt.py")
    if os.path.isfile(build_script):
        try:
            spec = importlib.util.spec_from_file_location("miro_build_prompt", build_script)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                brief = os.environ.get("MIRO_PROMPT_BRIEF", "").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                )
                soul, stats = mod.build_full_prompt(include_details=not brief)
                colored_log.success(
                    f"System Prompt 已加载 (模块化 core/prompts/, ~{stats.get('estimated_tokens', 0)} tokens)"
                )
                return soul
        except Exception as e:
            colored_log.fallback("System Prompt", f"模块化加载失败，尝试 agent.txt: {e}")

    soul = _load_agent_txt_fallback(script_dir)
    if soul is not None:
        colored_log.success(f"System Prompt 已加载 (agent.txt 回退, {len(soul)} 字符)")
        return soul
    colored_log.fallback("System Prompt", "未找到 prompts 构建脚本与 agent.txt，使用默认")
    return get_default_prompt()
