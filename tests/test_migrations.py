"""The Alembic history must build exactly the schema the ORM declares.

A migration that is merely runnable is not enough: a column that exists in the
model but not in the database (or the other way round) fails at runtime, in
production, long after the tests were green. So this builds a database from
scratch by walking the whole revision chain, and then compares it, column by
column, with ``Base.metadata``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

import nmi.backtest  # noqa: F401  (registers the Phase-6 models)
import nmi.core.models  # noqa: F401
from alembic import command
from nmi.core.db import Base

REPO_ROOT = Path(__file__).resolve().parents[1]
PHASE_6_REVISION = "a4c7e2b18d93"


def _alembic_config(db_url: str) -> Config:
    """The migration environment takes its URL from ``ALEMBIC_DB_URL``, not from
    the ini file, so a test that wants its own database sets the environment
    rather than quietly pointing at the developer's PostgreSQL."""
    os.environ["ALEMBIC_DB_URL"] = db_url
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return config


def _columns(inspector, table: str) -> dict[str, str]:
    return {col["name"]: str(col["type"]) for col in inspector.get_columns(table)}


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory) -> str:
    """A database built by running every migration from empty to head."""
    path = tmp_path_factory.mktemp("alembic") / "chain.db"
    url = f"sqlite+pysqlite:///{path}"
    config = _alembic_config(url)
    command.upgrade(config, "head")
    yield url
    # Prove the chain also rolls all the way back down.
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def test_every_revision_is_reachable_from_the_base(migrated_db):
    script = ScriptDirectory.from_config(_alembic_config(migrated_db))
    revisions = list(script.walk_revisions())
    assert revisions, "no migrations found"
    assert script.get_current_head() == revisions[0].revision
    # Each revision has exactly one parent, so there is no branching history.
    assert len({r.revision for r in revisions}) == len(revisions)
    for revision in revisions:
        assert revision.down_revision is None or isinstance(revision.down_revision, str)


def test_the_migrated_schema_matches_the_orm(migrated_db):
    engine = create_engine(migrated_db)
    inspector = inspect(engine)
    migrated = set(inspector.get_table_names()) - {"alembic_version"}
    declared = set(Base.metadata.tables)
    assert migrated == declared, (
        f"only in database: {sorted(migrated - declared)}; "
        f"only in models: {sorted(declared - migrated)}"
    )
    for table in sorted(declared):
        model_columns = {
            column.name: str(column.type) for column in Base.metadata.tables[table].columns
        }
        db_columns = _columns(inspector, table)
        assert db_columns == model_columns, f"{table}: {db_columns} != {model_columns}"


def test_the_phase_6_tables_are_created_and_dropped(migrated_db, tmp_path):
    config = _alembic_config(migrated_db)
    inspector = inspect(create_engine(migrated_db))
    phase_6_tables = {
        "backtest_runs",
        "backtest_trades",
        "backtest_equity_points",
        "backtest_rejections",
    }
    assert phase_6_tables <= set(inspector.get_table_names())

    # Stepping back to Phase 5 removes exactly the Phase-6 tables, and stepping
    # forward puts them back.
    command.downgrade(config, "d5e1a04b7c39")
    after_down = set(inspect(create_engine(migrated_db)).get_table_names())
    assert not (phase_6_tables & after_down)
    assert "strategies" in after_down  # earlier phases are untouched
    command.upgrade(config, PHASE_6_REVISION)
    after_up = set(inspect(create_engine(migrated_db)).get_table_names())
    assert phase_6_tables <= after_up
