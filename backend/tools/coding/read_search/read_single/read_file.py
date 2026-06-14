"""单文件精读：行范围、行号锚点、分块读取。"""
import zipfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, safe_path_for_read
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution
from backend.core.memory.workspace_state import record_file_read

MAX_FILE_READ_BYTES = 10 * 1024 * 1024
LARGE_FILE_PREVIEW_LINES = 1000


@trace_execution
def read_file(
    file_path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    skipPruning: bool = False,
    explanation: str = "",
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    """
    增强版文件读取 - 对应文档 02: 工具链 Schema 定义

    新增功能:
    - start_line/end_line: 精确行范围读取
    - offset/limit: 与 start_line/end_line 等价的分页读取参数
    - skipPruning: 兼容旧参数；False 时超长文件默认返回前 N 行
    - explanation: 兼容旧参数；当前优先保持原始行号锚点
    
    安全特性:
    - 路径限制在工作区内（防止穿越攻击）
    - 拒绝读取二进制文件
    """
    try:
        # 路径安全验证
        safe_path = safe_path_for_read(file_path)
    except PathSecurityError as e:
        return str(e)

    ext = safe_path.suffix.lower()
    if ext == ".docx":
        result = _read_docx_text(safe_path, file_path)
        if not result.startswith("❌"):
            record_file_read("", str(safe_path))
        return result

    _no_text = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
        ".pdf", ".zip", ".exe", ".dll", ".so", ".dylib", ".woff", ".woff2",
    }
    if ext in _no_text:
        return (
            f"❌ read_file 当前仅支持文本；检测到非文本扩展名 {ext!r}（对齐 C：图片/PDF 需多模态通道）。\n"
            "请勿用 Shell 查看二进制；需要文本时请换用已提取的源码或说明文件。"
        )

    try:
        size = safe_path.stat().st_size
        content, encoding_notice = _read_text_with_limits(safe_path, size)
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        if offset is not None:
            start_line = int(offset)
            if limit is not None:
                end_line = start_line + int(limit) - 1
        elif limit is not None and start_line is not None:
            end_line = int(start_line) + int(limit) - 1

        explicit_range = start_line is not None or end_line is not None
        if limit is not None and int(limit) <= 0:
            return "❌ limit 必须大于 0。请使用 limit=正整数 读取一段行窗口。"

        # 行范围读取
        if explicit_range:
            if total_lines == 0:
                record_file_read("", str(safe_path))
                return _format_numbered_output(
                    file_path,
                    [],
                    0,
                    0,
                    0,
                    encoding_notice=encoding_notice,
                )
            start = max(1, int(start_line or 1))
            end = int(end_line or total_lines)
            if total_lines and start > total_lines:
                record_file_read("", str(safe_path))
                return _format_numbered_output(
                    file_path,
                    [],
                    start,
                    start,
                    total_lines,
                    encoding_notice=encoding_notice,
                    pagination_hint=(
                        f"[提示: 起始行 {start} 超过文件总行数 {total_lines}。"
                        "请使用更小的 offset 或先读取文件末尾附近的行。]"
                    ),
                )
            if total_lines:
                end = max(start, min(end, total_lines))
            selected = lines[start - 1 : end]
            record_file_read("", str(safe_path))
            return _format_numbered_output(
                file_path,
                selected,
                start,
                end if selected else min(end, total_lines),
                total_lines,
                encoding_notice=encoding_notice,
                pagination_hint=_pagination_hint(end, total_lines, limit),
            )

        if total_lines == 0:
            record_file_read("", str(safe_path))
            return _format_numbered_output(
                file_path,
                [],
                0,
                0,
                0,
                encoding_notice=encoding_notice,
            )

        if not skipPruning and total_lines > LARGE_FILE_PREVIEW_LINES:
            end = LARGE_FILE_PREVIEW_LINES
            selected = lines[:end]
            record_file_read("", str(safe_path))
            return _format_numbered_output(
                file_path,
                selected,
                1,
                end,
                total_lines,
                encoding_notice=encoding_notice,
                pagination_hint=_pagination_hint(end, total_lines, LARGE_FILE_PREVIEW_LINES),
            )

        record_file_read("", str(safe_path))
        return _format_numbered_output(
            file_path,
            lines,
            1,
            total_lines,
            total_lines,
            encoding_notice=encoding_notice,
        )

    except Exception as e:
        return f"❌ 读取失败: {str(e)}"


