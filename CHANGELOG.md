# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Fixes from two external audits of v0.4.0. The first had eight findings, answered in four
decisions (ADR-0018 to ADR-0021). The second had about a hundred, listed with their evidence
in `docs/audits/2026-11-16-pre-0.4.1.md` and answered in ADR-0022 to ADR-0032.

After upgrading, run `rumbo update` (it refreshes the three scripts under `scripts/`) and
`rumbo doctor`: a project created before this version has hook matchers that do not name
PowerShell, and `doctor` says what to add to `.claude/settings.json`.

### Security
- `rumbo verify-dep` no longer sends the credentials of a private index over plain `http`:
  Basic authentication is not encryption, so a URL with a user name on an `http` index that
  is not on this machine (loopback) is refused before any request, the index lookup is
  reported as "verify by hand" and the PyPI measurements still run. `https`, loopback and an
  `http` index without credentials are unchanged.
- The guard (ADR-0023 to ADR-0025):
  - Every shell tool is judged like Bash. The hooks named only `Bash`, so a command sent
    through the PowerShell tool on Windows skipped the rules and the check after the command.
  - The files that shadow `pyproject.toml` are protected, in any directory: `ruff.toml`,
    `pytest.ini`, `tox.ini`, `setup.cfg`, `mypy.ini`, `pyrightconfig.json`, `.coveragerc`,
    `.importlinter`, `uv.toml` and their dotted forms. A new `ruff.toml` used to turn the
    linter green. `.git/` is protected too (the hooks and the configuration live there).
  - The guard remembers `HEAD`. A commit made without the `pre-commit` hook (a stash and a
    cherry-pick, the plumbing commands) left a clean tree and nothing to detect; it is now
    judged after the command. `Stop` also verifies code committed since the last green `HEAD`.
  - `git mv`, a file turned into a symlink, names with spaces or outside ASCII, files hidden
    from `git status` (`--skip-worktree`, ignored names) and an orphan branch no longer escape.
  - An agent cannot create a numbered ADR or spec without the template mark (it counted as
    signed after its first commit), and an edit is simulated to see whether the mark survives.
  - More spellings of a bypass are refused: abbreviated flags, flags followed by `;`, git
    configuration through `-c` or the environment, keys in another case.
  - `verify-dep --index` and `git diff --no-index` are refused from an agent's shell, and the
    generated `.claude/settings.json` no longer pre-approves `make rumbo *` or `rumbo decision
    *`: the first ran any binary through `RUMBO=`, the second recorded a gate signature.
