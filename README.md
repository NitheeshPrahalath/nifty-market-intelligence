# Nifty Market Intelligence & Stock Analysis Platform

Explainable, auditable, backtestable decision-support platform for the Indian
equity market (Nifty 50 / Midcap 150 / Smallcap 250). See `PLAN.md` for the
phased roadmap.

**Current status: Phase 9 (cited, audited AI explanations layer).**

## Repository layout

```
apps/
  web/                   # Phase 8 Next.js dashboard (App Router, TS, Tailwind)
    src/app/             # market overview, screener, stock, recommendations, alerts
    src/components/      # ui kit + feature components (charts, tables, panels)
    src/lib/             # typed API client, domain types, React Query hooks
    src/store/           # Zustand UI state (selected index)
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
  backtest/              # as-of event-driven backtester + metric suite
  api/                   # Phase 7 read-only REST API (routers, gateway, schemas)
  explain/               # Phase 9 cited AI explanations (facts, prompts, providers, audit)
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

Then backtest the same strategies that were just seeded and emitted signals
(Phase 6):

```bash
.venv/bin/nmi backtest --index NIFTY_50 \
    --start 2024-01-01 --end 2024-06-28 --capital 1000000
.venv/bin/nmi backtest-walk-forward --index NIFTY_50 \
    --start 2024-01-01 --end 2024-06-28 --folds 4
