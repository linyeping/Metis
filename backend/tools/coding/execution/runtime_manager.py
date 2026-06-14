# -*- coding: utf-8 -*-
"""Detect, install, and prepare development runtimes.

The module is intentionally conservative: command pre-checks only explain what
is missing. Actual installs happen only through the explicit install tool.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from backend.tools.coding.foundation.core_mechanisms.log_config import logger
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


class RuntimeStatus(Enum):
    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    INSTALLED = "installed"


@dataclass
class RuntimeInfo:
    name: str
    cli_names: list[str]
    winget_id: str
    version_cmd: list[str]
    category: str
    description: str
    status: RuntimeStatus = RuntimeStatus.NOT_FOUND
    path: Optional[str] = None
    version: Optional[str] = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "status": self.status.value,
            "path": self.path,
            "version": self.version,
            "winget_id": self.winget_id,
            "category": self.category,
            "description": self.description,
        }


KNOWN_RUNTIMES: list[RuntimeInfo] = [
    RuntimeInfo("Python", ["python", "python3", "py"], "Python.Python.3.12", ["python", "--version"], "language", "Python 3 interpreter"),
    RuntimeInfo("Node.js", ["node"], "OpenJS.NodeJS.LTS", ["node", "--version"], "language", "Node.js JavaScript runtime"),
    RuntimeInfo("Go", ["go"], "GoLang.Go", ["go", "version"], "language", "Go programming language"),
    RuntimeInfo("Rust", ["cargo", "rustc"], "Rustlang.Rustup", ["rustc", "--version"], "language", "Rust toolchain"),
    RuntimeInfo("Java", ["java", "javac"], "Microsoft.OpenJDK.21", ["java", "--version"], "language", "Java Development Kit"),
    RuntimeInfo("Git", ["git"], "Git.Git", ["git", "--version"], "tool", "Git version control"),
    RuntimeInfo("npm", ["npm"], "OpenJS.NodeJS.LTS", ["npm", "--version"], "tool", "Node.js package manager"),
    RuntimeInfo("pip", ["pip", "pip3"], "Python.Python.3.12", ["pip", "--version"], "tool", "Python package manager"),
    RuntimeInfo("CMake", ["cmake"], "Kitware.CMake", ["cmake", "--version"], "compiler", "CMake build system"),
    RuntimeInfo("GCC/MinGW", ["gcc", "g++"], "MSYS2.MSYS2", ["gcc", "--version"], "compiler", "GNU C/C++ compiler"),
]


PROJECT_MARKERS: dict[str, list[str]] = {
    ".py": ["Python"],
    ".ipynb": ["Python"],
    ".js": ["Node.js"],
    ".jsx": ["Node.js"],
    ".ts": ["Node.js"],
    ".tsx": ["Node.js"],
    ".go": ["Go"],
    ".rs": ["Rust"],
    ".java": ["Java"],
    ".c": ["GCC/MinGW", "CMake"],
    ".cc": ["GCC/MinGW", "CMake"],
    ".cpp": ["GCC/MinGW", "CMake"],
    "package.json": ["Node.js", "npm", "Git"],
    "pyproject.toml": ["Python", "pip", "Git"],
    "setup.py": ["Python", "pip", "Git"],
    "requirements.txt": ["Python", "pip"],
    "Cargo.toml": ["Rust", "Git"],
    "go.mod": ["Go", "Git"],
    "pom.xml": ["Java", "Git"],
    "CMakeLists.txt": ["CMake", "GCC/MinGW"],
    "Makefile": ["GCC/MinGW"],
    ".git": ["Git"],
    ".gitignore": ["Git"],
}


COMMAND_RUNTIME_MAP: dict[str, str] = {
    "python": "Python",
    "python3": "Python",
    "py": "Python",
    "pip": "Python",
    "pip3": "Python",
    "node": "Node.js",
    "npm": "Node.js",
    "npx": "Node.js",
    "git": "Git",
    "go": "Go",
    "cargo": "Rust",
    "rustc": "Rust",
    "java": "Java",
    "javac": "Java",
    "gcc": "GCC/MinGW",
    "g++": "GCC/MinGW",
    "cmake": "CMake",
    "make": "GCC/MinGW",
}


def _runtime_by_name(name: str) -> Optional[RuntimeInfo]:
    normalized = str(name or "").strip().lower()
    aliases = {
        "node": "node.js",
        "nodejs": "node.js",
        "gcc": "gcc/mingw",
        "mingw": "gcc/mingw",
    }
    normalized = aliases.get(normalized, normalized)
    for runtime in KNOWN_RUNTIMES:
        if runtime.name.lower() == normalized:
            return runtime
    return None


def _first_shell_word(command: str) -> str:
    text = str(command or "").strip()
    if not text:
        return ""
    match = re.match(r"^[&\s]*(?:cmd\s+/c\s+|powershell(?:\.exe)?\s+-command\s+)?(?P<word>\"[^\"]+\"|'[^']+'|\S+)", text, re.I)
    if not match:
        return ""
    word = match.group("word").strip("\"'")
    name = Path(word).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def _version_for(runtime: RuntimeInfo, cli_name: str) -> tuple[bool, str]:
    cmd = list(runtime.version_cmd)
    if cmd:
        cmd[0] = cli_name
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except Exception:
        return False, "unknown"
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output.splitlines()[0][:160] if output else "unknown"


def detect_runtime(runtime: RuntimeInfo) -> RuntimeInfo:
    for cli_name in runtime.cli_names:
        exe_path = shutil.which(cli_name)
        if not exe_path:
            continue
        version_ok, version = _version_for(runtime, cli_name)
        if not version_ok and cli_name in {"py", "python", "python3", "node", "npm", "git", "go", "cargo", "rustc", "java", "javac"}:
            continue
        runtime.status = RuntimeStatus.AVAILABLE
        runtime.path = exe_path
        runtime.version = version
        return runtime
    runtime.status = RuntimeStatus.NOT_FOUND
    runtime.path = None
    runtime.version = None
    return runtime


def detect_all_runtimes() -> list[RuntimeInfo]:
    results: list[RuntimeInfo] = []
    for runtime in KNOWN_RUNTIMES:
        copy_rt = copy.deepcopy(runtime)
        results.append(detect_runtime(copy_rt))
    return results


def detect_project_requirements(workspace_root: str) -> list[RuntimeInfo]:
    root = Path(workspace_root or ".").expanduser()
    try:
        root = root.resolve()
    except Exception:
        return []
    if not root.is_dir():
        return []

    needed: set[str] = set()
    for marker, runtimes in PROJECT_MARKERS.items():
        if not marker.startswith(".") and "." not in marker:
            continue
        if (root / marker).exists():
            needed.update(runtimes)

    for path in root.glob("*"):
        if path.is_file() and path.suffix.lower() in PROJECT_MARKERS:
            needed.update(PROJECT_MARKERS[path.suffix.lower()])
    for path in root.glob("*/*"):
        if path.is_file() and path.suffix.lower() in PROJECT_MARKERS:
            needed.update(PROJECT_MARKERS[path.suffix.lower()])

    results: list[RuntimeInfo] = []
    for name in sorted(needed):
        runtime = _runtime_by_name(name)
        if runtime:
            results.append(detect_runtime(copy.deepcopy(runtime)))
    return results


def _winget_available() -> bool:
    return os.name == "nt" and shutil.which("winget") is not None


def refresh_path_from_registry() -> None:
    if os.name != "nt":
        return
    try:
        import winreg

        values: list[str] = []
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as key:
            values.append(str(winreg.QueryValueEx(key, "Path")[0]))
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                values.append(str(winreg.QueryValueEx(key, "Path")[0]))
        except FileNotFoundError:
            pass

        current = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        seen = {p.lower() for p in current}
        for chunk in values:
            for entry in chunk.split(os.pathsep):
                entry = entry.strip()
                if entry and entry.lower() not in seen:
                    current.append(entry)
                    seen.add(entry.lower())
        os.environ["PATH"] = os.pathsep.join(current)
    except Exception as exc:
        logger.warning("PATH refresh failed: %s", exc)


def install_runtime(name: str, timeout: int = 300) -> str:
    runtime = _runtime_by_name(name)
    if not runtime:
        available = ", ".join(rt.name for rt in KNOWN_RUNTIMES)
        return f"未知运行时: {name}\n可安装: {available}"

    runtime = detect_runtime(copy.deepcopy(runtime))
    if runtime.status == RuntimeStatus.AVAILABLE:
        return f"{runtime.name} 已安装: {runtime.version or 'found'} ({runtime.path})"

    if not _winget_available():
        return (
            f"无法自动安装 {runtime.name}: winget 不可用。\n"
            f"可手动运行: winget install --id {runtime.winget_id}\n"
            "如果 winget 缺失，请先从 Microsoft Store 安装 App Installer。"
        )

    cmd = [
        "winget",
        "install",
        "--id",
        runtime.winget_id,
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--silent",
    ]
    logger.info("Installing runtime with winget: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"{runtime.name} 安装超时。可手动运行: winget install --id {runtime.winget_id}"
    except Exception as exc:
        return f"{runtime.name} 安装异常: {exc}"

    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part).strip()
    if result.returncode != 0:
        lowered = output.lower()
        if "administrator" in lowered or "elevation" in lowered or "admin" in lowered:
            return f"{runtime.name} 安装需要管理员权限。请以管理员身份运行: winget install --id {runtime.winget_id}"
        return f"{runtime.name} 安装失败 (退出码 {result.returncode})\n{output[:1200]}"

    refresh_path_from_registry()
    verified = detect_runtime(copy.deepcopy(runtime))
    if verified.status == RuntimeStatus.AVAILABLE:
        verified.status = RuntimeStatus.INSTALLED
        return f"{verified.name} 安装成功: {verified.version or 'found'} ({verified.path})"
    return (
        f"{runtime.name} 安装命令已完成，但当前进程 PATH 仍未找到它。\n"
        "请重启 Metis 或终端后重试。"
    )


def install_multiple_runtimes(names: list[str]) -> str:
    return "\n\n---\n\n".join(install_runtime(name) for name in names)


def setup_project_environment(workspace_root: str) -> str:
    root = Path(workspace_root or ".").expanduser()
    try:
        root = root.resolve()
    except Exception as exc:
        return f"工作区路径解析失败: {workspace_root} ({exc})"
    if not root.is_dir():
        return f"工作区不存在: {root}"

    requirements = detect_project_requirements(str(root))
    if not requirements:
        return f"未在 {root.name} 中检测到已知项目类型。"

    lines = [f"项目环境检查: {root}", ""]
    missing = [rt for rt in requirements if rt.status == RuntimeStatus.NOT_FOUND]
    available = [rt for rt in requirements if rt.status == RuntimeStatus.AVAILABLE]
    if available:
        lines.append("已安装:")
        lines.extend(f"- {rt.name}: {rt.version or 'found'}" for rt in available)
    if missing:
        lines.append("")
        lines.append("缺少:")
        lines.extend(f"- {rt.name}: winget install --id {rt.winget_id}" for rt in missing)
        lines.append("")
        lines.append("开始安装缺失运行时:")
        for rt in missing:
            lines.append(f"- {rt.name}: {install_runtime(rt.name).splitlines()[0]}")

    refresh_path_from_registry()
    lines.append("")
    lines.append("项目依赖:")
    if (root / "requirements.txt").exists() and shutil.which("pip"):
        result = subprocess.run(["pip", "install", "-r", "requirements.txt"], cwd=str(root), capture_output=True, text=True, timeout=180)
        lines.append(f"- pip install -r requirements.txt: {'成功' if result.returncode == 0 else '失败'}")
    if (root / "package.json").exists() and shutil.which("npm"):
        result = subprocess.run(["npm", "install"], cwd=str(root), capture_output=True, text=True, timeout=240)
        lines.append(f"- npm install: {'成功' if result.returncode == 0 else '失败'}")
    if (root / "Cargo.toml").exists() and shutil.which("cargo"):
        result = subprocess.run(["cargo", "check"], cwd=str(root), capture_output=True, text=True, timeout=180)
        lines.append(f"- cargo check: {'成功' if result.returncode == 0 else '失败'}")
    if lines[-1] == "项目依赖:":
        lines.append("- 未检测到需要自动安装的项目依赖，或对应包管理器不可用。")
    return "\n".join(lines)


def check_command_runtime(command: str) -> Optional[dict[str, str]]:
    first = _first_shell_word(command)
    if not first:
        return None
    runtime_name = COMMAND_RUNTIME_MAP.get(first)
    if not runtime_name:
        return None
    runtime = _runtime_by_name(runtime_name)
    if not runtime:
        return None
    detected = detect_runtime(copy.deepcopy(runtime))
    if detected.status == RuntimeStatus.AVAILABLE and shutil.which(first):
        return None
    return {
        "runtime": runtime.name,
        "winget_id": runtime.winget_id,
        "install_cmd": f"winget install --id {runtime.winget_id}",
        "description": runtime.description,
        "message": (
            f"需要 {runtime.name}，但当前系统没有找到可用命令 `{first}`。\n"
            f"自动安装工具: install_dev_runtime(\"{runtime.name}\")\n"
            f"手动安装命令: winget install --id {runtime.winget_id}"
        ),
    }


def _default_workspace(workspace: str) -> str:
    if workspace and workspace != ".":
        return workspace
    try:
        from backend.tools.coding.foundation.core_mechanisms.path_security import get_workspace_root

        return str(get_workspace_root())
    except Exception:
        return "."


@trace_execution
def check_dev_environment(workspace: str = ".") -> str:
    lines = ["开发环境检查", ""]
    runtimes = detect_all_runtimes()
    for category, title in [("language", "编程语言"), ("tool", "开发工具"), ("compiler", "编译工具")]:
        grouped = [rt for rt in runtimes if rt.category == category]
        if not grouped:
            continue
        lines.append(f"[{title}]")
        for rt in grouped:
            if rt.status == RuntimeStatus.AVAILABLE:
                lines.append(f"- {rt.name}: 已安装 ({rt.version or rt.path})")
            else:
                lines.append(f"- {rt.name}: 未安装，可运行 winget install --id {rt.winget_id}")
        lines.append("")

    lines.append(f"winget: {'可用' if _winget_available() else '不可用'}")
    workspace_path = _default_workspace(workspace)
    requirements = detect_project_requirements(workspace_path)
    if requirements:
        lines.append("")
        lines.append(f"项目需要 ({Path(workspace_path).name}):")
        for rt in requirements:
            label = "已安装" if rt.status == RuntimeStatus.AVAILABLE else f"缺少，winget install --id {rt.winget_id}"
            lines.append(f"- {rt.name}: {label}")
    return "\n".join(lines).strip()


@trace_execution
def install_dev_runtime(runtime_name: str) -> str:
    return install_runtime(runtime_name)


@trace_execution
def setup_workspace(workspace: str = ".") -> str:
    return setup_project_environment(_default_workspace(workspace))
