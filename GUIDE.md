# Running Nifty Market Intelligence — User Guide

A step-by-step guide to getting the platform running on your machine, from
first install to the dashboard in your browser. No prior knowledge of the
codebase required.

## What you need

- **Python 3.11**
- **Node.js 18+** (for the dashboard)
- **Docker** (for PostgreSQL; skip if you already have Postgres 16 running)
- **Make** (comes with macOS/Linux; use WSL on Windows)

## 1. Install

```bash
git clone https://github.com/NitheeshPrahalath/nifty-market-intelligence.git
cd nifty-market-intelligence
make install        # creates .venv and installs everything
```

Create your local configuration:

```bash
cp .env.example .env
```

The defaults work out of the box — they point at the local Docker database
and the bundled sample data. You normally do not need to edit anything here
for a first run.

## 2. Start the database

```bash
make db-up          # starts PostgreSQL 16 in Docker
make test           # optional sanity check: run the test suite
```

Apply the database schema:

```bash
.venv/bin/alembic upgrade head
```

## 3. Load data and run the pipeline

Load the sample universe (companies, instruments, index membership), prices,
corporate actions and fundamentals:

```bash
.venv/bin/nmi seed-universe
.venv/bin/nmi backfill-prices \
    --index NIFTY_50,NIFTY_MIDCAP_150 --start 2024-06-03 --end 2024-06-28
.venv/bin/nmi backfill-actions --index NIFTY_50
.venv/bin/nmi backfill-fundamentals
.venv/bin/nmi backfill-index-prices --index NIFTY_50
.venv/bin/nmi seed-strategies
```

Then run the daily chain — this computes indicators, scores, signals,
recommendations, tracking and notifications in one go:

```bash
.venv/bin/nmi compute-eod --index NIFTY_50 --start 2024-01-01 --end 2024-06-28
```

Optional: backtest the same strategies:

```bash
.venv/bin/nmi backtest --index NIFTY_50 \
    --start 2024-01-01 --end 2024-06-28 --capital 1000000
.venv/bin/nmi backtest-list
```

Optional: generate cited AI explanations for recommendations (offline
template provider by default — no API key needed):

```bash
.venv/bin/nmi explain --index NIFTY_50
```

## 4. Start the API

In one terminal:

```bash
.venv/bin/nmi serve --host 127.0.0.1 --port 8000
```

Check it is up:

```bash
curl -s localhost:8000/health
```

Interactive API docs: open **http://127.0.0.1:8000/docs** in your browser.

Useful endpoints:

| URL | What it shows |
|-----|---------------|
| `/health`, `/health/ready` | Service and dependency health |
| `/metrics` | Prometheus metrics |
| `/api/v1/market/overview?index=NIFTY_50` | Index overview |
| `/api/v1/screener?index=NIFTY_50` | Screener results |
| `/api/v1/stocks/TCS` | Stock detail |
| `/api/v1/recommendations` | Live recommendations |
| `/api/v1/quality` | Data-quality checks |

Auth is **off by default** for local use. To require an API key, set in
`.env`:

```
API_AUTH_ENABLED=true
API_KEYS=["my-secret-key"]
```

then send the key as `X-API-Key: my-secret-key` or
`Authorization: Bearer my-secret-key`. Roles (viewer / analyst / admin) can be
assigned per key via `API_KEY_ROLES`.

## 5. Start the dashboard

With the API still running, open a second terminal:

```bash
make web-install    # first time only
make web-dev
```

Open **http://localhost:3000**. You should see the market overview. Use the
index selector to switch between Nifty 50 / Midcap 150 / Smallcap 250, and
navigate to the screener, stock detail, recommendations and alert pages.

The dashboard talks to the API through a server-side proxy — no CORS setup
or extra configuration is needed. If it cannot reach the API, make sure
`nmi serve` is still running and `API_PROXY_TARGET` in `.env` matches
(default `http://127.0.0.1:8000`).

For a production build instead of the dev server:

```bash
make web-build && npm --prefix apps/web run start
```

## Everyday commands

| Task | Command |
|------|---------|
| Start/stop database | `make db-up` / `make db-down` |
| Run tests | `make test` |
| Lint | `make lint` |
| Serve API | `.venv/bin/nmi serve` |
| Daily data refresh | `.venv/bin/nmi compute-eod --index NIFTY_50 --start … --end …` |
| Dashboard dev server | `make web-dev` |
| List all CLI commands | `.venv/bin/nmi --help` |

## Troubleshooting

- **`connection refused` on the API** — run `make db-up`, then check
  `DATABASE_URL` in `.env` matches the Docker credentials.
- **Empty pages in the dashboard** — the pipeline has not been run for the
  selected index/date. Run the seeding and `compute-eod` steps in section 3.
- **Dashboard loads but shows errors** — confirm the API is up
  (`curl localhost:8000/health`) and that `API_PROXY_TARGET` points at it.
- **Port already in use** — pass a different port:
  `nmi serve --port 8001`, and set `API_PROXY_TARGET=http://127.0.0.1:8001`
  in `.env` before starting the dashboard.
- **Want a clean slate** — `make db-down && make db-up`, then re-run
  `alembic upgrade head` and section 3 from the top.
