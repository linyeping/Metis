from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


_UNSUPPORTED_STRICT_SCHEMA_KEYS = {
    "$schema",
    "default",
    "examples",
}


def sanitize_deepseek_strict_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return DeepSeek beta strict-mode compatible tool schemas.

    DeepSeek strict function calling rejects loose object schemas. The sanitizer
    keeps provider-specific changes at the boundary: normal OpenAI-compatible
    tools stay untouched, while DeepSeek receives strict functions with closed
    object schemas and explicit required fields.
    """
    sanitized = deepcopy(tools)
    for tool in sanitized:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        function["strict"] = True
        parameters = function.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        function["parameters"] = sanitize_deepseek_json_schema(parameters, root=True)
    return sanitized


def sanitize_deepseek_json_schema(schema: Dict[str, Any], *, root: bool = False) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_STRICT_SCHEMA_KEYS:
            continue
        clean[key] = value

    schema_type = clean.get("type")
    types = schema_type if isinstance(schema_type, list) else [schema_type]
    is_object = root or "object" in types or isinstance(clean.get("properties"), dict)
    is_array = "array" in types or isinstance(clean.get("items"), dict)

    if is_object:
        clean["type"] = _ensure_type(clean.get("type"), "object")
        properties = clean.get("properties") if isinstance(clean.get("properties"), dict) else {}
        clean["properties"] = {
            str(name): sanitize_deepseek_json_schema(value if isinstance(value, dict) else {})
            for name, value in properties.items()
        }
        clean["required"] = list(clean["properties"].keys())
        # DeepSeek strict mode requires closed objects. Free-form tool inputs
        # need an explicit typed field instead of an open root object.
        clean["additionalProperties"] = False

    if is_array:
        items = clean.get("items")
        if isinstance(items, dict):
            clean["items"] = sanitize_deepseek_json_schema(items)

    for key in ("anyOf", "oneOf", "allOf"):
        variants = clean.get(key)
        if isinstance(variants, list):
            clean[key] = [
                sanitize_deepseek_json_schema(item) if isinstance(item, dict) else item
                for item in variants
            ]

    for key in ("$defs", "definitions"):
        definitions = clean.get(key)
        if isinstance(definitions, dict):
            clean[key] = {
                str(name): sanitize_deepseek_json_schema(value) if isinstance(value, dict) else value
                for name, value in definitions.items()
            }

    return clean


def _ensure_type(value: Any, expected: str) -> Any:
    if value is None:
        return expected
    if isinstance(value, list):
        return value if expected in value else [expected, *value]
    return value
