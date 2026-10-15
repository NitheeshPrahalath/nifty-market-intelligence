"""Logging bootstrap.

Logs are plain text by default and structured JSON when ``LOG_FORMAT=json``.
Both carry the current request id when one is bound, so a log line emitted deep
in a request handler can be correlated with the ``X-Request-Id`` header and the
``api_requests`` audit row without threading a value through every call.

No third-party dependency: the JSON shape is small and stable, and the platform
already favours hand-rolled primitives (see ``nmi.api.kv`` and ``nmi.core.metrics``).
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime

from nmi.core.config import settings

_TEXT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

# Quiet noisy third-party loggers.
_NOISY = ("urllib3", "yfinance", "matplotlib", "apscheduler")

_request_id: ContextVar[str | None] = ContextVar("nmi_request_id", default=None)


def bind_request_id(value: str) -> Token:
    """Bind a request id to the current context; returns a reset token."""
    return _request_id.set(value)


def reset_request_id(token: Token) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    """The request id bound to the current context, if any."""
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """Render one log record as a single JSON object.

    Standard fields are always present; ``request_id`` appears when bound, an
    exception is rendered under ``error``, and any keyword passed via
    ``extra={"fields": {...}}`` is merged at the top level.
    """

    def format(self, record: logging.LogRecord) -> str:
        created = datetime.fromtimestamp(record.created, tz=UTC)
        payload: dict[str, object] = {
            "timestamp": created.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Configure the root logger. Safe to call more than once.

    Replaces the root handler so a re-invocation (e.g. a reloaded worker) does
    not stack duplicate handlers or double-print every line.
    """
    resolved_level = (level or settings.log_level).upper()
    resolved_fmt = (fmt or settings.log_format).lower()

    handler = logging.StreamHandler(sys.stderr)
    formatter = JsonFormatter() if resolved_fmt == "json" else logging.Formatter(_TEXT_FORMAT)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved_level)

    for noisy in _NOISY:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
