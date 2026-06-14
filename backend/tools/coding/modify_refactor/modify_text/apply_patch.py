"""应用 unified diff（优先 git apply / 系统 patch）。"""
import os
import subprocess
import tempfile
from shutil import which
from typing import List, Optional

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, validate_path
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


def _run_patch(cwd: str, patch_text: str, cmd: List[str], label: str) -> Optional[str]:
    try:
        r = subprocess.run(
            cmd,
            input=patch_text,
            text=True,
            capture_output=True,
            timeout=120,
            cwd=cwd,
        )
    except FileNotFoundError:
        return None
    if r.returncode == 0:
        return f"✅ 已应用补丁 ({label})"
    return None


@trace_execution
def apply_patch(patch_text: str, base_dir: str = ".") -> str:
    """
    将 unified diff 应用到工作区。顺序尝试：git apply → patch -p1 → patch -p0。

    说明：复杂合并或二进制补丁可能失败，需人工处理。调用方请勿并行对同一文件打补丁。
    """
    try:
        safe_base, _ = validate_path(base_dir, must_exist=True, allow_create=False)
    except PathSecurityError as e:
        return str(e)
    cwd = str(safe_base)
    if not os.path.isdir(cwd):
        return f"❌ base_dir 不是目录: {base_dir}"

    try:
        msg = _run_patch(
            cwd,
            patch_text,
            ["git", "apply", "--unsafe-paths", "--whitespace=nowarn", "-"],
            "git apply",
        )
        if msg:
            return msg

        patch_exe = which("patch") or which("patch.exe")
        if patch_exe:
            for p_level, label in ((["-p1"], "patch -p1"), (["-p0"], "patch -p0")):
                msg = _run_patch(
                    cwd,
                    patch_text,
                    [patch_exe] + p_level + ["--forward", "--batch", "-"],
                    label,
                )
                if msg:
                    return msg

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".patch", delete=False, encoding="utf-8", dir=cwd
            ) as tmp:
                tmp.write(patch_text)
                tmp_path = tmp.name
            try:
                r2 = subprocess.run(
                    [patch_exe, "-p1", "--forward", "--batch", "-i", tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=cwd,
                )
                if r2.returncode == 0:
                    return "✅ 已应用补丁 (patch -i 文件)"
                err = (r2.stderr or r2.stdout or "").strip()
                return f"❌ patch 失败: {err[:800]}"
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        return (
            "❌ 未找到可用的 git 或 patch。"
            " 请安装 Git for Windows 或将 patch 加入 PATH。"
        )
    except subprocess.TimeoutExpired:
        return "❌ 应用补丁超时"
    except Exception as e:
        return f"❌ apply_patch 异常: {str(e)}"
