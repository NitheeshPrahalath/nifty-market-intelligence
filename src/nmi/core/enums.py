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


class BacktestKind(enum.StrEnum):
    """A single historical run, or one window of a walk-forward analysis."""

    SINGLE = "SINGLE"
    WALK_FORWARD = "WALK_FORWARD"


class BacktestExitReason(enum.StrEnum):
    """Why a simulated position was closed (Phase 6).

    ``STOP_LOSS``/``TARGET``/``TIME_STOP`` are the mechanical exits, the
    ``*_EXIT`` members are the Phase-5 mechanisms, and the last two are
    bookkeeping exits forced by the end of the test window.
    """

    STOP_LOSS = "STOP_LOSS"
    TARGET = "TARGET"
    TIME_STOP = "TIME_STOP"
    STRATEGY_EXIT = "STRATEGY_EXIT"
    RISK_EXIT = "RISK_EXIT"
    TECHNICAL_EXIT = "TECHNICAL_EXIT"
    FUNDAMENTAL_EXIT = "FUNDAMENTAL_EXIT"
    VALUATION_EXIT = "VALUATION_EXIT"
    EVENT_EXIT = "EVENT_EXIT"
    END_OF_TEST = "END_OF_TEST"
    LIQUIDATION = "LIQUIDATION"


class BacktestRejectReason(enum.StrEnum):
    """Why a queued order never became a fill."""

    NO_BAR = "NO_BAR"
    NOT_A_MEMBER = "NOT_A_MEMBER"
    NO_CASH = "NO_CASH"
    BELOW_LOT = "BELOW_LOT"
    LIMIT_PRICE_MISSED = "LIMIT_PRICE_MISSED"
    ALREADY_HELD = "ALREADY_HELD"
    NO_FREE_SLOT = "NO_FREE_SLOT"
