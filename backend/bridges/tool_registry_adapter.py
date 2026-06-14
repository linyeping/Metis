"""Adapters between Metis runtime ToolRegistry and bridge ToolProfile objects."""

from __future__ import annotations

from typing import Any, List

from .tool_contract import ToolProfile
from .tool_profiles import infer_tool_profile


def profiles_from_runtime_registry(registry: Any) -> List[ToolProfile]:
    if hasattr(registry, "list_tool_profiles"):
        return list(registry.list_tool_profiles())

    tools = getattr(registry, "_tools", {})
    aliases = getattr(registry, "_aliases", {})
    profiles: List[ToolProfile] = []
    for name, tool in tools.items():
        canonical = aliases.get(name, name)
        available = True
        if hasattr(registry, "is_available"):
            available = bool(registry.is_available(name))
        profiles.append(
            infer_tool_profile(
                str(name),
                canonical_name=str(canonical),
                source=str(getattr(tool, "source", "builtin")),
                description=str(getattr(tool, "description", "")),
                available=available,
            )
        )
    return profiles
