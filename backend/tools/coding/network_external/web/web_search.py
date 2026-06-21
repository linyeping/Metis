from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution
from backend.tools.coding.network_external.web.search_broker import format_search_response, metis_search_query


@trace_execution
def web_search(query: str, max_results: int = 5, region: str = "", timelimit: str = "") -> str:
    return format_search_response(
        metis_search_query(query=query, max_results=max_results, region=region, timelimit=timelimit)
    )
