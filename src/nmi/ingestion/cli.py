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
    nmi seed-strategies
    nmi compute-signals --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi generate-recommendations --index NIFTY_50
    nmi track-recommendations --index NIFTY_50
    nmi dispatch-notifications --index NIFTY_50
    nmi compute-eod --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi backtest --index NIFTY_50 --start 2024-01-01 --end 2025-06-30
    nmi backtest-walk-forward --index NIFTY_50 --start 2024-01-01 --end 2025-06-30 --folds 4
    nmi backtest-list
    nmi backtest-report 1 --trades
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer

from nmi.core.config import settings
from nmi.core.db import Base, get_engine, get_session, init_engine
from nmi.core.logging import setup_logging
from nmi.ingestion import store

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
    """Run the full EOD chain: metrics, analysis, strategies, tracking (Phases 2-5)."""
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


@app.command()
def compute_signals(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Evaluate every active strategy version per member-day (Phase 4)."""
    _run_metrics("compute_signals", db_url, index, start, end)


@app.command()
def generate_recommendations(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    as_of: str | None = typer.Option(None, "--as-of", help="Defaults to the latest signal day"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Turn qualifying signals into tracked recommendations (Phase 4)."""
    from nmi.metrics.service import MetricsService

    index_codes = [c.strip() for c in index.split(",") if c.strip()]
    with _sessionctx(db_url) as session:
        result = MetricsService(session).generate_recommendations(
            index_codes, _cli_date(as_of) if as_of else None
        )
    typer.echo(result.as_dict())


@app.command()
def seed_strategies(
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Seed the default strategy catalog and its first rule versions (Phase 4)."""
    from nmi.metrics.service import MetricsService

    with _sessionctx(db_url) as session:
        stored = MetricsService(session).seed_strategies()
        session.commit()
    typer.echo({"strategy_versions_created": stored})


@app.command()
def track_recommendations(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    as_of: str | None = typer.Option(None, "--as-of", help="Defaults to the latest metric day"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Re-evaluate open recommendations against their original thesis (Phase 5)."""
    from nmi.metrics.service import MetricsService

    index_codes = [c.strip() for c in index.split(",") if c.strip()]
    with _sessionctx(db_url) as session:
        result = MetricsService(session).track_recommendations(
            index_codes, _cli_date(as_of) if as_of else None
        )
    typer.echo(result.as_dict())


@app.command()
def dispatch_notifications(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    as_of: str | None = typer.Option(None, "--as-of", help="Defaults to the latest event day"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Turn recommendation events and regime changes into notifications (Phase 5)."""
    from nmi.metrics.service import MetricsService

    index_codes = [c.strip() for c in index.split(",") if c.strip()]
    with _sessionctx(db_url) as session:
        result = MetricsService(session).dispatch_notifications(
            index_codes, _cli_date(as_of) if as_of else None
        )
    typer.echo(result.as_dict())


def _index_codes(index: str) -> list[str]:
    return [c.strip() for c in index.split(",") if c.strip()]


def _fmt(value, spec: str = ".4f", missing: str = "-") -> str:
    if value is None:
        return missing
    if isinstance(value, float | int) and not isinstance(value, bool):
        return format(value, spec)
    return str(value)


@app.command()
def backtest(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str = typer.Option(..., "--start", help="First session to simulate (YYYY-MM-DD)"),
    end: str = typer.Option(..., "--end", help="Last session to simulate (YYYY-MM-DD)"),
    name: str | None = typer.Option(None, "--name", help="Label stored with the run"),
    strategy: str | None = typer.Option(
        None, "--strategy", help="Comma-separated strategy codes (default: all active)"
    ),
    capital: float = typer.Option(1_000_000.0, "--capital", help="Starting cash"),
    max_positions: int = typer.Option(5, "--max-positions"),
    risk_per_trade: float = typer.Option(
        0.01, "--risk-per-trade", help="Fraction of equity risked per position"
    ),
    max_weight: float = typer.Option(0.15, "--max-weight", help="Cap on one position"),
    cost_bps: float = typer.Option(5.0, "--cost-bps", help="Brokerage + taxes per side"),
    slippage_bps: float = typer.Option(3.0, "--slippage-bps", help="Slippage per side"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Simulate one window and store the run, its trades and its equity curve."""
    from nmi.backtest.engine import BacktestConfig
    from nmi.backtest.service import BacktestService

    config = BacktestConfig(
        initial_capital=capital,
        max_positions=max_positions,
        risk_per_trade=risk_per_trade,
        max_position_weight=max_weight,
        cost_bps=cost_bps,
        slippage_bps=slippage_bps,
    )
    with _sessionctx(db_url) as session:
        run, result = BacktestService(session).run_backtest(
            _index_codes(index),
            _cli_date(start),
            _cli_date(end),
            name=name,
            strategy_codes=_index_codes(strategy) if strategy else None,
            config=config,
        )
        summary = BacktestService(session).run_summary(run.id)
    _echo_backtest_summary(summary, result)


@app.command()
def backtest_walk_forward(
    index: str = typer.Option(..., "--index", help="Comma-separated index codes"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    folds: int = typer.Option(4, "--folds", help="Number of out-of-sample windows"),
    train_ratio: float = typer.Option(0.5, "--train-ratio", help="History behind each window"),
    name: str | None = typer.Option(None, "--name"),
    strategy: str | None = typer.Option(None, "--strategy"),
    capital: float = typer.Option(1_000_000.0, "--capital"),
    max_positions: int = typer.Option(5, "--max-positions"),
    risk_per_trade: float = typer.Option(0.01, "--risk-per-trade"),
    max_weight: float = typer.Option(0.15, "--max-weight"),
    cost_bps: float = typer.Option(5.0, "--cost-bps"),
    slippage_bps: float = typer.Option(3.0, "--slippage-bps"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Score the strategy out of sample, one window at a time (Phase 6)."""
    from nmi.backtest.engine import BacktestConfig
    from nmi.backtest.service import BacktestService

    config = BacktestConfig(
        initial_capital=capital,
        max_positions=max_positions,
        risk_per_trade=risk_per_trade,
        max_position_weight=max_weight,
        cost_bps=cost_bps,
        slippage_bps=slippage_bps,
    )
    with _sessionctx(db_url) as session:
        run, result = BacktestService(session).run_walk_forward(
            _index_codes(index),
            _cli_date(start),
            _cli_date(end),
            folds=folds,
            train_ratio=train_ratio,
            name=name,
            strategy_codes=_index_codes(strategy) if strategy else None,
            config=config,
        )
        summary = BacktestService(session).run_summary(run.id)
    _echo_walk_forward(summary, failed=result.items_failed)


@app.command()
def backtest_list(
    limit: int = typer.Option(20, "--limit"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """List stored backtest runs, newest first."""
    from nmi.backtest.service import BacktestService

    with _sessionctx(db_url) as session:
        runs = BacktestService(session).list_runs(limit=limit)
    if not runs:
        typer.echo("no backtest runs yet")
        return
    typer.echo(
        f"  {'id':>4}  {'kind':<13} {'window':>6}  {'start':<10} {'end':<10} "
        f"{'trades':>6}  {'return':>8}  name"
    )
    for run in runs:
        typer.echo(
            f"  {run.id:>4}  {run.kind.value:<13} "
            f"{run.window_index if run.window_index is not None else '-':>6}  "
            f"{run.start_date.isoformat():<10} {run.end_date.isoformat():<10} "
            f"{run.trades_count:>6}  "
            f"{_fmt((run.metrics or {}).get('total_return'), '.2%'):>8}  {run.name}"
        )


@app.command()
def backtest_report(
    run_id: int = typer.Argument(..., help="Backtest run id from backtest-list"),
    trades: bool = typer.Option(False, "--trades", help="Print every trade as well"),
    db_url: str | None = typer.Option(None, "--db-url"),
):
    """Print the stored metrics of one backtest run."""
    from nmi.backtest.service import BacktestService

    with _sessionctx(db_url) as session:
        service = BacktestService(session)
        summary = service.run_summary(run_id)
        if not summary:
            typer.secho(f"no backtest run with id {run_id}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        trade_rows = None
        if trades:
            trade_rows = store.get_backtest_trades(session, run_id)
    if summary.get("kind") == "WALK_FORWARD":
        _echo_walk_forward(summary)
    else:
        _echo_backtest_summary(summary)
    if trade_rows is not None:
        _echo_backtest_trades(trade_rows)


def _echo_backtest_trades(trades) -> None:
    if not trades:
        typer.echo("\n  no trades")
        return
    typer.echo(
        f"\n  {'symbol':<8} {'strategy':<22} {'signal':<10} {'entry':<10} "
        f"{'exit':<10} {'qty':>6} {'net_pnl':>10} {'ret%':>7}  reason"
    )
    for trade in trades:
        typer.echo(
            f"  {trade.symbol:<8} {trade.strategy_code:<22} "
            f"{trade.entry_signal_date.isoformat():<10} "
            f"{trade.entry_date.isoformat():<10} {trade.exit_date.isoformat():<10} "
            f"{trade.quantity:>6} {float(trade.net_pnl):>10.2f} "
            f"{float(trade.return_pct):>7.2f}  {trade.exit_reason}"
        )


def _echo_walk_forward(summary: dict, failed: int | None = None) -> None:
    """A parent run has no equity curve of its own; its windows are the result."""
    typer.echo(
        f"walk-forward run {summary['backtest_run_id']}: {summary['name']} "
        f"({summary['start']} .. {summary['end']})"
    )
    for window in summary.get("windows") or []:
        typer.echo(
            f"  window {window['index']} "
            f"{window['test_start']}..{window['test_end']} "
            f"trades={window['metrics'].get('trades', 0)} "
            f"return={_fmt(window['metrics'].get('total_return'), '.2%')} "
            f"sharpe={_fmt(window['metrics'].get('sharpe'))} "
            f"max_dd={_fmt(window['metrics'].get('max_drawdown'), '.2%')}"
        )
    stats = summary.get("summary") or {}
    typer.echo(
        f"  windows={stats.get('windows', 0)} "
        f"profitable={stats.get('profitable_windows', 0)} "
        f"consistency={_fmt(stats.get('consistency'), '.2f')} "
        f"mean_return={_fmt(stats.get('mean_window_return'), '.2%')} "
        f"worst={_fmt(stats.get('worst_window_return'), '.2%')}"
    )
    if failed is not None:
        typer.echo(f"  failed windows: {failed}")


def _echo_backtest_summary(summary: dict, result=None) -> None:
    typer.echo(f"backtest run {summary['backtest_run_id']}: {summary['name']}")
    typer.echo(f"  window        {summary['start']} .. {summary['end']}")
    typer.echo(f"  strategies    {', '.join(summary['strategies']) or '-'}")
    typer.echo(
        f"  capital       {_fmt(summary['initial_capital'], '.2f')} "
        f"-> {_fmt(summary['final_equity'], '.2f')}"
    )
    typer.echo(
        f"  return        total={_fmt(summary['total_return'], '.2%')} "
        f"cagr={_fmt(summary['cagr'], '.2%')} "
        f"excess={_fmt(summary.get('excess_return'), '.2%')}"
    )
    typer.echo(
        f"  risk          vol={_fmt(summary['volatility'], '.2%')} "
        f"sharpe={_fmt(summary['sharpe'])} "
        f"sortino={_fmt(summary['sortino'])} "
        f"max_dd={_fmt(summary['max_drawdown'], '.2%')}"
        + (f" ({summary['max_drawdown_days']}d)" if summary["max_drawdown_days"] else "")
    )
    typer.echo(
        f"  trades        n={summary['trades']} "
        f"win_rate={_fmt(summary['win_rate'], '.1%')} "
        f"profit_factor={_fmt(summary['profit_factor'])} "
        f"expectancy={_fmt(summary['expectancy'], '.2f')}"
    )
    typer.echo(
        f"  exposure      in_market={_fmt(summary['days_in_market_pct'], '.1%')} "
        f"turnover={_fmt(summary['turnover'], '.2f')}x"
    )
    if summary.get("exit_reasons"):
        breakdown = ", ".join(f"{k}={v}" for k, v in summary["exit_reasons"].items())
        typer.echo(f"  exits         {breakdown}")
    if result is not None:
        typer.echo(f"  audit run     {result.run_id} ({result.status.value})")


def main() -> None:
    setup_logging()
    app()


if __name__ == "__main__":
    main()
