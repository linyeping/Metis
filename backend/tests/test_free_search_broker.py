from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from backend.core.paths import clear_metis_home_cache
from backend.tools.coding.network_external.web import search_broker
from backend.tools.coding.network_external.web.fetch_content import fetch_content
from backend.tools.coding.network_external.web.research_jobs import get_research_job, save_research_activity_job
from backend.tools.coding.network_external.web.web_research import web_research
from backend.tools.coding.network_external.web.web_search import web_search
from backend.tools.registry import AVAILABLE_TOOLS
from backend.tools.schema_definitions import build_tools_schema


@pytest.fixture(autouse=True)
def isolated_metis_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "metis-home"))
    clear_metis_home_cache()
    yield
    clear_metis_home_cache()


def test_search_query_normalizes_ddgs_results(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ddgs_text(query: str, *, max_results: int, region: str = "", timelimit: str = "") -> list[dict[str, Any]]:
        assert query == "metis search"
        assert max_results == 5
        return [
            {
                "title": "First",
                "href": "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%23frag",
                "body": "Snippet one",
            },
            {"title": "Duplicate", "href": "https://example.com/a", "body": "skip"},
            {"title": "Second", "href": "https://example.org/b", "body": "Snippet two"},
        ]

    monkeypatch.setattr(search_broker, "_ddgs_text", fake_ddgs_text)

    result = search_broker.metis_search_query("metis search", max_results=5)

    assert result["ok"] is True
    assert [item["url"] for item in result["results"]] == ["https://example.com/a", "https://example.org/b"]
    assert result["results"][0]["rank"] == 1
    assert result["results"][0]["source"] == "example.com"
    assert result["provider"] == "ddgs"
    assert result["research_activity"]["stats"]["search_results"] == 2
    assert result["research_activity"]["job_id"]
    assert get_research_job(result["research_activity"]["job_id"]) is not None


def test_web_search_reports_missing_ddgs(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_ddgs(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise ImportError("no ddgs")

    monkeypatch.setattr(search_broker, "_ddgs_text", missing_ddgs)

    result = web_search("anything")

    assert "web_search 失败" in result
    assert "pip install ddgs" in result


def test_search_query_relaxes_site_query_when_ddgs_reports_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_ddgs_text(query: str, *, max_results: int, region: str = "", timelimit: str = "") -> list[dict[str, Any]]:
        calls.append(query)
        if query.startswith("site:m.miit.gov.cn"):
            raise RuntimeError("ddgs: DDGSException: No results found.")
        return [
            {
                "title": "工信部智能网联汽车准入通知",
                "href": "https://www.miit.gov.cn/jgsj/zbys/qcgy/art/2024/art_demo.html",
                "body": "智能网联汽车准入和上路通行试点通知",
            }
        ]

    monkeypatch.setattr(search_broker, "_ddgs_text", fake_ddgs_text)

    result = search_broker.metis_search_query("site:m.miit.gov.cn 智能网联汽车 准入 通行试点 2024 通知")

    assert result["ok"] is True
    assert calls[:2] == [
        "site:m.miit.gov.cn 智能网联汽车 准入 通行试点 2024 通知",
        "m.miit.gov.cn 智能网联汽车 准入 通行试点 2024 通知",
    ]
    assert result["effective_query"] == calls[1]
    assert result["count"] == 1
    assert result["research_activity"]["phases"][0]["status"] == "complete"
    assert "已放宽检索" in result["research_activity"]["phases"][0]["summary"]
    job = get_research_job(result["research_activity"]["job_id"])
    assert job is not None
    assert job["status"] == "complete"
    assert any(attempt["status"] == "no_results" for attempt in job["attempts"])


def test_search_query_treats_ddgs_no_results_as_empty_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ddgs_text(query: str, *, max_results: int, region: str = "", timelimit: str = "") -> list[dict[str, Any]]:
        raise RuntimeError("DDGSException: No results found.")

    monkeypatch.setattr(search_broker, "_ddgs_text", fake_ddgs_text)

    result = search_broker.metis_search_query("unlikely exact search phrase")
    output = search_broker.format_search_response(result)

    assert result["ok"] is True
    assert result["count"] == 0
    assert result["research_activity"]["phases"][0]["status"] == "complete"
    assert "未找到匹配来源" in result["research_activity"]["phases"][0]["summary"]
    assert "web_search 失败" not in output
    assert "未找到可用结果" in output


def test_page_read_extracts_fixture_html(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        url = "https://example.com/final"
        headers = {"content-type": "text/html; charset=utf-8"}
        content = b"""
        <html>
          <head><title>Fixture Page</title></head>
          <body><main><h1>Hello</h1><p>Useful evidence.</p><script>bad()</script></main></body>
        </html>
        """

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        assert url == "https://example.com/page"
        assert kwargs["allow_redirects"] is True
        return FakeResponse()

    monkeypatch.setattr("requests.get", fake_get)

    result = search_broker.metis_page_read("https://example.com/page", max_chars=2000)

    assert result["ok"] is True
    assert result["title"] == "Fixture Page"
    assert "Hello" in result["text"]
    assert "Useful evidence" in result["text"]
    assert "bad()" not in result["text"]


def test_page_read_blocks_non_https_without_network() -> None:
    result = search_broker.metis_page_read("http://example.com/page")

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "https" in result["error"]


def test_page_read_marks_rate_limited_status_as_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        url = "https://example.com/page"
        status_code = 429
        headers = {"content-type": "text/html"}
        content = b"<html><body>rate limited</body></html>"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("requests.get", lambda *_a, **_k: FakeResponse())

    result = search_broker.metis_page_read("https://example.com/page")

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "429" in result["error"]


def test_page_read_marks_thin_content_as_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        url = "https://example.com/page"
        status_code = 200
        headers = {"content-type": "text/html"}
        content = b"<html><body><div id='root'></div></body></html>"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("requests.get", lambda *_a, **_k: FakeResponse())

    result = search_broker.metis_page_read("https://example.com/page")

    assert result["ok"] is True
    assert result["status"] == "partial"
    assert "note" in result


def test_research_dedupes_urls_and_limits_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        search_broker,
        "metis_search_query",
        lambda question, max_results=5, provider="auto": {
            "ok": True,
            "query": question,
            "provider": provider,
            "provider_chain": [provider],
            "results": [
                {"rank": 1, "title": "One", "url": "https://example.com/a", "snippet": "A"},
                {"rank": 2, "title": "Dup", "url": "https://example.com/a#section", "snippet": "Dup"},
                {"rank": 3, "title": "Two", "url": "https://example.org/b", "snippet": "B"},
            ],
        },
    )
    opened: list[str] = []

    def fake_page_read(url: str, max_chars: int = 4000) -> dict[str, Any]:
        opened.append(url)
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "title": "Evidence",
            "content_type": "text/html",
            "text": "Evidence text",
        }

    monkeypatch.setattr(search_broker, "metis_page_read", fake_page_read)

    result = search_broker.metis_search_research("question", max_results=5, max_pages=1)

    assert result["ok"] is True
    assert opened == ["https://example.com/a"]
    assert len(result["pages"]) == 1


def test_web_research_formats_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.tools.coding.network_external.web.web_research.metis_search_research",
        lambda **_kwargs: {
            "ok": True,
            "question": "what changed",
            "search": {
                "results": [{"rank": 1, "title": "Source", "url": "https://example.com", "snippet": "snippet"}]
            },
            "pages": [
                {
                    "search_result": {"rank": 1, "title": "Source", "url": "https://example.com"},
                    "final_url": "https://example.com",
                    "title": "Source",
                    "content_type": "text/html",
                    "text": "Evidence body",
                }
            ],
            "failures": [],
        },
    )

    result = web_research("what changed")

    assert "Web Research" in result
    assert "Evidence body" in result
    assert "Use the evidence URLs" in result
    assert "METIS_RESEARCH_JSON" in result


def test_metis_search_research_returns_report_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        search_broker,
        "_ddgs_text",
        lambda *_args, **_kwargs: [
            {
                "title": "Source",
                "href": "https://example.com/a",
                "body": "Useful source",
            }
        ],
    )
    monkeypatch.setattr(
        search_broker,
        "metis_page_read",
        lambda *_args, **_kwargs: {
            "ok": True,
            "status": "ok",
            "url": "https://example.com/a",
            "final_url": "https://example.com/a",
            "title": "Source",
            "content_type": "text/html",
            "text": "Evidence body",
            "chars": 13,
            "truncated": False,
        },
    )

    result = search_broker.metis_search_research("what changed", max_results=3, max_pages=1)
    activity = result["research_activity"]
    job = get_research_job(activity["job_id"])

    assert activity["job_status"] == "complete"
    assert activity["report_filename"].endswith(".md")
    assert Path(activity["report_path"]).is_file()
    assert job is not None
    assert job["report_filename"] == activity["report_filename"]


