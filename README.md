# Nifty Market Intelligence & Stock Analysis Platform

Explainable, auditable, backtestable decision-support platform for the Indian
equity market (Nifty 50 / Midcap 150 / Smallcap 250). See `PLAN.md` for the
phased roadmap.

**Current status: Phase 1 (Data Ingestion & Validation).**

## Repository layout

```
apps/                    # FastAPI + Next.js dashboards (Phase 7/8)
libs/                    # shared libraries (next to nmi.core)
services/                # analysis, strategies, backtest, notify (Phase 2+)
src/nmi/
  core/                  # enums, config, logging, db, SQLAlchemy models
  ingestion/
    providers/           # vendor adapters (csv, yahoo) + factory
    records.py           # canonical validated input records
    normalize.py         # dedupe / sort
    validation.py        # OHLC, duplicates, gaps, outliers, source flags
    adjustments.py       # corporate-action adjusted prices
    store.py             # dialect-safe idempotent upserts
    backfill.py          # orchestration, audit runs, retries
    cli.py               # `nmi` command-line interface
alembic/                 # schema migrations
tests/                   # unit + integration tests
data/                    # (git-ignored) raw/processed data, sample files in tests/fixtures
```

## Quickstart

```bash
make install           # create .venv (Python 3.11) and install -e ".[dev]"
make test              # run the test suite
make lint              # ruff
```

Run the full pipeline locally against the sample fixtures:

```bash
cp .env.example .env
make db-up             # PostgreSQL 16 + TimescaleDB (+ optional Redis)
.venv/bin/alembic upgrade head        # apply schema to your DATABASE_URL
.venv/bin/nmi seed-universe           # companies/instruments + index membership
.venv/bin/nmi backfill-prices \
    --index NIFTY_50,NIFTY_MIDCAP_150 --start 2024-06-03 --end 2024-06-28
.venv/bin/nmi backfill-actions --index NIFTY_50
.venv/bin/nmi backfill-fundamentals
```

`seed-universe` reads `tests/fixtures/symbols.csv` and
`tests/fixtures/memberships/index_memberships.csv` by default; point the CSV
providers anywhere via settings (see `.env.example`).

## Design invariants (Phase 1)

- **Universe is as-of.** Index membership is stored as `[effective_from,
  effective_to)` intervals (`index_memberships`), so analysis never assumes the
  current constituents were always members — this removes survivorship bias
  before it can reach the backtest engine.
- **Adjustments are auditable.** Every price row stores a per-row
  `adjustment_factor` that maps raw as-traded price to adjusted basis (splits,
  bonus, rights, dividends). Adjustment is reversible and unit-tested.
- **Nothing is silently computed from bad data.** Validation flags OHLC
  inconsistencies, duplicates, gaps, zero-volume/suspension, and price outliers
  (configurable). Failing rows are persisted with `is_incomplete = True` and
  recorded in `ingestion_errors` — they are never dropped or silently filled.
- **Every write is idempotent.** Re-running a backfill supersedes the same
  `(instrument, trade_date)` rows; `ingestion_runs` records each attempt for
  retry planning and audit.
- **Vendors are pluggable.** The pipeline only talks to provider protocols.
  `csv` (deterministic local files) and `yahoo` (network fallback) adapters ship
  today; Kite/NSE adapters slot in behind the same interface.

## Providers

Selection is configuration-driven:

| Family              | Setting                        | Adapters        |
|---------------------|--------------------------------|-----------------|
| prices              | `PRICE_PROVIDER`               | csv, yahoo      |
| corporate actions   | `CORPORATE_ACTION_PROVIDER`    | csv             |
| index membership    | `INDEX_MEMBERSHIP_PROVIDER`    | csv             |
| fundamentals        | `FUNDAMENTAL_PROVIDER`         | csv             |

## Tests

40 tests covering: adjustment math (splits, bonus, dividends, combined),
validation rules, normalization, CSV providers, membership interval semantics,
the end-to-end ingestion pipeline (seeding → backfill → corporate actions →
fundamentals) and the CLI. Run `make test`.