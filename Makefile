# One entry point for every routine command. Run `make help`.
# Python tooling always comes from the project venv; nothing is installed globally.

.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON ?= python3.14
VENV := $(CURDIR)/CredentialingVenv
BIN := $(VENV)/bin

# Makes uv manage CredentialingVenv instead of creating backend/.venv.
export UV_PROJECT_ENVIRONMENT := $(VENV)

# Frontend API types, generated from the backend's OpenAPI schema.
API_TYPES := src/lib/api-schema.d.ts
API_TYPES_TMP := $(CURDIR)/frontend/node_modules/.tmp

# Pinned Supabase CLI, kept in .tools/ (downloaded on first use).
SUPABASE := ./scripts/supabase

.PHONY: help setup setup-backend setup-frontend api web fmt lint typecheck test test-integration test-live test-web check build backend-add sync sources api-types api-types-check
.PHONY: db-start db-stop db-status db-reset db-migrate db-new

help: ## List available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-16s %s\n", $$1, $$2}'

setup: setup-backend setup-frontend ## First-time setup: venv, dependencies, .env, git hooks
	@test -f .env || cp .env.example .env
	@git config core.hooksPath .githooks
	@echo "Ready. Start the API with 'make api' and the UI with 'make web'."

setup-backend: ## Create CredentialingVenv if missing and install locked backend dependencies
	@test -x $(BIN)/python || $(PYTHON) -m venv $(VENV)
	@test -x $(BIN)/uv || $(BIN)/python -m pip install --quiet uv
	cd backend && $(BIN)/uv sync --locked

setup-frontend: ## Install locked frontend dependencies
	cd frontend && pnpm install --frozen-lockfile

api: ## Run the API on http://127.0.0.1:8000 with reload (needs `make db-start`)
	$(BIN)/uvicorn credentialing.main:app --reload --reload-dir backend/src --port 8000

web: ## Run the UI on http://localhost:5173 (proxies /api to the API)
	cd frontend && pnpm dev

db-start: ## Start the local database in Docker (Postgres only, on port 54322)
	$(SUPABASE) db start

db-stop: ## Stop the local database; its data is kept
	$(SUPABASE) stop

db-status: ## Show whether the local database is running and its connection URL
	$(SUPABASE) status

db-reset: ## Rebuild the local database from migrations and seed.sql (erases local data)
	$(SUPABASE) db reset

db-migrate: ## Apply migrations not yet applied to the local database, keeping its data
	$(SUPABASE) migration up --local

db-new: ## Create an empty migration file: make db-new NAME=create_providers
	@test -n "$(NAME)" || (echo "Usage: make db-new NAME=short_description" && exit 1)
	$(SUPABASE) migration new $(NAME)

fmt: ## Auto-format and auto-fix both codebases
	cd backend && $(BIN)/ruff format . && $(BIN)/ruff check --fix .
	cd frontend && pnpm format

lint: api-types-check ## Formatting, lint rules, architecture contracts and API types
	cd backend && $(BIN)/ruff format --check . && $(BIN)/ruff check . && $(BIN)/lint-imports
	cd frontend && pnpm format:check && pnpm lint

api-types: ## Regenerate frontend/src/lib/api-schema.d.ts from the backend OpenAPI schema
	@mkdir -p $(API_TYPES_TMP)
	$(BIN)/python -m credentialing.cli openapi > $(API_TYPES_TMP)/openapi.json
	cd frontend && pnpm exec openapi-typescript $(API_TYPES_TMP)/openapi.json -o $(API_TYPES) && pnpm exec prettier --write $(API_TYPES) > /dev/null

api-types-check: ## Fail when frontend/src/lib/api-schema.d.ts is out of date with the backend
	@mkdir -p $(API_TYPES_TMP)
	@$(BIN)/python -m credentialing.cli openapi > $(API_TYPES_TMP)/openapi.json
	@cd frontend && pnpm exec openapi-typescript $(API_TYPES_TMP)/openapi.json -o $(API_TYPES_TMP)/api.d.ts > /dev/null \
		&& pnpm exec prettier --stdin-filepath $(API_TYPES) < $(API_TYPES_TMP)/api.d.ts > $(API_TYPES_TMP)/api.formatted.d.ts
	@diff -q frontend/$(API_TYPES) $(API_TYPES_TMP)/api.formatted.d.ts > /dev/null \
		|| (echo "frontend/$(API_TYPES) is out of date with the backend API. Run 'make api-types'." && exit 1)

typecheck: ## Static types: mypy (strict) and tsc
	cd backend && $(BIN)/mypy
	cd frontend && pnpm typecheck

test: ## Fast offline backend tests
	cd backend && $(BIN)/pytest -m "not live and not integration"

test-integration: ## Backend tests against the local database (needs `make db-start`)
	cd backend && $(BIN)/pytest -m integration

test-web: ## Frontend unit tests (vitest)
	cd frontend && pnpm test

test-live: ## Smoke tests against the real public sources (network; never part of check)
	cd backend && $(BIN)/pytest -m live

build: ## Production build of the UI
	cd frontend && pnpm build

check: lint typecheck test test-integration test-web build ## Everything that must pass before work is called done (needs `make db-start`)

sync: ## Fetch a source: make sync SOURCE=oig_leie (or SOURCE=all; FORCE=1 re-parses)
	@test -n "$(SOURCE)" || (echo "Usage: make sync SOURCE=oig_leie (or SOURCE=all)" && exit 1)
	$(BIN)/python -m credentialing.cli sync $(if $(filter all,$(SOURCE)),--all,$(SOURCE)) $(if $(FORCE),--force)

sources: ## Source monitoring dashboard (make sources ARGS="--json")
	@$(BIN)/python -m credentialing.cli sources $(ARGS)

backend-add: ## Add a backend dependency: make backend-add PKG=name [DEV=1]
	@test -n "$(PKG)" || (echo "Usage: make backend-add PKG=name [DEV=1]" && exit 1)
	cd backend && $(BIN)/uv add $(if $(DEV),--dev) $(PKG)
