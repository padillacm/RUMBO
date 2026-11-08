# RUMBO — Instructions for Claude Code

> **How to use this file**
>
> - **New project:** install the harness with `rumbo init` (Part A, "fast path") or copy
>   `RUMBO.md` to the repository root, open Claude Code there and say:
>   **"Read RUMBO.md and run Part A."** Claude Code asks the A0 questions, mounts the harness
>   and stops at every approval point.
> - **Project that already has the harness** (`AGENTS.md` exists and `pyproject.toml` has a
>   `[tool.rumbo]` section): Part A is done. Do **not** ask the A0 questions again — the answers
>   are in `docs/decisions.md`. Go to step A8 ("Final verification") if the check (`uv run python scripts/rumbo_check.py`) was never
>   green, otherwise to Part C ("The RUMBO cycle").
>
> RUMBO is an end-to-end framework for building software with AI coding agents:
> **R**ecognize → **U**nify → **M**odel → **B**uild with ANCLA → **O**perate/Observe/Optimize,
> with a transversal "human compass". ANCLA is the loop of every change:
> **A**nchor in verifiers → **N**avigate with a plan → **C**lose the loop in a sandbox →
> **L**imit and review → Learn **A**nd measure.
>
> Guiding principle: AI amplifies the discipline that already exists; it does not replace it.
> That is why the focus is on **executable verifiers** (tests, properties, types, architecture
> contracts, evals) and not on prompts.

---

## Instructions for Claude (read before acting)

- You are the implementation agent. The human (project owner) decides the **what** and the
  **why**; you propose, implement and verify.
- Work **step by step**. At the end of every step marked 🛑, stop, summarize what you did in
  ≤5 lines and wait for approval.
- Never delete or weaken tests to make them pass. If a test looks wrong, stop and ask.
- Do not add dependencies that are not listed here without asking for permission and verifying
  that the package exists on PyPI, or on the index this project installs from (exact name,
  official repository, age): `rumbo verify-dep <package>`.
- Always use the latest stable versions of every tool; do not copy version numbers from
  memory. For pre-commit, run `pre-commit autoupdate` after creating the configuration.
- Never remove the `<!-- rumbo:template` mark from a document and never write
  `[tests-change-approved]`: both belong to the human.
- If something in this file clashes with the environment (a tool does not install, an option
  changed), say so and propose the closest alternative; do not hide it.
- Several of these rules are enforced by Claude Code hooks (`.claude/settings.json` →
  `scripts/rumbo_guard.py`) and, for any other agent, by the same script as git `pre-commit`
  and `commit-msg` hooks. A blocked tool call or commit means "ask the human", never "find
  another way".

---

## PART A — Mount the harness (once per repository)

### A0. Initial questions 🛑

Ask the human (all at once, in a single message):

1. Project name and Python package name (snake_case).
2. What are you building? `service` (an API or backend, the default), `library` (a package
   others install), `cli` (a command-line tool) or `data` (a pipeline). Does it call an LLM?
   (yes/no). If yes: which model provider? (`anthropic`, `openai`, `google`, `mistral`,
   `claude-code` — the Claude Code CLI with the developer's own login, no API key — or
   `other`). The package layout is never asked: `hexagonal` for a service, `flat` for the
   rest, `--layout layered --layers api,core` for an explicit stack (ADR-0010).
3. Rigor level: `prototype`, `mvp` or `production` (see Part E "Scaling the rigor").
4. Is there a concrete use case? (for example: a conversational agent over the Colombian
   electricity market data published by XM).

Do not continue without answers. Use `<pkg>` to refer to the package from here on.

### A1–A7. Fast path: the `rumbo` CLI

If `uv` is available, mount the whole harness (A1–A7) with one command, using the A0 answers:

```bash
uvx --from git+https://github.com/padillacm/RUMBO rumbo init --yes \
  --name "<project>" --package <pkg> --kind service|library|cli|data --rigor <rigor> \
  [--llm --provider anthropic|openai|google|mistral|claude-code | --no-llm] [--case "<case>"] \
  [--layout hexagonal|layered|flat [--layers outer,...,inner]] \
  [--python 3.11|3.12|3.13] [--types pyright|mypy] [--adopt]
```

`rumbo init` creates the structure, installs the dependencies with `uv add` (latest stable
versions), runs `pre-commit autoupdate` and `install`, and logs the A0 answers in
`docs/decisions.md`. It never overwrites an existing file (it merges `pyproject.toml` and
`.gitignore`) and reports what it skipped. Review the output, explain to the human what was
skipped or failed, and jump to **A8**. If you cannot use the CLI, follow A1–A7 by hand.

