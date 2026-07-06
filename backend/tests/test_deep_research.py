from __future__ import annotations

import json
from typing import Any

import pytest
from backend.core.paths import clear_metis_home_cache
from backend.tools.coding.network_external.web import deep_research
from backend.tools.coding.network_external.web.evidence_review import review_evidence_item
from backend.tools.coding.network_external.web.question_type import classify_question
from backend.tools.coding.network_external.web.research_jobs import get_research_job, list_research_jobs
from backend.tools.coding.network_external.web.source_policy import classify_source
from backend.tools.registry import AVAILABLE_TOOLS
from backend.tools.schema_definitions import build_tools_schema


@pytest.fixture(autouse=True)
def isolated_metis_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "metis-home"))
    clear_metis_home_cache()
    yield
    clear_metis_home_cache()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: fake LLM router + fake search/read
# ─────────────────────────────────────────────────────────────────────────────
def _make_fake_llm(*, plan: dict | None = None, fail: bool = False):
    """Return a fake _llm_chat that routes by prompt content."""
    plan = plan or {"brief": "研究目标X", "subquestions": ["子问题A", "子问题B"]}

    def fake(messages: list[dict[str, Any]], **_kwargs: Any) -> str:
        if fail:
            raise RuntimeError("llm unavailable")
        system = str(messages[0].get("content") or "")
        user = str(messages[-1].get("content") or "")
        if "研究规划助手" in system:
            return json.dumps(plan, ensure_ascii=False)
        if "搜索引擎查询词" in system:
            return json.dumps([f"{user} 查询1", f"{user} 查询2"], ensure_ascii=False)
        if "证据评估助手" in system:
            # score every item high
            catalog = json.loads(user.split("证据列表：\n", 1)[-1])
            return json.dumps([{"i": row["i"], "score": 0.9} for row in catalog], ensure_ascii=False)
        if "章节标题" in system:
            return json.dumps(["章节一", "章节二"], ensure_ascii=False)
        if "研究分析师" in system:
            return "## 章节一\n结论A [1]\n\n## 章节二\n结论B [2]"
        return ""

    return fake


def _fake_search(query: str, max_results: int = 5, provider: str = "auto") -> dict[str, Any]:
    return {
        "ok": True,
        "query": query,
        "provider": "ddgs",
        "results": [
            {"rank": 1, "title": f"T-{query}", "url": f"https://example.com/{abs(hash(query)) % 1000}", "snippet": "snip", "source": "example.com"},
        ],
    }


def _fake_page_read(url: str, max_chars: int = 4000) -> dict[str, Any]:
    return {"ok": True, "status": "opened", "url": url, "final_url": url, "title": f"Page {url[-3:]}", "text": "正文内容 " * 40, "chars": 200}


# ─────────────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────────────
def test_tools_registered() -> None:
    assert "deep_research_plan" in AVAILABLE_TOOLS
    assert "deep_research_run" in AVAILABLE_TOOLS
    schema_names = {(item.get("function") or {}).get("name") for item in build_tools_schema()}
    assert "deep_research_plan" in schema_names
    assert "deep_research_run" in schema_names


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: plan
# ─────────────────────────────────────────────────────────────────────────────
def test_generate_plan_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm())
    plan = deep_research._generate_plan("测试问题")
    assert plan["brief"] == "研究目标X"
    assert plan["subquestions"] == ["子问题A", "子问题B"]


def test_deep_research_plan_embeds_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm())
    out = deep_research.deep_research_plan("测试问题")
    assert "研究计划" in out
    assert "METIS_RESEARCH_PLAN" in out
    assert "子问题A" in out


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: query expansion
# ─────────────────────────────────────────────────────────────────────────────
def test_expand_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm())
    queries = deep_research._expand_queries("子问题A")
    assert len(queries) == 2
    assert all("子问题A" in q for q in queries)


def test_expand_queries_falls_back_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm(fail=True))
    queries = deep_research._expand_queries("子问题A")
    assert queries == ["子问题A"]


