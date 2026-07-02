"""Deep Research pipeline — plan → query → gather → grade → outline → synthesize.

对话驱动的两阶段深度研究：
  1. deep_research_plan(question)  → 生成研究计划（brief + 子问题），在聊天里给用户确认
  2. deep_research_run(question, plan_json) → 执行完整 6 阶段流水线并落地 research job

设计要点（对齐 Gemini Deep Research / LangChain Open Deep Research 的经验）：
  - 研究阶段并行（每个子问题一个 worker，隔离上下文）
  - 写作阶段单次 LLM 调用（避免分节并行导致风格割裂）
  - 任意 LLM 阶段失败 → 降级到 metis_search_research，保证不劣化
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import time
from typing import Any

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution
from backend.tools.coding.network_external.web.research_jobs import save_research_activity_job
from backend.tools.coding.network_external.web.search_broker import (
    _canonical_url_key,
    _clamp_int,
    _normalize_page_text,
    _provider_chain_for_request,
    _source_domain,
    _source_title,
    _source_url,
    _truncate,
    format_research_response,
    metis_page_read,
    metis_search_query,
    metis_search_research,
)

_SCHEMA = "metis.research_activity.v1"
_MAX_SUBQUESTIONS = 6
_MIN_SUBQUESTIONS = 2
_MAX_QUERIES_PER_SUB = 3
_GRADE_KEEP_THRESHOLD = 0.35


# ─────────────────────────────────────────────────────────────────────────────
# LLM 访问（延迟 import 防循环依赖）
# ─────────────────────────────────────────────────────────────────────────────
def _llm_chat(messages: list[dict[str, Any]], *, temperature: float = 0.3, max_tokens: int = 2048, timeout: float = 60.0) -> str:
    """Single LLM call returning plain text. Raises on failure (caller handles fallback)."""
    from backend.runtime.agent_loop import _create_backend
    from backend.web.app import _load_config_for_workspace, _active_workspace_root

    config = _load_config_for_workspace(_active_workspace_root())
    backend = _create_backend(config)
    response = backend.chat(messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
    return str(response.content or "").strip()


def _extract_json(text: str) -> Any:
    """Best-effort JSON extraction from an LLM response (handles ```json fences)."""
    value = str(text or "").strip()
    if not value:
        return None
    # strip code fences
    fence = re.search(r"```(?:json)?\s*(.+?)```", value, flags=re.DOTALL)
    if fence:
        value = fence.group(1).strip()
    # try direct parse
    try:
        return json.loads(value)
    except Exception:
        pass
    # try to locate the first {...} or [...] block
    for opener, closer in (("[", "]"), ("{", "}")):
        start = value.find(opener)
        end = value.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(value[start : end + 1])
            except Exception:
                continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 1：PLAN — 生成研究计划
# ─────────────────────────────────────────────────────────────────────────────
def _generate_plan(question: str) -> dict[str, Any]:
    """Produce a research brief + sub-questions. Raises on LLM failure."""
    prompt = (
        "你是一个深度研究规划助手。针对用户的研究问题，生成一个结构化研究计划。\n"
        "输出严格的 JSON，格式：\n"
        '{\n'
        '  "brief": "一句话研究目标（作为整个研究的北极星）",\n'
        '  "subquestions": ["子问题1", "子问题2", "子问题3"]\n'
        '}\n'
        f"子问题应彼此独立、覆盖问题的不同维度，数量 {_MIN_SUBQUESTIONS}~{_MAX_SUBQUESTIONS} 个。\n"
        "只输出 JSON，不要解释。"
    )
    raw = _llm_chat(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"研究问题：{question}"},
        ],
        temperature=0.4,
        max_tokens=1024,
    )
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise ValueError("plan generation returned non-dict")
    brief = str(data.get("brief") or question).strip()
    subs_raw = data.get("subquestions")
    subs = [str(item).strip() for item in subs_raw if str(item or "").strip()] if isinstance(subs_raw, list) else []
    if not subs:
        raise ValueError("plan generation returned no subquestions")
    subs = subs[:_MAX_SUBQUESTIONS]
    return {"brief": brief, "subquestions": subs}


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 2：QUERY — 每个子问题扩展搜索词
# ─────────────────────────────────────────────────────────────────────────────
def _expand_queries(subquestion: str) -> list[str]:
    """Expand a sub-question into 2~3 search query variants. Falls back to the sub-question itself."""
    try:
        raw = _llm_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "把研究子问题转换成 2~3 条高质量搜索引擎查询词。\n"
                        "查询词应包含关键实体、年份或版本等具体限定。\n"
                        '只输出 JSON 数组，如 ["查询1", "查询2"]。'
                    ),
                },
                {"role": "user", "content": subquestion},
            ],
            temperature=0.3,
            max_tokens=512,
        )
        data = _extract_json(raw)
        if isinstance(data, list):
            queries = [str(item).strip() for item in data if str(item or "").strip()]
            if queries:
                return queries[:_MAX_QUERIES_PER_SUB]
    except Exception:
        pass
    return [subquestion]


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 3：GATHER — 并行搜索 + 抓取
# ─────────────────────────────────────────────────────────────────────────────
def _gather_for_subquestion(
    subquestion: str,
    *,
    max_results: int,
    max_pages: int,
    max_chars_per_page: int,
    provider: str,
) -> dict[str, Any]:
    """Search + read pages for a single sub-question. Returns evidence + sources."""
    queries = _expand_queries(subquestion)
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for query in queries:
        search = metis_search_query(query, max_results=max_results, provider=provider)
        if not search.get("ok"):
            continue
        for item in search.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = _source_url(item.get("url"))
            key = _canonical_url_key(url)
            if not url or key in seen:
                continue
            seen.add(key)
            candidates.append(item)

    pages: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    char_limit = _clamp_int(max_chars_per_page, 800, 4000, default=1800)
    to_read = candidates[: max(1, max_pages)]
    if to_read:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(to_read))) as executor:
            futures = [(item, executor.submit(metis_page_read, _source_url(item.get("url")), char_limit)) for item in to_read]
            for item, future in futures:
                url = _source_url(item.get("url"))
                try:
                    page = future.result()
                except Exception as exc:
                    page = {"ok": False, "status": "error", "url": url, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                if page.get("ok"):
                    pages.append({"search_result": item, **page})
                else:
                    failures.append({"url": url, "status": str(page.get("status") or "error"), "error": str(page.get("error") or "")})
    return {"subquestion": subquestion, "queries": queries, "pages": pages, "failures": failures, "candidates": candidates}


def _gather_parallel(
    subquestions: list[str],
    *,
    max_results: int,
    max_pages: int,
    max_chars_per_page: int,
    provider: str,
) -> list[dict[str, Any]]:
    """Run all sub-questions in parallel, one worker each."""
    results: list[dict[str, Any]] = [{} for _ in subquestions]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, max(1, len(subquestions)))) as executor:
        future_map = {
            executor.submit(
                _gather_for_subquestion,
                sub,
                max_results=max_results,
                max_pages=max_pages,
                max_chars_per_page=max_chars_per_page,
                provider=provider,
            ): index
            for index, sub in enumerate(subquestions)
        }
        for future in concurrent.futures.as_completed(future_map):
            index = future_map[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                results[index] = {
                    "subquestion": subquestions[index],
                    "queries": [],
                    "pages": [],
                    "failures": [{"url": "", "status": "error", "error": f"{type(exc).__name__}: {str(exc)[:160]}"}],
                    "candidates": [],
                }
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 4：GRADE — 证据打分与过滤
# ─────────────────────────────────────────────────────────────────────────────
def _collect_evidence(gathered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten gathered pages into a deduplicated evidence list."""
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bucket in gathered:
        for page in bucket.get("pages") or []:
            source = page.get("search_result") or {}
            url = _source_url(page.get("final_url") or page.get("url") or source.get("url"))
            key = _canonical_url_key(url)
            if not url or key in seen:
                continue
            seen.add(key)
            text = _normalize_page_text(str(page.get("text") or ""))
            evidence.append(
                {
                    "title": _source_title(page.get("title") or source.get("title"), url),
                    "url": url,
                    "domain": _source_domain(source.get("source"), url),
                    "snippet": source.get("snippet") or "",
                    "text": text,
                    "chars": len(text),
                    "status": str(page.get("status") or "opened"),
                    "subquestion": bucket.get("subquestion") or "",
                }
            )
    return evidence


def _grade_evidence(question: str, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """LLM-score each evidence item 0~1 for relevance+credibility; filter low quality.
    Falls back to keeping all evidence if grading fails."""
    if not evidence:
        return evidence
    try:
        catalog = [
            {"i": index, "title": item["title"], "domain": item["domain"], "excerpt": _truncate(item["text"], 300)}
            for index, item in enumerate(evidence)
        ]
        raw = _llm_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是研究证据评估助手。针对研究问题，给每条证据打分（0~1）：\n"
                        "综合考虑相关性和来源可信度。\n"
                        '只输出 JSON 数组：[{"i": 序号, "score": 0.85}, ...]。'
                    ),
                },
                {"role": "user", "content": f"研究问题：{question}\n\n证据列表：\n{json.dumps(catalog, ensure_ascii=False)}"},
            ],
            temperature=0.2,
            max_tokens=1024,
        )
        data = _extract_json(raw)
        if isinstance(data, list):
            score_map: dict[int, float] = {}
            for row in data:
                if isinstance(row, dict) and "i" in row:
                    try:
                        score_map[int(row["i"])] = max(0.0, min(1.0, float(row.get("score", 0))))
                    except Exception:
                        continue
            if score_map:
                for index, item in enumerate(evidence):
                    item["score"] = score_map.get(index, _GRADE_KEEP_THRESHOLD)
                graded = [item for item in evidence if item.get("score", 0) >= _GRADE_KEEP_THRESHOLD]
                graded.sort(key=lambda item: item.get("score", 0), reverse=True)
                return graded or evidence
    except Exception:
        pass
    # fallback: keep all, no scores
    for item in evidence:
        item.setdefault("score", _GRADE_KEEP_THRESHOLD)
    return evidence


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 5：OUTLINE — 生成报告目录
# ─────────────────────────────────────────────────────────────────────────────
def _build_outline(question: str, brief: str, subquestions: list[str]) -> list[str]:
    """Generate report section headings. Falls back to sub-questions as headings."""
    try:
        raw = _llm_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "根据研究目标和子问题，规划研究报告的章节标题（3~6 个）。\n"
                        '只输出 JSON 数组：["章节1", "章节2", ...]。'
                    ),
                },
                {"role": "user", "content": f"研究目标：{brief}\n子问题：{json.dumps(subquestions, ensure_ascii=False)}"},
            ],
            temperature=0.4,
            max_tokens=512,
        )
        data = _extract_json(raw)
        if isinstance(data, list):
            headings = [str(item).strip() for item in data if str(item or "").strip()]
            if headings:
                return headings[:6]
    except Exception:
        pass
    return subquestions


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 6：SYNTHESIZE — 单次生成带引用的完整报告
# ─────────────────────────────────────────────────────────────────────────────
def _synthesize_report(
    question: str,
    brief: str,
    outline: list[str],
    evidence: list[dict[str, Any]],
) -> str:
    """One-shot report generation with [n] citations bound to evidence rank."""
    numbered = []
    for rank, item in enumerate(evidence, start=1):
        numbered.append(f"[{rank}] {item['title']} ({item['domain']})\n{_truncate(item['text'], 800)}")
    evidence_block = "\n\n".join(numbered)
    outline_block = "\n".join(f"- {heading}" for heading in outline)

    raw = _llm_chat(
        [
            {
                "role": "system",
                "content": (
                    "你是资深研究分析师。基于给定证据，撰写一篇结构化的中文研究报告。\n"
                    "要求：\n"
                    "1. 使用 Markdown，按给定大纲组织章节（## 标题）。\n"
                    "2. 关键论断后用 [n] 标注来源（n 对应证据编号）。\n"
                    "3. 只使用证据中的信息，不要编造。证据不足处如实说明。\n"
                    "4. 开头写一段简短摘要，结尾可给出结论。\n"
                    "不要输出证据列表本身（来源会单独附上）。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"研究问题：{question}\n研究目标：{brief}\n\n"
                    f"报告大纲：\n{outline_block}\n\n"
                    f"证据（带编号）：\n{evidence_block}"
                ),
            },
        ],
        temperature=0.5,
        max_tokens=4096,
        timeout=120.0,
    )
    return str(raw or "").strip()


# ─────────────────────────────────────────────────────────────────────────────
# Job 持久化
# ─────────────────────────────────────────────────────────────────────────────
def _build_activity_payload(
    question: str,
    brief: str,
    plan: dict[str, Any],
    gathered: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    outline: list[str],
    provider_chain: list[str],
) -> dict[str, Any]:
    """Build a research_activity payload compatible with research_jobs schema."""
    sources: list[dict[str, Any]] = []
    for rank, item in enumerate(evidence, start=1):
        sources.append(
            {
                "id": f"s{rank}",
                "rank": rank,
                "title": item["title"],
                "url": item["url"],
                "domain": item["domain"],
                "snippet": item.get("snippet") or "",
                "status": "opened",
                "evidence_status": item.get("status") or "opened",
                "chars": item.get("chars") or 0,
                "score": round(float(item.get("score", 0)), 2),
            }
        )
    failures: list[dict[str, Any]] = []
    for bucket in gathered:
        for failure in bucket.get("failures") or []:
            if failure.get("url"):
                failures.append(failure)

    total_opened = len(evidence)
    total_failures = len(failures)
    phases = [
        {"id": "plan", "label": "生成研究计划", "status": "complete", "summary": brief},
        {"id": "query", "label": "扩展搜索词", "status": "complete", "count": sum(len(b.get("queries") or []) for b in gathered)},
        {"id": "gather", "label": "并行搜索抓取", "status": "complete", "count": total_opened, "failed": total_failures},
        {"id": "grade", "label": "证据分级", "status": "complete", "count": total_opened},
        {"id": "outline", "label": "规划大纲", "status": "complete", "count": len(outline)},
        {"id": "synthesize", "label": "撰写报告", "status": "complete"},
    ]
    queries_flat: list[dict[str, Any]] = []
    for bucket in gathered:
        for query in bucket.get("queries") or []:
            queries_flat.append({"query": query, "subquestion": bucket.get("subquestion") or "", "at": int(time.time() * 1000)})

    return {
        "schema": _SCHEMA,
        "kind": "deep_research",
        "title": question,
        "query": question,
        "brief": brief,
        "providers": provider_chain,
        "stats": {
            "subquestions": len(plan.get("subquestions") or []),
            "queries": len(queries_flat),
            "sources": len(sources),
            "opened": total_opened,
            "failures": total_failures,
        },
        "phases": phases,
        "plan_subquestions": plan.get("subquestions") or [],
        "queries_detail": queries_flat,
        "outline": outline,
        "sources": sources,
        "failures": failures,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 降级
# ─────────────────────────────────────────────────────────────────────────────
def _fallback_to_simple(question: str, *, max_results: int, max_pages: int, max_chars_per_page: int, provider: str) -> str:
    """When deep pipeline can't run (LLM unavailable), degrade to the existing simple research."""
    return format_research_response(
        metis_search_research(
            question=question,
            max_results=max_results,
            max_pages=max_pages,
            max_chars_per_page=max_chars_per_page,
            provider=provider,
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# 公共工具入口
# ─────────────────────────────────────────────────────────────────────────────
@trace_execution
def deep_research_plan(question: str, reason: str = "") -> str:
    """阶段一：生成研究计划供用户确认。"""
    _ = reason
    question_value = str(question or "").strip()
    if not question_value:
        return "❌ deep_research_plan 需要非空研究问题。"
    try:
        plan = _generate_plan(question_value)
    except Exception as exc:
        return (
            f"⚠️ 计划生成失败（{type(exc).__name__}），可直接调用 deep_research_run 让其即时规划，"
            "或降级用 web_research。"
        )
    plan_json = json.dumps(plan, ensure_ascii=False)
    lines = [
        f"=== 研究计划：{question_value} ===",
        f"研究目标：{plan['brief']}",
        "",
        "子问题：",
    ]
    for index, sub in enumerate(plan["subquestions"], start=1):
        lines.append(f"  {index}. {sub}")
    lines.extend(
        [
            "",
            "请把以上计划展示给用户确认或修改。用户确认后，调用 deep_research_run 并传入 plan_json 执行研究。",
            "chat 输出策略：把计划要点用自然语言呈现给用户，不要粘贴 JSON。",
            f"<!-- METIS_RESEARCH_PLAN {plan_json} -->",
        ]
    )
    return "\n".join(lines)


@trace_execution
def deep_research_run(
    question: str,
    plan_json: str = "",
    max_results: int = 5,
    max_pages: int = 3,
    max_chars_per_page: int = 1800,
    reason: str = "",
    provider: str = "auto",
) -> str:
    """阶段二：执行完整 6 阶段深度研究流水线。"""
    _ = reason
    question_value = str(question or "").strip()
    if not question_value:
        return "❌ deep_research_run 需要非空研究问题。"

    provider_chain = _provider_chain_for_request(provider)

    # 阶段 1：PLAN（复用已确认计划或即时生成）
    plan: dict[str, Any] | None = None
    if plan_json:
        parsed = _extract_json(plan_json)
        if isinstance(parsed, dict) and parsed.get("subquestions"):
            subs = [str(item).strip() for item in parsed.get("subquestions") or [] if str(item or "").strip()]
            if subs:
                plan = {"brief": str(parsed.get("brief") or question_value).strip(), "subquestions": subs[:_MAX_SUBQUESTIONS]}
    if plan is None:
        try:
            plan = _generate_plan(question_value)
        except Exception:
            # LLM 不可用 → 降级
            return _fallback_to_simple(
                question_value,
                max_results=max_results,
                max_pages=max_pages,
                max_chars_per_page=max_chars_per_page,
                provider=provider,
            )

    brief = plan["brief"]
    subquestions = plan["subquestions"]

    # 阶段 2+3：QUERY + GATHER（并行）
    gathered = _gather_parallel(
        subquestions,
        max_results=max_results,
        max_pages=max_pages,
        max_chars_per_page=max_chars_per_page,
        provider=provider,
    )

    # 阶段 4：GRADE
    evidence = _collect_evidence(gathered)
    if not evidence:
        # 没抓到任何证据 → 降级
        return _fallback_to_simple(
            question_value,
            max_results=max_results,
            max_pages=max_pages,
            max_chars_per_page=max_chars_per_page,
            provider=provider,
        )
    evidence = _grade_evidence(question_value, evidence)

    # 阶段 5：OUTLINE
    outline = _build_outline(question_value, brief, subquestions)

    # 阶段 6：SYNTHESIZE
    try:
        report = _synthesize_report(question_value, brief, outline, evidence)
    except Exception:
        report = ""
    if not report:
        return _fallback_to_simple(
            question_value,
            max_results=max_results,
            max_pages=max_pages,
            max_chars_per_page=max_chars_per_page,
            provider=provider,
        )

    # 落地 research job
    activity = _build_activity_payload(question_value, brief, plan, gathered, evidence, outline, provider_chain)
    evidence_for_job = [
        {"title": item["title"], "url": item["url"], "text": _truncate(item["text"], 2000), "snippet": item.get("snippet") or "", "status": item.get("status") or "opened", "chars": item.get("chars") or 0}
        for item in evidence
    ]
    job_id = ""
    report_path = ""
    try:
        job = save_research_activity_job(activity, report=report, status="complete", evidence=evidence_for_job)
        job_id = str(job.get("id") or "")
        report_path = str(job.get("report_path") or "")
    except Exception as exc:
        activity["job_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"

    # chat 输出：简短摘要 + job 引用
    lines = [
        f"=== Deep Research 完成：{question_value} ===",
        f"研究目标：{brief}",
        f"子问题：{len(subquestions)} 个 | 证据来源：{len(evidence)} 条 | 大纲章节：{len(outline)} 个",
        f"报告已保存到 Research job store（job_id={job_id or 'n/a'}）。",
    ]
    if report_path:
        lines.append(f"报告文件：{report_path}")
    lines.extend(
        [
            "",
            "chat 输出策略：用自然语言给用户一段简短总结即可；完整报告、大纲、来源和导出请引导用户到 Research 报告视图查看，不要在聊天里粘贴整篇报告或长来源列表。",
        ]
    )
    return "\n".join(lines)
