PYTHON ?= .venv/bin/python

.PHONY: check install test

install:
	uv pip install -e ".[dev]"

check:
	$(PYTHON) -m pytest -q
	$(PYTHON) -m ruff check stack tests

test:
	$(PYTHON) -m pytest -q