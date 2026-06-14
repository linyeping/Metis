"""读取本机 MCP 配置说明（Cursor .cursor/mcp.json）。"""
import json
import os

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def manage_mcp_servers(action: str = "list", config_path: str = ".cursor/mcp.json") -> str:
    if action != "list":
        return f"⚠️ 当前仅支持 action=list，收到: {action}"
    if not os.path.isfile(config_path):
        return f"📭 未找到 {config_path}"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        keys = list(data.keys()) if isinstance(data, dict) else ["(非对象根)"]
        return f"✅ MCP 配置键: {keys}\n（完整 JSON 请直接 read_file）"
    except Exception as e:
        return f"❌ 读取 MCP 配置失败: {str(e)}"
