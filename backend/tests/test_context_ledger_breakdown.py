from __future__ import annotations

from backend.runtime.context_budget import context_ledger


def test_context_ledger_breaks_down_system_and_schema_tokens() -> None:
    messages = [
        {
            "role": "system",
            "content": (
                "Base prompt and loop rules.\n\n"
                "---\n[Desktop Automation Skill Reference]\nClick carefully.\n\n"
                "---\n[可用技能 / Available Skills]\n- skill: useful.\n\n"
                "---\n[User METIS.md]\nPrefer concise answers.\n\n"
                "---\n[Efficiency Rules - strictly follow to reduce steps and context usage]\nRead before write."
            ),
        },
        {"role": "user", "content": "hello"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "mcp_github_search",
                "description": "[MCP:github] Search repositories.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a local file.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        },
    ]

    ledger = context_ledger(messages, tools, model="deepseek-v4-flash")

    assert ledger["system_tokens"] > 0
    assert ledger["schema_tokens"] > 0
    assert ledger["history_tokens"] > 0
    assert ledger["context_limit"] == 1_000_000
    assert ledger["system_breakdown"]["system_prompt"] > 0
    assert ledger["system_breakdown"]["skills"] > 0
    assert ledger["system_breakdown"]["memory"] > 0
    assert ledger["schema_breakdown"]["mcp"] > 0
    assert ledger["schema_breakdown"]["builtin"] > 0
    assert ledger["schema_breakdown"]["mcp"] + ledger["schema_breakdown"]["builtin"] == ledger["schema_tokens"]
