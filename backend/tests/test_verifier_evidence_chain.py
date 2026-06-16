from __future__ import annotations

from backend.runtime.evidence_chain import EVIDENCE_CHAIN_SCHEMA, build_verifier_evidence_payload


def test_build_verifier_evidence_payload_marks_failed_checks() -> None:
    payload = build_verifier_evidence_payload(
        surface="preview_browser",
        assertion="确认登录按钮可见且没有控制台错误",
        checks={"button_visible": True, "no_console_errors": False},
        check_details={"no_console_errors": {"counts": {"console_errors": 1}}},
        evidence=[{"kind": "page", "url": "http://localhost:5174", "title": "Metis"}],
        subject={"url": "http://localhost:5174", "title": "Metis"},
    )

    assert payload["evidence_schema"] == EVIDENCE_CHAIN_SCHEMA
    assert payload["verdict"]["ok"] is False
    assert payload["verdict"]["failed_checks"] == ["no_console_errors"]
    assert payload["verdict"]["passed"] == 1
    assert payload["verdict"]["total"] == 2
    assert payload["evidence_chain_v2"][0]["kind"] == "page"
    assert payload["evidence_chain_v2"][-1]["check"] == "no_console_errors"
    assert payload["evidence_chain_v2"][-1]["ok"] is False
