from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, TextIO

from backend.version import __version__
from backend.web.session_db import MetisSessionDB

from .args import SessionCommandArgs


class CliSessionError(ValueError):
    pass


@dataclass(frozen=True)
class ResumeSession:
    session_id: str
    history: List[Dict[str, Any]]
    workspace: str
    mode: str


class CliSessionStore:
    def __init__(self, db: Optional[MetisSessionDB] = None) -> None:
        self.db = db or MetisSessionDB()

    def resolve_resume(self, session_id: str = "", *, latest: bool = False) -> ResumeSession:
        session = self._resolve_session(session_id, latest=latest)
        workspace = self.db.get_workspace(str(session.get("workspace_id") or ""))
        return ResumeSession(
            session_id=str(session["id"]),
            history=_copy_history(session.get("history")),
            workspace=str((workspace or {}).get("path") or ""),
            mode=str(session.get("mode") or "chat"),
        )

    def begin_run(
        self,
        *,
        prompt: str,
        workspace: Path,
        resume: Optional[ResumeSession] = None,
    ) -> tuple[str, List[Dict[str, Any]]]:
        user_message = {"role": "user", "content": prompt}
        workspace_row = self.db.create_workspace(str(workspace), name=workspace.name or str(workspace))
        if resume is not None:
            history = [*resume.history, user_message]
            if not self.db.update_session_fields(
                resume.session_id,
                history=history,
                mode=resume.mode,
                workspace_id=str(workspace_row["id"]),
            ):
                raise CliSessionError(f"session not found: {resume.session_id}")
            return resume.session_id, history

        now = self.db.next_timestamp()
        session_id = f"cli_{uuid.uuid4().hex}"
        history = [user_message]
        self.db.upsert_session(
            {
                "id": session_id,
                "title": _title_from_prompt(prompt),
                "history": history,
                "compact_state": {},
                "mode": "code",
                "workspace_id": str(workspace_row["id"]),
                "created_at": now,
                "updated_at": now,
                "archived_at": 0.0,
                "unread": False,
            }
        )
        return session_id, history

    def finish_run(
        self,
        session_id: str,
        *,
        transcript_records: Iterable[Mapping[str, Any]],
        final_text: str,
    ) -> None:
        session = self.db.get_session(session_id)
        if session is None:
            return
        history = _copy_history(session.get("history"))
        history.extend(dict(item) for item in transcript_records if isinstance(item, Mapping))
        if str(final_text or "").strip():
            history.append({"role": "assistant", "content": str(final_text)})
        self.db.update_session_fields(session_id, history=history)

    def list_payload(self, *, limit: int, archived: bool) -> Dict[str, Any]:
        items = self.db.list_sessions(archived=archived)[: max(1, int(limit))]
        sessions = [self._summary(item) for item in items]
        return {
            "schema": "metis.cli_sessions.v1",
            "sessions": sessions,
            "count": len(sessions),
            "archived": bool(archived),
        }

    def show_payload(self, session_id: str) -> Dict[str, Any]:
        session = self._resolve_session(session_id)
        workspace = self.db.get_workspace(str(session.get("workspace_id") or ""))
        return {
            "schema": "metis.cli_session.v1",
            **self._summary(session),
            "workspace": str((workspace or {}).get("path") or ""),
            "history": _copy_history(session.get("history")),
            "compact_state": dict(session.get("compact_state") or {}),
        }

    def export_payload(self, session_id: str) -> Dict[str, Any]:
        shown = self.show_payload(session_id)
        shown.pop("schema", None)
        return {
            "schema": "metis.session_export.v1",
            "exported_at": time.time(),
            "metis_version": __version__,
            "session": shown,
        }

    def _resolve_session(self, session_id: str, *, latest: bool = False) -> Dict[str, Any]:
        if latest:
            sessions = self.db.list_sessions()
            if not sessions:
                raise CliSessionError("no sessions are available to continue")
            resolved = self.db.get_session(str(sessions[0]["id"]))
            if resolved is None:
                raise CliSessionError("latest session is unavailable")
            return resolved

        query = str(session_id or "").strip()
        if not query:
            raise CliSessionError("session ID is required")
        exact = self.db.get_session(query)
        if exact is not None:
            return exact
        matches = [item for item in self.db.iter_full_sessions() if str(item.get("id") or "").startswith(query)]
        if not matches:
            raise CliSessionError(f"session not found: {query}")
        if len(matches) > 1:
            raise CliSessionError(f"session prefix is ambiguous: {query}")
        return matches[0]

    def _summary(self, session: Mapping[str, Any]) -> Dict[str, Any]:
        history = session.get("history") if isinstance(session.get("history"), list) else None
        if history is None:
            full = self.db.get_session(str(session.get("id") or "")) or {}
            history = full.get("history") if isinstance(full.get("history"), list) else []
        workspace = self.db.get_workspace(str(session.get("workspace_id") or ""))
        return {
            "id": str(session.get("id") or ""),
            "title": str(session.get("title") or ""),
            "mode": str(session.get("mode") or "chat"),
            "workspace": str((workspace or {}).get("path") or ""),
            "created_at": float(session.get("created_at") or 0.0),
            "updated_at": float(session.get("updated_at") or 0.0),
            "archived": bool(float(session.get("archived_at") or 0.0) > 0),
            "message_count": len(history),
        }


