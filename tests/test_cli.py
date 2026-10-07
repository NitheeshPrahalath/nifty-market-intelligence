from __future__ import annotations

from typer.testing import CliRunner

from nmi.ingestion import cli

runner = CliRunner()


def test_cli_seed_and_backfill(tmp_path, pointed_at_fixtures):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'nmi.db'}"

    r = runner.invoke(cli.app, ["init-db", "--db-url", db_url])
    assert r.exit_code == 0, r.output

    r = runner.invoke(cli.app, ["seed-universe", "--db-url", db_url])
    assert r.exit_code == 0, r.output

    r = runner.invoke(
        cli.app,
        [
            "backfill-prices",
            "--index",
            "NIFTY_50",
            "--start",
            "2024-06-03",
            "--end",
            "2024-06-28",
            "--db-url",
            db_url,
        ],
    )
    assert r.exit_code == 0, r.output
    assert "SUCCEEDED" in r.output

    r = runner.invoke(
        cli.app,
        [
            "backfill-actions",
            "--index",
            "NIFTY_50",
            "--start",
            "2024-06-01",
            "--end",
            "2024-06-30",
            "--db-url",
            db_url,
        ],
    )
    assert r.exit_code == 0, r.output
    assert "SUCCEEDED" in r.output


def test_cli_exposes_phase_5_commands():
    r = runner.invoke(cli.app, ["--help"])
    assert r.exit_code == 0
    for command in (
        "compute-eod",
        "generate-recommendations",
        "track-recommendations",
        "dispatch-notifications",
    ):
        assert command in r.output


def test_cli_exposes_phase_6_commands():
    r = runner.invoke(cli.app, ["--help"])
    assert r.exit_code == 0
    for command in ("backtest", "backtest-walk-forward", "backtest-list", "backtest-report"):
        assert command in r.output


def test_cli_backtest_runs_and_reports_against_a_real_db(
    tmp_path, pointed_at_long_fixtures
):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'nmi.db'}"
    for args in (
        ["init-db", "--db-url", db_url],
        ["seed-universe", "--db-url", db_url],
        ["backfill-actions", "--index", "NIFTY_50",
         "--start", "2024-01-01", "--end", "2024-06-28", "--db-url", db_url],
        ["backfill-prices", "--index", "NIFTY_50",
         "--start", "2024-01-01", "--end", "2024-06-28", "--db-url", db_url],
        ["backfill-fundamentals", "--db-url", db_url],
        ["backfill-index-prices", "--index", "NIFTY_50", "--db-url", db_url],
        ["compute-eod", "--index", "NIFTY_50",
         "--start", "2024-01-01", "--end", "2024-06-28", "--db-url", db_url],
    ):
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 0, f"{args}: {result.output}"

    r = runner.invoke(
        cli.app,
        ["backtest", "--index", "NIFTY_50", "--start", "2024-01-01",
         "--end", "2024-06-28", "--capital", "500000", "--db-url", db_url],
    )
    assert r.exit_code == 0, r.output
    assert "backtest run 1" in r.output
    assert "return" in r.output and "trades" in r.output

    r = runner.invoke(cli.app, ["backtest-list", "--db-url", db_url])
    assert r.exit_code == 0, r.output
    assert "NIFTY_50" in r.output

    r = runner.invoke(cli.app, ["backtest-report", "1", "--trades", "--db-url", db_url])
    assert r.exit_code == 0, r.output
    assert "capital" in r.output
    assert "trades" in r.output and "exposure" in r.output

    r = runner.invoke(cli.app, ["backtest-walk-forward", "--index", "NIFTY_50",
                                 "--start", "2024-01-01", "--end", "2024-06-28",
                                 "--folds", "2", "--db-url", db_url])
    assert r.exit_code == 0, r.output
    assert "window" in r.output
    assert "consistency" in r.output

    # An unknown id is an error, not an empty success.
    r = runner.invoke(cli.app, ["backtest-report", "999", "--db-url", db_url])
    assert r.exit_code == 1
    assert "no backtest run" in r.output


def test_cli_backtest_refuses_a_window_with_no_data(tmp_path, pointed_at_long_fixtures):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'nmi.db'}"
    runner.invoke(cli.app, ["init-db", "--db-url", db_url])
    r = runner.invoke(
        cli.app,
        ["backtest", "--index", "NIFTY_50", "--start", "2020-01-01", "--end", "2020-03-31",
         "--db-url", db_url],
    )
    assert r.exit_code != 0


def test_cli_tracking_and_notifications_run_against_a_real_db(tmp_path, pointed_at_fixtures):
    db_url = f"sqlite+pysqlite:///{tmp_path / 'nmi.db'}"
    runner.invoke(cli.app, ["init-db", "--db-url", db_url])
    runner.invoke(cli.app, ["seed-universe", "--db-url", db_url])
    runner.invoke(
        cli.app,
        ["backfill-prices", "--index", "NIFTY_50", "--start", "2024-06-03", "--end", "2024-06-28",
         "--db-url", db_url],
    )

    r = runner.invoke(cli.app, ["track-recommendations", "--index", "NIFTY_50", "--db-url", db_url])
    assert r.exit_code == 0, r.output
    assert "track_recommendations" in r.output

    r = runner.invoke(
        cli.app,
        ["dispatch-notifications", "--index", "NIFTY_50", "--db-url", db_url],
    )
    assert r.exit_code == 0, r.output
    assert "dispatch_notifications" in r.output
