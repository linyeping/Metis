"""Lightweight agent loop for single-purpose sub-agents."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List

from .llm_backends import LLMBackend
from .tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class MiniAgentConfig:
    system_prompt: str = ""
    max_turns: int = 8
    max_tokens: int = 4096
    temperature: float = 0.2
    timeout: float = 120.0
    tool_names: List[str] = field(default_factory=list)
    workspace_root: str = ""


@dataclass
class MiniAgentResult:
    ok: bool = True
    output: str = ""
    turns_used: int = 0
    tool_calls_made: int = 0
    error: str = ""

    def __str__(self) -> str:
        if not self.ok:
            return f"Sub-agent failed: {self.error}"
        return self.output


def run_mini_agent(
    task: str,
    config: MiniAgentConfig,
    registry: ToolRegistry,
    backend: LLMBackend,
) -> MiniAgentResult:
    """Run a small synchronous ReAct loop with a constrained tool set."""
    all_tools = registry.get_all_schemas(format="openai")
    if config.tool_names:
        allowed = set(config.tool_names)
        tools = [
            schema
            for schema in all_tools
            if ((schema.get("function") or {}).get("name") or "") in allowed
        ]
    else:
        tools = all_tools

    messages: List[Dict[str, Any]] = []
    if config.system_prompt:
        messages.append({"role": "system", "content": config.system_prompt})
    messages.append({"role": "user", "content": task})

    turns = 0
    tool_calls = 0
    final_text = ""

    while turns < config.max_turns:
        turns += 1
        try:
            response = backend.chat(
                messages,
                tools=tools or None,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                timeout=config.timeout,
            )
        except Exception as exc:
            logger.warning("mini agent llm call failed: %s", exc)
            return MiniAgentResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                turns_used=turns,
                tool_calls_made=tool_calls,
            )

        if response.content:
            final_text = response.content

        if not response.tool_calls:
            break

        messages.append(
            {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                        },
                    }
                    for tool_call in response.tool_calls
                ],
            }
        )

        for tool_call in response.tool_calls:
            tool_calls += 1
            try:
                result = registry.execute(
                    tool_call.name,
                    tool_call.arguments,
                    workspace_root=config.workspace_root or None,
                )
            except Exception as exc:
                result = f"Tool error: {type(exc).__name__}: {exc}"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.name,
                    "content": str(result)[:4000],
                }
            )

    if not final_text:
        final_text = "(Sub-agent completed without a final text response.)"
    return MiniAgentResult(
        ok=True,
        output=final_text,
        turns_used=turns,
        tool_calls_made=tool_calls,
    )
