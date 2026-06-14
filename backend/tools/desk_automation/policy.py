# -*- coding: utf-8 -*-
"""策略：哪些操作在「关」时仍允许（只读清单）。"""

from __future__ import annotations

from . import config


def allow_readonly_inventory() -> bool:
    """软件/CLI 清单扫描始终允许（不写屏、不键鼠）。"""
    return True


def allow_capture_or_input() -> None:
    config.assert_automation_allowed()
