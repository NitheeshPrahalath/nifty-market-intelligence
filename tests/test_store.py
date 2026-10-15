from __future__ import annotations

import numpy as np

from nmi.ingestion.store import _clean_value


def test_clean_value_maps_nan_and_inf_to_none():
    assert _clean_value(float("nan")) is None
    assert _clean_value(float("inf")) is None
    assert _clean_value(None) is None


def test_clean_value_converts_numpy_scalars_to_native():
    """psycopg2 adapts by exact type; numpy scalars must become Python natives."""
    assert _clean_value(np.float64(1.5)) == 1.5
    assert type(_clean_value(np.float64(1.5))) is float
    assert type(_clean_value(np.int64(7))) is int
    assert type(_clean_value(np.bool_(True))) is bool


def test_clean_value_numpy_nan_becomes_none():
    assert _clean_value(np.float64("nan")) is None
    assert _clean_value(np.float32("inf")) is None
