.PHONY: help up down logs ps test fmt obs-up obs-down clean

help:
	@echo "Targets:"
	@echo "  make up        - start all Phase 1 services"
	@echo "  make down      - stop services (keeps data volumes)"
	@echo "  make logs      - tail app logs"
	@echo "  make ps        - show running containers"
	@echo "  make test      - run pytest inside the app container"
	@echo "  make fmt       - ruff format + lint"
	@echo "  make obs-up    - start Prometheus + Grafana too"
	@echo "  make obs-down  - stop observability services"
	@echo "  make clean     - stop services AND wipe data volumes (destructive)"

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f app

ps:
	docker compose ps

test:
	docker compose exec app pytest -v
	
ingest:
	@if [ -z "$(FILE)" ]; then echo "usage: make ingest FILE=path/to/document.pdf [META='industry=mining doc_type=case_study']"; exit 2; fi
	@docker compose exec app python -m app.ingestion.cli $(FILE) $(addprefix --meta ,$(META))
fmt:
	ruff format app tests
	ruff check --fix app tests

obs-up:
	docker compose --profile observability up -d

obs-down:
	docker compose --profile observability down

clean:
	docker compose down -v