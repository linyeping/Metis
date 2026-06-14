from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


BASE_URL = os.environ.get("MIRO_E2E_BASE_URL", "http://127.0.0.1:5000")


def test_sync_chat() -> None:
    response = requests.get(f"{BASE_URL}/status", timeout=10)
    response.raise_for_status()
    status = response.json()
    print(f"Status: {status['tools_count']} tools, backend={status['llm_backend']}")

    response = requests.get(f"{BASE_URL}/tools", timeout=10)
    response.raise_for_status()
    tools = response.json()["tools"]
    tool_names = {tool["name"] for tool in tools}
    assert "read_file" in tool_names
    assert "desktop_screenshot" in tool_names
    print(f"Tools: {len(tools)} available")

    if not os.environ.get("MIRO_LLM_API_KEY"):
        print("Skipping chat checks because MIRO_LLM_API_KEY is not set")
        return

    response = requests.post(
        f"{BASE_URL}/chat/sync",
        json={"message": "What files are in the current directory? Use list_directory."},
        timeout=180,
    )
    response.raise_for_status()
    data = response.json()
    print(f"Chat response: {data.get('response', '')[:100]}")
    print(f"Tool calls: {[call['tool'] for call in data.get('tool_calls', [])]}")

    response = requests.post(
        f"{BASE_URL}/chat",
        json={"message": "What is 2+2?"},
        stream=True,
        timeout=180,
    )
    response.raise_for_status()
    events: List[Dict[str, Any]] = []
    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        events.append(json.loads(payload))
    assert events
    print(f"SSE events: {[event['type'] for event in events]}")

    response = requests.post(f"{BASE_URL}/reset", timeout=10)
    response.raise_for_status()
    print("Reset successful")


if __name__ == "__main__":
    print(f"Testing Miro server at {BASE_URL}")
    test_sync_chat()
