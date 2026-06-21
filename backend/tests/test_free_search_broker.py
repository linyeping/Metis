from __future__ import annotations

from typing import Any

import pytest
from backend.tools.coding.network_external.web import search_broker
from backend.tools.coding.network_external.web.web_research import web_research
from backend.tools.coding.network_external.web.web_search import web_search
from backend.tools.registry import AVAILABLE_TOOLS
from backend.tools.schema_definitions import build_tools_schema


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


def test_web_search_reports_missing_ddgs(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_ddgs(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise ImportError("no ddgs")

    monkeypatch.setattr(search_broker, "_ddgs_text", missing_ddgs)

    result = web_search("anything")

    assert "web_search 失败" in result
    assert "pip install ddgs" in result


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
        lambda question, max_results=5: {
            "ok": True,
            "query": question,
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


def test_web_research_is_registered_in_schema_and_tool_map() -> None:
    names = {schema["function"]["name"] for schema in build_tools_schema()}

    assert "web_research" in names
    assert "web_research" in AVAILABLE_TOOLS