def test_format_research_response_flags_partial_and_blocked_evidence() -> None:
    research = {
        "ok": True,
        "question": "what changed",
        "search": {"results": []},
        "pages": [
            {
                "search_result": {"rank": 1, "title": "Thin", "url": "https://example.com/thin"},
                "final_url": "https://example.com/thin",
                "title": "Thin",
                "content_type": "text/html",
                "text": "tiny",
                "status": "partial",
            }
        ],
        "failures": [
            {"url": "https://example.com/blocked", "status": "blocked", "error": "HTTP 429"},
        ],
    }

    output = search_broker.format_research_response(research)

    assert "PARTIAL EVIDENCE" in output
    assert "blocked/rate-limited" in output
    assert "could not be verified" in output


def test_fetch_content_uses_github_raw_and_embeds_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []

    def fake_page_read(url: str, max_chars: int = 4000) -> dict[str, Any]:
        opened.append(url)
        return {
            "ok": True,
            "status": "ok",
            "url": url,
            "final_url": url,
            "title": "README",
            "content_type": "text/plain",
            "text": "# README\nhello",
            "chars": 14,
            "truncated": False,
        }

    monkeypatch.setattr("backend.tools.coding.network_external.web.fetch_content.metis_page_read", fake_page_read)

    result = fetch_content("https://github.com/org/repo/blob/main/README.md", max_chars=2000)

    assert opened == ["https://raw.githubusercontent.com/org/repo/main/README.md"]
    assert "Source type: github_blob" in result
    assert "METIS_RESEARCH_JSON" in result
    assert "# README" in result


