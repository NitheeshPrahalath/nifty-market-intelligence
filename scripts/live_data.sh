#!/usr/bin/env bash
#
# Live NSE data pipeline: Nifty 50 + Midcap 150 + Smallcap 250.
#
# Pulls free official NSE end-of-day files (bhavcopy for stock prices,
# ind_close_all for the benchmark index closes), loads them into Postgres,
# then runs the full daily compute chain (indicators, scores, signals,
# recommendations, tracking). Safe to re-run: already-downloaded files are
# reused from data/raw/bhavcopy and data/raw/index_close.
#
# Usage:
#   ./scripts/live_data.sh
#   START=2025-09-15 END=2026-10-16 ./scripts/live_data.sh
#
set -euo pipefail

START="${START:-2025-09-15}"
END="${END:-2026-10-16}"
INDICES="NIFTY_50,NIFTY_MIDCAP_150,NIFTY_SMALLCAP_250"
NMI=".venv/bin/nmi"

cd "$(dirname "$0")/.."

# 1) Database (idempotent) -------------------------------------------------
make db-up
.venv/bin/alembic upgrade head

# 2) Reference data: universe + membership for all three indices ----------
"$NMI" seed-universe \
  --symbols-file data/raw/symbols.csv \
  --membership-file data/raw/memberships/index_memberships.csv

# 3) Live stock prices for every member of every index --------------------
"$NMI" backfill-prices --index "$INDICES" \
  --provider nse --start "$START" --end "$END"

# 4) Benchmark index closes -----------------------------------------------
"$NMI" backfill-index-prices --index "$INDICES" \
  --provider nse --start "$START" --end "$END"

# 5) Full daily chain ------------------------------------------------------
"$NMI" compute-eod --index "$INDICES" \
  --start "$START" --end "$END"

# 6) Serve the API (Ctrl+C to stop) ---------------------------------------
"$NMI" serve --host 127.0.0.1 --port 8000
