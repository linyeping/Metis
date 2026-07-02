from __future__ import annotations

import json
from typing import Any

import pytest
from backend.core.paths import clear_metis_home_cache
from backend.tools.coding.network_external.web import deep_research
from backend.tools.coding.network_external.web.research_jobs import get_research_job, list_research_jobs
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


# ─────────────────────────────────────────────────────────────────────────────
# Full run
# ─────────────────────────────────────────────────────────────────────────────
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