**An existing project** takes `rumbo init --adopt` (ADR-0013): it detects the package, the
kind, the layout and the tests directory (the flags still win), writes the harness without the
`src/` and tests skeleton, leaves an existing CI workflow alone and prints the two steps to add,
and makes `lint`, `format` and `types` run on the changed files only (`{changed}`), so the
legacy debt is measured nightly with `--full` instead of blocking every change. The adoption
commit touches protected files, so the **human** makes it; from then on the hooks apply.

### A1. Repository structure

```
.
├── AGENTS.md                 # the agent's map; outline in Part B
├── CLAUDE.md                 # one line: @AGENTS.md
├── RUMBO.md                  # this file
├── README.md
├── SECURITY.md               # reporting policy + what the harness does by design
├── Makefile                  # optional one-line aliases of the check
├── pyproject.toml            # includes [tool.rumbo], read by rumbo gate/doctor/update
├── .pre-commit-config.yaml
├── .gitignore
├── .env.example              # LLM products: which variable holds the credential
├── .github/workflows/ci.yml
├── .github/workflows/nightly.yml     # rigor mvp or production
├── .github/dependabot.yml            # rigor mvp or production
├── .github/pull_request_template.md  # the G4 checklist, ticked by the human
├── .github/CODEOWNERS                # humans who review the harness files (placeholder handles)
├── .claude/commands/rumbo-*.md       # /rumbo-r, -u, -m, -ancla, -review, -o, -gate, -tour
├── .claude/settings.json             # permissions + hooks (scripts/rumbo_guard.py)
├── docs/
│   ├── product/brief.md
│   ├── product/prd.md
│   ├── adr/0000-template.md
│   ├── architecture/c4.md
│   ├── security/threat-model.md
│   ├── ops/runbook.md        # service and data
│   ├── ops/slos.md           # service
│   ├── postmortems/0000-template.md
│   ├── evals/plan.md         # LLM products and data pipelines
│   ├── reviews/              # guided reviews written by rumbo tour
│   └── decisions.md          # human decisions log (the compass)
├── specs/0000-template.md
├── CHANGELOG.md              # library and cli (+ .github/workflows/release.yml, Trusted Publishing)
├── src/<pkg>/
│   ├── __init__.py, __main__.py   # (`[project.scripts]` entry for a cli)
│   │                         # the four packages below are the hexagonal layout (services);
│   │                         # `layered` creates the packages listed in --layers, `flat` none
│   ├── domain/__init__.py    # pure logic: no I/O, no LLM SDKs, no HTTP
│   ├── app/__init__.py       # use cases; depends only on domain (+ async ports.py, entrypoint.py, budget.py, telemetry.py with LLM)
│   ├── adapters/__init__.py  # external APIs, DB, LLM (+ llm.py, claude_code.py with LLM)
│   ├── prompts/__init__.py   # versioned prompts (LLM products only)
│   └── api/__init__.py       # FastAPI (+ http.py for a service, with or without LLM)
├── tests/
│   ├── unit/                 # + test_check_tests_not_weakened.py (tests of the sensor)
│   ├── property/
│   ├── integration/
│   └── architecture/
├── evals/                    # LLM products and data pipelines (judge.py only with LLM)
│   ├── datasets/smoke.jsonl
│   ├── graders/__init__.py   # deterministic graders · judge.py (LLM judge, calibrated first)
│   ├── calibration/example.jsonl   # human-labeled rows for judge calibration
│   ├── error_analysis/.gitkeep     # log.md grows with `rumbo eval add`
│   └── run_evals.py
├── scripts/rumbo_check.py                # verification runner (step A3)
├── scripts/check_tests_not_weakened.py   # anti-cheat sensor (step A6)
├── scripts/rumbo_guard.py                # guard hook for Claude Code (step A6)
├── spikes/README.md          # "Throwaway code. Never imported from src/."
└── infra/Dockerfile          # service and data
```

What `--kind` changes (ADR-0010): a `service` gets the HTTP skeleton, `infra/`, the runbook
and the SLOs; a `library` or `cli` gets `CHANGELOG.md`, a release workflow with PyPI Trusted
Publishing (no token anywhere) and a CI matrix over several Python versions; a `data` pipeline
gets `evals/` with deterministic graders even without a model, `infra/` and the runbook. `llm`
is a capability on top of any kind (it forces the hexagonal layout, where its skeleton lives).
Gate G5 asks each kind only for what it can have.

Documents under `docs/` and `specs/` start with the mark `<!-- rumbo:template … -->`. The
human deletes it when the document is complete; `rumbo gate` checks it.

### A2. Environment and tools (uv)

- Initialize with `uv init --package` (or adapt if it exists) and add the development
  dependencies: `ruff`, `pyright`, `pytest`, `pytest-cov`, `hypothesis`, `import-linter`,
  `pre-commit`, `pip-audit`, `bandit`, `mutmut`.