.venv/bin/nmi backtest-list
.venv/bin/nmi backtest-report 1 --trades
```

Then serve the read-only API (Phase 7):

```bash
.venv/bin/nmi serve --host 127.0.0.1 --port 8000     # OpenAPI docs at /docs
curl -s localhost:8000/health
curl -s 'localhost:8000/api/v1/market/overview?index=NIFTY_50'
curl -s 'localhost:8000/api/v1/screener?index=NIFTY_50&page_size=10'
curl -s localhost:8000/api/v1/stocks/TCS
curl -s localhost:8000/api/v1/recommendations
curl -s 'localhost:8000/api/v1/recommendations/1/explanation'  # 404 until `nmi explain` runs
```

Auth, rate limiting and response caching are off/default for local use; set
`API_AUTH_ENABLED=true` with `API_KEYS=[...]` to require a key (`X-API-Key` or
`Authorization: Bearer`), `API_RATE_LIMIT_PER_MINUTE` to bound callers, and
`REDIS_URL` for a shared cache/limiter. Every request is audited in
`api_requests`; `nmi audit-prune` trims rows older than the retention window.

`seed-universe` reads `tests/fixtures/symbols.csv` and
`tests/fixtures/memberships/index_memberships.csv` by default; point the CSV
providers anywhere via settings (see `.env.example`).

Then run the dashboard (Phase 8), with the API from the step above still
running:

```bash
make web-install       # npm install in apps/web
make web-dev           # next dev on http://localhost:3000
# or a production build:
make web-build && npm --prefix apps/web run start
```

The browser only ever calls the dashboard's own origin; Next proxies `/api/v1/*`
and `/health` to the API server-side (set `API_PROXY_TARGET`, default
`http://127.0.0.1:8000`), so the API needs no CORS configuration and its origin
is not exposed to the client. Five surfaces are covered — market overview,
screener, stock detail, recommendation dashboard and alert centre — each with
explicit loading / empty / error states, and `—` is rendered wherever the API
reports a missing input rather than a fabricated zero.

Generate cited natural-language explanations (Phase 9) for tracked
recommendations:

```bash
.venv/bin/nmi explain --index NIFTY_50                 # thesis (default), template provider
.venv/bin/nmi explain --index NIFTY_50 --kind change   # what changed since the baseline
.venv/bin/nmi explain --recommendation-id 1,2 --force  # specific ids, regenerate
```

The default `template` provider is deterministic and offline; set
`EXPLAIN_PROVIDER=openai`, `EXPLAIN_API_KEY` (and optionally `EXPLAIN_MODEL`,
`EXPLAIN_BASE_URL`) to call any OpenAI-compatible `/chat/completions` endpoint.
Every generated explanation is validated for citation coverage and stored in
`explanations`, then served read-only at
`GET /api/v1/recommendations/{id}/explanation?kind=thesis|change|research` and
rendered on the recommendation page in the dashboard.

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

## Design invariants (Phase 6)

- **The backtest sees the day the signal saw.** Membership intervals, scoring,
  regime, sector state and technicals are all resolved by `as_of`, and a
  decision taken on day D is filled at day D+1's open. No future bar, no
  current-constituent survivorship, no same-bar fill.
- **One rule implementation for live and backtest.** The engine reads
  `StrategyVersion.rules` and calls the same view/level helpers the live
  pipeline does, so a rule edit changes both sides at once — live/backtest
  parity by construction rather than by discipline.
- **Every price sits on one axis.** Indicators, strategy anchors, entry zones
  and simulated fills all use the adjusted basis (raw open/high/low scaled by
  that bar's own adjustment factor). Mixing raw highs with adjusted closes
  would shift every ATR-derived level by the split factor, which is exactly the
  kind of silent scale bug a backtest is worst at revealing.
- **Costs, slippage, sizing and risk are modelled, not assumed.** Per-side cost
  and slippage bps, risk-per-trade sizing against the stop distance, position
  and slot caps, cash and minimum-lot checks, and an explicit rejection reason
  (`NO_DATA`, `NO_SIGNAL`, `NO_CASH`, `BELOW_LOT`, `NO_FREE_SLOT`,
  `ALREADY_HELD`, ...) for every day and instrument that did not trade.
- **Stops beat targets.** When one bar touches both, the stop wins; a gap
  through a stop fills at the open, not at the stop price. Membership and bars
  are revalidated at fill time, so an order queued on day D dies if the
  instrument left the index overnight.
- **Every run is reproducible and auditable.** A run persists its config,
  window, metric suite, trades, equity curve and an audit log of engine and
  database operations; walk-forward windows are child runs of their parent, so
  an out-of-sample result can always be traced back to what produced it.

## Design invariants (Phase 7)

- **Every view is as-of.** The API resolves membership, scores, signals and
  levels for the requested date, never "now", so a historical question gets the
  historical answer and a dashboard cannot quietly show today's constituents
  against last quarter's recommendation.
- **The query layer is HTTP-free.** Routers translate HTTP to calls on
  `nmi.api.service`, a set of plain functions over a `Session` returning typed
  schemas. The gateway owns auth, limiting, caching and audit; neither side needs
  to know about the other, and the query layer is unit-tested without a client.
- **Cross-cutting concerns share one pass.** `GatewayMiddleware` authenticates
  (`X-API-Key` or `Authorization: Bearer`, constant-time compare), applies the
  per-principal fixed-window rate limit, serves the response cache (keyed on the
  full request target *and* the principal, so one tenant never sees another's
  body) and writes the audit row — because they share the same per-request facts.
- **Auditing never breaks serving.** Audit writes are best-effort and the
  principal stored is a short non-reversible key label, never the key itself.
  `/health` and `/audit` are never cached; every response carries `X-Request-Id`
  and `X-Cache`, and a 429 carries `Retry-After`.
- **Redis is an optimisation, never a dependency.** Cache and limiter use a tiny
  KV abstraction that defaults to an in-process store and degrades to a miss (or
  an allowed request) if Redis is unset or unreachable, so a missing cache box
  slows the API rather than taking it down.

## Design invariants (Phase 8)

- **The API origin never reaches the browser.** Next rewrites `/api/v1/*` and
  `/health` to `API_PROXY_TARGET` server-side, so the client calls its own
  origin: no CORS on the API, no backend URL in the bundle, and only
  `NEXT_PUBLIC_API_BASE_URL` can opt out for split hosting.
- **Types are hand-written, not generated.** `src/lib/types.ts` mirrors the
  Pydantic schemas, so a changed contract fails `tsc` at build time instead of
  surfacing as a runtime `undefined` — the dashboard cannot compile against a
  shape the API no longer serves.
- **Missing is shown as missing.** The backend emits `null` (never a fabricated
  zero) for absent inputs, and the formatters render `—`; "no data yet" and a
  real `0.00` stay visually distinct.
- **Server state and UI state are separated.** TanStack Query owns everything
  fetched (as-of keyed, `keepPreviousData` while paginating, one shared 60s stale
  window); Zustand holds only ephemeral UI state (the selected index shared
  across pages). Neither duplicates the other.
- **Charts are client-only and SSR-safe.** `lightweight-charts` touches the DOM,
  so `PriceChart` imports it inside an effect against a plain div rather than at
  module scope, keeping the route prerenderable.

## Design invariants (Phase 9)

- **No invented numbers.** A fact sheet is built first (as-of levels, scores,
  factors, thesis deltas, events); every numeric token the model emits must trace
  back to a cited fact, or the draft is rejected. A model cannot smuggle in a
  price it was not handed.
- **Facts are the single source of truth.** `source_hash` is the hash of the
  canonical fact sheet, so an explanation is cached by `(recommendation, kind,
  source_hash)` and reused until the underlying facts change — never staleness by
  date alone, and never a re-run that silently contradicts the stored one.
- **The LLM is optional and swappable.** Providers sit behind one protocol; the
  `template` provider is deterministic and offline, so the pipeline, tests and API
  work with no key or network. A provider failure or a citation failure degrades
  to the template with a recorded `fallback_reason` rather than surfacing nothing.
- **Every explanation is auditable.** Provider, model, prompt version, source
  hash, raw response, validation result and any fallback are persisted in
  `explanations`; the API and dashboard show which provider produced a summary and
  whether it passed citation validation.

## Providers

Selection is configuration-driven:

| Family              | Setting                        | Adapters        |
|---------------------|--------------------------------|-----------------|
| prices              | `PRICE_PROVIDER`               | csv, yahoo      |
| corporate actions   | `CORPORATE_ACTION_PROVIDER`    | csv             |
| index membership    | `INDEX_MEMBERSHIP_PROVIDER`    | csv             |
| fundamentals        | `FUNDAMENTAL_PROVIDER`         | csv             |
| AI explanations     | `EXPLAIN_PROVIDER`             | template, openai |

## Tests

331 tests covering: adjustment math (splits, bonus, dividends, combined),
validation rules, normalization, CSV providers, membership interval semantics,
indicator/metric math, regime/sector/horizon/scoring, the strategy rule engine,
the tracking engines (thesis, exits, lifecycle, notifications), the Phase-6
backtester (as-of dataset, event-driven fills, costs, sizing, exit handling, the
metric suite, walk-forward), the Phase-7 API (auth on/off, rate limiting,
response caching, audit logging, pagination, the typed data endpoints), the
Phase-9 explanation layer (fact sheets, citation validation, providers, caching
and fallback, the explanation endpoint), the Phase-10 observability, RBAC,
security headers, data-quality reconciliation and Alembic migration graph, the
end-to-end pipeline on a real SQLite database (seeding → backfill → signals →
recommendations → tracking → notifications → explanations → closure) and the CLI.
Run `make test`.

The Phase-8 dashboard has its own suite (30 Vitest tests over the API client,
formatters, screener table and pagination) plus a strict `tsc` typecheck and a
production `next build`. Run `make web-test`, `make web-typecheck` and
`make web-build`.