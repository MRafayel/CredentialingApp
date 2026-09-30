# One entry point for every routine command. Run `make help`.
# Python tooling always comes from the project venv; nothing is installed globally.

.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON ?= python3.14
VENV := $(CURDIR)/CredentialingVenv
BIN := $(VENV)/bin

# Makes uv manage CredentialingVenv instead of creating backend/.venv.
export UV_PROJECT_ENVIRONMENT := $(VENV)

.PHONY: help setup setup-backend setup-frontend api web fmt lint typecheck test check build backend-add

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

api: ## Run the API on http://127.0.0.1:8000 with reload
	$(BIN)/uvicorn credentialing.main:app --reload --reload-dir backend/src --port 8000

web: ## Run the UI on http://localhost:5173 (proxies /api to the API)
	cd frontend && pnpm dev

fmt: ## Auto-format and auto-fix both codebases
	cd backend && $(BIN)/ruff format . && $(BIN)/ruff check --fix .
	cd frontend && pnpm format

lint: ## Formatting, lint rules and architecture contracts
	cd backend && $(BIN)/ruff format --check . && $(BIN)/ruff check . && $(BIN)/lint-imports
	cd frontend && pnpm format:check && pnpm lint

typecheck: ## Static types: mypy (strict) and tsc
	cd backend && $(BIN)/mypy
	cd frontend && pnpm typecheck

test: ## Backend tests (live third-party tests are excluded)
	cd backend && $(BIN)/pytest

build: ## Production build of the UI
	cd frontend && pnpm build

check: lint typecheck test build ## Everything that must pass before work is called done

backend-add: ## Add a backend dependency: make backend-add PKG=name [DEV=1]
	@test -n "$(PKG)" || (echo "Usage: make backend-add PKG=name [DEV=1]" && exit 1)
	cd backend && $(BIN)/uv add $(if $(DEV),--dev) $(PKG)