- LLM products add (runtime): `pydantic`, `fastapi`, `httpx`, `uvicorn` and the official SDK of
  the provider (none for `claude-code`). Observability (`opentelemetry-sdk`, Langfuse or
  Phoenix) is added in phase O, not now.

Configure at least this in `pyproject.toml`:

```toml
[tool.rumbo]                   # what rumbo gate / doctor / update read
version = "<rumbo version>"
package = "<pkg>"
llm = false
provider = "none"
rigor = "mvp"

[tool.ruff]
line-length = 100
target-version = "py311"
extend-exclude = ["spikes"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "S", "RUF"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S603", "S607"]

[tool.pyright]
include = ["src", "tests"]
typeCheckingMode = "standard"
strict = ["src/<pkg>/domain"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"

[tool.bandit]
skips = ["B101"]

[tool.importlinter]
root_package = "<pkg>"
include_external_packages = true

[[tool.importlinter.contracts]]
name = "Hexagonal layers"
type = "layers"
layers = ["<pkg>.api", "<pkg>.adapters", "<pkg>.app", "<pkg>.domain"]

[[tool.importlinter.contracts]]
name = "The domain is pure"
type = "forbidden"
source_modules = ["<pkg>.domain"]
forbidden_modules = ["httpx", "requests", "fastapi"]   # add the LLM SDK here (e.g. "anthropic")

[tool.mutmut]
source_paths = ["src/"]
only_mutate = ["src/<pkg>/domain/*"]
```

(Adjust the keys if the installed version of a tool changed them; verify by running it.)

### A3. The check runner: one verification command

`scripts/rumbo_check.py` is a standard-library runner (the same module as `rumbo check`) that
reads an ordered `[tool.rumbo.checks]` table from `pyproject.toml`, one command string per step,
and runs each without a shell, so it behaves identically on Windows, macOS and Linux.
Illustrative excerpt; `rumbo init` writes the exact table for the rigor and the provider.

```toml
[tool.rumbo.checks]
lint     = "uv run ruff check ."
format   = "uv run ruff format --check ."
types    = "uv run pyright"
test     = "uv run pytest --cov --cov-report=term-missing:skip-covered"   # no --cov in prototype
arch     = "uv run lint-imports"
security = "uv run bandit -q -r src evals"
security-scripts = "uv run bandit -q -r scripts -s B404,B603,B607"
audit    = "uv run pip-audit --skip-editable"
evals    = "uv run python evals/run_evals.py --suite smoke"   # LLM products and data pipelines, offline
sensor   = "uv run python scripts/check_tests_not_weakened.py --base {base}"
```

Steps run in this order: `lint`, `format`, `types`, `test`, `arch`, `security`,
`security-scripts`, `audit`, `evals` (LLM products only), `sensor`. `uv run python
scripts/rumbo_check.py` runs them all and stops at the first failure; `--keep-going` runs them
all; a subset runs with `uv run python scripts/rumbo_check.py lint types`; `--brief` writes the
output to `.rumbo-check.log` and prints one line, or only the failing tail (this is what the
agent runs). `{base}` is `--base`, else `origin/main`, else `main`. Operators such as `&&` or `|`
are a configuration error: a step that needs logic becomes a small Python script.

Exit codes: `0` green; `1` a verification failed; `3` the environment is broken (a tool missing
or crashing, a malformed table). The `Stop` hook blocks the agent on `1`; on `3` it tells the
agent to report to the human and not to repair the environment. With the CLI installed,
`rumbo check` is the same command.

The `Makefile` keeps one-line aliases for people who have `make` (`make check`, `make check-brief`,
`make lint`, `make types`, `make test`, `make arch`, `make security`, `make sensor`,
`make evals`) plus `make fmt`, `make evals-live`, `make mutation`, `make sbom`, `make hooks`,
`make review` and `make rumbo`; the agent calls `rumbo` and the runner directly and does not need `make`.

**The check is offline**: it never needs a model credential. The real model runs only in
`make evals-live`. Prototypes drop the security steps.

### A4. pre-commit

Hooks: `ruff-check` and `ruff-format` (astral-sh/ruff-pre-commit), `gitleaks` (staged files),
`check-added-large-files`, `end-of-file-fixer`, `trailing-whitespace`, `check-yaml`,
`check-toml`, `check-merge-conflict`, `detect-private-key`, `uv-lock` (astral-sh/uv-pre-commit)
and a local hook that runs `uv run lint-imports`. Then `pre-commit autoupdate` and
`pre-commit install`.

### A5. CI (GitHub Actions)

