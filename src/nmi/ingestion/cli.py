"""Command-line interface for the ingestion layer.

Examples
--------
    nmi init-db --db-url 'postgresql+psycopg2://...'
    nmi seed-universe
    nmi backfill-prices --index NIFTY_50,NIFTY_MIDCAP_150 \
        --start 2024-01-01 --end 2025-06-30 --provider csv
    nmi backfill-actions --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi backfill-fundamentals
    nmi backfill-index-prices --index NIFTY_50,NIFTY_IT
    nmi compute-tech --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi compute-momentum --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi compute-valuation --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi compute-fundamentals --index NIFTY_50
    nmi seed-strategy-parameters
    nmi compute-sector --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi compute-regime --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi compute-horizon --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi compute-scoring --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi compute-eod --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer

from nmi.core.config import settings
from nmi.core.db import Base, get_engine, get_session, init_engine
from nmi.core.logging import setup_logging

app = typer.Typer(add_completion=False, help="Nifty Market Intelligence — ingestion CLI")


def _sessionctx(db_url: str | None):
    init_engine(url=db_url)
    return get_session()


def _cli_date(value: str) -> date:
    return date.fromisoformat(value)


@app.command()
def init_db(db_url: str | None = typer.Option(None, "--db-url")):
    """Create all tables (dev convenience; production uses Alembic)."""
    init_engine(url=db_url)
    Base.metadata.create_all(get_engine())
    typer.secho("schema created", fg=typer.colors.GREEN)


@app.command()
def seed_universe(
    db_url: str | None = typer.Option(None, "--db-url"),
    symbols_file: str | None = typer.Option(None, "--symbols-file"),
    membership_file: str | None = typer.Option(None, "--membership-file"),
):
    """Seed companies/instruments and effective-date index membership."""
    from nmi.ingestion.backfill import IngestionService

    if symbols_file:
        settings.data_dir = Path(symbols_file).parent
    if membership_file:
        settings.membership_dir = Path(membership_file).parent
    with _sessionctx(db_url) as session:
        result = IngestionService(session).seed_universe()
    typer.secho(str(result), fg=typer.colors.GREEN)


@app.command()
def backfill_prices(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    provider: str | None = typer.Option(None, "--provider", help="csv|yahoo"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Backfill daily prices for all historical members of the given indices."""
    from nmi.ingestion.backfill import IngestionService

    index_codes = [c.strip() for c in index.split(",") if c.strip()]
    with _sessionctx(db_url) as session:
        result = IngestionService(session).backfill_prices(
            index_codes, _cli_date(start), _cli_date(end), provider_name=provider
        )
    typer.echo(result.as_dict())


@app.command()
def backfill_actions(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Backfill corporate actions for the given indices' members."""
    from nmi.ingestion.backfill import IngestionService

    index_codes = [c.strip() for c in index.split(",") if c.strip()]
    with _sessionctx(db_url) as session:
        result = IngestionService(session).backfill_corporate_actions(
            index_codes,
            _cli_date(start) if start else None,
            _cli_date(end) if end else None,
        )
    typer.echo(result.as_dict())


@app.command()
def backfill_fundamentals(
    isins: str | None = typer.Option(None, "--isin", help="Comma-separated ISINs"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Backfill raw financial statements for known companies."""
    from nmi.ingestion.backfill import IngestionService

    with _sessionctx(db_url) as session:
        result = IngestionService(session).backfill_fundamentals(
            [i.strip() for i in isins.split(",") if i.strip()] if isins else None
        )
    typer.echo(result.as_dict())


def _run_metrics(job: str, db_url: str | None, index: str, start: str | None, end: str | None):
    init_engine(url=db_url)
    from nmi.metrics.service import MetricsService

    index_codes = [c.strip() for c in index.split(",") if c.strip()]
    with get_session() as session:
        svc = MetricsService(session)
        result = getattr(svc, job)(
            index_codes,
            _cli_date(start) if start else None,
            _cli_date(end) if end else None,
        )
    typer.echo(result.as_dict())


@app.command()
def backfill_index_prices(
    index: str = typer.Option(..., "--index", help="Comma-separated benchmark index codes"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Load index close series (benchmarks for relative strength)."""
    from nmi.metrics.service import MetricsService

    index_codes = [c.strip() for c in index.split(",") if c.strip()]
    with _sessionctx(db_url) as session:
        result = MetricsService(session).backfill_index_prices(index_codes)
    typer.echo(result.as_dict())


@app.command()
def compute_tech(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Compute per-day technical indicators for index members."""
    _run_metrics("compute_technical", db_url, index, start, end)


@app.command()
def compute_momentum(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Compute trailing returns and relative strength vs configured benchmarks."""
    _run_metrics("compute_momentum", db_url, index, start, end)


@app.command()
def compute_valuation(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Compute per-day valuation multiples (as-of fundamentals, no look-ahead)."""
    _run_metrics("compute_valuation", db_url, index, start, end)


@app.command()
def compute_fundamentals(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Compute derived fundamental metrics & quality scores for index members."""
    from nmi.metrics.service import MetricsService

    index_codes = [c.strip() for c in index.split(",") if c.strip()]
    with _sessionctx(db_url) as session:
        result = MetricsService(session).compute_fundamentals(index_codes)
    typer.echo(result.as_dict())


@app.command()
def compute_eod(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Run the full EOD chain: metrics (Phase 2) + analysis (Phase 3)."""
    from nmi.metrics.service import MetricsService

    index_codes = [c.strip() for c in index.split(",") if c.strip()]
    with _sessionctx(db_url) as session:
        results = MetricsService(session).compute_eod(
            index_codes,
            _cli_date(start) if start else None,
            _cli_date(end) if end else None,
        )
    for res in results:
        typer.echo(res.as_dict())


@app.command()
def seed_strategy_parameters(
    parameter_set: str | None = typer.Option(None, "--parameter-set"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Persist the default scoring component weights into strategy_parameters."""
    from nmi.metrics.service import MetricsService

    with _sessionctx(db_url) as session:
        stored = MetricsService(session).seed_strategy_parameters(parameter_set)
        session.commit()
    typer.echo({"parameter_set": parameter_set or settings.scoring_parameter_set, "stored": stored})


@app.command()
def compute_sector(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Aggregate per-sector cross-sectional metrics for index members."""
    _run_metrics("compute_sector", db_url, index, start, end)


@app.command()
def compute_regime(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Score the market regime (breadth, momentum, vol, drawdown, sectors)."""
    _run_metrics("compute_regime", db_url, index, start, end)


@app.command()
def compute_horizon(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Score short/medium/long-horizon fit for index members."""
    _run_metrics("compute_horizon", db_url, index, start, end)


@app.command()
def compute_scoring(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    parameter_set: str | None = typer.Option(None, "--parameter-set"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Compute eight component scores and the weighted composite per member-day."""
    from nmi.metrics.service import MetricsService

    index_codes = [c.strip() for c in index.split(",") if c.strip()]
    with _sessionctx(db_url) as session:
        result = MetricsService(session).compute_scoring(
            index_codes,
            _cli_date(start) if start else None,
            _cli_date(end) if end else None,
            parameter_set,
        )
    typer.echo(result.as_dict())


def main() -> None:
    setup_logging()
    app()


if __name__ == "__main__":
    main()