def test_classify_official_release_question() -> None:
    profile = classify_question("截至 2026-07-02，OpenAI GPT 5.6 模型是否已官方发布？")
    assert profile["type"] == "official_release_check"
    assert profile["requires_official_evidence"] is True
    assert any(entity.lower().replace(" ", "-") == "gpt-5.6" for entity in profile["entities"])
    assert "OpenAI" in profile["entities"]


def test_crypto_rumor_source_is_not_core_release_evidence() -> None:
    question = "截至 2026-07-02，OpenAI GPT 5.6 模型是否已官方发布？"
    profile = classify_question(question)
    policy = classify_source(
        {
            "title": "OpenAI 给模型取名 Luna，加密立刻给 Terra 的死币上了杠杆",
            "url": "https://www.marsbit.co/news/20260629-openai-gpt-56-luna",
            "snippet": "OpenAI 发布 GPT-5.6，三个层级分别叫 Sol、Terra、Luna。",
        },
        question,
        profile,
    )
    assert policy["label"] == "normal"
    assert policy["core_allowed"] is False
    assert policy["role"] == "rumor"


def test_evidence_review_rejects_unrelated_old_bbc_article_for_gpt_56() -> None:
    question = "截至 2026-07-02，OpenAI GPT 5.6 模型是否已官方发布？"
    profile = classify_question(question)
    review = review_evidence_item(
        {
            "title": "人工智能：苹果发布AI新功能 将把ChatGPT融入iPhone - BBC News 中文",
            "url": "https://www.bbc.com/zhongwen/simp/science-69108432",
            "domain": "bbc.com",
            "snippet": "苹果公司将通过与 ChatGPT 开发商 OpenAI 合作来增强 Siri。",
            "text": "Published 2024年6月11日。苹果宣布 Apple Intelligence，并将 ChatGPT 融入 iPhone。" * 8,
        },
        question,
        profile,
    )
    assert review["accepted"] is False
    assert "missing required entity/topic match" in review["reasons"]


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3: parallel gather
# ─────────────────────────────────────────────────────────────────────────────
def test_gather_parallel_no_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm())
    monkeypatch.setattr(deep_research, "metis_search_query", _fake_search)
    monkeypatch.setattr(deep_research, "metis_page_read", _fake_page_read)
    gathered = deep_research._gather_parallel(
        ["子问题A", "子问题B", "子问题C"], max_results=3, max_pages=2, max_chars_per_page=1800, provider="auto"
    )
    assert len(gathered) == 3
    assert all(bucket.get("pages") for bucket in gathered)


def test_official_query_variants_for_known_product() -> None:
    queries = deep_research._with_official_query_variants(["Gemini 最新模型"], "Gemini 最新模型")
    assert any("site:blog.google" in query for query in queries)
    assert any("site:deepmind.google" in query for query in queries)


