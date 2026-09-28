# Nifty Market Intelligence & Stock Analysis Platform

Explainable, auditable, backtestable decision-support platform for the Indian
equity market (Nifty 50 / Midcap 150 / Smallcap 250). See `PLAN.md` for the
phased roadmap.

**Current status: Phase 5 (Tracking, Thesis Monitoring, Exit, Notifications).**

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
  indicators/            # technical / fundamental / valuation / momentum metrics
  metrics/service.py     # EOD orchestrator: one audited job per stage
  strategies/            # versioned rule engine + catalog
  tracking/              # thesis snapshots, exit engines, lifecycle, notifications
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
.venv/bin/nmi backfill-index-prices --index NIFTY_50
.venv/bin/nmi seed-strategies
```

Then run the daily chain (indicators -> metrics -> regime/sector/horizon ->
scoring -> signals -> recommendations -> tracking -> notifications):

```bash
.venv/bin/nmi compute-eod --index NIFTY_50 --start 2024-01-01 --end 2024-06-28
```

Individual stages (`compute-fundamentals`, `compute-tech`, `compute-momentum`,
`compute-valuation`, `compute-sector`, `compute-regime`, `compute-horizon`,
`compute-scoring`, `compute-signals`, `generate-recommendations`) can be run on
their own, as can the two tracking stages (`track-recommendations`,
`dispatch-notifications`), which are safe to re-run for a day already covered.

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

## Design invariants (Phase 4–5)

- **A recommendation is never overwritten.** `recommendations` holds the live
  view; every state change appends an immutable `recommendation_versions` row, so
  the original levels, stop and thesis text stay auditable forever.
- **Every state change is explained.** `recommendation_events` records the
  previous state, the new state, the mechanism, the full ordered list of fired
  rules, and a human-readable summary. `exit_reason` is the concatenation of all
  reasons, not just the first one that happened to sort first.
- **The thesis is tracked against the day it was written.** A 13-factor snapshot
  is captured from the exact signal context when the recommendation is created,
  and later snapshots always compare to that original day. A legacy
  recommendation whose baseline cannot be reconstructed records a
  `BASELINE_UNAVAILABLE` gap instead of inventing one.
- **Thirteen factors, four directions, five flags.** Each factor is compared to
  its baseline as `IMPROVED` / `STABLE` / `WEAKENED` / `INVALIDATED` /
  `UNKNOWN`; at least `weaken_score_factors` weakenings move a live
  recommendation to `THESIS_WEAKENING`.
- **Exit mechanisms are independent.** `RISK`, `TECHNICAL`, `FUNDAMENTAL`,
  `VALUATION`, `TARGET`, `TIME`, `EVENT` and `STRATEGY` each decide review vs.
  exit on their own; the highest-severity mechanism drives the transition while
  every fired reason is preserved.
- **You can only exit a position you hold.** An exit condition on a
  recommendation that was never entered becomes `EXIT_REVIEW` with an explicit
  "not an active position" reason; an `EXIT` is stamped on the review day, then
  closed at the next distinct review day (same-day reruns are idempotent).
- **Every alert carries a reason and is deduplicated.** Notifications are keyed
  on the exact fact that produced them (event + recommendation + version), so
  re-running the chain never re-alerts, and each alert keeps the triggering
  reason, severity and event link.

## Providers

Selection is configuration-driven:

| Family              | Setting                        | Adapters        |
|---------------------|--------------------------------|-----------------|
| prices              | `PRICE_PROVIDER`               | csv, yahoo      |
| corporate actions   | `CORPORATE_ACTION_PROVIDER`    | csv             |
| index membership    | `INDEX_MEMBERSHIP_PROVIDER`    | csv             |
| fundamentals        | `FUNDAMENTAL_PROVIDER`         | csv             |

## Tests

212 tests covering: adjustment math (splits, bonus, dividends, combined),
validation rules, normalization, CSV providers, membership interval semantics,
indicator/metric math, regime/sector/horizon/scoring, the strategy rule engine,
the tracking engines (thesis, exits, lifecycle, notifications), the end-to-end
pipeline on a real SQLite database (seeding → backfill → signals →
recommendations → tracking → notifications → closure) and the CLI. Run
`make test`.