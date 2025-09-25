"""In-memory logging helpers used by the dashboard."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, List, Optional

from conductor.logging_utils import LOG_FORMAT


@dataclass
class LogEntry:
    """Represents a single log record captured for the UI."""

    id: str
    created: float
    timestamp: str
    level: str
    logger: str
    message: str
    formatted: str


class _LogBuffer:
    def __init__(self, capacity: int = 500):
        self._entries: Deque[LogEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._capacity = capacity

    def append(self, record: logging.LogRecord, formatted: str) -> None:
        entry = LogEntry(
            id=uuid.uuid4().hex,
            created=record.created,
            timestamp=datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
            level=record.levelname,
            logger=record.name,
            message=record.getMessage(),
            formatted=formatted,
        )
        with self._lock:
            self._entries.append(entry)

    def snapshot(self, *, level: Optional[str] = None) -> List[LogEntry]:
        with self._lock:
            entries = list(self._entries)
        if level:
            level_value = logging._nameToLevel.get(level.upper(), logging.INFO)
            entries = [item for item in entries if logging._nameToLevel.get(item.level, 0) >= level_value]
        return entries

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @property
    def capacity(self) -> int:
        return self._capacity


class _DashboardLogHandler(logging.Handler):
    """Handler that stores records inside the shared dashboard buffer."""

    def __init__(self, buffer: _LogBuffer):
        super().__init__(level=logging.DEBUG)
        self._buffer = buffer
        self.setFormatter(logging.Formatter(LOG_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
        except Exception:  # pragma: no cover - defensive
            formatted = record.getMessage()
        self._buffer.append(record, formatted)


_buffer: Optional[_LogBuffer] = None
_handler: Optional[_DashboardLogHandler] = None
_install_lock = threading.Lock()


def install_dashboard_log_handler(*, capacity: int = 500) -> _LogBuffer:
    """Attach the in-memory handler once and return the buffer instance."""

    global _buffer, _handler
    with _install_lock:
        if _buffer is None or _buffer.capacity != capacity:
            _buffer = _LogBuffer(capacity=capacity)
            _handler = _DashboardLogHandler(_buffer)
        assert _handler is not None
        logger = logging.getLogger("conductor")
        if _handler not in logger.handlers:
            logger.addHandler(_handler)
        if logger.level > logging.INFO:
            logger.setLevel(logging.INFO)
        return _buffer


def get_buffer() -> _LogBuffer:
    if _buffer is None:
        raise RuntimeError("Dashboard log handler not installed.")
    return _buffer


__all__ = ["LogEntry", "install_dashboard_log_handler", "get_buffer"]
