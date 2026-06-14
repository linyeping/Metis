"""标准终端执行（防死锁、超时、输出截断）。"""
import os
import subprocess
import time
from pathlib import Path

from backend.runtime.cancellation import OperationCancelled, current_cancel_event, is_cancel_requested
from ..core_mechanisms.log_config import logger
from ..core_mechanisms.path_security import PathSecurityError, get_workspace_root
from ..core_mechanisms.trace_execution import trace_execution
from ..core_mechanisms.shell_security import (
    ShellSecurityError,
    check_shell_command,
    format_security_error,
    get_shell_security_config,
)


def _runtime_precheck_message(command: str) -> str:
    try:
        from backend.tools.coding.execution.runtime_manager import check_command_runtime
    except Exception:
        return ""

    missing = check_command_runtime(command)
    if not missing:
        return ""
    runtime = missing.get("runtime", "运行时")
    return (
        f"❌ 运行时未安装: {runtime}\n\n"
        f"{missing.get('description', runtime)} 在此电脑上未找到。\n\n"
        "安装方法:\n"
        f"  自动: 调用 install_dev_runtime(\"{runtime}\")\n"
        f"  手动: {missing.get('install_cmd', '')}\n\n"
        "安装后重新执行此命令即可。"
    )


def _runtime_not_found_hint(command: str) -> str:
    try:
        from backend.tools.coding.execution.runtime_manager import check_command_runtime
    except Exception:
        return ""

    missing = check_command_runtime(command)
    if not missing:
        return ""
    runtime = missing.get("runtime", "运行时")
    return (
        f"\n\n提示: {runtime} 未安装。\n"
        f"安装: {missing.get('install_cmd', '')}\n"
        f"或调用 install_dev_runtime(\"{runtime}\")。"
    )


def _kill_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return
        try:
            os.killpg(os.getpgid(process.pid), 15)
        except Exception:
            process.terminate()
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(os.getpgid(process.pid), 9)
        except Exception:
            process.kill()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _validated_shell_cwd(cwd: str) -> str:
    """
    将 cwd 解析为绝对路径；默认必须落在 workspace_root 下（与路径安全对齐）。
    allow_shell_cwd_outside_workspace 或总闸开启时可使用工作区外目录。
    """
    from ..core_mechanisms.execution_boundary_context import get_effective_sub_allow

    root = get_workspace_root()
    user = Path(cwd or ".").expanduser()
    try:
        if not user.is_absolute():
            abs_cwd = (root / user).resolve()
        else:
            abs_cwd = user.resolve()
    except Exception as e:
        raise PathSecurityError(f"Shell cwd 解析失败: {cwd!r} — {e}") from e

    if not get_effective_sub_allow("allow_shell_cwd_outside_workspace"):
        try:
            abs_cwd.relative_to(root)
        except ValueError:
            raise PathSecurityError(
                "❌ Shell 工作目录超出工作区限制\n"
                f"  请求 cwd: {cwd!r}\n"
                f"  解析为: {abs_cwd}\n"
                f"  工作区根: {root}\n"
                "  提示: 开启「Shell cwd 可超出工作区」或总闸后再试。"
            ) from None

    if not abs_cwd.is_dir():
        raise PathSecurityError(f"❌ Shell 工作目录不存在或不是目录: {abs_cwd}")

    return str(abs_cwd)


@trace_execution
def execute_bash_command(
    command: str,
    timeout: int = 60,
    cwd: str = ".",
    description: str = "",
) -> str:
    """
    增强版命令执行 - 防死锁机制 + Shell 安全检查
    对应文档 03: 异常处理与降级策略

    description: 与 C Shell 工具一致，5–10 词说明用途，仅写入日志便于审计。
    """
    desc = f" [{description}]" if description else ""

    try:
        cwd = _validated_shell_cwd(cwd)
    except PathSecurityError as e:
        return str(e)

    # ===== Shell 安全检查 =====
    security_config = get_shell_security_config()
    
    if security_config["enabled"]:
        is_safe, reason, severity = check_shell_command(
            command,
            **security_config
        )
        
        if not is_safe:
            level = security_config["level"]
            error_msg = format_security_error(command, reason, severity, level)
            
            if level == "deny":
                # 拒绝执行
                logger.error(f"🚫 Shell 命令被拒绝: {command}")
                logger.error(f"   原因: {reason}")
                raise ShellSecurityError(error_msg)
            
            elif level in ("confirm", "warning"):
                # 警告但允许执行
                logger.warning(f"⚠️  Shell 命令警告: {command}")
                logger.warning(f"   原因: {reason}")
                logger.warning(error_msg)

    runtime_message = _runtime_precheck_message(command)
    if runtime_message:
        return runtime_message
    
    # ===== 执行命令 =====
    logger.info(f"🔨 执行命令{desc}: {command} (工作目录: {cwd}, 超时: {timeout}s)")

    process = None
    try:
        popen_kwargs = {
            "shell": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "cwd": cwd,
        }
        if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        elif os.name != "nt":
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(command, **popen_kwargs)
        cancel_event = current_cancel_event()
        deadline = time.time() + max(1, int(timeout))
        while True:
            if is_cancel_requested(cancel_event):
                _kill_process_tree(process)
                raise OperationCancelled("Shell command cancelled")
            remaining = deadline - time.time()
            if remaining <= 0:
                _kill_process_tree(process)
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        stdout = (stdout or "").strip()
        stderr = (stderr or "").strip()
        exit_code = process.returncode

        # 输出截断（防止过长）
        max_lines = 50
        if stdout.count('\n') > max_lines:
            lines = stdout.split('\n')
            stdout = '\n'.join(lines[-max_lines:])
            stdout = f"[输出过长，仅显示最后 {max_lines} 行]\n{stdout}"

        if stderr.count('\n') > max_lines:
            lines = stderr.split('\n')
            stderr = '\n'.join(lines[-max_lines:])
            stderr = f"[错误输出过长，仅显示最后 {max_lines} 行]\n{stderr}"

        status = "✅ 成功" if exit_code == 0 else f"❌ 失败 (退出码 {exit_code})"

        output = f"执行状态: {status}\n"
        if stdout:
            output += f"\n标准输出:\n{stdout}\n"
        if stderr:
            output += f"\n错误输出:\n{stderr}\n"

        if exit_code in (9009, 127):
            output += _runtime_not_found_hint(command)

        return output.strip()

    except subprocess.TimeoutExpired:
        if process is not None:
            _kill_process_tree(process)
        return f"❌ 超时: 命令执行超过 {timeout}s\n建议: 检查命令是否卡死或使用更长的超时时间"
    except OperationCancelled:
        raise
    except Exception as e:
        return f"❌ 异常: {str(e)}"
