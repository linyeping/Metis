"""HTTP GET 抓取 URL：仅 HTTPS，基础 SSRF 防护（对齐 C 资料）。"""
import ipaddress
import re
from typing import Optional
from urllib.parse import urlparse

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution
from backend.tools.coding.network_external.web.web_content import format_web_response

_LOCAL_HOST_PAT = re.compile(
    r"^(127\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|169\.254\.)",
)


def _blocked_host(hostname: str) -> Optional[str]:
    if not hostname:
        return "空主机名"
    h = hostname.lower().strip(".")
    if h == "localhost" or h.endswith(".localhost"):
        return "localhost"
    if h in ("0.0.0.0", "::", "::1"):
        return "保留地址"
    if h.endswith(".local") or h.endswith(".internal"):
        return "mDNS/内网后缀"
    try:
        ip = ipaddress.ip_address(h)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
        ):
            return "私有/环回/链路本地地址"
    except ValueError:
        pass
    if _LOCAL_HOST_PAT.match(h):
        return "疑似 RFC1918/本地网段"
    return None


@trace_execution
def web_fetch(url: str, limit: int = 0, raw: bool = False, max_chars: int = 8000) -> str:
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme.lower() != "https":
            return "❌ WebFetch 仅允许 https:// URL（与 C 一致）"
        if not parsed.hostname:
            return "❌ URL 缺少合法主机名"
        reason = _blocked_host(parsed.hostname)
        if reason:
            return f"❌ 禁止访问该主机（SSRF 防护: {reason}）: {parsed.hostname}"

        import requests

        response = requests.get(
            url,
            timeout=30,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; Metis/3.0; +https://metis.local) "
                    "AppleWebKit/537.36"
                )
            },
        )
        final = response.url
        fp = urlparse(final)
        if fp.scheme.lower() != "https":
            return "❌ 重定向到了非 HTTPS，已中止"
        if fp.hostname and _blocked_host(fp.hostname):
            return "❌ 重定向目标主机被拒绝（SSRF）"

        response.raise_for_status()
        return format_web_response(
            url=url,
            final_url=final,
            content_type=response.headers.get("content-type", ""),
            body=response.content,
            raw=bool(raw),
            max_chars=int(limit or max_chars or 8000),
        )
    except ImportError:
        return "❌ 需要安装 requests: pip install requests"
    except Exception as e:
        err = type(e).__name__
        if "Timeout" in err:
            return f"❌ 请求超时: {url}\n建议: 稍后重试，或用 web_search 获取摘要；JS/交互页可改用 browse_web。"
        message = str(e)
        if "403" in message:
            return f"❌ 获取失败: 403 Forbidden\n建议: 该站可能屏蔽程序访问，试试 web_search 摘要或 browse_web。"
        if "NameResolution" in message or "gaierror" in message or "DNS" in message:
            return f"❌ DNS 解析失败: {url}\n建议: 检查域名拼写，或用 web_search 搜索目标页面。"
        return f"❌ 获取失败: {str(e)}"
