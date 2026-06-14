"""尽力调用本机 linter（ruff JSON / pyright 或 basedpyright JSON / mypy / flake8 / pylint），否则回退提示。"""
import json
import os
import subprocess
from shutil import which
from typing import Optional

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


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
    """
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
