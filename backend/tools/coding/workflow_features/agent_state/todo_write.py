"""持久化 TODO 列表（JSON 文件）。"""
import json
import os
from typing import Any, Dict, List

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

from backend.tools.coding.workflow_features.agent_state.state_paths import AGENT_TODO_FILE

_DEFAULT = AGENT_TODO_FILE


@trace_execution
def todo_write(todos: List[Dict[str, Any]], merge: bool = True, path: str = _DEFAULT) -> str:
    """写入 TODO 列表。merge=True 时与磁盘合并（按 id）。"""
    try:
        existing: Dict[str, Any] = {}
        if merge and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for item in raw.get("todos", []):
                if isinstance(item, dict) and "id" in item:
                    existing[str(item["id"])] = item
        for item in todos:
            if isinstance(item, dict) and "id" in item:
                existing[str(item["id"])] = item
        out = {"todos": list(existing.values())}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return f"✅ 已写入 {len(out['todos'])} 条 TODO → {path}"
    except Exception as e:
        return f"❌ todo_write 失败: {str(e)}"
