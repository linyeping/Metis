from __future__ import annotations

import json
from typing import Any, Iterable

from backend.runtime.llm_backends import openai_compat
from backend.runtime.llm_backends.openai_compat import OpenAICompatBackend


class _Utf8SseResponse:
    status_code = 200
    encoding = "ISO-8859-1"

    def __init__(self, lines: Iterable[bytes]) -> None:
        self._lines = list(lines)

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self, decode_unicode: bool = False) -> Iterable[bytes]:
        assert decode_unicode is False
        return iter(self._lines)


def _sse_chunk(content: str, *, finish_reason: str | None = None) -> bytes:
    payload = {
        "choices": [
            {
                "delta": {"content": content},
                "finish_reason": finish_reason,
            }
        ]
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}".encode("utf-8")


def test_openai_compat_stream_decodes_utf8_sse_without_charset(monkeypatch: Any) -> None:
    response = _Utf8SseResponse(
        [
            _sse_chunk("你好，"),
            _sse_chunk("中文正常", finish_reason="stop"),
            b"data: [DONE]",
        ]
    )

    def fake_post_with_retries(*_: Any, **__: Any) -> _Utf8SseResponse:
        return response

    monkeypatch.setattr(openai_compat, "post_with_retries", fake_post_with_retries)
    backend = OpenAICompatBackend(
        base_url="https://relay.example.test/v1",
        api_key="test-key",
        model="gpt-5.5",
    )

    stream = backend.chat_stream([{"role": "user", "content": "say hi in Chinese"}])
    chunks: list[str] = []
    while True:
        try:
            chunks.append(next(stream))
        except StopIteration as stop:
            final_response = stop.value
            break

    assert chunks == ["你好，", "中文正常"]
    assert final_response.content == "你好，中文正常"
    assert "ä½" not in final_response.content
