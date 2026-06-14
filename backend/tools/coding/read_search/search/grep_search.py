"""优先使用 ripgrep (rg)；不可用时回退到 search_basic。"""
import os
import shutil
import subprocess
from typing import Optional

from backend.tools.coding.foundation.core_mechanisms.path_security import (
    PathSecurityError,
    get_workspace_root,
    validate_search_scope,
)
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def grep_search(
    pattern: str,
    path: str = ".",
    glob_pattern: Optional[str] = None,
    max_results: int = 50,
    case_sensitive: bool = False,
    *,
    glob: Optional[str] = None,
    output_mode: str = "content",
    head_limit: Optional[int] = None,
    multiline: bool = False,
    context_lines: Optional[int] = None,
    before_context: Optional[int] = None,
    after_context: Optional[int] = None,
    type_filter: Optional[str] = None,
) -> str:
    """
    基于 rg 的极速正则搜索（c 资料推荐）；未安装 rg 时回退到 findstr/grep 风格搜索。

    Args:
        pattern: 搜索模式（rg 中按正则解释；回退路径下由 findstr/grep 解释）。
        path: 根目录或单文件。
        glob_pattern: 传给 rg 的 --glob；glob 为其别名（C 风格）。
        max_results: content 模式最多返回的匹配行数。
        case_sensitive: False 时 rg 使用 -i。
        output_mode: content | files_with_matches | count（与 C 对齐；无 rg 时仅尽力而为）。
        head_limit: 覆盖 max_results 的上限（取二者较小非空值）。
        multiline: 启用多行匹配模式（rg -U）。
        context_lines: 显示匹配行前后各 N 行上下文（rg -C）。
        before_context: 显示匹配行前 N 行（rg -B）。
        after_context: 显示匹配行后 N 行（rg -A）。
        type_filter: 文件类型过滤（rg -t，如 'py', 'js', 'md'）。
    """
    gpat = glob_pattern or glob
    lim = head_limit if head_limit is not None else max_results
    if lim is not None and lim < 0:
        lim = 0
    mode = (output_mode or "content").strip().lower()

    try:
        try:
            scope = validate_search_scope(path)
        except PathSecurityError as e:
            return str(e)
        path = str(scope)
        ws_root = str(get_workspace_root())

        rg_exe = shutil.which("rg")
        if rg_exe:
            cmd: list = [rg_exe, "--color", "never"]
            
            # 输出模式
            if mode == "files_with_matches":
                cmd.append("-l")
            elif mode == "count":
                cmd.append("-c")
            else:
                cmd.extend(["-n", "--no-heading"])
            
            # 大小写
            if not case_sensitive:
                cmd.append("-i")
            
            # 多行模式
            if multiline:
                cmd.append("-U")
            
            # 上下文行
            if context_lines is not None and context_lines > 0:
                cmd.extend(["-C", str(context_lines)])
            else:
                if before_context is not None and before_context > 0:
                    cmd.extend(["-B", str(before_context)])
                if after_context is not None and after_context > 0:
                    cmd.extend(["-A", str(after_context)])
            
            # 文件类型过滤
            if type_filter:
                cmd.extend(["-t", type_filter])
            
            # Glob 模式
            if gpat:
                cmd.extend(["--glob", gpat])
            
            cmd.extend([pattern, path])
            
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=ws_root,
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            if proc.returncode not in (0, 1):
                return f"❌ rg 执行异常 (退出码 {proc.returncode}): {err or out or '无输出'}"
            if not out:
                return f"未找到匹配 '{pattern}' 的内容 (rg, mode={mode})"

            if mode == "files_with_matches":
                files = [ln.strip() for ln in out.split("\n") if ln.strip()]
                if lim is not None and lim > 0 and len(files) > lim:
                    files = files[:lim]
                    extra = f"\n\n[仅显示前 {lim} 个文件，请缩小范围或提高 head_limit]"
                else:
                    extra = ""
                return f"=== rg 文件列表 (files_with_matches): '{pattern}' ===\n" + "\n".join(files) + extra

            if mode == "count":
                lines = [ln for ln in out.split("\n") if ln.strip()]
                if lim is not None and lim > 0 and len(lines) > lim:
                    lines = lines[:lim]
                    extra = f"\n\n[仅显示前 {lim} 条计数，请提高 head_limit 或缩小 path]"
                else:
                    extra = ""
                return f"=== rg 计数 (count): '{pattern}' ===\n" + "\n".join(lines) + extra

            lines = out.split("\n")
            if lim is not None and lim > 0 and len(lines) > lim:
                out = "\n".join(lines[:lim])
                out += f"\n\n[结果过多，仅显示前 {lim} 条]"
            
            # 添加使用的选项说明
            options_used = []
            if multiline:
                options_used.append("multiline")
            if context_lines:
                options_used.append(f"context={context_lines}")
            elif before_context or after_context:
                if before_context:
                    options_used.append(f"before={before_context}")
                if after_context:
                    options_used.append(f"after={after_context}")
            if type_filter:
                options_used.append(f"type={type_filter}")
            
            options_str = f" [{', '.join(options_used)}]" if options_used else ""
            return f"=== rg 搜索结果{options_str}: '{pattern}' ===\n{out}"

        from backend.tools.coding.read_search.search.search_basic import search_in_files

        fp = gpat or "*.py"
        if mode != "content":
            return (
                search_in_files(pattern, fp, path)
                + f"\n\n[提示] 当前环境无 rg，output_mode={mode!r} 已退化为基础文本搜索输出。"
            )
        
        # 无 rg 时的增强选项提示
        if multiline or context_lines or before_context or after_context or type_filter:
            return (
                search_in_files(pattern, fp, path)
                + "\n\n[提示] 当前环境无 rg，multiline/context/type 等高级选项已忽略。建议安装 ripgrep。"
            )
        
        return search_in_files(pattern, fp, path)
    except subprocess.TimeoutExpired:
        return "❌ 搜索超时（30s）"
    except Exception as e:
        return f"❌ 搜索失败: {str(e)}"
