"""Default strategy catalog (Phase 4).

These three strategies ship enabled and are seeded into ``strategies`` /
``strategy_versions``. Their thresholds are ordinary rule documents, so they can
be tuned, re-versioned and audited without touching code — the engine evaluates
whatever document the database holds.
"""

from __future__ import annotations

from dataclasses import dataclass

from nmi.core.models import HorizonType
from nmi.strategies.rules import (
    RiskParams,
    StrategyRules,
    condition,
    group,
)


def _rules(conditions: dict) -> StrategyRules:
    return StrategyRules.from_dict(conditions)


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    code: str
    name: str
    description: str
    horizon: HorizonType
    rules: StrategyRules

    def strategy_row(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "horizon": self.horizon.value,
            "is_active": True,
        }

    def rules_document(self) -> dict:
        return self.rules.to_dict()


MT_MOMENTUM_QUALITY = StrategyDefinition(
    code="mt_momentum_quality",
    name="Medium-term momentum + quality",
    description=(
        "Pullback entries in quality names with positive trend, momentum and "
        "relative strength inside a supportive market regime."
    ),
    horizon=HorizonType.MEDIUM_TERM,
    rules=_rules(
        {
            "entry": group(
                condition("composite_score", ">=", 60.0, "Composite score"),
                condition("trend_score", ">=", 55.0, "Trend score"),
                condition("momentum_score", ">=", 55.0, "Momentum score"),
                condition("rs_trend", "in", ["IMPROVING", "STABLE"], "Relative-strength trend"),
                condition("valuation_score", ">=", 45.0, "Valuation score"),
                condition("risk_score", ">=", 40.0, "Risk score"),
                condition(
                    "preferred_horizon", "in", ["MEDIUM_TERM"], "Preferred horizon fit"
                ),
                condition("regime_label", "in", ["RISK_ON", "CAUTIOUS"], "Market regime"),
            ),
            "watch": group(
                condition("composite_score", ">=", 50.0, "Composite score"),
            ),
            "hold": group(
                condition("trend_state", "in", ["UPTREND", "CONSOLIDATION"], "Trend state"),
            ),
            "exit": group(
                condition("trend_state", "==", "DOWNTREND", "Trend state"),
                condition("regime_label", "in", ["RISK_OFF", "STRESSED"], "Market regime"),
                condition("composite_score", "<", 40.0, "Composite score"),
                mode="any",
            ),
            "invalidation": group(
                condition("risk_score", "<", 30.0, "Risk score"),
                condition("drawdown_pct", "<=", -20.0, "Drawdown"),
                mode="any",
            ),
            "risk": RiskParams(
                anchor="sma20",
                entry_low_atr=0.5,
                entry_high_atr=-0.25,
                stop_atr=2.5,
                target_r=2.5,
                holding_days_min=60,
                holding_days_max=270,
            ).to_dict(),
        }
    ),
)

ST_BREAKOUT_MOMENTUM = StrategyDefinition(
    code="st_breakout_momentum",
    name="Short-term 52-week breakout",
    description=(
        "Breakout entries in uptrending names on expanding volume, sized for "
        "short holding periods with tight stops."
    ),
    horizon=HorizonType.SHORT_TERM,
    rules=_rules(
        {
            "entry": group(
                condition("composite_score", ">=", 55.0, "Composite score"),
                condition("trend_state", "==", "UPTREND", "Trend state"),
                condition("breakout_52w", "==", True, "52-week breakout"),
                condition("volume_ratio", ">=", 1.2, "Volume ratio"),
                condition("rsi14", "<=", 75.0, "RSI not overbought"),
                condition("risk_score", ">=", 45.0, "Risk score"),
            ),
            "watch": group(
                condition("dist_from_sma20_pct", ">", 0.0, "Close above SMA20"),
                condition("rsi14", ">=", 55.0, "RSI"),
                mode="any",
            ),
            "hold": group(
                condition("dist_from_sma20_pct", ">", 0.0, "Close above SMA20"),
                condition("trend_state", "in", ["UPTREND", "CONSOLIDATION"], "Trend state"),
            ),
            "exit": group(
                condition("dist_from_sma20_pct", "<", 0.0, "Close below SMA20"),
                condition("volume_ratio", "<", 0.7, "Volume ratio"),
                condition("rsi14", ">", 80.0, "RSI overbought"),
                mode="any",
            ),
            "invalidation": group(
                condition("drawdown_pct", "<=", -10.0, "Drawdown"),
            ),
            "risk": RiskParams(
                anchor="high_52w",
                entry_low_atr=0.0,
                entry_high_atr=-0.25,
                stop_atr=1.5,
                target_r=2.0,
                holding_days_min=10,
                holding_days_max=45,
            ).to_dict(),
        }
    ),
)

LT_QUALITY_VALUE = StrategyDefinition(
    code="lt_quality_value",
    name="Long-term quality at a reasonable price",
    description=(
        "Fundamental entries in consistently profitable, growing companies "
        "whose valuation is not stretched versus their own history."
    ),
    horizon=HorizonType.LONG_TERM,
    rules=_rules(
        {
            "entry": group(
                condition("quality_score", ">=", 60.0, "Quality score"),
                condition("growth_score", ">=", 50.0, "Growth score"),
                condition("valuation_score", ">=", 55.0, "Valuation score"),
                condition("overall_quality_score", ">=", 55.0, "Overall quality score"),
                condition("composite_score", ">=", 55.0, "Composite score"),
                condition(
                    "preferred_horizon",
                    "in",
                    ["LONG_TERM", "MEDIUM_TERM"],
                    "Preferred horizon fit",
                ),
            ),
            "watch": group(
                condition("quality_score", ">=", 45.0, "Quality score"),
            ),
            "hold": group(
                condition("trend_state", "in", ["UPTREND", "CONSOLIDATION"], "Trend state"),
            ),
            "exit": group(
                condition("quality_score", "<", 40.0, "Quality score"),
                condition("valuation_score", "<", 30.0, "Valuation score"),
                condition("revenue_growth_pct", "<", 0.0, "Revenue growth"),
                mode="any",
            ),
            "invalidation": group(
                condition("risk_score", "<", 30.0, "Risk score"),
            ),
            "risk": RiskParams(
                anchor="sma50",
                entry_low_atr=0.75,
                entry_high_atr=-0.25,
                stop_atr=3.0,
                target_r=3.0,
                holding_days_min=270,
                holding_days_max=900,
            ).to_dict(),
        }
    ),
)

DEFAULT_STRATEGIES: tuple[StrategyDefinition, ...] = (
    MT_MOMENTUM_QUALITY,
    ST_BREAKOUT_MOMENTUM,
    LT_QUALITY_VALUE,
)


def catalog_payloads() -> list[dict]:
    """Strategy rows and rule documents for seeding (idempotent upserts)."""
    return [
        {"strategy": definition.strategy_row(), "rules": definition.rules_document()}
        for definition in DEFAULT_STRATEGIES
    ]
