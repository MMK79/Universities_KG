# Universities ML course database. See README.md.
SUBSET ?= pilot
export DATABASE_URL ?= postgresql://mlcourses:mlcourses@localhost:5434/mlcourses
FIRECRAWL_ENV ?= $(HOME)/Documents/Obsidian Vaults/Personal/.env

.PHONY: help download-materials export-site up down schema crawl extract load report test lint psql verify-isbns

up:            ## start Postgres (schema applied on first start)
	docker compose up -d --wait

down: ## stop Postgres
	docker compose down

schema:        ## (re)apply the schema — drops and recreates all tables
	uv run mlcourses schema

crawl:         ## discover + fetch + extract + load for $(SUBSET)
	set -a; . "$(FIRECRAWL_ENV)"; set +a; uv run mlcourses crawl --subset $(SUBSET)
	uv run mlcourses load --subset $(SUBSET)

extract:       ## re-run extraction from the cache only (no network)
	uv run mlcourses extract --subset $(SUBSET)

load: ## load extracted data for SUBSET into the database
	uv run mlcourses load --subset $(SUBSET)

report:        ## write docs/findings-data.md tables from the database
	uv run mlcourses report

verify-isbns:  ## check the canonical catalogue ISBNs against Open Library
	uv run mlcourses verify-isbns

download-materials:  ## phase 2: polite download of public PDF/PPTX slides + notes (resumable)
	uv run mlcourses download-materials --subset $(SUBSET)

export-site:   ## write JSON for the website to exports/site/
	uv run mlcourses export-site --subset $(SUBSET)

test: ## run pytest
	uv run pytest -q

lint: ## ruff check and format check
	uv run ruff check src tests
	uv run ruff format --check src tests

psql: ## open psql on the database
	docker compose exec db psql -U mlcourses mlcourses

help: ## list the commands
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:[^#]*## /\t/' | awk -F'\t' '{printf "  make %-20s %s\n", $$1, $$2}'
