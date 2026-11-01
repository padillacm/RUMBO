# Makefile del framework RUMBO (dogfooding: el mismo `make check` que generamos).
.PHONY: check lint types test fmt e2e

lint:  ; uv run ruff check . && uv run ruff format --check .
fmt:   ; uv run ruff check --fix . && uv run ruff format .
types: ; uv run pyright
test:  ; uv run pytest
e2e:   ; scripts/e2e.sh "--llm --proveedor anthropic --rigor mvp" && scripts/e2e.sh "--no-llm --rigor prototipo" && scripts/e2e.sh "--llm --proveedor openai --rigor produccion"

check: lint types test
