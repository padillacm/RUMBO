# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Fixes from an external audit of v0.4.0 (eight findings), in four decisions (ADR-0018 to
ADR-0021).

### Fixed
- ADR-0018: a step whose tool started and rejected the code is **red**, not a broken
  environment. pytest exits `2` on an import or syntax error while collecting and `5` when it
  collects nothing; mypy and `ruff format --check` exit `2` on a syntax error. The runner used
  to report all of them as exit `3`, so the `Stop` hook told the agent not to repair its own
  mistake and `rumbo gate G4` exited `0`. On such a code the runner now asks whether the tool
  can start (`<launcher> <tool> --version`): yes means red, no means environment. In G4 a
  runner or sensor that could not be measured fails the gate.
- ADR-0019: the guard no longer exempts the whole `pyproject.toml`. An agent's shell command
  or commit may change `project.dependencies`, `project.optional-dependencies`,
  `dependency-groups` and `tool.uv` (what `uv add` writes) and nothing else; a change to
  `[tool.rumbo.checks]`, the linter, the type checker, pytest, coverage or the version is
  reported after the command and refused at commit, with the tables named. `uv.lock` stays free.
- ADR-0020: adoption follows the project's layout. Gate G3 and `rumbo trace` read the tests
  directory from `[tool.rumbo].tests` instead of assuming `tests/`; a package at the root
  (no `src/`) is recorded in the new `[tool.rumbo].src` key and bandit, the type checker,
  coverage, the mutation paths and the architecture test point at it; `{changed}` never
  contains files under `spikes/`, and the generated `[tool.ruff]` sets `force-exclude = true`.
- ADR-0021: the anti-cheat sensor ignores an approval mark authored or co-authored by other
  agents, not only Claude, and a project adds its own with `[tool.rumbo].agent_authors`;
  `rumbo init` sets `rumbo.human` only for a human at a terminal, never when an agent runs it.
- The sensor resolves assertion helpers that are methods of the test class (`self._verify()`),
  including inherited ones: moving asserts into such a method is not reported as a weakening,
  and emptying it is.
- The human checklists of G3 and G5 follow `kind` and `layout` like the automatic checks: a
  flat package is not asked for import-linter contracts, a library or CLI is not asked for
  SLOs, a runbook or a canary.
- `rumbo init` printed `provider=` and `rigor=` twice in its summary line.

- On Windows `rumbo init` took a process without a terminal for a human (`isatty()` is true
  for the NUL device) and declared `rumbo.human`; it now asks the console API and requires
  both standard input and output to be consoles.

- Found while writing the specs below:
  - git output is decoded as UTF-8 everywhere. With the locale encoding (cp1252 on Windows)
    the sensor dropped from the base a test file holding a byte that encoding lacks, so
    emptying its tests passed, and such a byte in a commit message crashed the approval check.
  - `{changed}` includes files that are staged but not committed; `git add` hid them from
    the strict steps of an adopted project.
  - `rumbo verify-dep` no longer raises on a JSON answer of an unexpected shape (PyPI or OSV)
    nor on an index URL that cannot be parsed; a port that is not a number is refused
    instead of dropped.

### Added
- Specs for the features that were already built: F-0002 (the runner), F-0003 (the sensor),
  F-0004 (mount, adopt, update, promote), F-0005 (gates and traceability), F-0006 (`rumbo
  tour`) and F-0007 (`rumbo verify-dep`), each requirement traced to a test. Every feature
  of the PRD now has its spec.

## [0.4.0] - 2026-11-09

The six fronts of an external audit of v0.3.0, in twelve decisions (ADR-0006 to ADR-0017),
and RUMBO running its own harness.

### Added
- ADR-0006: `scripts/rumbo_check.py` / `rumbo check`, a standard-library verification runner
  driven by an ordered `[tool.rumbo.checks]` table in `pyproject.toml`, with `--brief`,
  `--keep-going` and subsets of steps. Exit 0 green, 1 a verification failed, 3 the environment
  is broken (the `Stop` hook then tells the agent to report to the human, not to repair it).
- Windows support: the harness no longer needs `make` or a POSIX shell, and the generated CI runs
  the check on `ubuntu-latest` and `windows-latest`; a generated `.gitattributes` keeps LF line
  endings.
- ADR-0007: the anti-cheat sensor counts `pytest.raises`, unittest and mock assertions, resolves
  assertion helpers inside the tests directory, and reads the directory from `[tool.rumbo].tests`.
- ADR-0008: the guard also runs as git `pre-commit` and `commit-msg` hooks (installed by
  `rumbo init` through pre-commit), so any coding agent is bound by the hard rules at commit
  time. The actor comes from `[tool.rumbo].agent_env` markers or `git config rumbo.human`;
  `rumbo doctor` warns when the clone is not declared. The root commit of a repository is
  exempt, so an agent can make the first commit of a generated project.
- ADR-0009: a fix lane in ANCLA. `/rumbo-fix` (red regression test, minimal change, one
  approval) and three extra `rumbo gate G4` checks when every commit is non-`feat`:
  regression test added, diff ≤ 150 lines, no new runtime dependency.
