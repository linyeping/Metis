"""尽力调用本机 linter（ruff JSON / pyright 或 basedpyright JSON / mypy / flake8 / pylint），否则回退提示。
在 MIRO_READLINTS_MODE=lsp|auto 且配置 LSP 时走语言服务器，否则为 CLI。"""
import json
import os
import subprocess
from shutil import which

from backend.tools.coding.diagnosis.errors.lsp_read_lints import _command0_executable
from typing import Optional, List

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


def _parse_paths(paths: str) -> List[str]:
    """解析 paths 参数为文件路径列表"""
    result = []
    
    # 尝试解析为 JSON 数组
    if paths.startswith('[') and paths.endswith(']'):
        try:
            parsed = json.loads(paths)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, str) and os.path.exists(item):
                        result.append(item)
            return result
        except json.JSONDecodeError:
            pass
    
    # 逗号分隔
    for path in paths.split(','):
        path = path.strip()
        if path and os.path.exists(path):
            result.append(path)
    
    return result


def _expand_directory(path: str) -> List[str]:
    """展开目录为文件列表（与现有 CLI 行为一致）"""
    if not os.path.isdir(path):
        return [path] if os.path.isfile(path) else []
    
    result = []
    for root, _, files in os.walk(path):
        for file in files:
            if file.endswith('.py'):
                result.append(os.path.join(root, file))
    
    return result


def _workspace_root_for_lsp(file_paths: List[str]) -> str:
    """LSP initialize.rootUri / 子进程 cwd：优先 MIRO_WORKSPACE_ROOT，否则推导公共目录。"""
    env = os.environ.get("MIRO_WORKSPACE_ROOT", "").strip()
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    abs_files = [os.path.abspath(p) for p in file_paths if os.path.isfile(p)]
    if not abs_files:
        return os.path.abspath(".")
    dirs = {os.path.dirname(p) for p in abs_files}
    if len(dirs) == 1:
        return dirs.pop()
    try:
        return os.path.commonpath(abs_files)
    except ValueError:
        return os.path.dirname(abs_files[0])


def _try_lsp_diagnostics(file_paths: List[str], max_output: int) -> Optional[str]:
    """尝试使用 LSP 获取诊断信息"""
    try:
        from .lsp_read_lints import try_lsp_diagnostics, get_lsp_command, get_language_id
    except ImportError:
        return None
    
    # 获取配置
    mode = os.environ.get('MIRO_READLINTS_MODE', 'cli').lower()
    if mode not in ('lsp', 'auto'):
        return None
    
    # 获取 LSP 命令
    lsp_command_str = os.environ.get('MIRO_LSP_COMMAND', 'pylsp')
    timeout_sec = int(os.environ.get('MIRO_LSP_DIAG_TIMEOUT_SEC', '30'))
    
    # 确定语言（使用第一个文件的扩展名）
    if not file_paths:
        return None
    
    language = get_language_id(file_paths[0])
    lsp_command = get_lsp_command(language, lsp_command_str)
    if not lsp_command:
        return None
    
    fb = os.environ.get("MIRO_LSP_FALLBACK_CLI", "1").strip().lower() in ("1", "true", "yes", "")

    # 检查可执行文件
    if not _command0_executable(lsp_command[0]):
        if mode == "lsp" and not fb:
            return "⚠️ LSP 服务器不可用，且 MIRO_LSP_FALLBACK_CLI=0 禁止回退"
        return None

    max_files = max(1, int(os.environ.get("MIRO_LSP_MAX_FILES", "80")))
    if len(file_paths) > max_files:
        file_paths = file_paths[:max_files]

    workspace_root = _workspace_root_for_lsp(file_paths)
    result = try_lsp_diagnostics(file_paths, lsp_command, workspace_root, timeout_sec, max_output)

    if result is not None:
        return result
    if mode == "lsp" and not fb:
        return "⚠️ LSP 诊断失败，且 MIRO_LSP_FALLBACK_CLI=0 禁止回退"
    return None


def _find_exe(name: str) -> Optional[str]:
    return which(name) or which(name + ".exe")


def _find_pyright_exe() -> Optional[str]:
    """pyright 与 basedpyright CLI 兼容 `--outputjson`，依次探测。"""
    for name in ("pyright", "basedpyright"):
        p = _find_exe(name)
        if p:
            return p
    return None


def _pyright_cli_label(exe: str) -> str:
    base = os.path.basename(exe).replace(".exe", "").lower()
    return "basedpyright" if "basedpyright" in base else "pyright"


