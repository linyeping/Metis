"""记录 Agent 模式（plan / act 等）。"""
import json
import os

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

from backend.tools.coding.workflow_features.agent_state.state_paths import AGENT_MODE_FILE

_MODE_FILE = AGENT_MODE_FILE


@trace_execution
def switch_mode(mode: str, note: str = "") -> str:
    """mode 如 plan、act、ask。"""
    try:
        data = {"mode": mode, "note": note}
        with open(_MODE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return f"✅ 模式已切换为: {mode}"
    except Exception as e:
        return f"❌ switch_mode 失败: {str(e)}"
