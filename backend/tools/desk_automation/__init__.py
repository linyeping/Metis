# -*- coding: utf-8 -*-
"""台式桌面自动化参考实现（Windows 优先）。默认关闭；见 README.md。"""

from __future__ import annotations

import os
from typing import Optional

__version__ = "0.1.0"

_SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_CACHE: Optional[str] = None


def load_skill_prompt() -> str:
    """加载 SKILL.md 作为系统提示注入内容（带缓存）。"""
    global _SKILL_CACHE
    if _SKILL_CACHE is not None:
        return _SKILL_CACHE
    skill_path = os.path.join(_SKILL_DIR, "SKILL.md")
    try:
        with open(skill_path, "r", encoding="utf-8") as f:
            _SKILL_CACHE = f.read()
    except OSError:
        _SKILL_CACHE = ""
    return _SKILL_CACHE
