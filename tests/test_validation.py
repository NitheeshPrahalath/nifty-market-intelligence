from __future__ import annotations

from datetime import date
from decimal import Decimal

from nmi.core.enums import Severity
from nmi.ingestion.validation import (
    ValidationConfig,
    incomplete_trade_dates,
    validate_candles,
)
from tests.conftest import make_candles


def test_clean_series_passes():
    candles = make_candles("TEST", [Decimal(100) + Decimal(i) for i in range(15)])
    report = validate_candles(candles)
    assert report.ok
    assert report.rows_flagged_incomplete == 0


def test_ohlc_inconsistency_is_error_and_flagged_incomplete():
    closes = [Decimal(100)] * 5
    candles = make_candles(
        "TEST",
        closes,
        opens=[Decimal(x) for x in [100, 110, 100, 100, 100]],  # open 110 > high 100
    )
    report = validate_candles(candles)
    assert not report.ok
    assert any(i.code == "OHLC_INCONSISTENT" and i.severity == Severity.ERROR
               for i in report.errors)
    incompletes = incomplete_trade_dates(report)
    assert len(incompletes) == 1


def test_negative_values_detected():
    closes = [Decimal(100)] * 3
    candles = make_candles(
        "TEST",
        closes,
        opens=[None, Decimal(-5), None],
    )

    report = validate_candles(candles)
    negatives = [i for i in report.errors if i.code == "NEGATIVE_VALUE"]
    assert len(negatives) == 1
    assert negatives[0].trade_date == date(2024, 6, 4)


def test_close_missing_flags_row():

    candles = make_candles("TEST", [Decimal(100)] * 2)
    candles[1].close = Decimal(0)
    report = validate_candles(candles)
    assert any(i.code == "CLOSE_MISSING" and i.severity == Severity.ERROR
               for i in report.errors)


def test_duplicate_trade_date_is_error():
    candles = make_candles("TEST", [Decimal(100)] * 3)
    candles.append(candles[0])  # same day twice
    report = validate_candles(candles)
    assert any(i.code == "DUPLICATE_TRADE_DATE" for i in report.issues)


def test_zero_volume_warning():
    candles = make_candles("TEST", [Decimal(100)] * 3, volumes=[100, 0, 200])
    report = validate_candles(candles)
    assert any(i.code == "ZERO_VOLUME" for i in report.warnings)


def test_gap_detection_flags_missing_business_days():
    candles = make_candles("TEST", [Decimal(100)] * 6, start=date(2024, 6, 3))
    # Keep only Mon/Fri -> 3 consecutive missing business days in the middle.
    candles = [c for c in candles if c.trade_date.weekday() in (0, 4)]
    assert len(candles) >= 2
    report = validate_candles(candles, ValidationConfig(max_gap_business_days=3))
    assert any(i.code == "TIME_GAP" for i in report.issues)


def test_outlier_detection_with_large_move():
    start = date(2024, 1, 2)
    base = [Decimal(100) for _ in range(15)] + [Decimal(160) for _ in range(15)]
    candles = make_candles("TEST", base, start=start)
    report = validate_candles(
        candles, ValidationConfig(outlier_zscore=4.0, min_sample_for_outliers=20)
    )
    outliers = [i for i in report.issues if i.code == "PRICE_OUTLIER"]
    assert len(outliers) >= 1
    assert outliers[0].trade_date == date(2024, 1, 23)  # 16th business day


def test_insufficient_sample_skips_outliers():
    candles = make_candles("TEST", [Decimal(100)] * 5, start=date(2024, 6, 3))
    report = validate_candles(candles)
    assert any(i.code == "INSUFFICIENT_SAMPLE" for i in report.issues)
    assert not any(i.code == "PRICE_OUTLIER" for i in report.issues)


def test_source_missing_warning():
    candles = make_candles("TEST", [Decimal(100)] * 2)
    candles[0].source = ""
    report = validate_candles(candles)
    assert any(i.code == "SOURCE_MISSING" for i in report.issues)


def test_report_summary_shape():
    candles = make_candles("TEST", [Decimal(100)] * 2)
    report = validate_candles(candles)
    d = report.as_dict()
    assert d["symbol"] == "TEST"
    assert d["rows_checked"] == 2
    assert isinstance(d["errors"], list)
