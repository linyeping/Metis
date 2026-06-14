from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from .base import ToolCall


def repair_arguments(value: Any, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    arguments = _parse_object(value)
    parameters = schema or {}
    properties = parameters.get("properties") if isinstance(parameters.get("properties"), dict) else {}
    if not isinstance(properties, dict):
        return arguments

    repaired = dict(arguments)
    for key, prop_schema in properties.items():
        if key not in repaired or not isinstance(prop_schema, dict):
            continue
        repaired[key] = _coerce_value(repaired[key], prop_schema)
    return repaired


def repair_tool_calls(
    raw_tool_calls: Any,
    *,
    tools: Optional[List[Dict[str, Any]]] = None,
    parallel: Optional[bool] = None,
) -> List[ToolCall]:
    if not raw_tool_calls:
        return []
    if not isinstance(raw_tool_calls, list):
        raw_tool_calls = [raw_tool_calls]

    schema_map = _schema_map(tools)
    allow_parallel = _parallel_enabled() if parallel is None else bool(parallel)
    tool_calls: List[ToolCall] = []
    for index, call in enumerate(raw_tool_calls):
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = function.get("name") or call.get("name") or ""
        if not name:
            continue
        arguments = repair_arguments(
            function.get("arguments", call.get("arguments")),
            schema_map.get(str(name)),
        )
        call_id = call.get("id") or f"call_{index}"
        tool_calls.append(ToolCall(id=str(call_id), name=str(name), arguments=arguments))
        if not allow_parallel:
            break
    return tool_calls


def _parse_object(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)

    parsed = _parse_jsonish(value)
    if isinstance(parsed, dict):
        return dict(parsed)
    if isinstance(parsed, str):
        reparsed = _parse_jsonish(parsed)
        if isinstance(reparsed, dict):
            return dict(reparsed)
        if reparsed is _BAD_JSON:
            return {"value": parsed}
        if reparsed is not parsed:
            return {"value": reparsed}
    if parsed is _BAD_JSON:
        return {"_raw": value}
    return {"value": parsed}


_BAD_JSON = object()


def _parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = _strip_markdown_fence(value.strip())
    if not text:
        return {}
    for _ in range(3):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return _BAD_JSON
        if isinstance(parsed, str):
            next_text = _strip_markdown_fence(parsed.strip())
            if next_text == text or not _looks_jsonish(next_text):
                return parsed
            text = next_text
            continue
        return parsed
    return parsed


def _strip_markdown_fence(text: str) -> str:
    match = re.match(r"^```(?:json|JSON)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    return match.group(1).strip() if match else text


def _looks_jsonish(text: str) -> bool:
    return text.startswith(("{", "[", '"')) and text.endswith(("}", "]", '"'))


def _coerce_value(value: Any, schema: Dict[str, Any]) -> Any:
    expected = schema.get("type")
    if isinstance(expected, list):
        expected = next((item for item in expected if item != "null"), expected[0] if expected else "")
    if expected == "boolean":
        return _coerce_bool(value)
    if expected == "integer":
        return _coerce_int(value)
    if expected == "number":
        return _coerce_float(value)
    if expected == "array":
        return _coerce_array(value)
    if expected == "object":
        parsed = _parse_jsonish(value)
        return parsed if isinstance(parsed, dict) else value
    return value


def _coerce_bool(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return value


def _coerce_int(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[-+]?\d+", text):
            try:
                return int(text)
            except ValueError:
                return value
    return value


def _coerce_float(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", text):
            try:
                return float(text)
            except ValueError:
                return value
    return value


def _coerce_array(value: Any) -> Any:
    if isinstance(value, str):
        parsed = _parse_jsonish(value)
        if isinstance(parsed, list):
            return parsed
    return value


def _schema_map(tools: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        name = function.get("name")
        parameters = function.get("parameters")
        if name and isinstance(parameters, dict):
            result[str(name)] = parameters
    return result


def _parallel_enabled() -> bool:
    value = os.environ.get("METIS_PARALLEL_TOOLCALLS", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}