def test_gather_skips_fake_brand_download_site_and_reads_official(monkeypatch: pytest.MonkeyPatch) -> None:
    read_urls: list[str] = []

    def fake_search(query: str, max_results: int = 5, provider: str = "auto") -> dict[str, Any]:
        results = [
            {
                "rank": 1,
                "title": "Gemini 教程网 - 谷歌Gemini下载",
                "url": "https://google-gemini.cc/gemini-download",
                "snippet": "谷歌Gemini下载 会员充值 教程网",
                "source": "google-gemini.cc",
            }
        ]
        if "site:blog.google" in query:
            results.append(
                {
                    "rank": 2,
                    "title": "Gemini model updates",
                    "url": "https://blog.google/technology/google-deepmind/gemini-model-updates/",
                    "snippet": "Official Google update about Gemini models.",
                    "source": "blog.google",
                }
            )
        return {"ok": True, "query": query, "provider": "ddgs", "results": results}

    def fake_page_read(url: str, max_chars: int = 4000) -> dict[str, Any]:
        read_urls.append(url)
        return {"ok": True, "status": "opened", "url": url, "final_url": url, "title": "Official Gemini update", "text": "官方 Gemini 更新内容。" * 40}

    monkeypatch.setattr(deep_research, "_expand_queries", lambda _subquestion: ["Gemini 最新模型"])
    monkeypatch.setattr(deep_research, "metis_search_query", fake_search)
    monkeypatch.setattr(deep_research, "metis_page_read", fake_page_read)

    gathered = deep_research._gather_for_subquestion(
        "Gemini 最新模型",
        max_results=5,
        max_pages=3,
        max_chars_per_page=1800,
        provider="auto",
    )
    assert "https://google-gemini.cc/gemini-download" not in read_urls
    assert "https://blog.google/technology/google-deepmind/gemini-model-updates/" in read_urls
    assert gathered["pages"][0]["search_result"]["_metis_source_quality"] == "official"


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4: grading
# ─────────────────────────────────────────────────────────────────────────────
def test_grade_evidence_filters_and_sorts(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(messages: list[dict[str, Any]], **_kwargs: Any) -> str:
        user = str(messages[-1].get("content") or "")
        catalog = json.loads(user.split("证据列表：\n", 1)[-1])
        # first item high, rest below threshold
        return json.dumps([{"i": row["i"], "score": 0.9 if row["i"] == 0 else 0.1} for row in catalog], ensure_ascii=False)

    monkeypatch.setattr(deep_research, "_llm_chat", fake)
    evidence = [
        {"title": "A", "url": "https://a.com", "domain": "a.com", "text": "x", "snippet": "", "status": "opened", "chars": 1},
        {"title": "B", "url": "https://b.com", "domain": "b.com", "text": "y", "snippet": "", "status": "opened", "chars": 1},
    ]
    graded = deep_research._grade_evidence("q", evidence)
    assert len(graded) == 1
    assert graded[0]["title"] == "A"


def test_grade_evidence_keeps_all_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm(fail=True))
    evidence = [
        {"title": "A", "url": "https://a.com", "domain": "a.com", "text": "x", "snippet": "", "status": "opened", "chars": 1},
    ]
    graded = deep_research._grade_evidence("q", evidence)
    assert len(graded) == 1


def test_collect_evidence_filters_network_errors_and_spam() -> None:
    gathered = [
        {
            "subquestion": "Gemini 最新消息",
            "pages": [
                {
                    "ok": True,
                    "status": "opened",
                    "url": "https://good.example/news",
                    "title": "Google Gemini 官方更新",
                    "text": "这是可信来源正文。" * 30,
                    "search_result": {"url": "https://good.example/news", "title": "Google Gemini 官方更新", "snippet": "官方更新", "source": "good.example"},
                },
                {
                    "ok": True,
                    "status": "opened",
                    "url": "https://bad.example/error",
                    "title": "页面读取失败",
                    "text": "ConnectionError: HTTPSConnectionPool(host='bad.example'): Max retries exceeded",
                    "search_result": {"url": "https://bad.example/error", "title": "页面读取失败", "snippet": "", "source": "bad.example"},
                },
                {
                    "ok": True,
                    "status": "opened",
                    "url": "https://seo.example/gemini",
                    "title": "Gemini 会员充值",
                    "text": "Gemini 会员充值\n请添加客服微信号：gpthuiyuan\n低价代充账号出售",
                    "search_result": {"url": "https://seo.example/gemini", "title": "Gemini 会员充值", "snippet": "客服微信 代充", "source": "seo.example"},
                },
            ],
        }
    ]
    evidence = deep_research._collect_evidence(gathered)
    assert len(evidence) == 1
    assert evidence[0]["url"] == "https://good.example/news"


def test_sanitize_report_removes_read_failures_and_spam_lines() -> None:
    report = "\n".join(
        [
            "## 正常章节",
            "正常内容。",
            "",
            "## Read Failures",
            "- https://bad.example [error]: ConnectionError: Max retries exceeded",
            "",
            "## 后续章节",
            "Gemini 是 Google DeepMind 推出的 AI。",
            "请添加客服微信号：gpthuiyuan",
            "继续正常内容。",
        ]
    )
    cleaned = deep_research._sanitize_report_text(report)
    assert "Read Failures" not in cleaned
    assert "ConnectionError" not in cleaned
    assert "客服微信" not in cleaned
    assert "后续章节" in cleaned
    assert "继续正常内容" in cleaned


