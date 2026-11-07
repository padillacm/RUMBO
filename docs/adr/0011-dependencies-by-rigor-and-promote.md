# ADR-0011: Development dependencies scale with rigor, and `rumbo promote` raises it
Status: accepted    Date: 2026-11-07

## Context
`rumbo init` installs ten development tools in every project, `prototype` included: ruff,
pyright, pytest, pytest-cov, hypothesis, import-linter, pre-commit, pip-audit, bandit, mutmut.
The gates already scale with rigor; the dependencies do not. cookiecutter-hypermodern-python was
admired and abandoned for exactly this: a dozen coupled tools from minute one, each harder to
remove than to start without. With kinds and layouts (ADR-0010) the base would only grow.

## Drivers (non-functional requirements)
A prototype installs what a prototype verifies; moving up a level is one command and adds, never
replaces; editing `rigor` by hand keeps working; the guard's git hooks (ADR-0008) are never
optional; the `[tool.rumbo.checks]` table stays in the human's hands.

## Options considered
1. Keep the flat list and document how to remove tools (status quo).
2. Scale the list by rigor and let the human edit `rigor` and run `rumbo update`.
3. Option 2 plus a visible command, `rumbo promote mvp|production`, that changes the rigor,
   adds the level's dependencies with `uv add --dev`, and runs the update.

## Decision and rationale
Option 3, built on option 2's machinery. The contract:

**Dependencies by rigor and kind** (resolved by `uv add`, never pinned from memory):

| | development (`--dev`) | runtime |
|---|---|---|
| every project | `ruff`, `pyright`, `pytest`, `hypothesis`, `pre-commit` | – |
| `arch` on (layout ≠ flat) | `import-linter` | – |
| `mvp` and above | `pytest-cov`, `bandit`, `pip-audit` | – |
| `production` | `mutmut` | – |
| `kind = service` | – | `fastapi`, `uvicorn`, `httpx` |
| `llm` | – | `pydantic` + the provider SDK |

`pre-commit` is in the base because it carries the guard's git hooks. `pytest-cov` moves to
`mvp` with the coverage flags of the `test` step.

**Checks by rigor.** `[tool.rumbo.checks]` renders `lint`, `format`, `types`, `test`, `arch`
(when `arch`), `sensor` for `prototype`, and inserts `security`, `security-scripts`, `audit`
after `arch` (and `evals` after them when `evals`) from `mvp` on. The order of the full table is
unchanged from ADR-0006.

**`rumbo update` on a rigor change.** `update` compares the table with the steps of the current
rigor and kind and appends the missing ones (steps are only added, never replaced or removed, as
ADR-0006's migration already does for the whole table); it renders the level's files that do not
exist yet (`nightly.yml`, `dependabot.yml` at `mvp` and above) through the normal plan; and it
prints the `uv add --dev …` line for the development dependencies of the level that
`pyproject.toml` does not list yet (`[dependency-groups].dev`, falling back to
`[tool.uv].dev-dependencies`).

**`rumbo promote <rigor>`.** Refuses a rigor equal to or lower than the current one (lowering is
a hand edit, by design), writes the new `rigor` in `[tool.rumbo]`, runs `update`, and runs the
`uv add --dev` line unless `--no-install`. It prints what changed and reminds the human of the
gate items the new level requires (`rumbo gate` shows them). `promote` never touches
`AGENTS.md`, the Makefile or CI beyond what `update` already reports.

## Consequences (+/−)
+ A prototype has five tools, not ten; the first `uv sync` is faster and nothing unused sits
  in `pyproject.toml`.
+ Growing up is `rumbo promote mvp`; nothing is removed, so the step is safe to take early.
+ Hand-editing `rigor` still works: `update` does the same without the `uv add`.
− Two code paths (`init`, `update`) must agree on the lists; they share one function.
− A project that removed a step from the table gets it re-added by `update` when its rigor
  requires it; the table comment says so.

## Fitness function
`tests/test_scaffold.py`: the dependency lists per rigor, kind and layout (prototype has no
bandit, pip-audit, pytest-cov or mutmut; flat has no import-linter; service adds fastapi,
uvicorn, httpx); the checks table per rigor; `update` on a project whose rigor was raised by
hand appends the missing steps once (idempotent) and reports the `uv add` line.
`tests/test_cli.py`: `rumbo promote` refuses equal or lower rigor, raises `prototype → mvp` and
`mvp → production` (rigor written, steps appended, nightly and dependabot rendered, the `uv add`
command recorded in the report with `--no-install`). The e2e script promotes the prototype
project to `mvp` and requires the runner to stay green.

## Reversibility: high
The lists are data; restoring one flat list is a one-line change, and `promote` is a thin
command over `update`.
