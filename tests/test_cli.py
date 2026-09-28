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