def test_fetch_content_uses_github_repo_clone_special_case(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_clone(repo_url: str, target: str, ref: str = "") -> dict[str, Any]:
        calls.append((repo_url, target, ref))
        return {"ok": True, "status": "cached", "error": ""}

    monkeypatch.setattr("backend.tools.coding.network_external.web.fetch_content._ensure_github_clone", fake_clone)
    monkeypatch.setattr(
        "backend.tools.coding.network_external.web.fetch_content._github_tree_summary",
        lambda root, subpath, max_chars: "# GitHub repository snapshot\n\n- README.md",
    )

    result = fetch_content("https://github.com/org/repo/tree/main/src", max_chars=2000)

    assert calls and calls[0][0] == "https://github.com/org/repo.git"
    assert "Source type: github_tree" in result
    assert "github_clone" in result
    assert "METIS_RESEARCH_JSON" in result


def test_fetch_content_jina_fallback_keeps_original_source_url(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_page_read(url: str, max_chars: int = 4000) -> dict[str, Any]:
        if "r.jina.ai" in url:
            return {
                "ok": True,
                "status": "ok",
                "url": url,
                "final_url": url,
                "title": "(untitled)",
                "content_type": "text/plain",
                "text": "Readable article body " * 80,
                "chars": 1760,
                "truncated": False,
            }
        return {
            "ok": True,
            "status": "partial",
            "url": url,
            "final_url": url,
            "title": "",
            "content_type": "text/html",
            "text": "short",
            "chars": 5,
            "truncated": False,
        }

    monkeypatch.setattr("backend.tools.coding.network_external.web.fetch_content.metis_page_read", fake_page_read)

    result = fetch_content("https://example.com/article", max_chars=2000)

    assert "Provider: jina_reader" in result
    assert "Final URL: https://example.com/article" in result
    assert "r.jina.ai" not in result


def test_research_job_sanitizes_jina_reader_sources_on_read() -> None:
    job = save_research_activity_job(
        {
            "kind": "research",
            "title": "Tesla FSD",
            "query": "Tesla FSD",
            "sources": [
                {
                    "id": "s1",
                    "title": "(untitled)",
                    "url": "https://r.jina.ai/http://r.jina.ai/http://https://finance.sina.com.cn/roll/2026-05-22/doc-inhytyyr7714288.shtml",
                    "domain": "r.jina.ai",
                    "status": "opened",
                }
            ],
            "stats": {"sources": 1, "opened": 1},
        },
        report="Source: https://r.jina.ai/http://https://finance.sina.com.cn/roll/2026-05-22/doc-inhytyyr7714288.shtml",
    )

    saved = get_research_job(str(job["id"]))

    assert saved is not None
    source = saved["sources"][0]
    assert source["url"] == "https://finance.sina.com.cn/roll/2026-05-22/doc-inhytyyr7714288.shtml"
    assert source["domain"] == "finance.sina.com.cn"
    assert source["title"].startswith("finance.sina.com.cn")
    assert "r.jina.ai" not in saved["report"]
    assert saved["report_filename"].endswith(".md")
    assert "Tesla FSD" in saved["report_filename"]
    assert Path(saved["report_path"]).is_file()


def test_web_research_is_registered_in_schema_and_tool_map() -> None:
    names = {schema["function"]["name"] for schema in build_tools_schema()}

    assert "web_research" in names
    assert "web_research" in AVAILABLE_TOOLS
    assert "fetch_content" in names
    assert "fetch_content" in AVAILABLE_TOOLS
