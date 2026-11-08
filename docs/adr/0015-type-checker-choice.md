# ADR-0015: The type checker is a project setting: pyright by default, mypy as the alternative
Status: accepted    Date: 2026-11-08

## Context
The harness type-checks with pyright, which downloads a Node runtime on first run. In closed
corporate networks that download fails, and many Python teams already standardise on mypy.
`ty`, Astral's checker, is fast and Rust-native but still in preview; promising a stable harness
on it today would be copying a version from memory.

## Drivers (non-functional requirements)
Same strictness contract whichever checker: the inner layer strict, the rest checked; one
setting that changes the dependency, the `types` step and the configuration section together;
no new A0 question; templates that pass the chosen checker in the generated project.

## Options considered
1. pyright only, document mypy as a manual swap (status quo).
2. `--types pyright|mypy`, stored in `[tool.rumbo].types`, feeding dependency, step and config.
3. Option 2 plus `ty` now.

## Decision and rationale
Option 2; `ty` is revisited when it leaves preview (a line in the CHANGELOG's Unreleased
section keeps the reminder). The contract:

**Setting.** `rumbo init --types pyright|mypy` (default `pyright`), stored as
`[tool.rumbo].types`; `Project.load` defaults it and `rumbo update` writes the key. The context
exposes `types` and the flags `pyright` and `mypy`.

**What changes with `mypy`.** `dev_dependencies` lists `mypy` instead of `pyright`. The
`types` step is `uv run mypy` (configuration drives the paths). `pyproject.toml` renders
`[tool.mypy]` instead of `[tool.pyright]`: `files = ["src", "tests"]`, `python_version =
"{{python}}"`, `check_untyped_defs = true`, `warn_unused_ignores = true`,
`warn_redundant_casts = true`, `no_implicit_optional = true`, and when `arch` is on a
`[[tool.mypy.overrides]]` for `{{package}}.{{inner_layer}}.*` with `strict = true`. The
`extraPaths` entry for `evals/` becomes `mypy_path = "evals"` when `evals` is on. The
generated `AGENTS.md` names the checker in its command block only through the runner, so it
does not change.

**Templates must pass both.** Every Python template is checked by the e2e matrix under both
checkers; where mypy and pyright disagree on a construct, the template is written so that both
accept it without ignores.

## Consequences (+/−)
+ A team behind a closed network, or standardised on mypy, gets the same harness.
+ The strictness contract (inner layer strict) is expressed in both configurations.
− Two configurations to keep equivalent; the e2e matrix is the sensor.
− `ty` users wait; the setting is ready to take a third value.

## Fitness function
`tests/test_scaffold.py`: `--types mypy` renders `[tool.mypy]` with the overrides, no
`[tool.pyright]`, `mypy` in the dev dependencies and `uv run mypy` in the `types` step; the
default is unchanged; an unknown value is refused; `Project.load` defaults and `update` writes
the key. The e2e matrix gains `--llm --provider anthropic --rigor mvp --types mypy` and
`--kind cli --layout flat --no-llm --rigor prototype --types mypy`.

## Reversibility: high
A setting with today's default; removing it leaves pyright everywhere.
