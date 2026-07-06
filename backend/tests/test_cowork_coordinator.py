from __future__ import annotations

from pathlib import Path

import pytest

from backend.runtime.cancellation import OperationCancelled
from backend.runtime.cowork_coordinator import iter_local_cowork_events


def test_local_cowork_propagates_cancel_before_subrun(tmp_path: Path) -> None:
    events = iter_local_cowork_events(
        "Inspect implementation",
        workspace_root=str(tmp_path),
        run_id="run_cancel",
        session_id="session_cancel",
        cancelled=lambda: True,
    )

    first = next(events)

    assert first["kind"] == "runtime_status"
    with pytest.raises(OperationCancelled):
        next(events)
