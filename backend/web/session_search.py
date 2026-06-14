from __future__ import annotations

from typing import Any, Dict, List

from .session_db import get_session_db


def index_session(session: Any) -> None:
    get_session_db().index_session(session)


def delete_session(session_id: str) -> None:
    get_session_db().delete_search_session(session_id)


def rebuild_index() -> None:
    get_session_db().rebuild_search_index()


def search_sessions(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    return get_session_db().search_sessions(query, limit=limit)
