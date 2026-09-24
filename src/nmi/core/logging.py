"""Logging bootstrap.

Every ingested record, validation finding and job step is logged in a way that
will feed the audit trail and observability (OpenTelemetry/Prometheus) layer in
Phase 10.
"""

from __future__ import annotations

import logging

from nmi.core.config import settings

_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | "
    "%(message)s"
)


def setup_logging(level: str | None = None) -> None:
    logging.basicConfig(
        level=(level or settings.log_level).upper(),
        format=_FORMAT,
    )
    # Quiet noisy third-party loggers.
    for noisy in ("urllib3", "yfinance", "matplotlib", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