- The anti-cheat sensor (ADR-0026, ADR-0027):
  - A project in a sub-directory of its repository always passed: the base files could not
    be read and counted as "no tests". They are read relative to the project, and a base that
    cannot be read exits `2`.
  - `[tool.rumbo].tests` and `agent_authors` are read from the base: the change under test
    could narrow the first or empty the second.
  - A test that stays in the file but pytest never runs is reported: under a condition,
    deleted with `del`, `__test__ = False`, replaced by a decorator. Tests are functions whose
    name starts with `test` (pytest's prefix), inherited ones included.
  - Skips are counted wherever they are (fixtures, module level, `skipTest`, `SkipTest`,
    `pytest.param(marks=...)`), and a hook added to a `conftest.py` is reported for review.
  - Asserts that cannot run or cannot fail count zero: a helper turned into a generator, a
    swallowed `AssertionError`, a nested function that is never called.
  - `--strict` exits `2` when the base found by default is `HEAD` itself, and the pull request
    label counts only in a pull request run of GitHub Actions.
- `rumbo verify-dep` (ADR-0028):
  - An index that answered `200` with a page that is not a package page (a login page) made
    a hallucinated name pass with exit `0`. It is now "could not confirm", verdict `2`.
  - urllib copied the `Authorization` header to wherever a redirect pointed. With credentials
    a redirect is followed only inside the same scheme, host and port; `https` is never left
    for plain `http`. A loopback index is reached without the proxies of the environment.
  - The argument must be a package name and is quoted into the URL; one request has a total
    deadline (60 s) and a body cap (32 MB); text from the network is printed without control
    characters.
- `rumbo tour` quotes every file name and ref it puts in a command of the guide.
- The generated Claude Code adapter sends the prompt on standard input. On Windows the CLI is
  a batch shim, and `cmd.exe` parsed a prompt passed as an argument: it could run commands.

### Changed
- ADR-0022 refines ADR-0018: a step is red only when its tool ran and judged the code. Exit
  `1` is probed like any other failure (`uv run` exits `1` when it cannot build the
  environment); crash codes, a missing project script and the codes a tool documents as "I
  could not do my job" (pytest `3`, `ruff check` `2`, pyright `2`, `3`, `4`) are environment.
  Exit `3` is therefore more frequent where `1` used to be.
- `{base}` and the sensor try `origin/main`, `origin/master`, `origin/HEAD`, `main` and
  `master`. The upstream of the branch is no longer a base.
- ADR-0032: G3 and G5 read every workflow under `.github/workflows/`, not only `ci.yml`, and
  one Python version is enough for G5 when it is the newest supported.
- A threat model or a C4 that still equals the template no longer passes G3 once the mark is
  removed; an eval case counts only when it has an `input`; an empty brief is not complete.
- `rumbo trace` reports a requirement line it cannot parse instead of dropping it, and
  `--strict` and G4 fail on it.
- Generated projects: graders receive the case input and may be coroutines, and the judge is
  asynchronous (ADR-0030); `RUMBO_LIVE=1` is the production switch of the offline guard, set
  in the service image (ADR-0031); the nightly mutates only at production and audits at every
  rigor; an adopted project's CI runs pre-commit on the changed files only; the sdist of a
  package carries the package and its tests, nothing else; `make security` exists from mvp on.

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

- From the second audit, the runner:
  - On Windows a `.cmd` shim (`npx`, `pnpm`) and a program with a directory part are found;
    a batch program still runs without a shell, and an argument `cmd.exe` would interpret is
    refused. A backslash in a step is literal (`python scripts\hello.py`).
  - A `{changed}` list too long for one command line becomes the whole tree, with a note;
    it used to be reported as "python: not found". A changed file named `-x.py` is passed as
    `./-x.py`.
  - A malformed `[tool.rumbo]` table, an unwritable log and any unexpected exception exit
    `3`, never `1`; `pyproject.toml` may carry a byte order mark; steps do not read standard
    input.
- From the second audit, gates, trace, metrics and tour:
  - G4 measures a project in a sub-directory of its repository, counts untracked files,
    excludes lockfiles at any depth, ignores merge commits when it decides the lane and keeps
    the runner's verdict in its detail.
  - A document saved as UTF-16 or cp1252 (PowerShell 5 redirection) fails its check with a
    message instead of a traceback; `"tags": null` in an eval dataset no longer crashes.
  - The EARS check no longer rejects a requirement that contains `<...>` or `...` in code.
  - `rumbo tour` missed removed asserts in a renamed test file, a file name outside ASCII, a
    merge commit, with `diff.noprefix` or forced colour, and in tests outside `tests/`; a
    commit message holding a separator hid the commit; the asserts of a deleted test file
    went to the previous file.
  - `rumbo trace` reads the tests once instead of once per requirement.
- From the second audit, the generated project:
  - CI: the SBOM artifact collided across the Python matrix; adding the approval label did
    not re-run the pull request.
  - `[tool.bandit]` was never read (the step lacked `-c pyproject.toml`); Dependabot listed
    docker for projects without `infra/`; the nightly ran mutmut at mvp, where it is not
    installed; `.env` was never loaded although the README said to create it.
  - The type checkers checked `spikes/` and the sensors when given an explicit path
    (`rumbo check --full` on an adopted project).
  - The `claude -p` process survived a cancelled call; JSON logs dropped tracebacks; the
    eval runner divided by zero with `--k 0`, let concurrent runs share a report file and
    rewrote the fingerprint after a failed live run; the budget accepted `nan`.

- From the second audit, `init`, `update`, `promote` and `doctor` (ADR-0029):
  - `rumbo init --force` overwrote its own `.bak` on the second run, losing what the human
    had written, and `doctor` recommended that command. A backup is never overwritten, a file
    equal to the template is skipped, and `doctor` no longer sends anyone to `--force`.
  - Adoption imposed a pytest configuration (`testpaths = ["tests"]`, `--strict-markers`):
    tests elsewhere stopped being collected and a configuration in `setup.cfg` or `tox.ini`
    was outranked. An existing pytest configuration is now left alone.
  - Adoption never detected the Python version (`.python-version` 3.11 on a project that
    requires 3.13). It is read from `requires-python`, else from `.python-version`; a
    `--python` below it is refused.
  - Adoption refuses to configure what is not on disk (a missing package directory, a
    single-module project, layers or an evals entry that do not exist) before writing
    anything; `app`, `api` and `domain` are reserved only for new projects whose layout
    generates them.
  - Adopting over an existing `.claude/settings.json` or `.pre-commit-config.yaml` left the
    guard unwired while `doctor` said "complete": the snippets are printed and `doctor` checks
    that the guard is wired and that its matchers name every shell tool.
  - What the project already had (its CI, its `infra/`) is recorded at adoption in
    `[tool.rumbo]` (`own_ci`, `own_infra`), so RUMBO's own generated files are not taken for
    the project's later.
  - `promote` wrote the new rigor before updating: a failure left the project promoted and
    the retry refused. It is atomic now. `update` refuses a harness newer than the CLI unless
    `--allow-downgrade` is passed, and reports the steps and Makefile aliases that differ from
    the template of the level.
  - Every file RUMBO writes ends its lines with `\n` on Windows too; `rumbo decision` rejects
    an empty or multi-line title; `rumbo new` numbers after four-digit prefixes only; an error
    while reading `pyproject.toml` or `.gitignore` no longer leaves a partial harness;
    `rumbo metrics --days` is validated by the parser.

### Added
- ADR-0022 to ADR-0032, each with its fitness function, and `docs/audits/`, the findings of
  the second audit with the evidence for each.
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