def test_sanitize_report_removes_toc_section_but_keeps_real_sections() -> None:
    report = "\n".join(
        [
            "# 研究报告",
            "",
            "## 目录",
            "- [不存在的章节](#missing)",
            "- [核心结论](#core)",
            "",
            "## 引言",
            "真实引言正文。",
            "",
            "## 1. 核心结论",
            "真实章节正文。",
        ]
    )
    cleaned = deep_research._sanitize_report_text(report)
    assert "不存在的章节" not in cleaned
    assert "## 目录" not in cleaned
    assert "## 引言" in cleaned
    assert "真实章节正文" in cleaned


def test_sanitize_report_removes_navigation_junk_and_truncation_marker() -> None:
    report = "\n".join(
        [
            "# 研究报告",
            "",
            "## 1. 正常章节",
            "真实正文。",
            "On This Page",
            "8 sections",
            "[MCP ServersMCP Servers](https://example.com/mcp)[Agent SkillsAgent Skills](https://example.com/skills)",
            "[... truncated 633 chars ...]",
            "继续真实正文。",
        ]
    )
    cleaned = deep_research._sanitize_report_text(report)
    assert "On This Page" not in cleaned
    assert "8 sections" not in cleaned
    assert "MCP ServersMCP Servers" not in cleaned
    assert "truncated" not in cleaned
    assert "继续真实正文" in cleaned


def test_sanitize_report_removes_legacy_evidence_dump_and_button_junk() -> None:
    report = "\n".join(
        [
            "# Google Gemini recent models",
            "- Status: complete",
            "- Providers: ddgs",
            "",
            "## Report",
            "# Google Gemini recent models",
            "",
            "## Evidence Opened",
            "### Sign in - Google Accounts",
            "- URL: https://accounts.google.com/signin",
            "- Status: ok",
            "Use your Google Account",
            "",
            "## 1. 正常章节",
            "Gemini 模型更新需要以 Google 官方信息为准。",
            "Gemini Omni 多模态创作模型： Go",
            "继续分析正文。",
        ]
    )
    cleaned = deep_research._sanitize_report_text(report)
    assert "Evidence Opened" not in cleaned
    assert "Status:" not in cleaned
    assert "Google Accounts" not in cleaned
    assert "： Go" not in cleaned
    assert "继续分析正文" in cleaned


def test_unwrap_reader_url_handles_nested_jina_links() -> None:
    url = "https://r.jina.ai/http://r.jina.ai/http://https://finance.sina.com.cn/roll/2026-05-22/doc.shtml"
    assert deep_research._unwrap_reader_url(url) == "https://finance.sina.com.cn/roll/2026-05-22/doc.shtml"


def test_plain_excerpt_does_not_emit_truncation_marker() -> None:
    text = "A" * 100
    excerpt = deep_research._plain_excerpt(text, 12)
    assert excerpt == "A" * 12
    assert "truncated" not in excerpt


