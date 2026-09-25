# Nifty Market Intelligence & Stock Analysis Platform — Implementation Plan

Production-oriented decision-support platform for the Indian equity market
(Nifty 50, Midcap 150, Smallcap 250). Explainable, auditable, and backtestable —
never a plain BUY/SELL generator.

## Recommended Stack

### Backend core
- **Python 3.11** + **uv** for dependency management
- **FastAPI + Uvicorn**, **Pydantic v2** for API & config (`pydantic-settings`)
- **SQLAlchemy 2.0** (typed ORM) + **Alembic** migrations
- **PostgreSQL 16 + TimescaleDB** extension — hypertables for `daily_prices`/`intraday_prices`,
  continuous aggregates for score history
- **Redis** — Celery broker/result backend, caching, rate limits

### Compute / analysis
- **Polars** (bulk transforms) + **Pandas/NumPy** where ecosystem libs require it
- Versioned indicator functions (NumPy/TA-Lib based) — every calc records `calc_version`
- **SciPy** for statistical percentiles/correlations

### Orchestration
- **Celery + Celery Beat** for EOD jobs (ingest → validate → indicators → analysis →
  re-evaluate tracked recs → notify). Upgrade path to **Prefect/Dagster**.

### Data validation
- **pandera / Great Expectations** wired into ingestion with failed-job log + retry

### Backtesting
- **Custom event-driven engine** reusing the same strategy rule modules as live →
  guaranteed live/backtest parity. Sanity-check against **vectorbt**.

### AI layer
- **LLM API** (OpenAI/Gemini) or **Ollama** locally. Thin layer: prompt templates +
  Pydantic-validated structured outputs with metric citations. No invented numbers.

### Frontend
- **Next.js (App Router) + TypeScript + Tailwind + shadcn/ui**
- **TanStack Query** (server state), **Zustand** (UI state)
- **TradingView lightweight-charts** (candlestick) + **Recharts** (scores/breadth)

### Observability / DevOps
- **structlog**, **OpenTelemetry**, **Prometheus + Grafana**, **Sentry**
- **GitHub Actions** CI, **Docker Compose** (dev/stage), optional **K8s** prod,
  **pgBackRest** backups + WAL streaming

### Data sources (India) — pluggable adapters, primary + fallback, source-tracked
- Prices: **Kite Connect (Zerodha)** or NSE/BSE dump; **yfinance** fallback
- Fundamentals: **screener.in** (scraped) or vendor packages
- Index membership history: reconstructed constituent lists captured over time
- Corporate actions: BSE/NSE announcements + manual reconciliation

## Phases

**P0 — Foundation (Wk 0–2)**
- Monorepo: `apps/`, `libs/`, `services/`, shared `nmi.core` (db, models, config)
- Docker Compose (Postgres+Timescale, Redis), Alembic baseline for all tables
- Config (`pydantic-settings`), secrets, ruff/mypy, pytest scaffolding, fixtures
- Exit: `docker compose up` boots; migrations apply; pipeline skeleton

**P1 — Data Ingestion & Validation (Wk 2–5)**
- Vendor adapters (prices, fundamentals, corporate actions, index membership)
- Normalization to canonical schema; corporate-action adjusted close
- Validation: OHLC consistency, duplicates, gaps/suspensions, outliers; flag incomplete data
- Backfill jobs + retry/alert plumbing
- Exit: full-history EOD ingestion for all historical constituents, validated and source-tracked

**P2 — Indicator & Metrics Engines (Wk 4–8)**
- Technical indicators (MAs, RSI, MACD, ATR, Bollinger, volume, 52-week structure)
- Fundamentals (derived ratios, quality), Valuation (percentiles vs own-history/sector/index),
  Momentum & Relative Strength vs benchmarks/sector
- Numeric values + `calc_version`; historical recompute each EOD

**P3 — Regime, Sector, Horizon, Scoring (Wk 6–9)**
- Market-regime model (breadth, %above DMA, momentum, vol, drawdown, sector participation)
- Sector analysis; horizon engine (ST/MT/LT) → component/weight selection
- Scoring: 8 component scores + weighted composite; weights in `strategy_parameters`

**P4 — Strategy, Signal & Recommendation Engine (Wk 8–12)**
- Versioned strategies (entry/hold/exit/invalidation rules)
- Full recommendation object (lifecycle states, entry zone, invalidation, thesis, reasons);
  `recommendation_versions` — never overwrite originals

**P5 — Tracking, Thesis Monitoring, Exit, Notifications (Wk 10–14)**
- Daily re-eval vs thesis snapshot; change detection → `THESIS_WEAKENING`/`EXIT_REVIEW`
- Six exit mechanisms wired to reasons; notification engine with reason + dedupe + history

**P6 — Backtesting Engine (Wk 12–16)**
- As-of snapshots (historical membership, fundamentals-at-date); no look-ahead/survivorship bias
- Event-driven engine reusing P4 rule modules; costs, slippage, sizing, risk mgmt
- Metric suite: CAGR, maxDD, Sharpe/Sortino, win rate, profit factor, turnover; walk-forward

**P7 — API Backend (Wk 14–17)**
- REST: market overview, screener, stock detail, recommendation dashboard, alerts, auth, audit
- Pagination, Redis caching, rate limits, typed Pydantic responses, OpenAPI

**P8 — Frontend Dashboard (Wk 15–19)**
- Market overview, screener, stock detail, recommendation dashboard, alert center

**P9 — AI Explanations Layer (Wk 17–19)**
- Structured data → natural-language thesis/change/research summaries; cited, audited

**P10 — Production Hardening (Wk 18–22)**
- Observability, data-quality reconciliation, security/RBAC/audit, backup/DR, load tests

## Key decisions locked early
1. **TimescaleDB from day one** — continuous re-analysis + score history is time-series.
2. **One rule implementation for live + backtest** — prevents rule drift invalidating backtests.
3. **Distrust data by default** — validation + source tracking are not optional (Indian market data).
4. **Index membership effective-dates** are first-class — reconstitution every 6 months silently
   distorts analysis and backtests otherwise.

## Current status
- [x] P0 Foundation
- [x] **P1 Data Ingestion & Validation**
- [x] **P2 Indicator & Metrics Engines**
- [ ] P3 Regime / Sector / Horizon / Scoring
- [ ] P4 Strategy / Signal / Recommendation
- [ ] P5 Tracking / Thesis Monitoring / Exit / Notifications
- [ ] P6 Backtesting Engine
- [ ] P7 API Backend
- [ ] P8 Frontend Dashboard
- [ ] P9 AI Explanations Layer
- [ ] P10 Production Hardening