from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin


def format_web_response(
    *,
    url: str,
    final_url: str,
    content_type: str,
    body: bytes,
    raw: bool = False,
    max_chars: int = 8000,
) -> str:
    text = _decode_body(body)
    if not _is_html(content_type, text):
        return _format_non_html(url=url, final_url=final_url, content_type=content_type, text=text, body=body, max_chars=max_chars)
    if raw:
        return _format_raw_html(url=url, final_url=final_url, text=text, max_chars=max_chars)

    extracted = extract_html_markdown(text, base_url=final_url)
    if not extracted.strip():
        raw_preview = _truncate(text, max_chars)
        return (
            f"✅ 获取成功: {url}\n最终 URL: {final_url}\n"
            "提取状态: 正文提取为空，已降级为原始 HTML 预览。\n"
            "提示: 若这是 JS 渲染页面，请改用 browse_web；如需完整 HTML，设置 raw=true。\n\n"
            f"{raw_preview}"
        )
    truncated = _truncate(extracted, max_chars)
    word_count = len(re.findall(r"\w+", extracted, flags=re.UNICODE))
    return (
        f"✅ 获取成功: {url}\n最终 URL: {final_url}\n"
        f"内容类型: {content_type or 'text/html'}\n"
        f"正文长度: {len(extracted)} 字符 / 约 {word_count} 词\n"
        f"正文 Markdown:\n{truncated}"
    )


def extract_html_markdown(text: str, *, base_url: str = "") -> str:
    extracted = _extract_with_trafilatura(text, base_url=base_url)
    if extracted:
        return extracted
    return _FallbackMarkdownExtractor(base_url=base_url).extract(text)


def _extract_with_trafilatura(text: str, *, base_url: str = "") -> str:
    try:
        import trafilatura  # type: ignore[import-untyped]
    except Exception:
        return ""
    try:
        extracted = trafilatura.extract(
            text,
            url=base_url or None,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            favor_precision=False,
        )
    except Exception:
        return ""
    return str(extracted or "").strip()


class _FallbackMarkdownExtractor(HTMLParser):
    _BLOCK_TAGS = {
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}

    def __init__(self, *, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self._title_parts: list[str] = []
        self._parts: list[str] = []
        self._skip_depth = 0
        self._link_stack: list[str] = []

    def extract(self, text: str) -> str:
        self.feed(text)
        self.close()
        body = _normalize_markdown("".join(self._parts))
        title = _normalize_space("".join(self._title_parts)) or self.title
        if title and not body.lower().startswith(f"# {title.lower()}"):
            return f"# {title}\n\n{body}".strip()
        return body

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag == "title":
            self._link_stack.append("__title__")
            return
        if tag == "a":
            href = attr_map.get("href", "").strip()
            self._link_stack.append(urljoin(self.base_url, href) if href else "")
            return
        if tag in {"h1", "h2", "h3"}:
            level = {"h1": "#", "h2": "##", "h3": "###"}[tag]
            self._parts.append(f"\n\n{level} ")
            return
        if tag == "li":
            self._parts.append("\n- ")
            return
        if tag == "br":
            self._parts.append("\n")
            return
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title" and self._link_stack:
            self._link_stack.pop()
            return
        if tag == "a" and self._link_stack:
            href = self._link_stack.pop()
            if href:
                self._parts.append(f"]({href})")
            return
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = _normalize_space(data)
        if not text:
            return
        if self._link_stack and self._link_stack[-1] == "__title__":
            self._title_parts.append(text)
            return
        if self._link_stack:
            self._parts.append(f"[{text}")
        else:
            self._parts.append(text + " ")


def _format_non_html(
    *,
    url: str,
    final_url: str,
    content_type: str,
    text: str,
    body: bytes,
    max_chars: int,
) -> str:
    lowered = (content_type or "").lower()
    if "json" in lowered or lowered.startswith("text/") or "xml" in lowered:
        return (
            f"✅ 获取成功: {url}\n最终 URL: {final_url}\n"
            f"内容类型: {content_type or 'text/plain'}\n长度: {len(text)} 字符\n\n"
            f"{_truncate(text, max_chars)}"
        )
    return (
        f"✅ 获取成功: {url}\n最终 URL: {final_url}\n"
        f"内容类型: {content_type or 'application/octet-stream'}\n"
        f"二进制大小: {len(body)} bytes\n"
        "提示: 该资源不是文本/HTML，未把二进制内容注入上下文。"
    )


def _format_raw_html(*, url: str, final_url: str, text: str, max_chars: int) -> str:
    return (
        f"✅ 获取成功: {url}\n最终 URL: {final_url}\n"
        f"原始 HTML 长度: {len(text)} 字符\n"
        "提示: raw=true 已返回原始内容；通常正文提取模式更适合模型阅读。\n\n"
        f"{_truncate(text, max_chars)}"
    )


def _decode_body(body: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "gb18030", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _is_html(content_type: str, text: str) -> bool:
    lowered = (content_type or "").lower()
    if "html" in lowered:
        return True
    prefix = text[:500].lower()
    return "<!doctype html" in prefix or "<html" in prefix


def _truncate(text: str, max_chars: int) -> str:
    limit = max(1000, int(max_chars or 8000))
    if len(text) <= limit:
        return text
    return (
        text[:limit].rstrip()
        + f"\n\n[... 内容已截断，省略 {len(text) - limit} 字符。需要完整页面可设置 raw=true 或提高 max_chars。]"
    )


def _normalize_markdown(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_space(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()