- `rumbo tour --out <dir>/` names the review file `<date>-<sha>.md`; the generated harness
  calls `rumbo` directly instead of `make rumbo ARGS=...`.
- ADR-0010: `--kind service|library|cli|data` and `--layout hexagonal|layered|flat`
  (`--layers` for a layered package); A0 question 2 asks what is being built, the LLM question
  is its sub-question. A library or cli gets `CHANGELOG.md`, a Trusted Publishing release
  workflow, a CI matrix and `[project.scripts]`; a data pipeline gets `evals/` without a model;
  a service keeps its HTTP skeleton without LLM. G3 and G5 ask each kind and layout only for
  what it can have. Older projects load as service/hexagonal; `rumbo update` writes the keys.
- ADR-0011: development dependencies and the checks table scale with rigor (five tools for a
  prototype); `rumbo promote mvp|production` raises the level without removing anything.
- ADR-0012: the LLM skeleton is async-first (`async` `Completion` port, `answer` and adapters;
  the Claude Code adapter uses `asyncio.create_subprocess_exec`), the request id travels in a
  context variable shared by nested spans, and `run_evals.py --concurrency N` bounds the
  calls in flight while keeping the case order.
- ADR-0013: `rumbo init --adopt` mounts the harness on an existing project (detects package,
  kind, layout and tests directory; no `src/`/tests skeleton; existing CI kept with the steps
  to add); the checks table accepts `{changed}` (changed `.py` files) next to `{base}`, and
  `rumbo check --full` expands it to the whole tree, which the nightly of an adopted project
  runs.
- ADR-0014: `rumbo verify-dep` checks existence and versions on the project's own index
  (`--index`, else `UV_INDEX_URL`, `UV_DEFAULT_INDEX`, `PIP_INDEX_URL`; PEP 691 JSON with a
  PEP 503 HTML fallback) and labels the PyPI measurements; credentials in the URL never
  reach the console. `rumbo init --python X.Y` stores the minimum Python in
  `[tool.rumbo].python`, which feeds `requires-python`, ruff's `target-version`, the CI
  matrix, the Dockerfile and a generated `.python-version`; `rumbo update` writes the key.
- ADR-0015: `rumbo init --types pyright|mypy` (default pyright), stored as `[tool.rumbo].types`:
  the dev tool, the `types` step and the `[tool.pyright]` / `[tool.mypy]` section follow it,
  with the inner layer strict under both. `rumbo update` writes the key. Reminder: add `ty`
  as a third value when it leaves preview.
- ADR-0017: a numbered ADR or spec committed without the template mark is signed and
  read-only for the agent (edit blocked, shell change reported, commit refused); a template
  mark removed through the shell is reported after the command. RUMBO's own brief, PRD and
  spec F-0001 (the guard, twelve requirements traced to tests) are written.
- ADR-0016: the eval runner fingerprints the prompts, the judge and the entry (sha256) in
  every report, records them in `evals/fingerprint.json` on a live run, and warns offline
  when fixtures or calibration may be stale; the warning never fails the step.
- RUMBO adopts its own harness (`--adopt --kind cli --layout flat --rigor production`): the
  runner, the guard as Claude Code and git hooks, the sensor and the gates run on this
  repository; `tests/test_self_adoption.py` keeps `scripts/` identical to `src/rumbo/sensors/`.
  Its own method documents are signed: brief, PRD, spec F-0001 (fifteen requirements traced
  to tests), C4 and threat model; the first interview is recorded.

### Changed
- `make check`, `make check-brief` and the other verification targets are optional one-line
  aliases of the runner; AGENTS.md, README, hooks, gates and CI call the runner directly.
- `domain/` may import pure names (`PurePath`, `StringIO`); the architecture test now also
  forbids the builtin `open()`.
- The repository pins Python 3.13 for development (`.python-version`); the CLI still supports
  3.11+.

### Fixed
- The CLI reconfigures stdout and stderr to UTF-8, so ✅/❌ no longer crash on a Windows
  console with a legacy code page.
- Templates checked out with CRLF on Windows no longer leak CRLF into generated files.
- The generated example integration test accepts the module's `SystemExit(0)`.
- `scripts/e2e.sh` used a `git revert` flag that does not exist.

## [0.3.0] - 2026-11-01

### Added
- Claude Code hooks: the generated `.claude/settings.json` pre-approves the harness commands
  and wires `scripts/rumbo_guard.py` (PreToolUse and Stop) so the agent cannot edit the
  Makefile, pyproject, CI, infra, the sensors or `.env`, remove a template mark, write the
  approval mark, force-push, bypass git hooks or finish with `make check-brief` red.
- Requirement → test traceability: requirement ids `F-NNNN.Rk`, `rumbo trace [--strict]`, a
  G4 row (recommended below production, required in production), `/rumbo-review` starting
  from the generated table.
- `.github/pull_request_template.md` (ANCLA checklist), `.github/CODEOWNERS` and `SECURITY.md`
  in generated projects and in this repository.
