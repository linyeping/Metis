from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List

from backend.core.paths import legacy_miro_path, metis_dir

from .tool_registry import ToolDefinition, ToolRegistry


def discover_plugins(plugin_dir: str = "") -> List[Dict[str, Any]]:
    """Discover plugin tool folders containing tool.json and handler.py."""
    plugins: List[Dict[str, Any]] = []
    for root in _plugin_roots(plugin_dir):
        if not root.exists():
            continue
        for tool_dir in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if not tool_dir.is_dir():
                continue
            tool_json = tool_dir / "tool.json"
            handler_py = tool_dir / "handler.py"
            if not tool_json.exists() or not handler_py.exists():
                continue
            try:
                config = json.loads(tool_json.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"Plugin: Failed to read {tool_json}: {exc}")
                continue
            if not isinstance(config, dict):
                print(f"Plugin: Ignoring {tool_json}: config must be an object")
                continue
            plugins.append(
                {
                    "config": config,
                    "handler_path": str(handler_py),
                    "dir": str(tool_dir),
                }
            )
    return plugins


def register_plugins(registry: ToolRegistry, plugin_dir: str = "") -> int:
    """Load and register all discovered plugin tools."""
    count = 0
    for plugin in discover_plugins(plugin_dir):
        config = plugin["config"]
        raw_name = str(config.get("name") or "").strip()
        if not _valid_tool_name(raw_name):
            print(f"Plugin: Ignoring invalid tool name '{raw_name}' in {plugin['dir']}")
            continue

        try:
            module = _load_handler(raw_name, str(plugin["handler_path"]))
            execute = getattr(module, "execute", None)
            if not callable(execute):
                print(f"Plugin: {raw_name} missing callable execute()")
                continue

            parameters = config.get("parameters")
            if not isinstance(parameters, dict):
                parameters = {"type": "object", "properties": {}, "required": []}

            registry.register(
                ToolDefinition(
                    name=raw_name,
                    description=str(config.get("description") or f"Plugin tool: {raw_name}"),
                    parameters=parameters,
                    execute_fn=execute,
                    usage_hint=str(config.get("usage_hint") or ""),
                    source="plugin",
                    toolset=str(config.get("toolset") or "plugin"),
                    requires_approval=bool(config.get("requires_approval", True)),
                    destructive=bool(config.get("destructive", False)),
                )
            )
            for alias in config.get("aliases") or []:
                alias_value = str(alias or "").strip()
                if alias_value:
                    registry.register_alias(alias_value, raw_name)
            count += 1
            print(f"Plugin: Registered '{raw_name}' from {plugin['dir']}")
        except Exception as exc:
            print(f"Plugin: Failed to register {raw_name or plugin['dir']}: {exc}")
    return count


def _load_handler(tool_name: str, handler_path: str) -> ModuleType:
    module_name = f"metis_plugin_{_safe_module_name(tool_name)}_{abs(hash(handler_path))}"
    spec = importlib.util.spec_from_file_location(module_name, handler_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import plugin handler: {handler_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plugin_roots(plugin_dir: str = "") -> List[Path]:
    explicit = str(plugin_dir or os.environ.get("METIS_PLUGIN_DIR") or os.environ.get("MIRO_PLUGIN_DIR") or "").strip()
    if explicit:
        return [Path(item).expanduser() for item in explicit.split(os.pathsep) if item.strip()]
    return [metis_dir("plugins"), legacy_miro_path("plugins")]


def _valid_tool_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name))


def _safe_module_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "tool"
