# ADR-0013: `rumbo init --adopt` mounts the harness on an existing project; strict checks apply to changed files
Status: accepted    Date: 2026-11-07

## Context
`rumbo init` already respects existing files and merges `pyproject.toml` and `.gitignore`, but
it still generates the `src/` and `tests/` skeleton a real project does not need, and the
checks table runs ruff, pyright and bandit over the whole tree. On a 30,000-line codebase that
is hundreds of findings on day one, the `Stop` hook red forever, and the human trained to
bypass. The community study rejects the obvious fix, a baseline file of pre-existing findings:
entries go stale as soon as a file moves (basedpyright's `--writebaseline` suffers exactly
that). The alternative it recommends is to run the strict tools on the files the change touches
(`git diff --name-only base..HEAD`), the way diff-cover does for coverage. RUMBO itself, a flat
CLI with an existing Makefile, CI and AGENTS.md, is the first brownfield project to adopt.

## Drivers (non-functional requirements)
No skeleton written over a living codebase; detection of what exists (package, tests
directory, kind) with explicit overrides; strict rules on what the agent touches from the first
commit, the legacy debt visible but not blocking; no baseline file to maintain; the four A0
questions still the only questions; every placeholder in `[tool.rumbo.checks]` resolvable
offline.

## Options considered
1. Document how to delete the skeleton and add excludes by hand (status quo).
2. A baseline file of accepted findings (stale entries, hidden regressions).
3. Ruff and pyright excludes for the legacy directories generated at adoption (coarse and
   permanent).
4. An adoption mode that detects the project and writes only the harness, plus a `{changed}`
   placeholder so strict steps run on changed files, with `--full` for the whole tree.

## Decision and rationale
Option 4. The contract:

**`rumbo init --adopt`.** A flag of `init`, so A0 and the kind/layout/rigor flags are the same.
Detection, each overridable by the usual flag:
- package: `[project].name` normalised to snake_case when `src/<name>/` or `<name>/` exists,
  else the single top-level directory with an `__init__.py` that is not `tests`, `test`,
  `docs`, `scripts`, `spikes`, `evals` or `infra`; two or more candidates → error asking for
  `--package`;
- kind: `cli` when `[project.scripts]` is non-empty, `service` when `fastapi`, `flask`,
  `django` or `starlette` is a runtime dependency, else `library`;
- layout: `hexagonal` when `src/<pkg>/domain` and `src/<pkg>/app` both exist, else `flat`;
- tests directory: `tests` if it exists, else `test` if it exists, else `tests`; written to
  `[tool.rumbo].tests` when it is not `tests`.
In interactive mode A0 shows the detected values as defaults. `[tool.rumbo]` gains
`adopted = true`.

**What adoption writes.** The plan of `plan_files` minus everything under `src/` and minus the
example tests, keeping under the detected tests directory only
`architecture/test_architecture.py` (spikes rule; purity rules when `arch`) and
`unit/test_check_tests_not_weakened.py`. Everything else follows the init rules (existing files
skipped and reported, `pyproject.toml` and `.gitignore` merged). When `.github/workflows/`
already holds a workflow, `ci.yml` is not written and the report prints the two steps to add
(the runner and the strict sensor). `infra/` is written only when the kind has it and the
directory does not exist.

**`{changed}`.** A second placeholder in `[tool.rumbo.checks]`, next to `{base}` (this
supersedes ADR-0006's "only placeholder" sentence). `scripts/rumbo_check.py` expands it to the
`.py` files that differ from the merge base with `{base}` (`git diff --name-only
--diff-filter=ACMR <base>...HEAD`), plus the uncommitted changes (`git diff --name-only
--diff-filter=ACMR`) and the untracked files (`git ls-files --others --exclude-standard`),
deduplicated, existing, sorted. When the list is empty the step is not run and is reported as
`ok   lint (no changed files)`. `rumbo check --full` (and `scripts/rumbo_check.py --full`)
replaces `{changed}` with `.` for the whole tree. Adoption writes `lint = "uv run ruff check
{changed}"`, `format = "uv run ruff format --check {changed}"`, `types = "uv run pyright
{changed}"`; `test`, `security*`, `audit`, `evals` and `sensor` are unchanged. In CI a pull
request's `{changed}` is exactly the files it touches; the nightly workflow of an adopted
project runs `--full` so the legacy debt is measured, not blocking. The generated `AGENTS.md`
of an adopted project says so in one line.

**The self-adoption test.** RUMBO adopts itself with `--adopt --kind cli --layout flat --no-llm
--rigor production`: `[tool.rumbo]` and the checks table in its own `pyproject.toml`, the
canonical scripts copied to `scripts/` (kept identical by `rumbo update`, as every project), the
hooks, and `rumbo doctor` plus the five gates run on this repository.

## Consequences (+/−)
+ A brownfield project gets the harness in one command and strict rules from the first
  change, with no skeleton to delete and no baseline to maintain.
+ The legacy debt is visible every night and never blocks a pull request.
+ RUMBO can finally be audited by RUMBO.
− A change to a legacy file surfaces that file's whole history of findings at once; the team
  fixes or formats it in a `refactor:` commit of its own (the fix lane handles the size).
− `{changed}` depends on git; without a base the runner falls back to `.` and says so.
− Detection is heuristic; the flags remain the authority.

## Fitness function
`tests/test_scaffold.py`: detection of package (name, single directory, ambiguity error), kind,
layout and tests directory; the adoption plan has nothing under `src/`, only the two test
files, `adopted = true`, `{changed}` in the three steps; `ci.yml` skipped with the printed
steps when a workflow exists. `tests/test_check.py`: `{changed}` expansion from a tmp git repo
(committed, uncommitted and untracked files; non-`.py` ignored), the empty case reported `ok`
and not run, `--full`. `tests/test_cli.py`: `rumbo init --adopt --yes` on a tmp project with a
package, a `test/` directory and an existing workflow; `rumbo check --full` passes through.
The e2e script adopts a small pre-existing project and requires the runner, `rumbo doctor` and
`rumbo gate G4 --no-run` to pass. The repository's own `pyproject.toml` carries `[tool.rumbo]`
with `kind = "cli"`, `layout = "flat"`, and `tests/test_fitness.py` asserts `rumbo doctor` passes
on the repository itself.

## Reversibility: high
`--adopt` is a flag and `{changed}` a placeholder; removing both leaves `init` and the runner
as they were.
