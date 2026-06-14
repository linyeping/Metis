from __future__ import annotations

from typing import Any, Dict, List


def normalize_ollama_base_url(base_url: str = "http://localhost:11434") -> str:
    """Return the Ollama root URL from either root or OpenAI-compatible /v1 URL."""
    value = str(base_url or "").strip().rstrip("/") or "http://localhost:11434"
    lower = value.lower()
    for suffix in ("/v1", "/api/chat", "/api/generate", "/api/tags"):
        if lower.endswith(suffix):
            return value[: -len(suffix)].rstrip("/")
    return value


def check_ollama_running(base_url: str = "http://localhost:11434") -> bool:
    """Check whether the local Ollama daemon is reachable."""
    import requests

    try:
        response = requests.get(normalize_ollama_base_url(base_url), timeout=3)
        return response.status_code == 200
    except Exception:
        return False


def list_ollama_models(base_url: str = "http://localhost:11434") -> List[Dict[str, Any]]:
    """Return installed Ollama models from /api/tags."""
    import requests

    try:
        response = requests.get(f"{normalize_ollama_base_url(base_url)}/api/tags", timeout=5)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    models = payload.get("models") if isinstance(payload, dict) else []
    if not isinstance(models, list):
        return []
    return [item for item in models if isinstance(item, dict) and item.get("name")]