def handle_session_command(args: SessionCommandArgs, *, stdout: TextIO) -> int:
    store = CliSessionStore()
    if args.action == "list":
        payload = store.list_payload(limit=args.limit, archived=args.archived)
        if args.output_format == "json":
            _write_json_line(stdout, payload)
        else:
            _write_session_list(stdout, payload["sessions"])
        return 0
    if args.action == "show":
        payload = store.show_payload(args.session_id)
        if args.output_format == "json":
            _write_json_line(stdout, payload)
        else:
            stdout.write(_session_markdown(payload))
            stdout.flush()
        return 0
    if args.action == "export":
        payload = store.export_payload(args.session_id)
        rendered = (
            _session_markdown(payload["session"])
            if args.export_format == "markdown"
            else json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
        if args.output:
            _atomic_write_text(Path(args.output).expanduser(), rendered)
        else:
            stdout.write(rendered)
            stdout.flush()
        return 0
    raise CliSessionError(f"unsupported sessions action: {args.action}")


def tool_transcript_record(event: Any) -> Optional[Dict[str, Any]]:
    kind = str(getattr(event, "type", "") or getattr(event, "kind", "") or "")
    if kind != "tool_result":
        return None
    result = str(getattr(event, "result", "") or "")
    if len(result) > 8000:
        result = result[:7997] + "..."
    return {
        "role": "assistant",
        "content": "",
        "metis_kind": "tool",
        "metis_tool": {
            "call_id": str(getattr(event, "call_id", "") or ""),
            "name": str(getattr(event, "tool_name", "") or ""),
            "result": result,
        },
    }


def _copy_history(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _title_from_prompt(prompt: str) -> str:
    title = " ".join(str(prompt or "").split())
    return title[:77] + "..." if len(title) > 80 else title or "CLI session"


def _write_session_list(stdout: TextIO, sessions: Iterable[Mapping[str, Any]]) -> None:
    items = list(sessions)
    if not items:
        stdout.write("No sessions.\n")
        stdout.flush()
        return
    for item in items:
        timestamp = _format_time(float(item.get("updated_at") or 0.0))
        workspace = str(item.get("workspace") or "-")
        stdout.write(f"{item.get('id')}  {timestamp}  {item.get('title')}  [{workspace}]\n")
    stdout.flush()


def _session_markdown(session: Mapping[str, Any]) -> str:
    lines = [
        f"# {session.get('title') or 'Metis session'}",
        "",
        f"- Session: `{session.get('id') or ''}`",
        f"- Workspace: `{session.get('workspace') or ''}`",
        f"- Updated: {_format_time(float(session.get('updated_at') or 0.0))}",
        "",
    ]
    for message in session.get("history") or []:
        if not isinstance(message, Mapping):
            continue
        if message.get("metis_kind") == "tool":
            tool = message.get("metis_tool") if isinstance(message.get("metis_tool"), Mapping) else {}
            lines.extend([f"## Tool: {tool.get('name') or 'tool'}", "", "```text", str(tool.get("result") or ""), "```", ""])
            continue
        role = str(message.get("role") or "message").capitalize()
        content = message.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
        lines.extend([f"## {role}", "", str(text or ""), ""])
    return "\n".join(lines).rstrip() + "\n"


def _format_time(value: float) -> str:
    if value <= 0:
        return "-"
    return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")


def _write_json_line(stdout: TextIO, payload: Mapping[str, Any]) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    stdout.flush()


def _atomic_write_text(path: Path, text: str) -> None:
    target = path.resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
