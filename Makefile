.PHONY: help install dev dev-hub dev-web build-web test demo-swarm docker-up docker-prod

HUB_URL ?= http://127.0.0.1:8000

help:
	@echo "AI Watchdogs India (aiwatchdogs.in) — Developer Targets"
	@echo ""
	@echo "  make install      Install Python deps (uv sync) and web deps (npm install)"
	@echo "  make dev          Run Coordinator Hub (:8000) and Vite Dashboard (:5173) together"
	@echo "  make dev-hub      Run FastAPI Coordinator Hub on http://127.0.0.1:8000"
	@echo "  make dev-web      Run Vite React dev server on http://127.0.0.1:5173"
	@echo "  make build-web    Build production frontend bundle into web/dist"
	@echo "  make test         Run full Python pytest test suite via uv"
	@echo "  make demo-swarm   Run a local multi-node zero-cost CPU swarm against HUB_URL"
	@echo "  make docker-up    Build and start local Docker container on port 8000"
	@echo "  make docker-prod  Build and start Hub + Caddy automatic HTTPS (aiwatchdogs.in)"

install:
	uv sync
	cd web && npm install

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

demo-swarm:
	@echo "Running local zero-cost CPU contributor swarm demo against $(HUB_URL)..."
	@echo "(Make sure the Hub is running first via 'make dev-hub' or 'make dev')"
	@TMP_DIR=$$(mktemp -d); \
		trap 'rm -rf "$$TMP_DIR"' EXIT; \
		echo "--> [Node 1/3] auditor-alpha: claiming anomaly_scan tasks..."; \
		AIWATCHDOGS_CONFIG_PATH="$$TMP_DIR/auditor-alpha.json" \
			uv run aiwatchdogs run --hub "$(HUB_URL)" --handle auditor-alpha --provider rule-engine --mode investigate --max-tasks 2; \
		echo "--> [Node 2/3] verifier-beta: independent peer verification pass 1..."; \
		AIWATCHDOGS_CONFIG_PATH="$$TMP_DIR/verifier-beta.json" \
			uv run aiwatchdogs run --hub "$(HUB_URL)" --handle verifier-beta --provider rule-engine --mode verify --max-tasks 2; \
		echo "--> [Node 3/3] verifier-gamma: independent peer verification pass 2 (quorum promotion)..."; \
		AIWATCHDOGS_CONFIG_PATH="$$TMP_DIR/verifier-gamma.json" \
			uv run aiwatchdogs run --hub "$(HUB_URL)" --handle verifier-gamma --provider rule-engine --mode verify --max-tasks 2

docker-up:
	docker compose up --build

docker-prod:
	docker compose --profile tls up --build -d
