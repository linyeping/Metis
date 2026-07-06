from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

RUN_EXECUTION_PROFILE_SCHEMA = "metis.run_execution_profile.v1"

LOCAL_DIRECT = "local_direct"
LOCAL_WORKTREE = "local_worktree"
LOCAL_VM = "local_vm"

RUN_EXECUTION_PROFILES = {LOCAL_DIRECT, LOCAL_WORKTREE, LOCAL_VM}


@dataclass(frozen=True)
class RunExecutionProfile:
    profile: str
    ok: bool = True
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": RUN_EXECUTION_PROFILE_SCHEMA,
            "profile": self.profile,
            "ok": self.ok,
            "reason": self.reason,
        }


def normalize_execution_profile(value: Any, *, default: str = LOCAL_DIRECT) -> RunExecutionProfile:
    raw = str(value or "").strip().lower()
    if not raw:
        raw = default
    raw = raw.replace("-", "_")
    if raw in RUN_EXECUTION_PROFILES:
        return RunExecutionProfile(profile=raw)
    return RunExecutionProfile(
        profile=default,
        ok=False,
        reason=f"unsupported execution_profile: {value!r}",
    )


def default_execution_profile_for_surface(surface_mode: str) -> str:
    surface = str(surface_mode or "").strip().lower()
    if surface == "code":
        return LOCAL_WORKTREE
    return LOCAL_DIRECT


def is_valid_execution_profile(value: Any) -> bool:
    return str(value or "").strip().lower().replace("-", "_") in RUN_EXECUTION_PROFILES


__all__ = [
    "LOCAL_DIRECT",
    "LOCAL_VM",
    "LOCAL_WORKTREE",
    "RUN_EXECUTION_PROFILE_SCHEMA",
    "RUN_EXECUTION_PROFILES",
    "RunExecutionProfile",
    "default_execution_profile_for_surface",
    "is_valid_execution_profile",
    "normalize_execution_profile",
]