# ─────────────────────────────────────────────────────────────────────────────
# Full run
# ─────────────────────────────────────────────────────────────────────────────
def test_official_release_without_official_evidence_generates_absence_report(monkeypatch: pytest.MonkeyPatch) -> None:
    question = "截至 2026-07-02，OpenAI GPT 5.6 模型是否已官方发布？如果未发布，相关传闻和误称来自哪里？"

    def fake_search(query: str, max_results: int = 5, provider: str = "auto") -> dict[str, Any]:
        return {
            "ok": True,
            "query": query,
            "provider": "ddgs",
            "results": [
                {
                    "rank": 1,
                    "title": "OpenAI 给模型取名「Luna」，加密立刻给 Terra 的死币上了杠杆",
                    "url": "https://www.marsbit.co/news/20260629-openai-gpt-56-luna",
                    "snippet": "OpenAI 发布 GPT-5.6，三个层级分别叫 Sol、Terra、Luna。",
                    "source": "marsbit.co",
                },
                {
                    "rank": 2,
                    "title": "人工智能：苹果发布AI新功能 将把ChatGPT融入iPhone - BBC News 中文",
                    "url": "https://www.bbc.com/zhongwen/simp/science-69108432",
                    "snippet": "苹果将 ChatGPT 融入 iPhone。",
                    "source": "bbc.com",
                },
            ],
        }

    def fake_page_read(url: str, max_chars: int = 4000) -> dict[str, Any]:
        if "marsbit" in url:
            return {
                "ok": True,
                "status": "opened",
                "url": url,
                "final_url": url,
                "title": "OpenAI 给模型取名「Luna」",
                "text": "OpenAI 发布 GPT-5.6，三个层级分别叫 Sol、Terra、Luna。这是币圈传闻文本，用于解释误称来源。" * 12,
            }
        return {
            "ok": True,
            "status": "opened",
            "url": url,
            "final_url": url,
            "title": "苹果发布 AI 新功能",
            "text": "苹果公司在 2024 年宣布 Apple Intelligence，并将 ChatGPT 融入 iPhone。" * 12,
        }

    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm())
    monkeypatch.setattr(deep_research, "metis_search_query", fake_search)
    monkeypatch.setattr(deep_research, "metis_page_read", fake_page_read)

    out = deep_research.deep_research_run(question)
    assert "Deep Research 完成" in out

    jobs = list_research_jobs(limit=10)
    assert jobs
    job = get_research_job(jobs[0]["id"])
    assert job is not None
    assert "未发现官方发布证据" in job["report"]
    assert "OpenAI 发布 GPT-5.6，三个层级分别叫 Sol、Terra、Luna" not in job["report"].split("## 结论", 1)[-1].split("##", 1)[0]


def test_deep_research_run_full_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm())
    monkeypatch.setattr(deep_research, "metis_search_query", _fake_search)
    monkeypatch.setattr(deep_research, "metis_page_read", _fake_page_read)

    out = deep_research.deep_research_run("测试研究问题")
    assert "Deep Research 完成" in out
    assert "job_id=" in out

    jobs = list_research_jobs(limit=10)
    assert jobs
    job = get_research_job(jobs[0]["id"])
    assert job is not None
    assert job["kind"] == "deep_research"
    assert job["report"]
    assert "结论A" in job["report"]
    # citations bound to sources
    assert job["sources"]
    assert job["sources"][0]["rank"] == 1


def test_deep_research_run_uses_confirmed_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(messages: list[dict[str, Any]], **_kwargs: Any) -> str:
        system = str(messages[0].get("content") or "")
        if "研究规划助手" in system:
            calls.append("plan")
            return json.dumps({"brief": "should not be used", "subquestions": ["X"]}, ensure_ascii=False)
        return _make_fake_llm()(messages, **_kwargs)

    monkeypatch.setattr(deep_research, "_llm_chat", fake)
    monkeypatch.setattr(deep_research, "metis_search_query", _fake_search)
    monkeypatch.setattr(deep_research, "metis_page_read", _fake_page_read)

    plan_json = json.dumps({"brief": "确认的目标", "subquestions": ["确认子问题1", "确认子问题2"]}, ensure_ascii=False)
    out = deep_research.deep_research_run("测试问题", plan_json=plan_json)
    assert "Deep Research 完成" in out
    # plan generation should NOT have been called since we passed a confirmed plan
    assert "plan" not in calls


# ─────────────────────────────────────────────────────────────────────────────
# Fallback
# ─────────────────────────────────────────────────────────────────────────────
def test_run_falls_back_when_llm_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm(fail=True))

    called = {"fallback": False}

    def fake_fallback(question: str, **_kwargs: Any) -> str:
        called["fallback"] = True
        return "FALLBACK RESULT"

    monkeypatch.setattr(deep_research, "_fallback_to_simple", fake_fallback)
    out = deep_research.deep_research_run("测试问题")
    assert called["fallback"] is True
    assert out == "FALLBACK RESULT"


