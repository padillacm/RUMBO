# Makefile of the RUMBO framework. `make check` is an alias of the runner the framework generates
# (scripts/rumbo_check.py, steps in [tool.rumbo.checks]); the other targets are conveniences.
.PHONY: check lint types test fmt e2e review

lint:   ; uv run ruff check . && uv run ruff format --check .
fmt:    ; uv run ruff check --fix . && uv run ruff format .
types:  ; uv run pyright
test:   ; uv run pytest
e2e:    ; scripts/e2e.sh "--llm --provider anthropic --rigor mvp" && scripts/e2e.sh "--no-llm --rigor prototype" && scripts/e2e.sh "--llm --provider openai --rigor production" && scripts/e2e.sh "--llm --provider claude-code --rigor mvp" && scripts/e2e.sh "--kind library --no-llm --rigor mvp" && scripts/e2e.sh "--kind cli --layout flat --no-llm --rigor production"
review: ; uv run rumbo tour $(or $(BASE),origin/main) --out docs/reviews/$$(date +%Y-%m-%d)-$$(git rev-parse --short HEAD).md

check:  ; uv run python scripts/rumbo_check.py