- `ci.yml` (every push and PR), with a `check` job on `ubuntu-latest` and a `check-windows` job
  on `windows-latest` (the runner needs no `make`), actions pinned by SHA, `permissions: contents: read`:
  checkout with `fetch-depth: 0` → `astral-sh/setup-uv` → `uv sync --locked` → `python scripts/rumbo_check.py`
  (no secrets) → `scripts/check_tests_not_weakened.py --strict` with `RUMBO_BASE_REF` set to
  the PR base → `pre-commit run --all-files` (mvp+) → SBOM artifact (production). A separate
  `secrets` job runs `gitleaks/gitleaks-action` over the full history: the pre-commit hook only
  sees staged files, so this job is the sensor that actually protects the repository.
- `nightly.yml` (daily cron, mvp+): `make mutation` (mutmut needs Linux, macOS or WSL) on `src/<pkg>/domain` and, for LLM
  products, `make evals-live` in a protected `evals` environment. **That is the only workflow
  that holds a model credential**, and it never runs on pull requests.
- `.github/pull_request_template.md` carries the G4 checklist where the human is already
  looking; `.github/CODEOWNERS` names the humans who review the harness files (Makefile,
  pyproject, CI, hooks, scripts, specs, decisions). Replace the placeholder handles and enable
  "require review from code owners" in the branch protection to make it binding.

### A6. Sensors the agent cannot bypass: the anti-cheat sensor and the guard hook

Comparing against `origin/main` (or the PR base commit), the script fails if:

- a file under `tests/` is deleted or a `test_*` function disappears;
- a test loses assertions, or the total number of assertions in the tests directory drops
  (`tests/` by default, `[tool.rumbo].tests` for other layouts);
- a test gains always-true asserts (`assert True`), `skip`/`xfail` markers, or a shorter
  `parametrize` list.

It can only be bypassed by a **human**: a commit in `base..HEAD` with `[tests-change-approved]`
in its message that was not authored by an agent, or the `tests-change-approved` label on the
PR. What it does not see (review by hand; mutation testing covers part of it): early `return`,
mocks that empty the behavior, helpers built dynamically or imported from outside the tests
directory, changes to pytest configuration.
An assertion is an `assert`, a `pytest.raises`/`warns`/`deprecated_call` block, a `self.assert*`
or `self.fail` call, a mock assertion (`assert_called_once_with`, ...) or `pytest.fail`. A call
to a helper defined in the same file, a `conftest.py` or a module imported from the tests
directory counts as the assertions of its body (depth 3), so extracting repeated asserts into a
helper passes and hollowing the helper is reported.
It ships with its own tests (`tests/unit/test_check_tests_not_weakened.py`).

`scripts/rumbo_guard.py` is the **guard hook**, wired by `.claude/settings.json` as Claude Code
hooks. Before every `Edit`/`Write`/`Bash` call it blocks (exit 2, the reason reaches the agent):
edits to the protected files (Makefile, `pyproject.toml`, CI, `infra/`, pre-commit, both
sensors, `evals/run_evals.py`, `.env*`), removal of the `<!-- rumbo:template` mark, writing
`[tests-change-approved]`, `--no-verify`, force pushes, `git reset --hard`, disabling hooks,
`rm -rf` on the root and shell redirections into protected files. On `Stop`, if Python, TOML
or JSONL files changed since the last commit, it runs `scripts/rumbo_check.py --brief` and refuses to finish
while it is red; if the environment is broken (exit 3) it asks the agent to report to the human instead. The human is never blocked: edits by hand do not go through the hook, and a
rule can be overridden in `.claude/settings.local.json`. `settings.json` also pre-approves the
harness commands (the check runner, pytest, ruff, read-only git) and denies `curl`/`wget` and
reading `.env`.

The same script is also a **git hook** (ADR-0008), installed by `rumbo init` through
pre-commit (`pre-commit` and `commit-msg`), so an agent running in Cursor, Codex, Gemini CLI
or Aider meets the same rules when it commits. The actor is an agent when one of the
environment markers in `[tool.rumbo].agent_env` is set (default: `CLAUDECODE`, `GEMINI_CLI`,
`CURSOR_AGENT`, `CODEX_SANDBOX_NETWORK_DISABLED`), the human when `git config rumbo.human` is
`true` in the clone (`rumbo init` sets it; `rumbo doctor` warns when it is missing), and
unknown otherwise, which counts as an agent. For an agent the hooks refuse a staged protected
file, a staged document whose template mark is gone, and a commit message carrying
`[tests-change-approved]`. The block is late (at commit) and an agent that unsets its own
marker is breaking a rule, not defeating a sensor: CI remains the binding layer.

### A7. Evals skeleton (LLM products only)

`evals/run_evals.py`:

- reads JSONL cases (`{"id", "input", "expected", "grader", "tags"}`) from
  `evals/datasets/<suite>.jsonl`;
