"""Vendor adapter contracts and the provider factory.

Each data family has a pluggable provider interface. The rest of the pipeline
only talks to these protocols — never to a vendor's specific types. Provider
selection is configuration-driven (``Settings.*_provider``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol

from nmi.ingestion.records import (
    Candle,
    CorporateActionRecord,
    IncomeStatementRecord,
    IndexMembershipRecord,
    UniverseRow,
)

_FAMILY_SETTING = {
    "price": "price_provider",
    "fundamental": "fundamental_provider",
    "corporate_action": "corporate_action_provider",
    "index_membership": "index_membership_provider",
    "universe": "index_membership_provider",
}


class PriceProvider(Protocol):
    name: str

    def fetch_prices(
        self,
        symbol: str,
        exchange,
        start: date,
        end: date,
        source_timestamp: datetime | None = None,
    ) -> list[Candle]: ...


class MembershipProvider(Protocol):
    name: str

    def fetch_membership(
        self, index_codes: Sequence[str] | None = None
    ) -> list[IndexMembershipRecord]: ...


class CorporateActionProvider(Protocol):
    name: str

    def fetch_actions(
        self,
        symbols: Sequence[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[CorporateActionRecord]: ...


class UniverseProvider(Protocol):
    name: str

    def fetch_universe(self) -> list[UniverseRow]: ...


class FundamentalProvider(Protocol):
    name: str

    def fetch_income_statements(
        self, isins: Sequence[str] | None = None
    ) -> list[IncomeStatementRecord]: ...


def get_provider(family: str) -> object:
    """Build the configured provider adapter for a data family."""
    from nmi.core.config import settings

    setting = _FAMILY_SETTING[family]
    provider_name = getattr(settings, setting)

    if provider_name == "csv":
        from nmi.ingestion.providers.csv_provider import (
            CSVCorporateActionProvider,
            CSVFundamentalProvider,
            CSVMembershipProvider,
            CSVPriceProvider,
            CSVUniverseProvider,
        )

        mapping = {
            "price": CSVPriceProvider,
            "fundamental": CSVFundamentalProvider,
            "corporate_action": CSVCorporateActionProvider,
            "index_membership": CSVMembershipProvider,
            "universe": CSVUniverseProvider,
        }
        return mapping[family]()
    if provider_name == "yahoo" and family == "price":
        from nmi.ingestion.providers.yahoo_provider import YahooPriceProvider

        return YahooPriceProvider()
    if provider_name == "yahoo" and family == "universe":
        from nmi.ingestion.providers.yahoo_provider import YahooUniverseProvider

        return YahooUniverseProvider()
    raise ValueError(f"Unknown provider '{provider_name}' for family '{family}'")


def price_provider() -> PriceProvider:
    return get_provider("price")  # type: ignore[return-value]


def membership_provider() -> MembershipProvider:
    return get_provider("index_membership")  # type: ignore[return-value]


def corporate_action_provider() -> CorporateActionProvider:
    return get_provider("corporate_action")  # type: ignore[return-value]


def fundamental_provider() -> FundamentalProvider:
    return get_provider("fundamental")  # type: ignore[return-value]


def universe_provider() -> UniverseProvider:
    return get_provider("universe")  # type: ignore[return-value]
