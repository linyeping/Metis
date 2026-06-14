# -*- coding: utf-8 -*-

from .scan_cli import scan_cli_candidates
from .scan_env import snapshot_environment
from .scan_software import scan_installed_software
from .scan_windows import list_running_processes, list_start_menu_shortcuts, list_visible_windows

__all__ = [
    "scan_installed_software",
    "scan_cli_candidates",
    "snapshot_environment",
    "list_visible_windows",
    "list_running_processes",
    "list_start_menu_shortcuts",
]