def _ruff_outputjson(
    exe: str, target: str, cwd: str, max_output: int
) -> Optional[str]:
    """优先 ruff check --output-format=json，与 pyright 路径同为结构化诊断摘要。"""
    if os.environ.get("MIRO_RUFF_PLAIN", "").strip() in ("1", "true", "yes"):
        return None
    try:
        r = subprocess.run(
            [exe, "check", target, "--output-format=json"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=cwd,
        )
        raw = (r.stdout or "").strip()
        if not raw:
            return None
        data = json.loads(raw)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    lines = [
        f"=== ruff check --output-format=json (退出码 {r.returncode}) ===",
        f"诊断条数: {len(data)}",
        "",
    ]
    if not data:
        lines.append("（无违反规则）")
        out = "\n".join(lines)
        return out if len(out) <= max_output else out[:max_output] + "\n... (截断)"
    limit = min(len(data), 100)
    for item in data[:limit]:
        fn = item.get("filename") or item.get("path") or ""
        msg = item.get("message") or ""
        code = item.get("code") or ""
        loc = item.get("location") or {}
        row = loc.get("row", "?")
        col = loc.get("column", "?")
        extra = f" [{code}]" if code else ""
        lines.append(f"  {fn}:{row}:{col}{extra} {msg}")
    if len(data) > limit:
        lines.append(f"  ... 另有 {len(data) - limit} 条未显示")
    out = "\n".join(lines)
    if len(out) > max_output:
        out = out[:max_output] + "\n... (截断)"
    return out


def _pyright_outputjson(
    exe: str, target: str, cwd: str, max_output: int
) -> Optional[str]:
    """块8.3：优先使用 pyright --outputjson 压缩诊断（对齐「一小步」JSON）。"""
    if os.environ.get("MIRO_PYRIGHT_PLAIN", "").strip() in ("1", "true", "yes"):
        return None
    try:
        r = subprocess.run(
            [exe, "--outputjson", target],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=cwd,
        )
        data = json.loads(r.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, TypeError):
        return None
    diags = data.get("generalDiagnostics") or []
    label = _pyright_cli_label(exe)
    lines = [
        f"=== {label} --outputjson (退出码 {r.returncode}) ===",
        f"诊断条数: {len(diags)}",
        "",
    ]
    limit = min(len(diags), 100)
    for d in diags[:limit]:
        fn = d.get("file", "")
        msg = d.get("message", "")
        sev = d.get("severity", "")
        rule = d.get("rule", "")
        extra = f" ({rule})" if rule else ""
        lines.append(f"  [{sev}] {fn}: {msg}{extra}")
    if len(diags) > limit:
        lines.append(f"  ... 另有 {len(diags) - limit} 条未显示")
    out = "\n".join(lines)
    if len(out) > max_output:
        out = out[:max_output] + "\n... (截断)"
    return out


@trace_execution
def read_lints(paths: str = ".", max_output: int = 8000) -> str:
    """
    paths: 文件或目录。依次尝试 ruff（优先 --output-format=json）、pyright 或 basedpyright（--outputjson）、mypy、flake8、pylint。
    环境变量 MIRO_RUFF_PLAIN / MIRO_PYRIGHT_PLAIN=1 可强制对应工具走纯文本输出。
    在 MIRO_READLINTS_MODE=lsp|auto 且配置 LSP 时走语言服务器，否则为 CLI。
    """
    # 首先尝试 LSP 模式
    parsed_paths = _parse_paths(paths)
    if parsed_paths:
        # 展开目录
        file_paths = []
        for path in parsed_paths:
            file_paths.extend(_expand_directory(path))
        
        if file_paths:
            lsp_result = _try_lsp_diagnostics(file_paths, max_output)
            if lsp_result is not None:
                return lsp_result
    
    # 回退到 CLI 模式（原始逻辑）
    cwd = "."
    target = paths
    if os.path.isfile(paths):
        target = paths
        parent = os.path.dirname(os.path.abspath(paths)) or "."
        if os.path.isdir(parent):
            cwd = parent

    exe = _find_exe("ruff")
    if exe:
        formatted = _ruff_outputjson(exe, target, cwd, max_output)
        if formatted is not None:
            return formatted
        try:
            r = subprocess.run(
                [exe, "check", target],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=cwd,
            )
            out = (r.stdout or r.stderr or "").strip()
            if len(out) > max_output:
                out = out[:max_output] + "\n... (截断)"
            header = f"=== ruff (退出码 {r.returncode}) ===\n"
            if out:
                return header + out
            return header + "（无输出）"
        except Exception:
            pass

    exe = _find_pyright_exe()
    if exe:
        plabel = _pyright_cli_label(exe)
        formatted = _pyright_outputjson(exe, target, cwd, max_output)
        if formatted is not None:
            return formatted
        try:
            r = subprocess.run(
                [exe, target],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=cwd,
            )
            out = (r.stdout or r.stderr or "").strip()
            if len(out) > max_output:
                out = out[:max_output] + "\n... (截断)"
            header = f"=== {plabel} (退出码 {r.returncode}) ===\n"
            return header + (out or "（无输出）")
        except Exception:
            pass

    for tool, cmd in (
        ("mypy", ["mypy", target]),
        ("flake8", ["flake8", target]),
        ("pylint", ["pylint", target]),
    ):
        exe = _find_exe(cmd[0])
        if not exe:
            continue
        try:
            r = subprocess.run(
                [exe] + cmd[1:],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=cwd,
            )
            out = (r.stdout or r.stderr or "").strip()
            if len(out) > max_output:
                out = out[:max_output] + "\n... (截断)"
            header = f"=== {tool} (退出码 {r.returncode}) ===\n"
            if not out:
                return header + "（无输出）"
            return header + out
        except Exception:
            continue

    return (
        "⚠️ 未检测到 ruff / pyright|basedpyright / mypy / flake8 / pylint。\n"
        "建议安装：pip install ruff\n"
        "类型检查可选：pip install pyright 或 basedpyright\n"
        "或使用 verify_compilation 检查 Python 语法。"
    )