- runs the real system (entry function in `src/<pkg>/app`, by default
  `<pkg>.app.entrypoint:answer`, a coroutine; a synchronous entry such as a data pipeline's
  `run` works too) for each case, `k` times (`--k`, default 1; use 3 to measure consistency),
  with at most `--concurrency N` calls in flight (default 1; the report keeps the case order);
- applies **deterministic** graders from `evals/graders/` (numeric accuracy with tolerance,
  field equality, assertions on tool-call arguments, abstention when the data is missing);
- reports `pass@1` and `pass^k` (a case passes only if all `k` runs pass) and exits non-zero
  when `pass^k` falls below the threshold (`--min-pass`);
- fingerprints the prompts and the judge (sha256) in every report; a `--live` run records them
  in `evals/fingerprint.json` and an offline run warns when they changed since, so stale
  fixtures or an uncalibrated judge are noticed (ADR-0016; the warning never fails the step);
- is **offline** unless `--live` is passed: adapters raise `OfflineError` instead of calling the
  model, so the smoke suite must run from recorded fixtures. Cases tagged `example` do not
  count toward the dataset minimum (20 prototype · 50 mvp · 100 production).

Two tools keep the dataset honest:

- `rumbo eval add <suite> --input '{...}' --expected '{...}' --grader <name> [--source ...]`
  turns a production failure or a reviewed trace into a case and logs it in
  `evals/error_analysis/log.md` (the weekly ritual reads that log).
- LLM judges (`evals/graders/judge.py`) are added **after** the first error analysis and only
  once calibrated: `calibrate()` over ≥30 human-labeled rows in `evals/calibration/` must give
  TPR and TNR ≥ 0.8 before the judge is registered in `GRADERS`; the numbers and the date go in
  `docs/evals/plan.md`.

### A8. Final verification of Part A 🛑

Run `uv run python scripts/rumbo_check.py` and show the result. It must be green with a trivial example test in every
`tests/` folder. Run `rumbo doctor` if available. Commit: `chore: initial RUMBO harness`.
Stop and summarize.

---

## PART B — Content of `AGENTS.md`

`rumbo init` generates `AGENTS.md` from this outline (adjusted to `<pkg>`, the rigor and the
LLM flag; the generated file also has a "Commands" and a "Human compass" section):

```markdown
# AGENTS.md — project map (read me before every task)

## Map
- Product: docs/product/brief.md, docs/product/prd.md
- Feature specs (EARS): specs/
- Architecture decisions: docs/adr/  (read them before any structural decision)
- Layers: src/<pkg>/{domain,app,adapters,api}; contracts in pyproject.toml [tool.importlinter]
- Security: docs/security/threat-model.md
- Evals: evals/ (LLM products only)

## Before declaring a task done
uv run python scripts/rumbo_check.py   # must be green; paste the summarized output

## Per-task flow (ANCLA)
1. Read the spec and the relevant ADRs.
2. Write the tests first (they must fail) and wait for the human to approve them.
3. Propose a short plan (files, tests, risks) and wait for approval.
4. Minimal code, then refactor; iterate until the check passes.
5. Summarize the change in 3 sentences and list new dependencies (if any).

## Hard rules
- Never delete, skip or weaken tests. If a test looks wrong, stop and ask.
- Never write [tests-change-approved] or remove the rumbo:template mark: only the human does.
- Separate structural commits (refactor) from behavioral commits.
- Small diffs (~300 lines max). If the task is bigger, propose splitting it.
- `domain/` is pure: no I/O, no HTTP, no LLM SDKs (pure names such as `PurePath` and `StringIO` are fine; `open()` is not).
- Numbers shown to the user are computed by code, never by the LLM.
- Prompts live only in src/<pkg>/prompts/, with a version identifier.
- No new dependency without approval and PyPI verification.
- Do not touch infra/, secrets, the Makefile, CI workflows or evals/run_evals.py unless asked.
- Nothing in spikes/ is imported from src/.
- If you make the same mistake twice, propose a test, lint rule or contract that catches it.
```

Keep the file under ~100 lines (`rumbo doctor` checks it). It is a map, not a manual.

---

## PART C — The RUMBO cycle (how we work after the harness)

When the human says **"phase R"**, **"phase U"**, etc. (or uses `/rumbo-r`, `/rumbo-u`,
`/rumbo-m`, `/rumbo-ancla`, `/rumbo-review`, `/rumbo-o`, `/rumbo-gate`, `/rumbo-tour`), follow
the matching section. Every gate is evaluated with `rumbo gate G1..G5` (automatic checks scaled
to the rigor + a checklist the human signs; the exit code never means "signed").

### R — Recognize (is it worth building?)

