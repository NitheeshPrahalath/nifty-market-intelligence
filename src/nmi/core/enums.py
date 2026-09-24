"""Shared enumerations for the Nifty Market Intelligence platform."""

from __future__ import annotations

import enum


class Exchange(enum.StrEnum):
    NSE = "NSE"
    BSE = "BSE"


class InstrumentType(enum.StrEnum):
    EQUITY = "EQ"
    INDEX = "INDEX"


class CorporateActionType(enum.StrEnum):
    SPLIT = "SPLIT"
    BONUS = "BONUS"
    DIVIDEND = "DIVIDEND"
    RIGHTS = "RIGHTS"
    MERGER = "MERGER"
    DEMERGER = "DEMERGER"
    OTHER = "OTHER"


class PeriodType(enum.StrEnum):
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"
    HALF_YEARLY = "HALF_YEARLY"


class RunStatus(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class Severity(enum.StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class IndexCode(enum.StrEnum):
    NIFTY_50 = "NIFTY_50"
    NIFTY_MIDCAP_150 = "NIFTY_MIDCAP_150"
    NIFTY_SMALLCAP_250 = "NIFTY_SMALLCAP_250"
