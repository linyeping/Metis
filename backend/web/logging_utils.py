from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler


class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotating file handler that degrades cleanly when another process owns the log.

    Python's stdlib RotatingFileHandler is not safe when multiple Windows
    processes hold the same log file. If rollover loses the file-lock race, keep
    writing the current file instead of emitting noisy logging errors to stderr.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.shouldRollover(record):
                try:
                    self.doRollover()
                except OSError:
                    self._ensure_stream()
            logging.FileHandler.emit(self, record)
        except Exception:
            self.handleError(record)

    def _ensure_stream(self) -> None:
        if self.stream is None:
            self.stream = self._open()


def backend_log_path(base_path: str) -> str:
    """Return the backend log path, with an opt-in pid suffix for diagnostics."""
    if os.environ.get("METIS_LOG_PID_SUFFIX", "").strip().lower() in {"1", "true", "yes", "on"}:
        root, ext = os.path.splitext(base_path)
        return f"{root}.{os.getpid()}{ext or '.log'}"
    return base_path
