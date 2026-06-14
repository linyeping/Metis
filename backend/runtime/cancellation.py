from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional


class OperationCancelled(RuntimeError):
    """Raised when a run is cancelled by the user."""


_local = threading.local()


def is_cancel_requested(cancel_event: Optional[threading.Event] = None) -> bool:
    event = cancel_event or current_cancel_event()
    return bool(event and event.is_set())


def raise_if_cancelled(cancel_event: Optional[threading.Event] = None) -> None:
    if is_cancel_requested(cancel_event):
        raise OperationCancelled("Operation cancelled")


def current_cancel_event() -> Optional[threading.Event]:
    event = getattr(_local, "cancel_event", None)
    return event if isinstance(event, threading.Event) else None


@contextmanager
def cancellation_context(cancel_event: Optional[threading.Event]) -> Iterator[None]:
    previous = current_cancel_event()
    _local.cancel_event = cancel_event
    try:
        yield
    finally:
        _local.cancel_event = previous


def wait_or_cancel(cancel_event: Optional[threading.Event], seconds: float) -> None:
    if cancel_event is not None and cancel_event.wait(max(0.0, seconds)):
        raise OperationCancelled("Operation cancelled")
