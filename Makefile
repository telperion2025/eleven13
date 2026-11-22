# eleven13 — flat Makefile target set (spec §18.1; no justfile).
# All targets run through uv so the pinned toolchain from uv.lock is used.

.PHONY: sync test lint type check clean

sync:            ## create/refresh the uv venv and install dev extras
	uv sync --extra dev

test: sync       ## run the pytest suite
	uv run pytest

lint: sync       ## ruff check — zero findings required (spec §18.2)
	uv run ruff check

type: sync       ## mypy --strict on the modules the spec gates (spec §18.2)
	uv run mypy --strict pdp11/config pdp11/cpu pdp11/bus pdp11/memory \
	    pdp11/system_clock.py pdp11/board.py pdp11/main.py

check: test lint type   ## all quality gates (spec §18.2)

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
