# Every target is a thin wrapper over docker compose; README lists the same
# commands for machines without make.

COMPOSE ?= docker compose
BACKEND := $(COMPOSE) exec backend

.PHONY: help env up down logs build migrate makemigration seed test test-bot lint fmt frontend-build shell psql

help:
	@echo "up | down | logs | build | migrate | makemigration m=... | seed | test | test-bot | lint | fmt | frontend-build | shell | psql"

env:
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")

up: env
	$(COMPOSE) up -d --build
	@echo "Open http://localhost:8080"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

build: env
	$(COMPOSE) build

migrate:
	$(BACKEND) alembic upgrade head

makemigration:
	$(BACKEND) alembic revision --autogenerate -m "$(m)"

seed:
	$(BACKEND) python -m scripts.seed

test:
	$(BACKEND) pytest

test-bot:
	$(COMPOSE) exec bot pytest

lint:
	$(BACKEND) ruff check .
	$(BACKEND) ruff format --check .
	$(COMPOSE) exec bot ruff check .
	$(COMPOSE) exec bot ruff format --check .
	$(COMPOSE) exec frontend npm run lint

fmt:
	$(BACKEND) ruff check --fix .
	$(BACKEND) ruff format .
	$(COMPOSE) exec bot ruff check --fix .
	$(COMPOSE) exec bot ruff format .

frontend-build:
	$(COMPOSE) exec frontend npm run build

shell:
	$(BACKEND) bash

psql:
	$(COMPOSE) exec postgres sh -c 'psql -U $$POSTGRES_USER -d $$POSTGRES_DB'
