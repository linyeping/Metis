from __future__ import annotations

from backend.core.paths import clear_metis_home_cache
from backend.runtime.checkpoint_store import (
    list_checkpoints,
    load_checkpoint,
    load_latest,
    save_checkpoint,
)


def test_checkpoint_store_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("METIS_HOME", str(tmp_path))
    clear_metis_home_cache()
    try:
        first_id = save_checkpoint(
            "session:one",
            {"history": [{"role": "user", "content": "hello"}], "runtime": {"turn": 1}},
        )
        second_id = save_checkpoint(
            "session:one",
            {"history": [{"role": "assistant", "content": "world"}], "runtime": {"turn": 2}},
        )

        listed = list_checkpoints("session:one")
        assert [item["checkpoint_id"] for item in listed[:2]] == [second_id, first_id]
        assert listed[0]["message_count"] == 1
        assert listed[0]["turn"] == 2

        latest = load_latest("session:one")
        assert latest is not None
        assert latest["checkpoint_id"] == second_id
        assert latest["state"]["history"][0]["content"] == "world"

        first = load_checkpoint("session:one", first_id)
        assert first is not None
        assert first["state"]["history"][0]["content"] == "hello"
    finally:
        clear_metis_home_cache()
