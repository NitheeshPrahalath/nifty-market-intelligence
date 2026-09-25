"""Phase-3 analysis engines: regime, sector, horizon and scoring.

These are pure functions over already-computed Phase-2 metric rows. They return
plain dictionaries; ``nmi.metrics.service.MetricsService`` owns persistence,
versioning and audit trails.
"""

from __future__ import annotations

from nmi.analysis.horizon import compute_horizon_metrics
from nmi.analysis.regime import compute_market_regime, regime_label
from nmi.analysis.scoring import (
    COMPONENTS,
    DEFAULT_WEIGHTS,
    compute_component_scores,
    compute_scoring,
    effective_weights,
    score_label,
)
from nmi.analysis.sector import compute_sector_metrics, sector_state

__all__ = [
    "COMPONENTS",
    "DEFAULT_WEIGHTS",
    "compute_component_scores",
    "compute_horizon_metrics",
    "compute_market_regime",
    "compute_scoring",
    "compute_sector_metrics",
    "effective_weights",
    "regime_label",
    "score_label",
    "sector_state",
]
