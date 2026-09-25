"""Versioned strategy, signal and recommendation engine (Phase 4).

* ``rules``   — declarative condition documents and price-level maths
* ``catalog`` — the default strategies seeded into the database
* ``engine``  — evaluates a rule document against an instrument-day context
"""

from nmi.strategies.catalog import (
    DEFAULT_STRATEGIES,
    MT_MOMENTUM_QUALITY,
    StrategyDefinition,
    catalog_payloads,
)
from nmi.strategies.engine import (
    StrategyContext,
    StrategyDecision,
    StrategyMeta,
    evaluate_strategy,
)
from nmi.strategies.rules import (
    Condition,
    ConditionOutcome,
    GroupResult,
    RiskParams,
    RuleGroup,
    StrategyRules,
)

ENGINE_VERSION = "v1"

__all__ = [
    "ENGINE_VERSION",
    "DEFAULT_STRATEGIES",
    "MT_MOMENTUM_QUALITY",
    "Condition",
    "ConditionOutcome",
    "GroupResult",
    "RuleGroup",
    "RiskParams",
    "StrategyContext",
    "StrategyDecision",
    "StrategyDefinition",
    "StrategyMeta",
    "StrategyRules",
    "catalog_payloads",
    "evaluate_strategy",
]
