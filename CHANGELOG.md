# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-11-21

Fixes from two external audits of v0.4.0; the findings and their evidence are in
`docs/audits/2026-11-16-pre-0.4.1.md`.

### Upgrading a project
1. Run `rumbo update` yourself, with no agent working in the project, and **commit the result
   before the agent goes on**. It rewrites protected files (`scripts/`, the workflows), and
   the guard stops an agent that finds them changed and not committed.
2. Run `rumbo doctor` and do what it says. `rumbo update` never rewrites an existing
   `.claude/settings.json`, so a project created before this version keeps two things that
   `doctor` now names: hook matchers that do not name `PowerShell`, and the pre-approved
   commands `make rumbo *` and `rumbo decision *`, which you should remove.
3. `rumbo update` replaces `RUMBO.md`, which now describes two things your own code does not
   have until you port them, because files under `src/` and `evals/graders/` are yours and
   are never replaced:
   - `RUMBO_LIVE=1` as the production switch: copy `live()` and `LIVE_VARIABLES` from the
     new template of `app/ports.py`.
   - Graders that take the case input and may be coroutines: take the new
     `evals/run_evals.py` together with the new `evals/graders/__init__.py` (the first
     imports `grade` from the second), and make a judge you registered asynchronous.
   `rumbo update --diff` shows each of these files against the new template. Until you port
   them your project keeps working as before: nothing forces the port.
4. `--strict` in the anti-cheat sensor now exits `2` when it finds no base other than `HEAD`.
   The generated CI and `[tool.rumbo.checks]` pass a base and are not affected; a force push
   to the default branch, whose previous commit is unreachable, is.

### Security
- The guard judges every shell tool like Bash (the `PowerShell` tool on Windows skipped it),
  protects `.git/` and the files that shadow `pyproject.toml` (`ruff.toml`, `pytest.ini`,
  `setup.cfg` and the like), lets an agent change in `pyproject.toml` only the dependency
  tables `uv add` writes, and refuses `verify-dep --index` and `git diff --no-index` from an
  agent's shell (ADR-0019, ADR-0023).
- The guard remembers `HEAD`: a commit made without the `pre-commit` hook is judged after the
  command, and `Stop` also checks code committed since the last green `HEAD` (ADR-0024).
- An agent can no longer create a numbered ADR or spec without the template mark, and
  renames, symlinks, hidden files, unusual file names, orphan branches and more spellings of
  a bypass no longer escape the guard (ADR-0025).
- The anti-cheat sensor reports a test that stays in the file but no longer runs, a skip that
  reaches a test from a fixture, a class or the module, and an assert that cannot fail; a
  project in a sub-directory of its repository is measured instead of always passing
  (ADR-0027).
- The anti-cheat sensor reads its settings from the base, so the change under test cannot
  narrow them; it ignores an approval mark authored by any known agent, not only Claude; the
  pull request label counts only in a pull request run (ADR-0021, ADR-0026).
- `rumbo verify-dep` never sends index credentials over plain `http` to another machine nor to
  another host through a redirect, and an answer that is not a package page is "could not
  confirm" (exit `2`), never "exists" (ADR-0028).
- `rumbo tour` quotes every file name and ref it puts in a command of its guide.
- The generated Claude Code adapter sends the prompt on standard input, so on Windows `cmd.exe`
  no longer parses it, and ends the whole process tree at the time limit.
- On Windows the runner no longer runs a `uv.cmd` planted in the project in place of `uv`.

### Changed
- The runner's exit codes (ADR-0018, ADR-0022): a step is red (`1`) only when its tool ran and
  judged the code, so pytest failing to import a test is the agent's to fix. A crash, a missing
  project script, a launcher that cannot start its interpreter and the codes a tool keeps for
  "I could not do my job" are a broken environment (`3`). G4 fails when the runner or the
  sensor could not be measured.
- `{base}` and the sensor try `origin/main`, `origin/master`, `origin/HEAD`, `main` and
  `master`; the upstream of the branch is no longer a base.
- Gates: G3 and G5 read every workflow under `.github/workflows/`, and one Python version is
  enough for G5 when it is the newest supported (ADR-0032); their human checklists follow
  `kind` and `layout`; a threat model or C4 still equal to the template, an eval case without
  `input` and an empty brief no longer count; `rumbo trace` reports a requirement line it
  cannot parse, and `--strict` and G4 fail on it.
