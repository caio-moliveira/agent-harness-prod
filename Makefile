install:
	pip install uv
	uv sync

DOCKER_COMPOSE ?= docker-compose

set-env:
	@if [ -z "$(ENV)" ]; then \
		echo "ENV is not set. Usage: make set-env ENV=development|staging|production"; \
		exit 1; \
	fi
	@if [ "$(ENV)" != "development" ] && [ "$(ENV)" != "staging" ] && [ "$(ENV)" != "production" ] && [ "$(ENV)" != "test" ]; then \
		echo "ENV is not valid. Must be one of: development, staging, production, test"; \
		exit 1; \
	fi
	@echo "Setting environment to $(ENV)"
	@bash -c "source scripts/set_env.sh $(ENV)"

prod:
	@echo "Starting server in production environment"
	@bash -c "source scripts/set_env.sh production && ./.venv/bin/python -m uvicorn src.app.main:app --host 0.0.0.0 --port 8000 --loop uvloop"

staging:
	@echo "Starting server in staging environment"
	@bash -c "source scripts/set_env.sh staging && ./.venv/bin/python -m uvicorn src.app.main:app --host 0.0.0.0 --port 8000 --loop uvloop"

dev:
	@echo "Starting server in development environment"
	@bash -c "source scripts/set_env.sh development && uv run uvicorn src.app.main:app --reload --port 8000 --loop uvloop"

# Evaluation commands
eval:
	@echo "Running evaluation with interactive mode"
	@bash -c "source scripts/set_env.sh ${ENV:-development} && python -m src.evals.main --interactive"

eval-quick:
	@echo "Running evaluation with default settings"
	@bash -c "source scripts/set_env.sh ${ENV:-development} && python -m src.evals.main --quick"

eval-no-report:
	@echo "Running evaluation without generating report"
	@bash -c "source scripts/set_env.sh ${ENV:-development} && python -m src.evals.main --no-report"

lint:
	ruff check .

format:
	ruff format .

clean:
	rm -rf .venv
	rm -rf __pycache__
	rm -rf .pytest_cache

docker-build:
	docker build -t fastapi-langgraph-template .

docker-build-env:
	@if [ -z "$(ENV)" ]; then \
		echo "ENV is not set. Usage: make docker-build-env ENV=development|staging|production"; \
		exit 1; \
	fi
	@if [ "$(ENV)" != "development" ] && [ "$(ENV)" != "staging" ] && [ "$(ENV)" != "production" ]; then \
		echo "ENV is not valid. Must be one of: development, staging, production"; \
		exit 1; \
	fi
	@./scripts/build-docker.sh $(ENV)

docker-run:
	@ENV_FILE=.env.development; \
	if [ ! -f $$ENV_FILE ]; then \
		echo "Environment file $$ENV_FILE not found. Please create it."; \
		exit 1; \
	fi; \
	APP_ENV=development $(DOCKER_COMPOSE) --env-file $$ENV_FILE up -d --build db app

docker-run-env:
	@if [ -z "$(ENV)" ]; then \
		echo "ENV is not set. Usage: make docker-run-env ENV=development|staging|production"; \
		exit 1; \
	fi
	@if [ "$(ENV)" != "development" ] && [ "$(ENV)" != "staging" ] && [ "$(ENV)" != "production" ]; then \
		echo "ENV is not valid. Must be one of: development, staging, production"; \
		exit 1; \
	fi
	@ENV_FILE=.env.$(ENV); \
	if [ ! -f $$ENV_FILE ]; then \
		echo "Environment file $$ENV_FILE not found. Please create it."; \
		exit 1; \
	fi; \
	APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file $$ENV_FILE up -d --build db app
	# @./scripts/ensure-db-user.sh $(ENV)

docker-logs:
	@if [ -z "$(ENV)" ]; then \
		echo "ENV is not set. Usage: make docker-logs ENV=development|staging|production"; \
		exit 1; \
	fi
	@if [ "$(ENV)" != "development" ] && [ "$(ENV)" != "staging" ] && [ "$(ENV)" != "production" ]; then \
		echo "ENV is not valid. Must be one of: development, staging, production"; \
		exit 1; \
	fi
	@ENV_FILE=.env.$(ENV); \
	if [ ! -f $$ENV_FILE ]; then \
		echo "Environment file $$ENV_FILE not found. Please create it."; \
		exit 1; \
	fi; \
	APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file $$ENV_FILE logs -f app db

docker-stop:
	@if [ -z "$(ENV)" ]; then \
		echo "ENV is not set. Usage: make docker-stop ENV=development|staging|production"; \
		exit 1; \
	fi
	@if [ "$(ENV)" != "development" ] && [ "$(ENV)" != "staging" ] && [ "$(ENV)" != "production" ]; then \
		echo "ENV is not valid. Must be one of: development, staging, production"; \
		exit 1; \
	fi
	@ENV_FILE=.env.$(ENV); \
	if [ ! -f $$ENV_FILE ]; then \
		echo "Environment file $$ENV_FILE not found. Please create it."; \
		exit 1; \
	fi; \
	APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file $$ENV_FILE down

