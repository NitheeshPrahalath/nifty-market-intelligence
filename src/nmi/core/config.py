"""Centralised configuration for the platform.

Values are read from environment variables (see `.env.example`). No secrets are
ever committed; the `.env` file is git-ignored.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+psycopg2://nmi:nmi@localhost:5432/nmi"
    )
    db_echo: bool = False
    log_level: str = "INFO"

    # Provider selection for each data family. Each value names an adapter
    # registered in ``nmi.ingestion.providers`` ("csv", "yahoo", ...).
    price_provider: str = "csv"
    fundamental_provider: str = "csv"
    corporate_action_provider: str = "csv"
    index_membership_provider: str = "csv"
    market_data_symbol_suffix: str = ".NS"  # yahoo suffix for NSE-listed equities

    # Local data directories used by the "csv" family of providers.
    data_dir: Path = Field(default=Path("./data/raw"))
    membership_dir: Path = Field(default=Path("./data/raw/memberships"))
    corporate_actions_dir: Path = Field(default=Path("./data/raw/corporate_actions"))
    fundamentals_dir: Path = Field(default=Path("./data/raw/fundamentals"))

    # Validation tuning.
    validation_outlier_zscore: float = 8.0
    validation_max_gap_business_days: int = 5

    # Ingestion retries (tenacity).
    ingest_retry_max: int = 3

    @property
    def sqlite_in_memory(self) -> str:
        return "sqlite+pysqlite:///:memory:"


settings = Settings()