- Phase O skeleton for LLM products: `app/budget.py` (per-request token, cost and time limits
  from `RUMBO_BUDGET_*`, `BudgetExceeded` → abstention) and `app/telemetry.py` (spans with
  OpenTelemetry GenAI attribute names and cost, logged as JSON); runbook entries, SLO rows,
  `.env.example` variables and a G5 check that both are wired into the entry point.
- `rumbo eval add`: turns a failure into a JSONL case and logs it in
  `evals/error_analysis/log.md`.
- `rumbo metrics [--days --json]`: specs closed and sessions per spec, gates signed, commits
  by type, deploy/incident decisions as DORA proxies, requirements traced, latest eval run.
- `rumbo update --diff`: preview of what `update` would replace and how harness files drifted.
- `evals/graders/judge.py`: LLM-as-judge grader with `calibrate()` (TPR, TNR, kappa) against
  human-labeled rows in `evals/calibration/`; admitted only at TPR/TNR ≥ 0.8 on ≥30 cases.
- Framework CI on Python 3.11–3.13 (Linux and macOS), an anti-cheat sensor job and a
  release workflow (`vX.Y.Z` tag → GitHub Release from the CHANGELOG, optional PyPI trusted
  publishing); five ADRs with fitness functions in `docs/adr/`; `docs/tutorial.md`.
- Context-limit practices, grounded in the research report
  `reports/Desarrollar con agentes bajo límite de contexto.md` (`docs/method.md`, section 3.1 "Working within the agent's effective context"): a "Data
  contracts and ownership" section in the spec template (checked by gate G2), a one-writer
  rule and a `# Compact instructions` section in the generated AGENTS.md, `make check-brief`
  (only the failing tail reaches the agent's context), a requirement-by-requirement
  traceability table in `/rumbo-review`, and a session-count log when a spec closes.

### Changed
- Newcomer UX (from a from-zero usability run): `rumbo` alone prints help and the two commands
  to start with; `rumbo tour` and the generated Makefile fall back to `main` when `origin/main`
  does not exist; the Makefile uses a locally installed `rumbo` before fetching it with `uvx`;
  the gate header is ✅ when no check failed (still "NOT SIGNED" until the human ticks the
  checklist); interactive `rumbo init` asks before writing into a non-empty directory;
  `rumbo doctor` warns about the `@your-handle` and security-contact placeholders; gate G3 no
  longer accepts sections of untouched templates; `pre-commit autoupdate` rev bumps are not
  reported as drift; the README opens with the install block and a "just the sensors" path.
- `rumbo update` lists only harness files (Makefile, AGENTS.md, CI, hooks…) under "differ",
  never the human's documents.

### Fixed
- Generated files are written with LF on every platform (CRLF on Windows before), including
  merged `pyproject.toml` / `.gitignore`.
- Review fixes before release: the guard's Bash rules are backed by a git-based post-check
  (protected files vs HEAD) and `.claude/` is protected; the generated skeleton configures JSON
  logging at the process boundaries (spans were never emitted) and treats blank `RUMBO_BUDGET_*`
  as defaults; `rumbo metrics` tolerates hand-written decision headings, missing git and
  malformed eval files; `rumbo eval add` reads `GRADERS` with the AST and escapes the log table;
  `rumbo trace` ignores nested `R1.1` items and `R10a` look-alikes; release notes stop at the
  Keep-a-Changelog link references and PyPI publishes the released files.

## [0.2.0] - 2026-11-01

### Added
- `rumbo update`: refreshes the files the framework owns (RUMBO.md, `.claude/commands`,
  `scripts/`, the `0000` templates) without touching documents the human edited.
- `rumbo tour` and `make review`: a guided, pedagogical review of a git range (risk hotspots
  first, reading order with tests first, verification commands, explanation-test questions).
- Provider `claude-code`: evals call the model through the Claude Code CLI with the developer's
  own login; no API key on the machine.
- Gates scale with the rigor level; unmeasurable checks are reported instead of shown green.
- Anti-cheat sensor: per-test assert counts, always-true asserts, skip/xfail markers and
  parametrize cuts; approvals authored by an agent are ignored; PR label support.
- `rumbo verify-dep`: typosquatting check against popular packages and OSV vulnerability query.
- Full-history secret scan in CI (gitleaks action); SBOM from `uv.lock` (CycloneDX 1.5).
- `.env.example`, OWASP LLM/Agentic checklist in the threat model, `docs/reviews/`.

### Changed
- The whole framework is in English (CLI, templates, documentation).
- `make check` is offline: no model credential in CI on pushes or pull requests; the real
  model only runs in the nightly `evals-live` job (protected environment).
- `--force` keeps `.bak` copies and always merges `pyproject.toml` / `.gitignore`.
- The coverage threshold in production was replaced by nightly mutation testing.

### Fixed
- Dockerfile started `uvicorn` without installing it.
- Provider `other` generated invalid workflows; project names were not escaped in TOML/Python.
- Tracebacks in `rumbo new` (backslashes), `rumbo verify-dep` (incomplete JSON) and gates.

## [0.1.0] - 2026-11-01

### Added
- First version: `rumbo init`, `new`, `decision`, `gate`, `doctor`, `check-tests`,
  `verify-dep`; harness templates; RUMBO.md, method and the XM example.
