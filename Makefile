SHELL := /bin/bash
.DEFAULT_GOAL := help
PY := uv run
API := $(PY) uvicorn api.main:app --reload --port 8000
WEB := npm --prefix apps/web run dev

.PHONY: help setup dev api worker web test cov lint type fmt check seed demo tearsheet migrate deploy docker up down clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python + web dependencies
	uv venv --python 3.11
	uv pip install -e ".[dev]"
	npm --prefix apps/web install
	cp -n .env.example .env || true

dev: ## Run API and web together
	@echo "API  → http://localhost:8000/docs"
	@echo "Web  → http://localhost:3000"
	@trap 'kill 0' INT TERM; ($(API) & $(WEB) & wait)

api: ## API only
	$(API)

worker: ## Arq worker (sprint queue + nightly scheduler)
	$(PY) arq api.jobs.WorkerSettings

web: ## Web only
	$(WEB)

test: ## Full test suite (excludes network tests)
	$(PY) pytest --cov --cov-report=term-missing

cov: ## Enforce >80% coverage on engine/validation
	$(PY) pytest --cov=engine --cov-report=term-missing --cov-fail-under=80

noise: ## Anti-overfitting noise gate used in CI
	$(PY) pytest tests/test_noise_gate.py -q

lint: ## Ruff
	$(PY) ruff check .

type: ## Mypy
	$(PY) mypy engine data agents sandbox apps/api/api

check: lint type test ## All checks

seed: ## Create schema and seed a demo org, ideas, strategies, experiments
	$(PY) python scripts/seed.py

demo: ## Autonomous sprint on SPY/QQQ + sector ETFs, offline-capable
	$(PY) python scripts/demo_sprint.py

tearsheet: ## Render HTML tearsheets for promoted strategies into artifacts/
	$(PY) python scripts/make_tearsheet.py

migrate: ## Apply SQL schema (idempotent; Alembic optional later)
	$(PY) python -c "from api.db import init_db; init_db()"

docker: ## Build local images
	docker compose build

up: ## Start full stack (postgres, redis, api, worker, web)
	docker compose up --build -d

down: ## Stop stack
	docker compose down

deploy: ## Print the deployment checklist (no implicit cloud actions)
	@cat infra/DEPLOY.md

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