- `rumbo init --adopt` follows the project (ADR-0020, ADR-0029): the tests directory and a
  package at the root are recorded in `[tool.rumbo]` (`tests`, `src`); the Python version is
  read from `requires-python` or `.python-version`; an existing pytest configuration,
  `.claude/settings.json` and `.pre-commit-config.yaml` are left alone and the guard snippets
  printed; no runtime dependency is added; `security` judges the changed files; nothing that
  is not on disk is configured; the project's own CI and `infra/` are recorded (`own_ci`,
  `own_infra`).
- Generated projects: graders receive the case input and may be coroutines, and the judge is
  asynchronous (ADR-0030); the nightly runs mutation testing only at production; an adopted
  project's CI runs pre-commit on the changed files only; a package's sdist carries the
  package and its tests only; `make security` exists from mvp on.

### Added
- `RUMBO_LIVE=1` in generated LLM projects: the production switch of the offline guard, set in
  the service image (ADR-0031).
- `rumbo update --allow-downgrade`: without it `update` refuses a harness newer than the CLI.
  `update` also reports the check steps and Makefile aliases that differ from the template.
- `rumbo doctor` checks that the guard is wired in `.claude/settings.json` and
  `.pre-commit-config.yaml`, and warns when the harness is newer than the CLI.
- `[tool.rumbo].agent_authors`: more commit identities whose approval mark the sensor ignores
  (ADR-0021).
- The generated CI passes `RUMBO_DEFAULT_BRANCH` to the sensor, so a default branch other than
  `main` or `master` is found.

### Fixed
- Runner: on Windows `.cmd` shims (`npx`, `pnpm`) are found and a backslash in a step is
  literal; `{changed}` includes staged files, never files under `spikes/`, and becomes the whole
  tree when too long for one command line, and a changed file named `-x.py` is passed as
  `./-x.py`; a malformed `[tool.rumbo]` table, an unwritable log or an unexpected error exits
  `3`, never `1`; `pyproject.toml` may start with a byte order mark (what PowerShell 5
  writes), for the guard and the anti-cheat sensor too: the guard refused an agent's `uv add`
  and the sensor ignored `[tool.rumbo].tests` and `agent_authors`.
- Anti-cheat sensor: git output is read as UTF-8 (on Windows a test file could drop out of the
  base); assertion helpers that are methods of the test class are resolved.
- `rumbo init`: `--force` never overwrites an earlier `.bak` and skips files equal to the
  template, and `doctor` no longer suggests it; the summary line no longer repeats `provider=`
  and `rigor=`; `rumbo.human` is set only for a human at a terminal, never when an agent runs
  it, on Windows too (ADR-0021); an error reading `pyproject.toml` or `.gitignore` no longer
  leaves a partial harness.
- `rumbo promote` is atomic: a failed update no longer leaves the project promoted.
- `rumbo gate`: G4 measures a project in a sub-directory of its repository, counts untracked
  files, excludes lockfiles at any depth and ignores merge commits when it decides the lane; a
  document saved as UTF-16 or cp1252 fails its check with a message; `"tags": null` in a dataset
  no longer crashes; the EARS check accepts `<...>` and `...` in code.
- `rumbo tour` finds removed asserts in renamed test files, in tests outside `tests/` and in
  merge commits, and with `diff.noprefix` or forced colour; a file name outside ASCII or a
  commit message holding a separator no longer hides a change; the revert command it suggests
  for a merge commit carries `-m 1`, which git requires.
- `rumbo gate` and `rumbo tour` without git installed end with one error line (exit `2`)
  instead of a traceback.
- `rumbo verify-dep` no longer crashes on an unexpected JSON answer or index URL; a port that is
  not a number is refused.
- `rumbo decision` rejects an empty or multi-line title; `rumbo new` numbers after four-digit
  prefixes only; `rumbo metrics --days` is validated; every file RUMBO writes ends its lines
  with `\n` on Windows too.
- Generated CI: the SBOM artifact no longer collides across the Python matrix; adding or
  removing the approval label re-runs the pull request.
- Generated project: bandit reads `[tool.bandit]`; Dependabot lists docker only with `infra/`;
  the nightly no longer runs mutmut at mvp; `make evals-live` loads `.env`; the type checkers
  skip `spikes/` given an explicit path; JSON logs keep tracebacks; a cancelled call ends the
  `claude -p` process; the eval runner rejects `--k 0`, gives concurrent runs their own report
  and keeps the fingerprint after a failed live run; the budget rejects `nan`.

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
  spec F-0001 (the guard, fifteen requirements traced to tests) are written.
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
