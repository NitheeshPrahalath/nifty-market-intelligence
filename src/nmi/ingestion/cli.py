"""Command-line interface for the ingestion layer.

Examples
--------
    nmi init-db --db-url 'postgresql+psycopg2://...'
    nmi seed-universe
    nmi backfill-prices --index NIFTY_50,NIFTY_MIDCAP_150 \
        --start 2024-01-01 --end 2025-06-30 --provider csv
    nmi backfill-actions --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi backfill-fundamentals
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


def main() -> None:
    setup_logging()
    app()


if __name__ == "__main__":
    main()
