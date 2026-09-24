.PHONY: install dev test lint db-up alembic-init seeds backfill

install:
	python3.11 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check src tests

lint-fix:
	.venv/bin/ruff check --fix src tests

db-up:
	docker compose up -d db

db-down:
	docker compose down

seed:
	.venv/bin/nmi seed-universe --membership-file data/raw/memberships/index_memberships.csv

backfill:
	.venv/bin/nmi backfill-prices --index NIFTY_50 --start 2024-01-01 --end 2025-06-30