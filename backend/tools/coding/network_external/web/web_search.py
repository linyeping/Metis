"""简易网页搜索：DuckDuckGo HTML（无官方 API，仅供开发辅助）。"""
import re
from html import unescape
from urllib.parse import quote_plus

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def web_search(query: str, max_results: int = 5) -> str:
    try:
        import requests
        url = "https://lite.duckduckgo.com/lite/?q=" + quote_plus(query)
        r = requests.get(url, timeout=20, headers={"User-Agent": "MiroAgent/1.0"})
        r.raise_for_status()
        text = r.text
        links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', text)
        lines = [f"=== 搜索: {query!r} (DDG lite) ==="]
        n = 0
        for href, title in links:
            if n >= max_results:
                break
            if href.startswith("http") and "duckduckgo" not in href:
                lines.append(f"- {unescape(title.strip())}\n  {href}")
                n += 1
        if n == 0:
            lines.append("（未解析到外链，可能页面结构变化）")
        return "\n".join(lines)
    except ImportError:
        return "❌ 需要 requests: pip install requests"
    except Exception as e:
        return f"❌ web_search 失败: {str(e)}"