def test_run_falls_back_when_no_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm())

    def empty_search(query: str, max_results: int = 5, provider: str = "auto") -> dict[str, Any]:
        return {"ok": True, "query": query, "provider": "ddgs", "results": []}

    monkeypatch.setattr(deep_research, "metis_search_query", empty_search)

    called = {"fallback": False}

    def fake_fallback(question: str, **_kwargs: Any) -> str:
        called["fallback"] = True
        return "FALLBACK RESULT"

    monkeypatch.setattr(deep_research, "_fallback_to_simple", fake_fallback)
    out = deep_research.deep_research_run("测试问题")
    assert called["fallback"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Live progress: incremental persistence
# ─────────────────────────────────────────────────────────────────────────────
def test_run_persists_multiple_times(monkeypatch: pytest.MonkeyPatch) -> None:
    """deep_research_run should call save_research_activity_job >1 times with running status."""
    from backend.tools.coding.network_external.web import research_jobs

    save_calls: list[str] = []
    original_save = research_jobs.save_research_activity_job

    def counting_save(activity: dict, *, report: str = "", status: str = "complete", evidence=None) -> dict:
        save_calls.append(status)
        return original_save(activity, report=report, status=status, evidence=evidence)

    monkeypatch.setattr(research_jobs, "save_research_activity_job", counting_save)
    # deep_research.py imports save_research_activity_job directly, patch there too
    monkeypatch.setattr(deep_research, "save_research_activity_job", counting_save)
    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm())
    monkeypatch.setattr(deep_research, "metis_search_query", _fake_search)
    monkeypatch.setattr(deep_research, "metis_page_read", _fake_page_read)

    deep_research.deep_research_run("测试研究问题")

    # Must persist more than once; first save must be running
    assert len(save_calls) > 1, f"expected >1 saves, got {save_calls}"
    assert save_calls[0] == "running", f"first save should be running, got {save_calls[0]}"
    assert save_calls[-1] == "complete"


def test_gather_progress_callback_invoked(monkeypatch: pytest.MonkeyPatch) -> None:
    """on_progress callback must be called once per subquestion."""
    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm())
    monkeypatch.setattr(deep_research, "metis_search_query", _fake_search)
    monkeypatch.setattr(deep_research, "metis_page_read", _fake_page_read)

    progress_calls: list[int] = []

    def on_progress(evidence: list, done: int, total: int) -> None:
        progress_calls.append(done)

    gathered = deep_research._gather_parallel(
        ["子问题1", "子问题2", "子问题3"],
        max_results=3,
        max_pages=2,
        max_chars_per_page=1800,
        provider="auto",
        on_progress=on_progress,
    )
    assert len(gathered) == 3
    assert len(progress_calls) == 3, f"expected 3 progress calls, got {progress_calls}"
    assert sorted(progress_calls) == [1, 2, 3]


def test_completed_result_embeds_research_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Completed deep_research_run output must contain METIS_RESEARCH_JSON with job_id."""
    import re as _re

    monkeypatch.setattr(deep_research, "_llm_chat", _make_fake_llm())
    monkeypatch.setattr(deep_research, "metis_search_query", _fake_search)
    monkeypatch.setattr(deep_research, "metis_page_read", _fake_page_read)

    out = deep_research.deep_research_run("测试研究问题")
    match = _re.search(r"<!--\s*METIS_RESEARCH_JSON\s+([\s\S]*?)\s*-->", out)
    assert match, "output must contain METIS_RESEARCH_JSON comment"
    payload = json.loads(match.group(1))
    assert payload.get("job_id"), "payload must have a non-empty job_id"
    assert payload.get("kind") == "deep_research"
    assert payload.get("job_status") == "complete"