- The human writes `docs/product/brief.md` first. You critique it: weak assumptions, missing
  metric, current alternatives.
- Propose interview scripts; the human runs the interviews.
- Prototypes **only in `spikes/`**. What moves to the next phase is what was learned, not the
  code.
- LLM products: feasibility test with 20–30 real questions, run "by hand" with the model and
  the data.
- 🛑 **Gate G1:** problem validated with users or real data (≥5), success metric and
  abandonment criterion written down.

### U — Unify (what must it do and how will we know?)

- Light PRD with non-goals and non-functional requirements **with numbers** (p95 latency, cost
  per request, availability). Prototypes may replace it with a criteria list.
- Specs in `specs/` (`rumbo new spec "<name>"`) with EARS patterns:
  - Ubiquitous: "The system shall…"
  - Event: "When…, the system shall…"
  - State: "While…, the system shall…"
  - Unwanted: "If…, then the system shall…"
  - Optional: "Where…, the system shall…"
- Your role: interrogate the spec (ambiguities, edge cases, contradictions) and propose
  acceptance tests. The human signs.
- LLM products: seed dataset of **real** questions in `evals/datasets/` and the eval plan.
- 🛑 **Gate G2:** every requirement has a planned test or eval; the dataset has the minimum of
  real cases for the rigor.

### M — Model (how do we structure it to change it safely?)

- Modular monolith by default. ADRs in `docs/adr/` (`rumbo new adr "<decision>"`, MADR format,
  template in Part D); **every ADR with a fitness function** (import-linter contract, architecture test or
  CI threshold) — required in production, recommended before.
- The human sketches the irreversible decisions first; you complete options and trade-offs.
- LLM products: an explicit **workflow vs. agent** ADR (start with the simplest: single call →
  workflow → agent only if the evals demand it). Few tools, typed with Pydantic, read-only
  unless needed.
- One-hour threat model: STRIDE per boundary, the OWASP Top 10 for LLM Applications 2025 and
  Agentic Applications 2026 checklist, and the **lethal trifecta** test (private data +
  untrusted content + external communication): document which leg is removed and which sensor
  proves it.
- 🛑 **Gate G3:** contracts in CI, threat model reviewed, C4 levels 1–2 in
  `docs/architecture/c4.md`.

### B — Build with ANCLA (every PR)

- **A**nchor: acceptance tests first (Hypothesis for domain invariants), each naming the
  requirement it covers (`F-000N.Rk` in its docstring; `rumbo trace` builds the table). The
  human approves them.
- **N**avigate: short plan before coding.
- **C**lose the loop: iterate in a sandbox until the check is green.
- **L**imit: small diff; human review + a second review by a clean session or another model
  (`/rumbo-review`, which starts with a requirement-by-requirement traceability table against
  the spec). `rumbo tour origin/main --out docs/reviews/` writes a guided review for the human: risk hotspots first, reading
  order (tests first), verification commands and three explanation-test questions.
- Learn **A**nd measure: repeated mistakes → a new sensor or a line in AGENTS.md. When a spec
  closes, log how many sessions it took (`rumbo decision "F-000N closed" --why "1 session"`);
  `rumbo metrics` turns those decisions, git and the evals into the team's numbers (sessions
  per spec, gates signed, deploys and incidents, requirements with a test, latest eval run).
- **Fix lane** (ADR-0009): a defect in existing behaviour does not need a spec or two pauses.
  `/rumbo-fix <bug>` writes the failing regression test first (its docstring names the bug and
  the requirement when one exists), makes the minimal change, keeps the diff under 150 lines
  and the commits typed `fix:`/`test:`/`refactor:`, adds no dependency, and stops once at the
  PR. `rumbo gate G4` recognises the lane from the commit types (every commit non-`feat`, no
  `!`) and adds three checks: regression test added, diff ≤ 150, no new runtime dependency. A
  change to a data contract, a public signature or a spec requirement is a feature: back to
  ANCLA with a spec. G4 cannot see semantic drift; the reviewer still reads the spec.
- 🛑 **Gate G4 (per PR):** CI green, no test weakened, dependencies verified, and the human can
  explain the change in 3 sentences without reading your summary.

**Golden rule of the harness:** if the agent makes the same mistake twice, add a sensor (test,
lint rule, contract) or a guide, in addition to fixing the code.

