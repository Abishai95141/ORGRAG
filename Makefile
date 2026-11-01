.PHONY: setup up down logs dev-db test-db migrate seed rechunk test lint api worker web

DEV_DB_URL ?= postgresql+asyncpg://postgres:postgres@localhost:5432/strata
BACKEND := cd backend &&
UV_RUN := uv run --extra local-embeddings

setup:         ## Install backend + web dependencies for host development
	$(BACKEND) uv sync --extra local-embeddings
	cd web && pnpm install


up:            ## Build and start everything (db, api, worker, web)
	docker compose up -d --build
	@echo "Strata is starting at http://localhost:3000 — run 'make seed' for demo data"

down:          ## Stop all services
	docker compose down

logs:          ## Tail service logs
	docker compose logs -f api worker web

dev-db:        ## Start only Postgres (for running api/worker/web on the host)
	docker compose up -d db

test-db:       ## Create the strata_test database in the compose Postgres
	docker compose exec db psql -U postgres -c "CREATE DATABASE strata_test" || true

migrate:       ## Apply database migrations (host)
	$(BACKEND) DATABASE_URL=$(DEV_DB_URL) $(UV_RUN) alembic upgrade head

seed:          ## Load demo orgs, documents and users
	@if docker compose ps api --status running -q 2>/dev/null | grep -q .; then \
		docker compose exec api python -m app.seed; \
	else \
		$(BACKEND) DATABASE_URL=$(DEV_DB_URL) $(UV_RUN) python -m app.seed; \
	fi

rechunk:       ## Report (or with APPLY=1, rebuild) documents with over-long or one-row chunks
	$(BACKEND) DATABASE_URL=$(DEV_DB_URL) $(UV_RUN) python -m app.ingestion.rechunk $(if $(APPLY),--apply,)

test:          ## Run the backend test suite (integration tests need TEST_DATABASE_URL)
	$(BACKEND) uv run pytest -q

lint:          ## Lint and format-check the backend
	$(BACKEND) uv run ruff check app tests alembic && uv run ruff format --check app tests alembic

api:           ## Run the API on the host with reload
	$(BACKEND) DATABASE_URL=$(DEV_DB_URL) $(UV_RUN) uvicorn app.main:app --reload --port 8000

worker:        ## Run the ingestion worker on the host
	$(BACKEND) DATABASE_URL=$(DEV_DB_URL) $(UV_RUN) python -m app.worker

web:           ## Run the web app on the host
	cd web && pnpm dev
