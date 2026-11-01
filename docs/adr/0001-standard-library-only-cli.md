# ADR-0001: The `rumbo` CLI uses the standard library only
Status: accepted    Date: 2026-10-30

## Context
`rumbo` is installed with `uvx` into projects we do not control and copies files into them.
Every runtime dependency of the CLI would be resolved inside the user's environment, could
conflict with the project's own pins and would widen the supply-chain surface of a tool whose
job is to *reduce* it (`verify-dep`, pinned actions, SBOM).

## Drivers (non-functional requirements)
Zero install friction (`uvx --from git+... rumbo init` must work on a clean machine);
no version drift between the CLI and the project; auditable in one read.

## Options considered
1. Standard library only (argparse, tomllib, difflib, json, subprocess).
2. Click/Typer + Jinja2 + Rich: nicer output, three transitive dependency trees.
3. Pydantic for the configuration: validation for free, one heavy dependency.

## Decision and rationale
Option 1. The CLI is a few thousand lines of plain Python; the comfort of option 2 does not pay
for a dependency tree in every user's project. Templates use a small Mustache subset (ADR-0002)
instead of Jinja2.

## Consequences (+/−)
+ `uvx` works offline after the first fetch; nothing to audit but our code.
+ Python ≥ 3.11 is the only requirement (tomllib).
− No colours, no progress bars; output is plain text. Argument parsing is verbose.
− Every utility (Levenshtein, diff, TOML block merge) is written by hand and tested here.

## Fitness function
`tests/test_fitness.py::test_cli_has_no_runtime_dependencies` asserts that
`[project].dependencies` in `pyproject.toml` is empty and that no module under `src/rumbo/`
imports a third-party package.

## Reversibility: medium
A dependency can be added later, but every generated project would inherit it on `rumbo update`.
