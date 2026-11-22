PYTHON ?= .venv/bin/python

.PHONY: check install test

install:
	uv sync --locked --extra dev --extra ui

check:
	$(PYTHON) -m pytest -q
	$(PYTHON) -m ruff check stack tests

test:
	$(PYTHON) -m pytest -q