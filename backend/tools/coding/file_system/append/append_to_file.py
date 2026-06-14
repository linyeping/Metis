"""在文件末尾追加内容。"""
from typing import Literal, Optional

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, safe_path_for_write
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def append_to_file(
    file_path: str,
    content: str,
    *,
    encoding: str = "utf-8",
    newline: Optional[Literal["", "\n", "\r\n"]] = None,
    ensure_parent_dir: bool = True,
) -> str:
    """
    向已有文件末尾追加文本；文件不存在时创建。
    """
    try:
        try:
            target = safe_path_for_write(file_path)
        except PathSecurityError as e:
            return str(e)

        if ensure_parent_dir:
            target.parent.mkdir(parents=True, exist_ok=True)

        mode = "a"
        kwargs = {"encoding": encoding}
        if newline is not None:
            kwargs["newline"] = newline

        with open(target, mode, **kwargs) as f:
            f.write(content)

        size = target.stat().st_size
        return (
            f"✅ 追加成功: {target}\n"
            f"追加长度: {len(content)} 字符\n文件大小: {size} 字节"
        )
    except Exception as e:
        return f"❌ 追加失败: {str(e)}"
