.PHONY: install web-install config fetch runs serve web-dev build db test lint

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]"
	$(PIP) install -q datasette
	@echo "ok — run 'make config' to check your setup"

web-install:
	cd frontend && npm install

config:
	$(VENV)/bin/jobscout config

fetch:
	$(VENV)/bin/jobscout fetch

runs:
	$(VENV)/bin/jobscout runs

# build the React SPA into the Python package, then serve API + UI on :8000
build:
	cd frontend && npm run build

serve: build
	$(VENV)/bin/jobscout serve

# frontend dev server on :5173 with hot reload (run `make serve` in another shell for the API)
web-dev:
	cd frontend && npm run dev

# local web UI for the SQLite DB at http://127.0.0.1:8001
db:
	$(VENV)/bin/datasette jobscout.db --open

test:
	$(VENV)/bin/pytest -q

lint:
	$(VENV)/bin/ruff check src
