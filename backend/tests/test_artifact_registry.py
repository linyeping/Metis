from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from flask import Flask

from backend.runtime import artifact_registry
from backend.core.paths import clear_metis_home_cache
from backend.runtime.artifact_registry import (
    ArtifactFilters,
    ArtifactRegistryError,
    get_artifact,
    list_artifacts,
    register_artifact,
    registry_path,
    reindex_artifacts,
)
from backend.tools.coding.network_external.web.research_jobs import save_research_activity_job
from backend.web import artifact_routes
from backend.web.artifact_routes import artifact_bp


@pytest.fixture(autouse=True)
def isolated_artifact_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "metis-home"))
    monkeypatch.setenv("METIS_DATA_ROOT", str(tmp_path / "data-root"))
    clear_metis_home_cache()
    yield
    clear_metis_home_cache()


def test_register_artifact_upserts_and_persists(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "report.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Report\n", encoding="utf-8")

    first = register_artifact(
        kind="report",
        title="Report",
        path=str(target),
        source_event_id="evt_1",
        workspace_root=str(workspace),
    )
    second = register_artifact(
        kind="report",
        title="Updated report",
        path=str(target),
        source_event_id="evt_1",
        workspace_root=str(workspace),
        metadata={"updated": True},
    )

    assert first["artifact_id"].startswith("art_")
    assert second["artifact_id"] == first["artifact_id"]
    assert second["created_at"] == first["created_at"]
    assert registry_path().is_file()
    assert get_artifact(first["artifact_id"])["title"] == "Updated report"

    rows = list_artifacts(ArtifactFilters(kind="report", limit=20))
    assert len(rows) == 1
    assert rows[0]["metadata"]["updated"] is True


def test_register_artifact_rejects_unsafe_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    denied = Path("C:/metis-denied/outside.txt") if os.name == "nt" else Path("/metis-denied/outside.txt")

    with pytest.raises(ArtifactRegistryError):
        register_artifact(
            kind="document",
            title="Outside",
            path=str(denied),
            workspace_root=str(workspace),
        )


def test_register_office_document_requires_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "output" / "report.docx"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"placeholder")

    monkeypatch.setattr(
        artifact_registry.office_artifact_validation,
        "validate_office_artifact",
        lambda path: {"ok": False, "schema": "metis.office_artifact_validation.v1", "summary": "render failed"},
    )
    with pytest.raises(ArtifactRegistryError, match="office artifact validation failed"):
        register_artifact(kind="document", title="Report", path=str(target), workspace_root=str(workspace))

    monkeypatch.setattr(
        artifact_registry.office_artifact_validation,
        "validate_office_artifact",
        lambda path: {
            "ok": True,
            "schema": "metis.office_artifact_validation.v1",
            "status": "validated",
            "summary": "validated with render evidence",
        },
    )
    artifact = register_artifact(kind="document", title="Report", path=str(target), workspace_root=str(workspace))
    assert artifact["metadata"]["validated"] is True
    assert artifact["metadata"]["artifact_state"] == "complete"
    assert artifact["metadata"]["office_validation"]["ok"] is True


def test_research_report_registers_artifact() -> None:
    job = save_research_activity_job(
        {
            "kind": "research",
            "title": "Artifact registry report",
            "query": "artifact registry",
            "session_id": "sess_1",
            "run_id": "run_1",
        },
        report="## Summary\n\nRegistry report body.",
    )

    assert job["artifact_id"].startswith("art_")
    artifact = get_artifact(job["artifact_id"])
    assert artifact is not None
    assert artifact["schema"] == "metis.artifact.v1"
    assert artifact["kind"] == "report"
    assert artifact["session_id"] == "sess_1"
    assert artifact["run_id"] == "run_1"
    assert artifact["metadata"]["job_id"] == job["id"]
    assert Path(artifact["path"]).is_file()


def test_reindex_restores_reports_and_preview_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.core.paths import metis_dir

    report = metis_dir("research", "reports") / "saved-report.md"
    report.write_text("# Saved\n", encoding="utf-8")

    data_root = Path(os.environ["METIS_DATA_ROOT"])
    evidence_dir = data_root / "electron" / "preview-evidence"
    evidence_dir.mkdir(parents=True)
    evidence = evidence_dir / "preview.json"
    evidence.write_text(
        json.dumps({"result": {"title": "Preview title", "url": "http://127.0.0.1:5173/"}}),
        encoding="utf-8",
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = reindex_artifacts(workspace_root=str(workspace))

    assert result["ok"] is True
    kinds = {artifact["kind"] for artifact in list_artifacts(ArtifactFilters(limit=20))}
    assert "report" in kinds
    assert "preview_evidence" in kinds


def test_artifact_routes_register_and_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "doc.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Doc\n", encoding="utf-8")
    monkeypatch.setattr(artifact_routes, "active_workspace_root", lambda: str(workspace))

    app = Flask(__name__)
    app.register_blueprint(artifact_bp)
    with app.test_client() as client:
        response = client.post(
            "/artifacts",
            json={"kind": "document", "title": "Doc", "path": str(target), "mime": "text/markdown"},
        )
        assert response.status_code == 200
        payload = response.get_json()
        artifact_id = payload["artifact"]["artifact_id"]

        response = client.get("/artifacts?kind=document")
        assert response.status_code == 200
        listed = response.get_json()["artifacts"]
        assert [item["artifact_id"] for item in listed] == [artifact_id]

        response = client.get(f"/artifacts/{artifact_id}")
        assert response.status_code == 200
        assert response.get_json()["artifact"]["title"] == "Doc"
