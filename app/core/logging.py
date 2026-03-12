"""
Structured JSON logging setup for the Winnow application.

``setup_logging()`` is called once from the FastAPI lifespan handler in
``app/main.py``. It configures the root logger to emit newline-delimited JSON
records, which are easy to ingest by log aggregators (Loki, CloudWatch, etc.).

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class _JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_object: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_object["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_object)


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure the root logger with a JSON formatter writing to stdout.

    Safe to call multiple times — subsequent calls are no-ops because the
    handler is only added when the root logger has no handlers yet.
    """
    root = logging.getLogger()
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.setLevel(level)
    root.addHandler(handler)
