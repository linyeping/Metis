import logging

from backend.web.logging_utils import SafeRotatingFileHandler


def test_safe_rotating_file_handler_writes_when_rollover_is_locked(tmp_path) -> None:
    log_path = tmp_path / "metis-backend.log"
    handler = SafeRotatingFileHandler(str(log_path), maxBytes=1, backupCount=1, encoding="utf-8")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "second line", (), None)

    handler.emit(logging.LogRecord("test", logging.INFO, __file__, 1, "first line", (), None))
    handler.doRollover = lambda: (_ for _ in ()).throw(PermissionError("locked"))  # type: ignore[method-assign]
    handler.emit(record)
    handler.close()

    content = log_path.read_text(encoding="utf-8")
    assert "first line" in content
    assert "second line" in content
