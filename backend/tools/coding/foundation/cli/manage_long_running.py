"""后台常驻进程管理（dev server 等），与 legacy 行为一致。"""
import subprocess
import time
import uuid
from typing import Any, Dict, Optional

from ..core_mechanisms.trace_execution import trace_execution

background_processes: Dict[int, Dict[str, Any]] = {}


@trace_execution
def start_long_running_process(
    command: str,
    name: Optional[str] = None,
    cwd: str = ".",
    env: Optional[Dict[str, str]] = None,
) -> str:
    """启动后台进程（不阻塞；登记 PID）。"""
    try:
        popen_kw: Dict[str, Any] = {
            "shell": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "stdin": subprocess.PIPE,
            "start_new_session": True,
            "cwd": cwd,
        }
        if env is not None:
            import os
            popen_kw["env"] = {**os.environ, **env}

        process = subprocess.Popen(command, **popen_kw)
        pid = process.pid
        process_name = name or f"process_{uuid.uuid4().hex[:8]}"
        background_processes[pid] = {
            "process": process,
            "name": process_name,
            "command": command,
            "start_time": time.time(),
        }
        return (
            f"✅ 后台进程启动成功\nPID: {pid}\n名称: {process_name}\n命令: {command}"
        )
    except Exception as e:
        return f"❌ 启动后台进程失败: {str(e)}"


@trace_execution
def stop_long_running_process(
    pid: int,
    graceful_timeout_sec: float = 5.0,
    force_kill: bool = True,
) -> str:
    """终止已登记的后台进程。"""
    del graceful_timeout_sec
    try:
        if pid not in background_processes:
            return f"❌ 未找到 PID 为 {pid} 的进程"

        process_info = background_processes[pid]
        process = process_info["process"]

        if process is None:
            del background_processes[pid]
            return f"⚠️ PID {pid} 为外部登记项，无法通过本接口终止，请用系统工具结束进程"

        # FABLEADV-18: terminate the whole process tree (children included).
        _terminate_process_tree(process)

        del background_processes[pid]
        return f"✅ 进程终止成功\nPID: {pid}\n名称: {process_info['name']}"
    except Exception as e:
        return f"❌ 终止进程失败: {str(e)}"


@trace_execution
def list_long_running_processes() -> str:
    """列出登记的后台进程。"""
    if not background_processes:
        return "📊 当前没有运行的后台进程"

    result = "📊 后台进程列表:\n"
    for pid, info in background_processes.items():
        result += f"  PID: {pid}\n"
        result += f"    名称: {info['name']}\n"
        result += f"    命令: {info['command'][:50]}...\n"
        result += f"    运行时间: {time.time() - info['start_time']:.1f}s\n"
        result += "    ---\n"
    return result.strip()


def get_long_running_status(pid: int) -> Optional[Dict[str, Any]]:
    """查询进程是否仍在运行。"""
    if pid not in background_processes:
        return None
    info = background_processes[pid]
    proc = info["process"]
    if proc is None:
        return {
            "pid": pid,
            "name": info["name"],
            "command": info["command"],
            "alive": None,
            "returncode": None,
        }
    alive = proc.poll() is None
    return {
        "pid": pid,
        "name": info["name"],
        "command": info["command"],
        "alive": alive,
        "returncode": proc.poll(),
    }


@trace_execution
def register_external_process(pid: int, name: str, command: str) -> str:
    """将外部 PID 登记为只读记录（仅展示；终止请用系统工具）。"""
    background_processes[pid] = {
        "process": None,
        "name": name,
        "command": command,
        "start_time": time.time(),
    }
    return f"✅ 已登记外部进程 PID={pid} ({name})（终止请用 taskkill / kill）"


# 与历史单体函数名兼容
start_background_process = start_long_running_process
stop_background_process = stop_long_running_process
list_background_processes = list_long_running_processes


def _terminate_process_tree(process: Any) -> None:
    """FABLEADV-18: terminate a process AND its children (dev servers spawn
    sub-processes, e.g. vite → esbuild). POSIX uses the process group created by
    start_new_session=True; Windows uses taskkill /T."""
    import os
    import signal

    pid = getattr(process, "pid", None)
    if pid is None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
            return
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            pass
    try:
        process.terminate()
        process.wait(timeout=2)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _cleanup_all_background_processes() -> None:
    """FABLEADV-18: atexit hook — never leave orphaned dev-server/long-running
    processes spinning after the backend exits or crashes."""
    for pid in list(background_processes):
        info = background_processes.get(pid) or {}
        process = info.get("process")
        if process is None:
            continue
        _terminate_process_tree(process)
    background_processes.clear()


import atexit as _atexit

_atexit.register(_cleanup_all_background_processes)
