# -*- coding: utf-8 -*-
"""目标/任务持久化状态机。

状态文件位于 ~/.miro/desk_tasks.json，结构:
{
  "current_goal": "...",
  "status": "idle|running|paused|done|error",
  "steps": [ {id, action, status, result, ts} ],
  "log": [ {ts, level, msg} ],
  "created_at": "...",
  "updated_at": "..."
}
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .. import config


def _state_path() -> Path:
    base = Path(config._config_path().parent)
    base.mkdir(parents=True, exist_ok=True)
    return base / "desk_tasks.json"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


_EMPTY: dict[str, Any] = {
    "current_goal": "",
    "status": "idle",
    "steps": [],
    "log": [],
    "created_at": "",
    "updated_at": "",
}


def load_state() -> dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return dict(_EMPTY)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(_EMPTY)
    for k, v in _EMPTY.items():
        data.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)())
    return data


def save_state(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def set_goal(goal: str) -> dict[str, Any]:
    """设置新目标，清空旧步骤。"""
    st = {
        "current_goal": goal,
        "status": "running",
        "steps": [],
        "log": [{"ts": _now(), "level": "info", "msg": f"目标设定: {goal}"}],
        "created_at": _now(),
        "updated_at": "",
    }
    save_state(st)
    return st


def add_step(action: str, detail: str = "") -> dict[str, Any]:
    """追加一个执行步骤。"""
    st = load_state()
    step = {
        "id": uuid.uuid4().hex[:8],
        "action": action,
        "detail": detail,
        "status": "pending",
        "result": "",
        "ts": _now(),
    }
    st["steps"].append(step)
    _log(st, "info", f"+ step {step['id']}: {action}")
    save_state(st)
    return step


def update_step(step_id: str, status: str, result: str = "") -> bool:
    """更新步骤状态: pending|running|done|error|skipped。"""
    st = load_state()
    for s in st["steps"]:
        if s["id"] == step_id:
            s["status"] = status
            if result:
                s["result"] = result
            _log(st, "info", f"step {step_id} → {status}")
            save_state(st)
            return True
    return False


def finish_goal(success: bool = True, summary: str = "") -> dict[str, Any]:
    st = load_state()
    st["status"] = "done" if success else "error"
    _log(st, "info" if success else "error", f"目标{'完成' if success else '失败'}: {summary}")
    save_state(st)
    return st


def pause_goal() -> dict[str, Any]:
    st = load_state()
    if st["status"] == "running":
        st["status"] = "paused"
        _log(st, "warn", "目标已暂停")
        save_state(st)
    return st


def resume_goal() -> dict[str, Any]:
    st = load_state()
    if st["status"] == "paused":
        st["status"] = "running"
        _log(st, "info", "目标继续")
        save_state(st)
    return st


def append_log(level: str, msg: str) -> None:
    st = load_state()
    _log(st, level, msg)
    save_state(st)


def _log(st: dict, level: str, msg: str) -> None:
    st["log"].append({"ts": _now(), "level": level, "msg": msg})
    if len(st["log"]) > 500:
        st["log"] = st["log"][-300:]