# Docker Compose commands for the entire stack
docker-compose-up:
	@if [ -z "$(ENV)" ]; then \
		echo "ENV is not set. Usage: make docker-compose-up ENV=development|staging|production"; \
		exit 1; \
	fi
	@if [ "$(ENV)" != "development" ] && [ "$(ENV)" != "staging" ] && [ "$(ENV)" != "production" ]; then \
		echo "ENV is not valid. Must be one of: development, staging, production"; \
		exit 1; \
	fi
	@ENV_FILE=.env.$(ENV); \
	if [ ! -f $$ENV_FILE ]; then \
		echo "Environment file $$ENV_FILE not found. Please create it."; \
		exit 1; \
	fi; \
	APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file $$ENV_FILE up -d

docker-compose-down:
	@if [ -z "$(ENV)" ]; then \
		echo "ENV is not set. Usage: make docker-compose-down ENV=development|staging|production"; \
		exit 1; \
	fi
	@ENV_FILE=.env.$(ENV); \
	if [ ! -f $$ENV_FILE ]; then \
		echo "Environment file $$ENV_FILE not found. Please create it."; \
		exit 1; \
	fi; \
	APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file $$ENV_FILE down

docker-compose-logs:
	@if [ -z "$(ENV)" ]; then \
		echo "ENV is not set. Usage: make docker-compose-logs ENV=development|staging|production"; \
		exit 1; \
	fi
	@ENV_FILE=.env.$(ENV); \
	if [ ! -f $$ENV_FILE ]; then \
		echo "Environment file $$ENV_FILE not found. Please create it."; \
		exit 1; \
	fi; \
	APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file $$ENV_FILE logs -f

# Postgres (pgvector) — local database only
db-up:
	@echo "Starting Postgres (pgvector)..."
	$(DOCKER_COMPOSE) up -d db
	@echo "Waiting for database to be healthy..."
	@for i in $$(seq 1 30); do \
		status=$$(docker inspect --format='{{.State.Health.Status}}' agent-harness-db 2>/dev/null || echo starting); \
		if [ "$$status" = "healthy" ]; then echo "Database is healthy."; exit 0; fi; \
		sleep 2; \
	done; \
	echo "Database did not become healthy in time. Check: make db-logs"; exit 1

db-down:
	$(DOCKER_COMPOSE) stop db

db-logs:
	$(DOCKER_COMPOSE) logs -f db

db-reset:
	@echo "WARNING: destroying the Postgres data volume."
	$(DOCKER_COMPOSE) rm -sf db
	docker volume rm agent-harness-prod_postgres-data 2>/dev/null || true
	$(MAKE) db-up

# Database migrations (Alembic). Applies to the Postgres in .env.<ENV>.
migrate:
	uv run alembic upgrade head

migrate-down:
	uv run alembic downgrade -1

# Autogenerate a migration from model changes, then REVIEW it before committing.
# Usage: make migration m="add X to Y"
migration:
	uv run alembic revision --autogenerate -m "$(m)"

# Mark an already-create_all'd (fresh) database as being at the latest revision, without running
# the migrations — use on a brand-new DB the app just bootstrapped, not on an existing one.
migrate-stamp:
	uv run alembic stamp head

# Help
help:
	@echo "Usage: make <target>"
	@echo "Targets:"
	@echo "  install: Install dependencies"
	@echo "  set-env ENV=<environment>: Set environment variables (development, staging, production, test)"
	@echo "  run ENV=<environment>: Set environment and run server"
	@echo "  prod: Run server in production environment"
	@echo "  staging: Run server in staging environment"
	@echo "  dev: Run server in development environment"
	@echo "  eval: Run evaluation with interactive mode"
	@echo "  eval-quick: Run evaluation with default settings"
	@echo "  eval-no-report: Run evaluation without generating report"
	@echo "  db-up: Start local Postgres (pgvector) and wait until healthy"
	@echo "  db-down: Stop local Postgres (keeps data)"
	@echo "  db-logs: Follow Postgres logs"
	@echo "  db-reset: Destroy the Postgres volume and recreate it"
	@echo "  test: Run tests"
	@echo "  clean: Clean up"
	@echo "  docker-build: Build default Docker image"
	@echo "  docker-build-env ENV=<environment>: Build Docker image for specific environment"
	@echo "  docker-run: Run default Docker container"
	@echo "  docker-run-env ENV=<environment>: Run Docker container for specific environment"
	@echo "  docker-logs ENV=<environment>: View logs from running container"
	@echo "  docker-stop ENV=<environment>: Stop and remove container"
	@echo "  docker-compose-up: Start the entire stack (API, Prometheus, Grafana)"
	@echo "  docker-compose-down: Stop the entire stack"
	@echo "  docker-compose-logs: View logs from all services"