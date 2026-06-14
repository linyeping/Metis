from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from backend.core.paths import metis_path

TaskRunner = Callable[[Dict[str, Any]], Dict[str, Any]]

_lock = threading.Lock()
_runner: Optional[TaskRunner] = None
_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _cron_path() -> str:
    return str(metis_path("cron.json"))


def _load_tasks() -> List[Dict[str, Any]]:
    path = _cron_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _save_tasks(tasks: List[Dict[str, Any]]) -> None:
    with open(_cron_path(), "w", encoding="utf-8") as handle:
        json.dump(tasks, handle, ensure_ascii=False, indent=2)


def list_tasks() -> List[Dict[str, Any]]:
    with _lock:
        tasks = _load_tasks()
        changed = False
        now = time.time()
        for task in tasks:
            if not task.get("nextRun"):
                task["nextRun"] = next_run(str(task.get("schedule") or "every 1 minute"), now)
                changed = True
        if changed:
            _save_tasks(tasks)
        return tasks


def create_task(data: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    task = {
        "id": str(uuid.uuid4()),
        "name": str(data.get("name") or "Scheduled task").strip(),
        "schedule": str(data.get("schedule") or "every 1 minute").strip(),
        "prompt": str(data.get("prompt") or "").strip(),
        "workspace_id": str(data.get("workspace_id") or ""),
        "enabled": bool(data.get("enabled", True)),
        "createdAt": now,
        "lastRun": 0,
        "nextRun": next_run(str(data.get("schedule") or "every 1 minute"), now),
        "lastSessionId": "",
        "lastStatus": "",
    }
    with _lock:
        tasks = _load_tasks()
        tasks.append(task)
        _save_tasks(tasks)
    return task


def update_task(task_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt required")

    with _lock:
        tasks = _load_tasks()
        for task in tasks:
            if str(task.get("id")) != task_id:
                continue
            previous_schedule = str(task.get("schedule") or "every 1 minute")
            next_schedule = str(data.get("schedule") or previous_schedule).strip() or previous_schedule
            task["name"] = str(data.get("name") or task.get("name") or "Scheduled task").strip()
            task["schedule"] = next_schedule
            task["prompt"] = prompt
            task["workspace_id"] = str(data.get("workspace_id") or task.get("workspace_id") or "")
            if "enabled" in data:
                task["enabled"] = bool(data.get("enabled"))
            if next_schedule != previous_schedule:
                task["nextRun"] = next_run(next_schedule, time.time())
            _save_tasks(tasks)
            return task
    return None


def delete_task(task_id: str) -> bool:
    with _lock:
        tasks = _load_tasks()
        next_tasks = [task for task in tasks if str(task.get("id")) != task_id]
        _save_tasks(next_tasks)
        return len(next_tasks) != len(tasks)


def toggle_task(task_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        tasks = _load_tasks()
        for task in tasks:
            if str(task.get("id")) == task_id:
                task["enabled"] = not bool(task.get("enabled", True))
                task["nextRun"] = next_run(str(task.get("schedule") or "every 1 minute"), time.time())
                _save_tasks(tasks)
                return task
    return None


def run_task_now(task_id: str) -> Optional[Dict[str, Any]]:
    task = None
    with _lock:
        for item in _load_tasks():
            if str(item.get("id")) == task_id:
                task = item
                break
    if task is None:
        return None
    return _execute_task(task)


def start_scheduler(runner: TaskRunner) -> None:
    global _runner, _thread
    _runner = runner
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="metis-cron")
    _thread.start()


def next_run(schedule: str, now: Optional[float] = None) -> float:
    now = now or time.time()
    value = schedule.strip().lower()
    if value.startswith("every"):
        minutes = 1
        for part in value.split():
            if part.isdigit():
                minutes = max(1, int(part))
                break
        return now + minutes * 60
    if ":" in value:
        hour_text, minute_text = value.split(":", 1)
        try:
            hour = max(0, min(23, int(hour_text)))
            minute = max(0, min(59, int(minute_text[:2])))
        except ValueError:
            return now + 24 * 60 * 60
        local = time.localtime(now)
        candidate = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, hour, minute, 0, 0, 0, -1))
        if candidate <= now:
            candidate += 24 * 60 * 60
        return candidate
    return now + 24 * 60 * 60


def _loop() -> None:
    while not _stop.wait(15):
        due: List[Dict[str, Any]] = []
        now = time.time()
        with _lock:
            tasks = _load_tasks()
            for task in tasks:
                if bool(task.get("enabled", True)) and float(task.get("nextRun") or 0) <= now:
                    due.append(dict(task))
        for task in due:
            _execute_task(task)


def _execute_task(task: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": False, "error": "runner unavailable"}
    if _runner:
        try:
            result = _runner(task)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
    with _lock:
        tasks = _load_tasks()
        for item in tasks:
            if str(item.get("id")) == str(task.get("id")):
                item["lastRun"] = time.time()
                item["nextRun"] = next_run(str(item.get("schedule") or "every 1 minute"), time.time())
                item["lastStatus"] = "ok" if result.get("ok") else str(result.get("error") or "failed")
                item["lastSessionId"] = str(result.get("session_id") or item.get("lastSessionId") or "")
                break
        _save_tasks(tasks)
    return result
