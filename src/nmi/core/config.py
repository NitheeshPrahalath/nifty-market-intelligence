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
    # "text" (default) or "json" for structured, request-correlated logs.
    log_format: str = "text"

    # Provider selection for each data family. Each value names an adapter
    # registered in ``nmi.ingestion.providers`` ("csv", "yahoo", ...).
    price_provider: str = "csv"
    fundamental_provider: str = "csv"
    corporate_action_provider: str = "csv"
    index_membership_provider: str = "csv"
    index_price_provider: str = "csv"
    market_data_symbol_suffix: str = ".NS"  # yahoo suffix for NSE-listed equities

    # Local data directories used by the "csv" family of providers.
    data_dir: Path = Field(default=Path("./data/raw"))
    prices_dir: Path = Field(default=Path("./data/raw/prices"))
    membership_dir: Path = Field(default=Path("./data/raw/memberships"))
    corporate_actions_dir: Path = Field(default=Path("./data/raw/corporate_actions"))
    fundamentals_dir: Path = Field(default=Path("./data/raw/fundamentals"))
    indexes_dir: Path = Field(default=Path("./data/raw/indexes"))

    # Metrics engine (Phase 2) tuning.
    calc_version: str = "v1"
    rs_benchmarks: list[str] = ["NIFTY_50"]
    technical_volume_spike: float = 2.5
    rs_trend_threshold_pp: float = 0.5

    # Analysis engines (Phase 3) tuning.
    scoring_parameter_set: str = "default"

    # Validation tuning.
    validation_outlier_zscore: float = 8.0
    validation_max_gap_business_days: int = 5

    # Ingestion retries (tenacity).
    ingest_retry_max: int = 3

    # API layer (Phase 7).
    api_title: str = "Nifty Market Intelligence API"
    api_version: str = "v1"
    api_prefix: str = "/api/v1"
    api_auth_enabled: bool = False
    api_keys: list[str] = []
    api_rate_limit_per_minute: int = 120
    api_cache_ttl_seconds: int = 60
    api_audit_enabled: bool = True
    api_audit_retention_days: int = 90
    redis_url: str | None = None

    # AI explanations layer (Phase 9). "template" is the deterministic,
    # offline provider and the fallback for every other provider.
    explain_provider: str = "template"
    explain_model: str = "gpt-4o-mini"
    explain_base_url: str = "https://api.openai.com/v1"
    explain_api_key: str | None = None
    explain_timeout_seconds: float = 30.0
    explain_max_tokens: int = 800
    explain_temperature: float = 0.2

    # Observability (Phase 10). Metrics are served in Prometheus text format at
    # ``metrics_path`` and instrumented from the gateway middleware.
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"
    security_headers_enabled: bool = True

    # Security / RBAC (Phase 10). A JSON map of API key -> role name. Roles are
    # ``viewer`` (read), ``analyst`` (read + audit) and ``admin`` (everything);
    # keys listed in ``api_keys`` above default to ``admin`` for backward
    # compatibility. Unknown roles are treated as ``viewer`` (least privilege).
    api_key_roles: dict[str, str] = {}

    # Data-quality reconciliation (Phase 10).
    quality_lookback_days: int = 30
    quality_stale_business_days: int = 4
    quality_adjustment_tolerance_pct: float = 0.5

    @property
    def sqlite_in_memory(self) -> str:
        return "sqlite+pysqlite:///:memory:"


settings = Settings()
