"""Logging helpers with optional remote delivery."""
from __future__ import annotations

import json
import logging
import ssl
import sys
from logging import Handler
from typing import Optional
from urllib import request

from .config import GlobalConfig, RemoteLoggingConfig

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


class RemoteLogHandler(Handler):
    """Send log records to a remote HTTP endpoint."""

    def __init__(self, config: RemoteLoggingConfig):
        super().__init__()
        self._config = config
        self._context = ssl.create_default_context()
        if not config.verify:
            self._context.check_hostname = False
            self._context.verify_mode = ssl.CERT_NONE

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - network side effects
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": self.formatTime(record),
            "module": record.module,
        }
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(self._config.target, data=data, method=self._config.method.upper())
        req.add_header("Content-Type", "application/json")
        for header_name, header_value in self._config.headers.items():
            req.add_header(header_name, header_value)
        try:
            with request.urlopen(req, context=self._context) as response:
                response.read()
        except Exception as exc:  # pragma: no cover - best effort logging
            print(f"Failed to emit remote log: {exc}", file=sys.stderr)


def configure_logging(config: Optional[GlobalConfig], level: int = logging.INFO) -> logging.Logger:
    """Configure the core conductor logger and return it."""

    logger = logging.getLogger("conductor")
    logger.setLevel(level)
    logger.handlers = []

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(stream_handler)

    if config and config.remote_logging and config.remote_logging.enabled:
        remote_handler = RemoteLogHandler(config.remote_logging)
        remote_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(remote_handler)

    return logger


def get_node_logger(node_id: str) -> logging.Logger:
    """Return a logger namespaced for a node."""

    return logging.getLogger(f"conductor.node.{node_id}")

