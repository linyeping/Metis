"""Quick smoke tests for LLM backends. Network calls run only when keys are set."""

from __future__ import annotations

import os

from . import get_backend


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]


def test_openai() -> None:
    base_url = os.environ.get("METIS_LLM_BASE_URL") or os.environ.get("MIRO_LLM_BASE_URL")
    api_key = os.environ.get("METIS_LLM_API_KEY") or os.environ.get("MIRO_LLM_API_KEY")
    if not base_url or not api_key:
        print("OpenAI compat: no METIS_LLM_BASE_URL or METIS_LLM_API_KEY, skipped")
        return
    backend = get_backend(
        "openai",
        base_url=base_url,
        api_key=api_key,
        model=os.environ.get("METIS_LLM_MODEL") or os.environ.get("MIRO_LLM_MODEL", "deepseek-v4-flash"),
    )
    response = backend.chat(
        [{"role": "user", "content": "What is the weather in Beijing?"}],
        tools=TOOLS,
    )
    assert response.tool_calls and response.tool_calls[0].name == "get_weather"
    print(f"OpenAI compat: {response.tool_calls[0]}")


def test_anthropic() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Anthropic: no ANTHROPIC_API_KEY, skipped")
        return
    backend = get_backend("anthropic", api_key=api_key)
    response = backend.chat(
        [{"role": "user", "content": "What is the weather in Beijing?"}],
        tools=TOOLS,
    )
    assert response.tool_calls
    print(f"Anthropic: {response.tool_calls[0]}")


def test_gemini() -> None:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("Gemini: no GEMINI_API_KEY, skipped")
        return
    backend = get_backend("gemini", api_key=api_key)
    response = backend.chat(
        [{"role": "user", "content": "What is the weather in Beijing?"}],
        tools=TOOLS,
    )
    assert response.tool_calls
    print(f"Gemini: {response.tool_calls[0]}")


if __name__ == "__main__":
    test_openai()
    test_anthropic()
    test_gemini()