def _read_docx_text(safe_path: Path, original_path: str) -> str:
    try:
        with zipfile.ZipFile(safe_path) as archive:
            document_xml = archive.read("word/document.xml")
    except Exception as exc:
        return f"❌ 读取 docx 失败: {type(exc).__name__}: {exc}"

    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        return f"❌ 解析 docx 失败: {exc}"

    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{{{namespace['w']}}}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{{{namespace['w']}}}tab":
                parts.append("\t")
            elif node.tag == f"{{{namespace['w']}}}br":
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)

    content = "\n".join(paragraphs)
    if not content:
        return f"=== {original_path} (docx) ===\n[空文档或未找到正文文本]"
    return f"=== {original_path} (docx) ===\n{content}"


def _format_numbered_output(
    file_path: str,
    lines: list[str],
    start_line: int,
    end_line: int,
    total_lines: int,
    *,
    encoding_notice: str = "",
    pagination_hint: str = "",
) -> str:
    header = f"=== {file_path} (lines {start_line}-{end_line} of {total_lines}) ==="
    body = _numbered_lines(lines, start_line)
    parts = [header]
    if encoding_notice:
        parts.append(encoding_notice.rstrip("\n"))
    if body:
        parts.append(body.rstrip("\n"))
    if pagination_hint:
        parts.append(pagination_hint)
    return "\n".join(parts)


def _numbered_lines(lines: list[str], start_line: int) -> str:
    return "".join(
        f"{line_no:>4}→{line}"
        for line_no, line in enumerate(lines, start=start_line)
    )


def _pagination_hint(end_line: int, total_lines: int, limit: Optional[int]) -> str:
    if total_lines <= end_line:
        return ""
    next_offset = end_line + 1
    next_limit = int(limit or LARGE_FILE_PREVIEW_LINES)
    remaining = total_lines - end_line
    return (
        f"[提示: 文件共 {total_lines} 行，当前显示到第 {end_line} 行。"
        f"继续读取请调用 read_file(offset={next_offset}, limit={min(next_limit, remaining)})。"
        "编辑时不要把左侧行号和箭头复制进 search_text/content。]"
    )


def _decode_text(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8"), ""
    except UnicodeDecodeError:
        for encoding in ("utf-8-sig", "gbk", "gb2312", "shift_jis", "latin-1"):
            try:
                return raw.decode(encoding), f"[注意：文件编码为 {encoding}，非 UTF-8]\n"
            except (UnicodeDecodeError, LookupError):
                continue
    return raw.decode("utf-8", errors="replace"), "[注意：文件包含无法识别的字节，已替换不可解码字符]\n"


def _read_text_with_limits(path: Path, size: int) -> tuple[str, str]:
    if size <= MAX_FILE_READ_BYTES:
        return _decode_text(path.read_bytes())

    with path.open("rb") as handle:
        raw = handle.read(MAX_FILE_READ_BYTES)
    text, notice = _decode_text(raw)
    preview = "".join(text.splitlines(keepends=True)[:LARGE_FILE_PREVIEW_LINES])
    mb = max(1, size // 1024 // 1024)
    return (
        f"文件过大 ({mb}MB)，仅显示前 {LARGE_FILE_PREVIEW_LINES} 行。\n{preview}",
        notice,
    )


@trace_execution
def read_file_chunk(file_path: str, chunk_size: int = 8192, max_chunks: int = 10) -> str:
    """
    分块读取大文件（路径校验与 read_file 一致，尊重执行边界）。
    """
    try:
        safe_path = safe_path_for_read(file_path)
    except PathSecurityError as e:
        return str(e)

    try:
        if not safe_path.is_file():
            return f"❌ 不是可读文件: {file_path}"

        file_size = safe_path.stat().st_size
        result = f"📖 文件分块读取: {file_path}\n文件大小: {file_size} 字节\n"

        chunks_read = 0
        total_read = 0

        with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
            while chunks_read < max_chunks:
                chunk = f.read(chunk_size)
                if not chunk:
                    break

                result += (
                    f"\n--- 块 {chunks_read + 1} (位置: {total_read}-{total_read + len(chunk)}) ---\n"
                )
                result += chunk[:500]  # 限制每块显示长度
                if len(chunk) > 500:
                    result += f"\n... (块过长，仅显示前 500 字符)"

                total_read += len(chunk)
                chunks_read += 1

        if total_read < file_size:
            result += (
                f"\n\n📊 读取统计: 已读取 {total_read}/{file_size} 字节 ({chunks_read} 块)"
            )

        return result
    except Exception as e:
        return f"❌ 分块读取失败: {str(e)}"