**Context and parallel work.** Model quality degrades with context length from the first few
thousand tokens (there is no safe threshold; see `docs/method.md`, section 3.1 "Working within
the agent's effective context"), so the harness keeps each unit of work small:

- One spec per session, started fresh from the spec; `/clear` between unrelated tasks. The
  session that wrote the spec is not the one that implements it.
- Drop old tool output instead of summarizing it: `scripts/rumbo_check.py --brief` prints only the failing
  tail; the `# Compact instructions` section of AGENTS.md tells the agent what to keep.
- Decisions that must outlive the session go to an ADR or `docs/decisions.md`.
- **One writer per set of files.** The spec lists the files it owns. Agents that run in
  parallel are read-only (exploration, alternative hypotheses, review against the spec, noisy
  test runs). For long features, parallel drafts are assembled by one integrator holding the
  full spec; writers never coordinate ad hoc.
- Parallelize at the level of specs, not inside them: at most 3–5 worktrees, each with its own
  session, the check and small PR, merged in series. If a change touches an interface another
  spec depends on, write the new signature in that spec's "Data contracts" section.

### O — Operate, Observe, Optimize

- Deploy on a managed platform (PaaS: Fly.io, Railway, Render or Cloud Run) with an immutable image
  (`infra/Dockerfile`, tag = SHA) and a **rehearsed rollback**.
- 2–3 SLOs in `docs/ops/slos.md` (LLM products: cost per request, budget abstentions, online
  accuracy). LLM products are instrumented from day one: every request runs inside
  `app/telemetry.span` (OTel GenAI attribute names and `cost.usd`, logged as JSON) and an
  `app/budget.Meter` (tokens, cost, time; `BudgetExceeded` becomes an abstention with the
  reason). Charge every model call to the meter. Shipping the spans to Langfuse, Phoenix or an
  OTLP collector is an adapter (`opentelemetry-sdk`, pinned); call sites in `app/` do not change.
- Cost budget per request: `RUMBO_BUDGET_*` in the environment, alerts on
  `abstain_reason=budget`; the runbook has an entry for the spike.
- `docs/ops/runbook.md` with one entry per failure mode; blameless postmortems
  (`rumbo new postmortem "<title>"`) that answer "which sensor was missing?".
- Weekly ritual: the human reviews 20–30 traces (open coding); you propose categories; new
  failures become eval cases.
- Maintenance: Dependabot/Renovate weekly with a cooldown; biweekly cleanup with Tidy First
  refactors; documentation updated in the same PR.
- `rumbo metrics` for the monthly review: deploys and incidents are `rumbo decision "deploy
  vX"` / `rumbo decision "incident: ..."` entries, the cheapest DORA proxy for a small team.
- 🛑 **Gate G5:** rollback rehearsed, SLOs and alerts active, cost limits configured (the
  automatic check verifies budget and telemetry are wired into the entry point).

### Human compass (always)

- For design decisions, ask the human for their version first.
- When the human is learning something new, explain and ask instead of solving everything.
- Do not propose agents for: irreversible decisions, security code without prior tests, or
  topics the human needs to master (e.g. their thesis).
- Log the decisions the human made, and why, in `docs/decisions.md`
  (`rumbo decision "<title>" --why "<reason>"`).

---

## PART D — Templates

Outlines only. The generated files (`rumbo init`, refreshed by `rumbo update`) are canonical and
carry guidance comments these outlines omit.

`rumbo init` creates these templates; `rumbo new adr|spec|postmortem "<title>"` creates
numbered documents from them.

**`docs/product/brief.md`**

```
# Brief
Problem: <who> cannot <what> because <cause>; today they use <alternative>.
Evidence: <interviews/data, n=>
Outcome metric:
Risky assumptions (value / usability / feasibility / viability):
Experiments and success/failure criterion:
Non-goals:
G1 decision: build / iterate / abandon
```

**`docs/adr/0000-template.md`** (MADR)

```
# ADR-000N: <decision>
Status: proposed | accepted | superseded by ADR-XXXX    Date:
## Context
## Drivers (non-functional requirements)
## Options considered
## Decision and rationale
## Consequences (+/−)
## Fitness function
<contract / test / CI threshold that enforces it>
## Reversibility: high | medium | low
```

Accepted ADRs are immutable: to change a decision, write a new ADR that supersedes it. When
reversibility is low, the human decides without a prior draft from the agent.

**`specs/0000-template.md`**

```
# F-000N: <name>
Story: As a <user> I want <capability> so that <benefit>.
## Requirements (EARS)
- R1 (event): When …, the system shall …
- R2 (unwanted): If …, then the system shall …
## Acceptance criteria
<pytest tests / Hypothesis properties that verify them; each mentions its requirement id F-000N.Rk>
## Data contracts and ownership
Types/schemas · signatures · shared invariants · files owned by this spec (one writer) ·
interfaces other specs depend on
## Evals (LLM products): <case IDs, threshold>
## NFR: p95 < X s · cost < Y USD
## Out of scope
```

**`docs/security/threat-model.md`**

```
# Threat model
Data flow (Mermaid diagram):
Assets:
Trust boundaries:
STRIDE per boundary:
Lethal trifecta: private data? untrusted content? external communication? which leg is removed?
OWASP LLM 2025 / Agentic 2026 checklist (ID · applies? · mitigation · test):
Mitigations (each with its test):
Accepted risks (signed by the human):
```

**`docs/ops/runbook.md`**

```
# Runbook
Dashboards:  Key rotation:  Vendor contacts:
## <Alert / failure mode>
Symptom · Diagnosis (trace query) · Mitigation (flag / rollback) · Escalation
```

**`docs/postmortems/YYYY-MM-DD-title.md`**

```
Summary · Impact · Timeline · Root cause
Which sensor or guide was missing:
Actions (owner, date):
Test or eval case added:
```

---

## PART E — Scaling the rigor

| Practice | prototype | mvp | production |
|---|---|---|---|
| Product | 5-line brief | Brief + PRD | Reviewed PRD |
| Specs | Criteria list | EARS in the core | EARS everywhere, requirement → test traceability |
| ADRs | 0–1 | Irreversible decisions | All + fitness function |
| Tests | Smoke | Unit + integration + Hypothesis | + mutation testing, contract tests with external APIs |
| Evals (LLM) | 20 cases by hand | Error analysis + 50 cases in CI | 100 cases, validated judges, pass^k, red teaming |
| Security | No secrets in code | Threat model + bandit + pip-audit + gitleaks | + SBOM, privacy review |
| Observability | Logs | OTel + LLM traces + cost alerts | SLOs with error budget |
| Deployment | Local | PaaS + rehearsed rollback | Canary / feature flags |
| Review | Informal | Human + second model | + two pairs of eyes in critical areas |
| Human compass | Optional | Explanation test | Mandatory + documented mental models |

What `rumbo init` and `rumbo gate` do per rigor:

| | prototype | mvp | production |
|---|---|---|---|
| the check | lint, format, types, test, arch unless flat (+evals) | + security, audit, coverage report | same (coverage is informational: mutation testing is the sensor) |
| dev dependencies | ruff, pyright, pytest, hypothesis, pre-commit (+ import-linter unless flat) | + pytest-cov, bandit, pip-audit | + mutmut |
| CI | check + sensor + gitleaks | + pre-commit on all files | + SBOM (CycloneDX from uv.lock) |
| Nightly | — | mutation (+ live evals) | mutation + live evals with k=3 |
| Dependabot | — | weekly, 7-day cooldown | same |
| Gates | PRD, threat model, C4, runbook optional; 20 real eval cases | all required; 50 cases; budget + telemetry wired (G5) | + fitness function per ADR, nightly, SBOM, every requirement traced to a test (G4); 100 cases |
| Hooks | `.claude/settings.json` + `scripts/rumbo_guard.py`, also as git hooks via pre-commit | same | same |

Moving up is one command: `rumbo promote mvp` (or `production`) rewrites the rigor, adds the
level's dependencies with `uv add --dev`, appends the missing steps to `[tool.rumbo.checks]`
and renders the level's files (nightly, Dependabot); nothing is removed (ADR-0011). Editing
`rigor` by hand and running `rumbo update` does the same without the `uv add`.

What matters is the **worst plausible impact**, not the size of the code. An agent that hands
out figures used in commercial decisions is, at the very least, an "MVP with production
controls" as far as accuracy goes. Adjust `[tool.rumbo.checks]` and CI to the level chosen in A0.

---

## PART F — Case profiles (optional)

Examples of how to apply RUMBO to a concrete case. The complete one is in
`docs/examples/xm-agent.md` of the RUMBO repository.

### Conversational agent over the Colombian electricity market (XM)

- Data source: the official `pydataxm` library (verify on PyPI and in the
  `EquipoAnaliticaXM/API_XM` repository before installing: `rumbo verify-dep pydataxm`). The
  public API limits the range per call: the adapter must page by blocks and cache
  (DuckDB/Parquet).
- Architecture (ADR-0001): **workflow, not agent**:
  1. the LLM extracts `{metric, entity, range, aggregation}` into a Pydantic schema with a
     closed enum of metrics;
  2. deterministic code validates and queries XM through the adapter;
  3. deterministic code computes;
  4. the LLM writes the answer using only the returned data, showing unit, source and query
     date.
- Initial Hypothesis properties: the union of date blocks covers the range exactly with no
  overlap; daily aggregations consistent with hourly data; unit conversions invertible;
  `America/Bogota` time zone.
- Deterministic evals: numeric accuracy with tolerance, correct tool arguments, abstention on
  missing data; consistency with pass^3.
- Security: no write or arbitrary-network tools, output rendered without links or remote
  images → the trifecta is broken by design.
- First ANCLA task: the XM adapter with cache and pagination.
