from __future__ import annotations

from typing import Any

from backend.web import llm_state


def test_provider_probe_ignores_env_proxy_when_proxy_is_off(monkeypatch: Any) -> None:
    monkeypatch.setenv("METIS_PROXY_MODE", "off")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    calls: list[dict[str, Any]] = []

    class FakeResponse:
        headers: dict[str, str] = {"content-type": "application/json"}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, bool]:
            return {"ok": True}

    class FakeSession:
        def __init__(self) -> None:
            self.trust_env = True

        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            calls.append({"url": url, "trust_env": self.trust_env, "proxies": kwargs.get("proxies")})
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr(llm_state.requests, "Session", FakeSession)

    assert llm_state._provider_get_json("https://api.deepseek.com/user/balance", "sk-test") == {"ok": True}
    assert calls == [
        {
            "url": "https://api.deepseek.com/user/balance",
            "trust_env": False,
            "proxies": None,
        }
    ]
