# -*- coding: utf-8 -*-
"""块5：在独立子进程中运行 Task，父进程只读结果 JSON。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .delegate_workspace import delegate_workspace_outside_allowed, resolve_delegate_workspace_for_task
from .task_session_persistence import DELEGATE_SESSIONS_DIRNAME, resume_state_file_missing_message
from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError


def _repo_root() -> Path:
    import backend.tools as backend_tools
    return Path(backend_tools.__file__).resolve().parents[2]


def run_task_subprocess(
    *,
    prompt: str,
    subagent_type: str,
    workspace_root: str = ".",
    timeout_sec: int = 180,
    resume: str = "",
    forced_session_id: str = "",
) -> Tuple[bool, str, int, str]:
    """
    启动 `python -m backend.tools.coding.workflow_features.subagents.task_subprocess_worker`。
    返回 (ok, result_text, exit_code, session_id)。

    forced_session_id 非空时用作子进程会话 id（DAG 节点稳定 sid），不校验 resume 状态文件。
    resume 非空且未使用 forced_session_id 时：必须先存在 `<ws>/.delegate_sessions/<resume>.json`。
    """
    try:
        ws = resolve_delegate_workspace_for_task(workspace_root)
    except PathSecurityError as e:
        return False, str(e), 1, ""

    ws.mkdir(parents=True, exist_ok=True)
    session_dir = ws / DELEGATE_SESSIONS_DIRNAME
    session_dir.mkdir(parents=True, exist_ok=True)

    fid = (forced_session_id or "").strip()
    rid = (resume or "").strip()
    if fid:
        sid = fid
    else:
        if rid:
            msg = resume_state_file_missing_message(str(ws), rid)
            if msg:
                return False, msg, 1, rid
        sid = rid or uuid.uuid4().hex[:16]
    in_path = session_dir / f"{sid}_in.json"
    out_path = session_dir / f"{sid}_out.json"

    payload: Dict[str, Any] = {
        "prompt": prompt,
        "subagent_type": subagent_type,
        "workspace_root": str(ws),
        "session_id": sid,
        # 子进程无父进程 ContextVar，用快照做与父进程一致的边界判定
        "_delegate_outside_effective": delegate_workspace_outside_allowed(),
    }
    in_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    repo = _repo_root()
    env = os.environ.copy()
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(repo) + (os.pathsep + prev if prev else "")

    cmd = [
        sys.executable,
        "-m",
        "backend.tools.coding.workflow_features.subagents.task_subprocess_worker",
        str(in_path),
        str(out_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ws),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return False, f"❌ 子进程超时（>{timeout_sec}s）已终止。", -9, sid

    if out_path.is_file():
        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
            ok = bool(data.get("ok", False))
            res = str(data.get("result", ""))
            code = int(data.get("exit_code", proc.returncode))
            return ok, res, code, sid
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    err = (proc.stderr or proc.stdout or "").strip()[:4000]
    return False, f"❌ 子进程未写出有效 JSON 结果。\nstderr/stdout 摘要:\n{err}", proc.returncode, sid
