"""Final report review gates for Metis Research."""
from __future__ import annotations

import re
from typing import Any

_REPORT_NAV_PATTERNS = [
    r"Evidence Opened|Read Failures|Status:|Providers?:",
    r"下载APP|登录|注册新账号|跳过此内容|Use your Google Account",
    r"On This Page|Main menu|Navigation|Get API key",
]


def review_report(report: str, question: str, evidence: list[dict[str, Any]], question_type: dict[str, Any] | None = None) -> dict[str, Any]:
    text = str(report or "")
    qtype = str((question_type or {}).get("type") or "")
    issues: list[str] = []
    if len(text.strip()) < 120:
        issues.append("report too short")
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _REPORT_NAV_PATTERNS):
        issues.append("report contains crawler/tool/navigation noise")
    if has_empty_headings(text):
        issues.append("report contains empty headings")
    if qtype == "official_release_check":
        official_count = sum(1 for item in evidence if str(item.get("source_quality") or "") == "official")
        if official_count == 0:
            # A report can still be valid, but only if it explicitly states no
            # official evidence was found.
            if not re.search(r"未发现.*官方.*(?:发布|证据)|no official .* evidence", text, flags=re.IGNORECASE):
                issues.append("official release check lacks official evidence conclusion")
    return {"ok": not issues, "issues": issues}


def has_empty_headings(report: str) -> bool:
    lines = str(report or "").splitlines()
    heading_indices = [index for index, line in enumerate(lines) if re.match(r"^#{1,6}\s+\S", line.strip())]
    for pos, index in enumerate(heading_indices):
        next_index = heading_indices[pos + 1] if pos + 1 < len(heading_indices) else len(lines)
        body = "\n".join(line for line in lines[index + 1 : next_index] if line.strip())
        if next_index - index > 1 and len(body.strip()) < 12:
            return True
    return False


def build_official_absence_report(question: str, evidence: list[dict[str, Any]], official_domains: list[str]) -> str:
    rumor_rows = [
        item
        for item in evidence
        if str(item.get("source_quality") or "") != "official"
    ]
    lines = [
        f"# {question}",
        "",
        "## 结论",
        "",
        "截至本次检索，未发现官方发布证据。也就是说，不能把第三方文章、社交转述、金融/币圈站点或 SEO 聚合页当作官方发布依据。",
        "",
        "## 已优先检查的官方渠道",
        "",
    ]
    if official_domains:
        for domain in official_domains:
            lines.append(f"- `{domain}`")
    else:
        lines.append("- 未识别到明确官方域名，需补充 source policy。")
    lines.extend(["", "## 传闻和误称来源", ""])
    if rumor_rows:
        for item in rumor_rows[:10]:
            title = str(item.get("title") or item.get("domain") or item.get("url") or "来源").strip()
            domain = str(item.get("domain") or "").strip()
            snippet = re.sub(r"\s+", " ", str(item.get("snippet") or item.get("text") or "")).strip()[:260]
            reason = str(item.get("source_reason") or item.get("source_role") or "非官方来源").strip()
            lines.append(f"- **{title}**{f'（{domain}）' if domain else ''}：{reason}。{snippet}")
    else:
        lines.append("- 本次没有保留下可复查的传闻来源；低质量、无关或页面壳内容已被过滤。")
    lines.extend(
        [
            "",
            "## 判断方法",
            "",
            "1. 先查官方域名和官方文档/博客/变更日志。",
            "2. 没有官方证据时，不把第三方报道改写成“已发布”。",
            "3. 第三方内容只用于解释传闻或误称来源，不能支撑核心事实结论。",
        ]
    )
    return "\n".join(lines).strip()
