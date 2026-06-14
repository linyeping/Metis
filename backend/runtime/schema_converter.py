from __future__ import annotations

from typing import Any, Dict, List


def openai_to_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI function calling tools to Anthropic tools."""
    result: List[Dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", tool)
        name = function.get("name")
        if not name:
            continue
        result.append(
            {
                "name": name,
                "description": function.get("description", ""),
                "input_schema": function.get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
        )
    return result


def openai_to_gemini(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI tools to Gemini FunctionDeclaration format."""
    declarations: List[Dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", tool)
        name = function.get("name")
        if not name:
            continue
        declarations.append(
            {
                "name": name,
                "description": function.get("description", ""),
                "parameters": function.get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
        )
    return [{"function_declarations": declarations}]


def anthropic_to_openai(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert Anthropic tools to OpenAI function calling format."""
    result: List[Dict[str, Any]] = []
    for tool in tools:
        name = tool.get("name")
        if not name:
            continue
        result.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get(
                        "input_schema", {"type": "object", "properties": {}}
                    ),
                },
            }
        )
    return result
