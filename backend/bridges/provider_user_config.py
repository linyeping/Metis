"""FABLEADV-15: load user-defined providers from providers.json.

Three-layer merge (later overrides earlier):
  builtin defaults < global METIS_HOME/providers.json < project <ws>/.metis/providers.json

Security: api keys are NEVER stored here — only `api_key_env` (env var name).
A plaintext `api_key` field, if present, is ignored and warned.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, List, Optional

from .provider_contract import ProviderId, ProviderProfile

logger = logging.getLogger(__name__)

_VALID_BACKENDS = {"openai", "anthropic", "gemini", "ollama", "fake"}
_PROVIDER_ID_RE = __import__("re").compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def _global_providers_path() -> Path:
    from backend.core.paths import metis_home

    return metis_home() / "providers.json"


def _project_providers_path(workspace_root: str) -> Optional[Path]:
    root = str(workspace_root or "").strip()
    if not root:
        return None
    return Path(root) / ".metis" / "providers.json"


def _coerce_profile(row: Any, *, source: str) -> Optional[ProviderProfile]:
    if not isinstance(row, dict):
        return None
    pid = str(row.get("id") or row.get("provider_id") or "").strip()
    if not pid or not _PROVIDER_ID_RE.fullmatch(pid):
        logger.warning("providers.json: skipping invalid provider id %r", pid)
        return None
    backend_type = str(row.get("backend_type") or "openai").strip().lower()
    if backend_type not in _VALID_BACKENDS:
        logger.warning("providers.json: provider %s has unknown backend_type %r; skipping", pid, backend_type)
        return None
    base_url = str(row.get("base_url") or "").strip()
    if base_url and not base_url.lower().startswith(("http://", "https://")):
        logger.warning("providers.json: provider %s base_url must be http(s); skipping", pid)
        return None
    if "api_key" in row:
        logger.warning(
            "providers.json: provider %s has a plaintext 'api_key' field — ignored for security. "
            "Use 'api_key_env' (env var name) instead.",
            pid,
        )

    models = row.get("models") or row.get("fallback_models") or []
    if isinstance(models, str):
        models = [models]
    fallback = tuple(str(m).strip() for m in models if str(m).strip()) if isinstance(models, list) else ()

    ctx = row.get("context_windows") or row.get("model_context_windows") or {}
    ctx_map = {str(k): int(v) for k, v in ctx.items() if isinstance(v, (int, float))} if isinstance(ctx, dict) else {}

    aliases = row.get("aliases") or []
    alias_tuple = tuple(str(a).strip().lower() for a in aliases if str(a).strip()) if isinstance(aliases, list) else ()

    return ProviderProfile(
        provider_id=ProviderId(pid),
        display_name=str(row.get("display_name") or pid),
        backend_type=backend_type,
        aliases=alias_tuple,
        base_url=base_url,
        default_model=str(row.get("default_model") or (fallback[0] if fallback else "")),
        fallback_models=fallback,
        api_key_required=bool(row.get("api_key_required", backend_type != "ollama" and backend_type != "fake")),
        supports_stream=bool(row.get("supports_stream", True)),
        supports_tools=bool(row.get("supports_tools", True)),
        supports_vision=bool(row.get("supports_vision", False)),
        parallel_tool_calls=bool(row.get("parallel_tool_calls", False)),
        requires_reasoning_passback=bool(row.get("requires_reasoning_passback", False)),
        openai_compatible=bool(row.get("openai_compatible", backend_type == "openai")),
        model_context_windows=ctx_map,
        source=source,
        api_key_env=str(row.get("api_key_env") or "").strip(),
    )


def _load_file(path: Optional[Path], *, source: str) -> List[ProviderProfile]:
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("providers.json at %s is invalid (%s); ignoring", path, exc)
        return []
    rows = data.get("providers") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        logger.warning("providers.json at %s: 'providers' must be a list; ignoring", path)
        return []
    out: List[ProviderProfile] = []
    for row in rows:
        profile = _coerce_profile(row, source=source)
        if profile is not None:
            out.append(profile)
    return out


def load_user_provider_profiles(workspace_root: str = "") -> List[ProviderProfile]:
    """Return user-defined profiles, global first then project (project overrides
    are handled by the registry merge which applies later entries last)."""
    profiles: List[ProviderProfile] = []
    profiles.extend(_load_file(_global_providers_path(), source="user"))
    profiles.extend(_load_file(_project_providers_path(workspace_root), source="project"))
    return profiles


def _read_raw_global() -> List[dict]:
    path = _global_providers_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = data.get("providers") if isinstance(data, dict) else data
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _write_raw_global(rows: List[dict]) -> None:
    path = _global_providers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"providers": rows}, ensure_ascii=False, indent=2)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    # FABLEADV-18: retry os.replace past transient Windows file locks.
    import time

    delay = 0.05
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


def save_user_provider(row: dict) -> ProviderProfile:
    """Upsert a user provider into the global providers.json. Strips any plaintext
    api_key (security). Returns the validated profile or raises ValueError."""
    if not isinstance(row, dict):
        raise ValueError("provider must be an object")
    clean = {k: v for k, v in row.items() if k != "api_key"}
    profile = _coerce_profile(clean, source="user")
    if profile is None:
        raise ValueError("invalid provider definition (check id / backend_type / base_url)")
    pid = str(profile.provider_id)
    rows = [r for r in _read_raw_global() if str(r.get("id") or r.get("provider_id") or "") != pid]
    rows.append(clean)
    _write_raw_global(rows)
    return profile


def delete_user_provider(provider_id: str) -> bool:
    pid = str(provider_id or "").strip()
    rows = _read_raw_global()
    kept = [r for r in rows if str(r.get("id") or r.get("provider_id") or "") != pid]
    if len(kept) == len(rows):
        return False
    _write_raw_global(kept)
    return True
