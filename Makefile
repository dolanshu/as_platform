# Makefile — the as-platform library's own gate (REQ-NF-021).
#
# A library, not an application: there are no dev / mock / console / demo / docker
# targets. Everything runs through uv so the locked environment is used.

SHELL := /bin/bash
UV ?= uv
RUN := $(UV) run
ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help sync lint format type test check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

sync: ## Install or sync the locked environment
	$(UV) sync

lint: sync ## ruff format --check + ruff check + mypy
	$(RUN) ruff format --check .
	$(RUN) ruff check .
	$(RUN) mypy

format: sync ## Apply ruff formatting
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

type: sync ## Type check only
	$(RUN) mypy

test: sync ## Run the library's own test suite
	$(RUN) pytest

check: lint test ## The full gate: lint + test

clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
