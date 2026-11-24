PYTHON ?= .venv/bin/python

.PHONY: check install test

install:
	uv sync --locked --extra dev --extra ui

check:
	$(PYTHON) -m ruff check stack tests
	$(PYTHON) -m mypy stack tests
	$(PYTHON) -m pytest -q

test:
	$(PYTHON) -m pytest -q