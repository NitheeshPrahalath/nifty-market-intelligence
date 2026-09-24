"""Data-quality validation for ingested market data.

Rules (configurable, never silently pass):

* ``OHLC_INCONSISTENT``    low > min(open,close) or high < max(open,close)
* ``NEGATIVE_VALUE``       any price/volume component below zero
* ``CLOSE_MISSING``        close is missing or zero (row unusable)
* ``OHLC_INCOMPLETE``      open/high/low missing but close present
* ``DUPLICATE_TRADE_DATE`` same instrument+date appears more than once in a batch
* ``NEGATIVE_VOLUME``      volume below zero
* ``ZERO_VOLUME``          close > 0 but volume == 0 (possible suspension)
* ``PRICE_OUTLIER``        |z-score| of log-return beyond configured threshold
* ``TIME_GAP``             long run of missing business days (potential gap)
* ``SOURCE_MISSING``       row lacks source/source timestamp

Severity:
* ERROR   -> row is unusable / must be flagged incomplete
* WARNING -> usable but suspicious; must be reported, not dropped
* INFO    -> informational

Nothing is silently calculated from incomplete data: rows that raise ERROR are
still persisted (so history stays complete) but are marked ``is_incomplete`` and
counted in the run's error tally.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from nmi.core.config import settings
from nmi.core.enums import Severity
from nmi.ingestion.records import Candle

IssueCode = Literal[
    "OHLC_INCONSISTENT",
    "NEGATIVE_VALUE",
    "CLOSE_MISSING",
    "OHLC_INCOMPLETE",
    "DUPLICATE_TRADE_DATE",
    "NEGATIVE_VOLUME",
    "ZERO_VOLUME",
    "PRICE_OUTLIER",
    "TIME_GAP",
    "SOURCE_MISSING",
    "INSUFFICIENT_SAMPLE",
]


@dataclass(slots=True)
class ValidationIssue:
    code: IssueCode
    severity: Severity
    message: str
    symbol: str | None = None
    trade_date: date | None = None
    context: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "symbol": self.symbol,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "context": self.context,
        }


@dataclass(slots=True)
class ValidationReport:
    symbol: str
    issues: list[ValidationIssue] = field(default_factory=list)
    rows_checked: int = 0
    rows_flagged_incomplete: int = 0

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def summary(self) -> str:
        return (
            f"{self.symbol}: {self.rows_checked} rows, "
            f"{len(self.errors)} errors, {len(self.warnings)} warnings, "
            f"{self.rows_flagged_incomplete} flagged incomplete"
        )

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "rows_checked": self.rows_checked,
            "rows_flagged_incomplete": self.rows_flagged_incomplete,
            "errors": [i.as_dict() for i in self.errors],
            "warnings": [i.as_dict() for i in self.warnings],
        }


@dataclass(slots=True)
class ValidationConfig:
    outlier_zscore: float = field(
        default_factory=lambda: settings.validation_outlier_zscore
    )
    max_gap_business_days: int = field(
        default_factory=lambda: settings.validation_max_gap_business_days
    )
    min_sample_for_outliers: int = 20


def validate_candles(
    candles: Sequence[Candle],
    config: ValidationConfig | None = None,
) -> ValidationReport:
    cfg = config or ValidationConfig()
    report = ValidationReport(symbol=_symbol(candles))
    report.rows_checked = len(candles)
    if not candles:
        report.issues.append(
            ValidationIssue(
                code="CLOSE_MISSING",
                severity=Severity.WARNING,
                message="provider returned no rows",
                symbol=report.symbol,
            )
        )
        return report

    _validate_row_level(candles, report)
    _validate_duplicates(candles, report)
    _validate_gaps(candles, report, cfg)
    _validate_outliers(candles, report, cfg)
    _validate_source(candles, report)
    return report


def incomplete_trade_dates(report: ValidationReport) -> set[date]:
    """Dates whose row raised an ERROR and must be flagged incomplete."""
    return {i.trade_date for i in report.errors if i.trade_date is not None}


def _symbol(candles: Sequence[Candle]) -> str:
    return candles[0].symbol if candles else "<empty>"


def _validate_row_level(candles: Sequence[Candle], report: ValidationReport) -> None:
    for c in candles:
        vals = {"open": c.open, "high": c.high, "low": c.low, "close": c.close}
        for name, v in vals.items():
            if v is not None and v < 0:
                report.issues.append(
                    ValidationIssue(
                        code="NEGATIVE_VALUE",
                        severity=Severity.ERROR,
                        message=f"{name} is negative ({v})",
                        symbol=c.symbol,
                        trade_date=c.trade_date,
                    )
                )
        if c.close is None or c.close <= 0:
            report.issues.append(
                ValidationIssue(
                    code="CLOSE_MISSING",
                    severity=Severity.ERROR,
                    message=f"close is {c.close!r}; row unusable",
                    symbol=c.symbol,
                    trade_date=c.trade_date,
                )
            )
            report.rows_flagged_incomplete += 1
            continue

        if c.open is None or c.high is None or c.low is None:
            report.issues.append(
                ValidationIssue(
                    code="OHLC_INCOMPLETE",
                    severity=Severity.ERROR,
                    message="open/high/low missing (close-only row)",
                    symbol=c.symbol,
                    trade_date=c.trade_date,
                )
            )
            report.rows_flagged_incomplete += 1
        else:
            lo, hi = c.low, c.high
            if lo > min(c.open, c.close) or hi < max(c.open, c.close):
                report.issues.append(
                    ValidationIssue(
                        code="OHLC_INCONSISTENT",
                        severity=Severity.ERROR,
                        message=(
                            f"low={lo} <= o/c={min(c.open, c.close)} "
                            f"and high={hi} >= o/c={max(c.open, c.close)} violated"
                        ),
                        symbol=c.symbol,
                        trade_date=c.trade_date,
                        context={"open": str(c.open), "high": str(hi),
                                 "low": str(lo), "close": str(c.close)},
                    )
                )
                report.rows_flagged_incomplete += 1

        if c.volume is not None and c.volume < 0:
            report.issues.append(
                ValidationIssue(
                    code="NEGATIVE_VOLUME",
                    severity=Severity.ERROR,
                    message=f"volume is negative ({c.volume})",
                    symbol=c.symbol,
                    trade_date=c.trade_date,
                )
            )
        elif c.volume == 0:
            report.issues.append(
                ValidationIssue(
                    code="ZERO_VOLUME",
                    severity=Severity.WARNING,
                    message="zero volume with positive close (possible suspension)",
                    symbol=c.symbol,
                    trade_date=c.trade_date,
                )
            )


def _validate_duplicates(candles: Sequence[Candle], report: ValidationReport) -> None:
    seen: dict[date, int] = {}
    for c in candles:
        seen[c.trade_date] = seen.get(c.trade_date, 0) + 1
    for d, n in seen.items():
        if n > 1:
            report.issues.append(
                ValidationIssue(
                    code="DUPLICATE_TRADE_DATE",
                    severity=Severity.ERROR,
                    message=f"trade_date {d.isoformat()} appears {n} times",
                    symbol=report.symbol,
                    trade_date=d,
                    context={"count": n},
                )
            )


def _validate_source(candles: Sequence[Candle], report: ValidationReport) -> None:
    for c in candles:
        if not c.source or not c.source_timestamp:
            report.issues.append(
                ValidationIssue(
                    code="SOURCE_MISSING",
                    severity=Severity.WARNING,
                    message="row lacks source/source_timestamp",
                    symbol=c.symbol,
                    trade_date=c.trade_date,
                )
            )


def _business_days(a: date, b: date):
    d = a
    while d <= b:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def _validate_gaps(
    candles: Sequence[Candle], report: ValidationReport, cfg: ValidationConfig
) -> None:
    dates = sorted({c.trade_date for c in candles})
    if len(dates) < 2:
        return
    first, last = dates[0], dates[-1]
    present = set(dates)
    gap_run = 0
    for d in _business_days(first, last):
        if d in present:
            gap_run = 0
        else:
            gap_run += 1
            if gap_run == cfg.max_gap_business_days:
                report.issues.append(
                    ValidationIssue(
                        code="TIME_GAP",
                        severity=Severity.WARNING,
                        message=(
                            f"{cfg.max_gap_business_days}+ consecutive business "
                            "days missing (potential data gap)"
                        ),
                        symbol=report.symbol,
                        trade_date=d,
                        context={"gap_end": d.isoformat()},
                    )
                )


def _validate_outliers(
    candles: Sequence[Candle], report: ValidationReport, cfg: ValidationConfig
) -> None:
    closes = [c.close for c in sorted(candles, key=lambda x: x.trade_date)]
    if len(closes) < cfg.min_sample_for_outliers:
        report.issues.append(
            ValidationIssue(
                code="INSUFFICIENT_SAMPLE",
                severity=Severity.INFO,
                message=(
                    f"{len(closes)} observations < {cfg.min_sample_for_outliers}; "
                    "outlier check skipped"
                ),
                symbol=report.symbol,
            )
        )
        return

    import math

    log_returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] and closes[i - 1] > 0 and closes[i] > 0:
            log_returns.append(math.log(float(closes[i]) / float(closes[i - 1])))
    if len(log_returns) < 5:
        return
    mean = sum(log_returns) / len(log_returns)
    var = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return
    ordered = sorted(candles, key=lambda x: x.trade_date)
    for i, c in enumerate(ordered):
        if i == 0:
            continue
        prev = ordered[i - 1].close
        if not prev or prev <= 0 or c.close <= 0:
            continue
        r = math.log(float(c.close) / float(prev))
        z = (r - mean) / std
        if abs(z) > cfg.outlier_zscore:
            report.issues.append(
                ValidationIssue(
                    code="PRICE_OUTLIER",
                    severity=Severity.WARNING,
                    message=f"log-return z={z:.2f} exceeds threshold",
                    symbol=c.symbol,
                    trade_date=c.trade_date,
                    context={"return_pct": round(r * 100, 2), "z": round(z, 2)},
                )
            )
