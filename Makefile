.PHONY: install dev backend frontend test build run stats events export user

install:
	cd backend && uv sync
	cd frontend && npm install

# Backend on :8000 with reload, Vite on :5173 (proxies /api and /feedback to the backend).
dev:
	@trap 'kill 0' INT TERM EXIT; \
	$(MAKE) backend & \
	$(MAKE) frontend & \
	wait

backend:
	cd backend && DATA_DIR=../data PUBLIC_URL=http://localhost:5173 uv run uvicorn app.main:create_app --factory --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest -q
	cd frontend && npm run typecheck

build:
	docker compose build

run:
	docker compose up

# Inspect captured data (works while the server is running). Admin page: /admin
stats:
	@cd backend && DATA_DIR=../data uv run python -m app.cli stats

events:
	@cd backend && DATA_DIR=../data uv run python -m app.cli events -n 30

export:
	@cd backend && DATA_DIR=../data uv run python -m app.cli export

# Accounts, e.g. make user ARGS="list", ARGS="move-signins <new> owner"
user:
	@cd backend && DATA_DIR=../data uv run python -m app.cli user $(ARGS)

# Private hosting targets (gitignored), if present.
-include deploy.mk
