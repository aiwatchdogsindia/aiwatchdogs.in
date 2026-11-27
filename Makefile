.PHONY: help install dev dev-hub dev-web build-web test run-swarm docker-up docker-prod

HUB_URL ?= http://127.0.0.1:8000

help:
	@echo "AI Watchdogs India (aiwatchdogs.in) — Developer Targets"
	@echo ""
	@echo "  make install      Install Python deps (uv sync --locked) and web deps (npm ci)"
	@echo "  make dev          Run Coordinator Hub (:8000) and Vite Dashboard (:5173) together"
	@echo "  make dev-hub      Run FastAPI Coordinator Hub on http://127.0.0.1:8000 (SQLite or DATABASE_URL Postgres)"
	@echo "  make dev-web      Run Vite React dev server on http://127.0.0.1:5173"
	@echo "  make build-web    Build production frontend bundle into web/dist"
	@echo "  make test         Run full Python pytest test suite via uv"
	@echo "  make run-swarm    Run a live 4-node zero-cost CPU swarm (1 auditor + 3 verifiers) against HUB_URL"
	@echo "  make docker-up    Build and start local Docker container on port 8000"
	@echo "  make docker-prod  Build and start Hub + Caddy automatic HTTPS (aiwatchdogs.in)"

install:
	uv sync --locked
	cd web && npm ci

dev-hub:
	uv run aiwatchdogs-hub

dev-web:
	cd web && npm run dev

dev:
	@echo "Starting Hub (http://127.0.0.1:8000) and Vite Dashboard (http://127.0.0.1:5173)..."
	@trap 'kill 0' INT TERM EXIT; \
		uv run aiwatchdogs-hub & \
		(cd web && npm run dev) & \
		wait

build-web:
	cd web && npm run build

test:
	uv run pytest -v

run-swarm:
	@echo "Running live 4-node zero-cost CPU contributor swarm against $(HUB_URL)..."
	@echo "(Audits real queued Government of India datasets and promotes leads via 3-of-3 peer quorum)"
	@TMP_DIR=$$(mktemp -d); \
		trap 'rm -rf "$$TMP_DIR"' EXIT; \
		echo "--> [Node 1/4] auditor-alpha: claiming real Government of India anomaly_scan tasks..."; \
		AIWATCHDOGS_CONFIG_PATH="$$TMP_DIR/auditor-alpha.json" \
			uv run aiwatchdogs run --hub "$(HUB_URL)" --handle auditor-alpha --provider rule-engine --mode investigate --max-tasks 2; \
		echo "--> [Node 2/4] verifier-beta: independent peer verification pass 1..."; \
		AIWATCHDOGS_CONFIG_PATH="$$TMP_DIR/verifier-beta.json" \
			uv run aiwatchdogs run --hub "$(HUB_URL)" --handle verifier-beta --provider rule-engine --mode verify --max-tasks 2; \
		echo "--> [Node 3/4] verifier-gamma: independent peer verification pass 2..."; \
		AIWATCHDOGS_CONFIG_PATH="$$TMP_DIR/verifier-gamma.json" \
			uv run aiwatchdogs run --hub "$(HUB_URL)" --handle verifier-gamma --provider rule-engine --mode verify --max-tasks 2; \
		echo "--> [Node 4/4] verifier-delta: independent peer verification pass 3 (quorum promotion)..."; \
		AIWATCHDOGS_CONFIG_PATH="$$TMP_DIR/verifier-delta.json" \
			uv run aiwatchdogs run --hub "$(HUB_URL)" --handle verifier-delta --provider rule-engine --mode verify --max-tasks 2

docker-up:
	docker compose up --build

docker-prod:
	docker compose --profile tls up --build -d
