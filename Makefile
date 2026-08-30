# Common tasks. Everything here works without Docker except the compose targets.

BACKEND := backend
FRONTEND := frontend
VENV := $(BACKEND)/.venv
PY := $(VENV)/bin/python
# Windows virtualenvs put the interpreter somewhere else.
ifeq ($(OS),Windows_NT)
	PY := $(VENV)/Scripts/python.exe
endif

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup
.PHONY: setup
setup: setup-backend setup-frontend  ## Install everything for local development

.PHONY: setup-backend
setup-backend:  ## Create the virtualenv and install Python dependencies
	python -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r $(BACKEND)/requirements-dev.txt
	@echo "Copy .env.example to .env and adjust it if you have not already."

.PHONY: setup-frontend
setup-frontend:  ## Install frontend dependencies
	cd $(FRONTEND) && npm install

# ---------------------------------------------------------------- running
.PHONY: dev
dev:  ## Run the API with reload (needs .env)
	cd $(BACKEND) && .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000 \
		|| cd $(BACKEND) && .venv/bin/python -m uvicorn app.main:app --reload --port 8000

.PHONY: worker
worker:  ## Run the background worker (needs Redis)
	cd $(BACKEND) && $(PY) -m arq app.worker.WorkerSettings

.PHONY: ui
ui:  ## Run the frontend dev server
	cd $(FRONTEND) && npm run dev

# ---------------------------------------------------------------- database
.PHONY: migrate
migrate:  ## Apply database migrations
	cd $(BACKEND) && $(PY) -m alembic upgrade head

.PHONY: migration
migration:  ## Create a migration: make migration m="what changed"
	cd $(BACKEND) && $(PY) -m alembic revision --autogenerate -m "$(m)"

# ---------------------------------------------------------------- quality
.PHONY: test
test:  ## Run unit and integration tests
	cd $(BACKEND) && $(PY) -m pytest tests/unit tests/integration -q

.PHONY: test-all
test-all:  ## Run every test including the evaluation suite
	cd $(BACKEND) && $(PY) -m pytest -q

.PHONY: eval
eval:  ## Run the evaluation suite against the stub model
	cd $(BACKEND) && $(PY) -m pytest tests/evaluation -q -s

.PHONY: check-endpoint
check-endpoint:  ## Verify a real model endpoint before using it
	cd $(BACKEND) && $(PY) scripts/check_endpoint.py

.PHONY: eval-live
eval-live:  ## Run the evaluation cases against the real endpoint (resumable)
	cd $(BACKEND) && $(PY) scripts/run_evaluation.py --live --resume 		--out var/evaluation-live.json

.PHONY: hard-fixtures
hard-fixtures:  ## Render the degraded fixtures to var/hard-fixtures for eyeballing
	cd $(BACKEND) && $(PY) scripts/run_extraction_eval.py --render-only var/hard-fixtures

.PHONY: eval-extraction
eval-extraction:  ## Check the degradation harness end to end against the stub
	cd $(BACKEND) && $(PY) scripts/run_extraction_eval.py --core --limit 3

.PHONY: eval-extraction-live
eval-extraction-live:  ## Measure where extraction degrades, against the real endpoint (resumable)
	cd $(BACKEND) && $(PY) -u scripts/run_extraction_eval.py --live --core --resume

.PHONY: typecheck
typecheck:  ## Typecheck the frontend
	cd $(FRONTEND) && npm run typecheck

.PHONY: build-ui
build-ui:  ## Production build of the frontend
	cd $(FRONTEND) && npm run build

# ---------------------------------------------------------------- docker
.PHONY: up
up:  ## Start the stack (no GPU needed)
	docker compose up --build

.PHONY: up-gpu
up-gpu:  ## Start the stack including the local model server
	docker compose --profile gpu up --build

.PHONY: down
down:  ## Stop the stack
	docker compose down

.PHONY: clean
clean:  ## Remove local artefacts (keeps Docker volumes)
	rm -rf $(BACKEND)/ldai.db $(BACKEND)/var $(BACKEND)/.pytest_cache
	find $(BACKEND) -name __pycache__ -type d -prune -exec rm -rf {} +
