from __future__ import annotations

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution
from backend.tools.coding.network_external.web.search_broker import format_research_response, metis_search_research


@trace_execution
def web_research(
    question: str,
    max_results: int = 5,
    max_pages: int = 3,
    max_chars_per_page: int = 1800,
    reason: str = "",
    provider: str = "auto",
) -> str:
    _ = reason
    return format_research_response(
        metis_search_research(
            question=question,
            max_results=max_results,
            max_pages=max_pages,
            max_chars_per_page=max_chars_per_page,
            provider=provider,
        )
    )
