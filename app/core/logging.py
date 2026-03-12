"""
Structured JSON logging setup for the Winnow application.

``setup_logging()`` is called once from the FastAPI lifespan handler in
``app/main.py`` **before** ``bootstrap()`` so that all startup log records
are emitted as valid JSON from the very first line.

Uses ``python-json-logger`` (``pythonjsonlogger.json.JsonFormatter``) which
correctly reads timestamps from ``LogRecord.created``, preserves all ``extra``
fields passed via ``logger.info(..., extra={...})``, and handles exception
formatting automatically.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
"""

from __future__ import annotations

import logging
import sys

from pythonjsonlogger.json import JsonFormatter


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure the root logger with a JSON formatter writing to stdout.

    Safe to call multiple times — subsequent calls are no-ops because the
    handler is only added when the root logger has no handlers yet.

    The formatter emits the following fields on every record:
    - ``timestamp`` — ISO-8601 UTC derived from ``LogRecord.created``
    - ``level``     — log level name (INFO, WARNING, …)
    - ``logger``    — logger name (module path)
    - ``message``   — formatted log message
    - any ``extra`` keys passed at the call site (e.g. ``submission_id``)
    """
    root = logging.getLogger()
    if root.handlers:
        return

    formatter = JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={
            "asctime": "timestamp",
            "levelname": "level",
            "name": "logger",
        },
        datefmt="%Y-%m-%dT%H:%M:%S.%f%z",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.setLevel(level)
    root.addHandler(handler)
