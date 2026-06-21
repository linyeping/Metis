from __future__ import annotations

from typing import Any, Dict, List

from backend.runtime.llm_backends import openai_compat
from backend.runtime.llm_backends.openai_compat import OpenAICompatBackend


class _FakeResponse:
    headers: Dict[str, str] = {}

    def json(self) -> Dict[str, Any]:
        return {
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
        }


def _tool_schema() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "write_report",
                "description": "Write a report.",
                "parameters": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "default": "Untitled"},
                        "sections": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "heading": {"type": "string"},
                                    "body": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["title"],
                    "additionalProperties": True,
                },
            },
        }
    ]


def test_deepseek_payload_uses_strict_closed_tool_schema(monkeypatch):
    monkeypatch.setenv("METIS_DEEPSEEK_STRICT", "1")  # strict is opt-in now
    captured: Dict[str, Any] = {}

    def fake_post(*args: Any, **kwargs: Any) -> _FakeResponse:
        captured["payload"] = kwargs["payload"]
        return _FakeResponse()

    monkeypatch.setattr(openai_compat, "post_with_retries", fake_post)
    tools = _tool_schema()
    OpenAICompatBackend("https://api.deepseek.com", "key", "deepseek-v4-flash").chat(
        [{"role": "user", "content": "write"}],
        tools=tools,
    )

    sent_tool = captured["payload"]["tools"][0]["function"]
    params = sent_tool["parameters"]
    nested = params["properties"]["sections"]["items"]

    assert sent_tool["strict"] is True
    assert "$schema" not in params
    assert params["required"] == ["title", "sections"]
    assert params["additionalProperties"] is False
    assert "default" not in params["properties"]["title"]
    assert nested["required"] == ["heading", "body"]
    assert nested["additionalProperties"] is False
    assert tools[0]["function"]["parameters"]["required"] == ["title"]
    assert tools[0]["function"]["parameters"]["additionalProperties"] is True


def test_deepseek_strict_schema_closes_empty_objects() -> None:
    from backend.runtime.llm_backends.deepseek_schema import sanitize_deepseek_json_schema

    sanitized = sanitize_deepseek_json_schema({"type": "object", "properties": {}})

    assert sanitized["type"] == "object"
    assert sanitized["properties"] == {}
    assert sanitized["required"] == []
    assert sanitized["additionalProperties"] is False


def test_non_deepseek_payload_keeps_tool_schema_unchanged(monkeypatch):
    captured: Dict[str, Any] = {}

    def fake_post(*args: Any, **kwargs: Any) -> _FakeResponse:
        captured["payload"] = kwargs["payload"]
        return _FakeResponse()

    monkeypatch.setattr(openai_compat, "post_with_retries", fake_post)
    tools = _tool_schema()
    OpenAICompatBackend("https://relay.example/v1", "key", "gpt-5").chat(
        [{"role": "user", "content": "write"}],
        tools=tools,
    )

    sent_tool = captured["payload"]["tools"][0]["function"]
    assert "strict" not in sent_tool
    assert sent_tool["parameters"] is tools[0]["function"]["parameters"]
