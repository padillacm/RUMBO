# RUMBO

An end-to-end framework for building software with AI coding agents (Claude Code first),
without losing control of the *what* and the *why*. Python projects, teams of one to three
people, from prototype to production.

**R**ecognize → **U**nify → **M**odel → **B**uild with ANCLA → **O**perate/Observe/Optimize,
with a transversal **human compass**. ANCLA is the loop of every change: **A**nchor in
verifiers → **N**avigate with a plan → **C**lose the loop in a sandbox → **L**imit and review →
Learn **A**nd measure.

> AI amplifies the discipline that already exists; it does not replace it. RUMBO therefore
> invests in **executable verifiers** (tests, properties, types, architecture contracts, evals,
> hooks) and in keeping the human in charge of the decisions, not in prompts.

## Contents

1. [What RUMBO is](#what-rumbo-is)
2. [What is in this repository](#what-is-in-this-repository)
3. [Install and mount the harness](#install-and-mount-the-harness)
4. [Day-to-day workflow](#day-to-day-workflow)
5. [CLI reference](#cli-reference)
6. [What `rumbo init` generates](#what-rumbo-init-generates)
7. [Hard rules as hooks](#hard-rules-as-hooks)
8. [Rigor levels](#rigor-levels)
9. [Security by design](#security-by-design)
10. [Working within the context limit](#working-within-the-context-limit)
11. [Reviewing without drowning](#reviewing-without-drowning-rumbo-tour)
12. [Updating a project's harness](#updating-a-projects-harness)
13. [Developing the framework](#developing-the-framework)
14. [Glossary](#glossary) · [License](#license)

## What RUMBO is

Three pieces that work together:

| Piece | What it does |
|---|---|
| **The method** ([`RUMBO.md`](RUMBO.md), [`docs/method.md`](docs/method.md)) | The phases, the gates, the templates, the per-rigor rules and the evidence behind them. `RUMBO.md` is written for Claude Code: drop it in a repository and the agent can mount the harness and follow the cycle without the CLI. |
| **The `rumbo` CLI** | Mounts and updates the *harness* in any Python project, creates documents from templates, evaluates gates, runs the anti-cheat sensor, verifies dependencies, builds guided reviews, traces requirements to tests, turns failures into eval cases and reports process metrics. Standard library only. |
| **The generated harness** | Everything that surrounds the agent so it can self-correct and cannot cheat: `AGENTS.md` (a map, ≤100 lines), one offline `make check`, CI with pinned actions, Claude Code hooks, hexagonal layers with import contracts, tests, evals with deterministic graders, budget and telemetry for LLM products, and the document templates the human fills. |

The cycle, in one paragraph. **Recognize**: the human writes the brief first; the agent
critiques it (gate G1). **Unify**: EARS specs with requirement ids and explicit data
contracts, a PRD, a dataset of real questions when there is an LLM (G2). **Model**: ADRs with
fitness functions, hexagonal layers enforced by import-linter, a threat model with the lethal
trifecta and the OWASP LLM/Agentic checklist (G3). **Build with ANCLA**: one spec per fresh
session, failing tests approved by the human before the code, small diffs, a second review by
a clean session and a guided tour for the human (G4, per PR). **Operate**: immutable image,
rehearsed rollback, SLOs, a per-request budget and GenAI telemetry, runbook, blameless
postmortems, production failures turned into eval cases (G5). Across all of it the **human
compass**: the human sketches design decisions first, passes the explanation test before a
merge, and logs decisions in `docs/decisions.md`.

Who it is for: developers and small teams that already use Claude Code (or a similar agent)
and want the speed without the instability, the cognitive debt or the security holes that the
evidence associates with unsupervised agent use. The method is language-agnostic; the CLI and
the templates are Python (`uv`, ruff, pyright, pytest, Hypothesis, import-linter, bandit,
pip-audit, mutmut, FastAPI for LLM products).

## What is in this repository

| Path | Purpose |
|---|---|
| [`RUMBO.md`](RUMBO.md) | Instructions for Claude Code, Parts A–F: mount the harness (A), content of AGENTS.md (B), the cycle (C), templates (D), rigor (E), case profiles (F). Works on its own. |
| `src/rumbo/` | The CLI: `cli.py`, `scaffold.py` (init/update), `render.py` (template engine), `gates.py`, `trace.py`, `metrics.py`, `tour.py`, `sensors/` (anti-cheat sensor and guard hook, copied into every project), `templates/`. |
| [`docs/method.md`](docs/method.md) | The why: evidence, phases, review design, the context limit, RACI, stack, rigor, anti-patterns, adoption plan, open questions. |
| [`docs/tutorial.md`](docs/tutorial.md) | 30 minutes from `rumbo init` to the first spec closed with gate G4. |
| [`docs/adr/`](docs/adr/) | The framework's own decisions, each with a fitness function in `tests/`. |
| [`docs/examples/xm-agent.md`](docs/examples/xm-agent.md) | Worked example: an agent over the Colombian electricity market (XM). |
| `docs/reviews/` | Guided reviews of this repository's own batches (`make review`). |
| `reports/`, `research_notes/` | The research report on context-length degradation and parallel work (Spanish) and its notes. |

## Install and mount the harness

Requirements: Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), git and `make` (on Windows use
Git Bash or WSL). Claude Code to work with the agent.

```bash
mkdir my-project && cd my-project && git init
uvx --from git+https://github.com/padillacm/RUMBO rumbo init      # asks the four A0 questions
make check                                                        # must be green (offline)
git add -A && git commit -m "chore: initial RUMBO harness"
```

The four A0 questions: project name, LLM product or not (and the provider), rigor level, and
the concrete use case. Non-interactive:

```bash
rumbo init --yes --name "XM Agent" --package xm_agent \
  --llm --provider claude-code --rigor mvp --case "agent over XM market data"
```

| Option | Values |
|---|---|
| `--llm` / `--no-llm` | LLM products get `evals/`, `prompts/`, a FastAPI skeleton, the offline guard, budget and telemetry, and the provider SDK |
| `--provider` | `anthropic`, `openai`, `google`, `mistral`, `claude-code` (the Claude Code CLI with your own login: no API key anywhere), `other` (with `--sdk-package`, `--sdk-module`, `--sdk-env-key`) |
| `--rigor` | `prototype`, `mvp`, `production` (scales `make check`, CI, nightly, Dependabot and the gates) |
| `--no-install` | write files only; skip `uv add` and pre-commit |
| `--force` | overwrite existing files (keeps `.bak` copies). `pyproject.toml` and `.gitignore` are always merged, never replaced |

In an existing project nothing is overwritten by default: the missing `[tool.*]` sections are
merged into `pyproject.toml`, missing lines into `.gitignore`, and everything skipped is
reported. Two things only you can do afterwards: replace `@your-handle` in
`.github/CODEOWNERS` and the contact in `SECURITY.md`.

Permanent install: `uv tool install git+https://github.com/padillacm/RUMBO`. Pin a release once
one is tagged: `…/RUMBO@v0.2.0`. Inside a generated project the CLI is also available as
`make rumbo ARGS="gate G4"`.

Without the CLI: copy `RUMBO.md` to the repository root and ask Claude Code to *"Read RUMBO.md
and run Part A"*.

## Day-to-day workflow

Open Claude Code in the project and say *"Read AGENTS.md and RUMBO.md; let's start phase R"*,
or use the slash commands. First time? Follow the [tutorial](docs/tutorial.md).

| Phase | You (the human) | The agent | Commands |
|---|---|---|---|
| **R** Recognize | Write `docs/product/brief.md` by hand; validate the problem with users or data | Critiques the brief, proposes interview questions | `/rumbo-r`, `rumbo gate G1`, `rumbo decision "G1 signed" --why ...` |
| **U** Unify | Write the specs (EARS, requirement ids `R1…`, data contracts and owned files); PRD; real questions for the dataset | Lists ambiguities, proposes acceptance tests | `rumbo new spec "..."`, `/rumbo-u`, `rumbo gate G2` |
| **M** Model | Sketch the irreversible decisions first; one-hour threat model | Completes ADR options, proposes the import-linter contract and adversarial tests | `rumbo new adr "..."`, `/rumbo-m`, `rumbo gate G3` |
| **B** Build (ANCLA) | Approve the failing tests, then the plan; read the guided tour; pass the explanation test; merge | Writes tests first (each naming its requirement `F-NNNN.Rk`), implements until `make check-brief` is green, keeps diffs small | `/rumbo-ancla F-0001`, `rumbo trace`, `rumbo gate G4`, `make review`, `/rumbo-review` (fresh session), `rumbo decision "F-0001 closed" --why "1 session"` |
| **O** Operate | Rehearse the rollback; review 20–30 traces weekly; write the runbook entries | Instruments with `app/telemetry.span` and `app/budget.Meter`, proposes failure categories | `/rumbo-o`, `rumbo eval add ...`, `rumbo metrics`, `rumbo new postmortem "..."`, `rumbo gate G5` |

Every document the harness generates starts with `<!-- rumbo:template … -->`. You delete that
line when the document is complete; `rumbo gate` checks it and the agent cannot remove it.
Gates print the automatic checks and a checklist that **only the human signs**
(`rumbo decision "G4 signed"`).

Slash commands installed in `.claude/commands/`: `/rumbo-r`, `/rumbo-u`, `/rumbo-m`,
`/rumbo-ancla`, `/rumbo-review`, `/rumbo-o`, `/rumbo-gate`, `/rumbo-tour`.

## CLI reference

```bash
rumbo init [--yes ...]               # mount the harness (Part A); see the table above
rumbo update [--diff]                # refresh framework-owned files; --diff previews and writes nothing
rumbo doctor                         # structure complete, AGENTS.md ≤ 100 lines, harness up to date
rumbo new adr|spec|postmortem "T"    # docs/adr/0001-t.md · specs/0001-t.md · docs/postmortems/<date>-t.md
rumbo decision "title" --why "..." [--who human|ci]   # append to docs/decisions.md
rumbo gate G1|G2|G3|G4|G5 [--no-run] # automatic checks (by rigor) + the human checklist; exit 1 on failure
rumbo check-tests [--base REF] [--strict]             # anti-cheat sensor (tests not weakened)
rumbo verify-dep NAME...             # exists on PyPI, age, repository, typosquatting, OSV vulnerabilities
rumbo tour [BASE] [--head H] [--out FILE]             # guided, pedagogical review of a git range
rumbo trace [--strict]               # requirement (F-NNNN.Rk) → test/eval table; G4 reads it
rumbo eval add SUITE --input '{...}' --expected '{...}' --grader NAME [--id --tag --source --note]
rumbo metrics [--days 30] [--json]   # specs closed, sessions per spec, gates signed, deploys/incidents, evals
rumbo --version
```

Conventions `rumbo metrics` understands in `docs/decisions.md`: `F-000N closed` with
`--why "k sessions"`, `Gk signed`, titles starting with `deploy` or `incident`.

In a generated project, the `Makefile` wraps the harness: `make check` (everything, offline),
`make check-brief` (only the failing tail reaches the agent; full log in `.rumbo-check.log`),
`make fmt`, `make sensor`, `make review`, `make evals` / `make evals-live`, `make mutation`,
`make sbom`, `make hooks`, `make rumbo ARGS="..."`.

## What `rumbo init` generates

- `AGENTS.md` (a map, ≤100 lines), `CLAUDE.md` (`@AGENTS.md`), `RUMBO.md`, `SECURITY.md`,
  `README.md`, `.env.example`.
- `.claude/settings.json` (permissions and hooks) and `scripts/rumbo_guard.py`: the hard rules
  of AGENTS.md as sensors (next section). `.claude/commands/` with the slash commands.
- A `Makefile` whose `make check` is the single, **offline** verification command: ruff,
  pyright (strict in `domain/`), pytest + Hypothesis, import-linter, bandit + pip-audit and the
  smoke evals, scaled by rigor.
- Hexagonal layers `src/<pkg>/{domain,app,adapters,api}` with import-linter contracts and an
  architecture test that forbids I/O in `domain/` and imports from `spikes/`.
- `scripts/check_tests_not_weakened.py`: fails when tests or files are deleted, asserts drop,
  always-true asserts or skip markers appear, or `parametrize` lists shrink, unless a human
  approves it (`[tests-change-approved]` in a human-authored commit, or the PR label).
- `evals/` (LLM products): JSONL datasets, deterministic graders (numeric with tolerance,
  field equality, tool arguments, abstention), an LLM judge that counts only after calibration
  against human labels (`evals/calibration/`), and `run_evals.py` with pass@1 and pass^k,
  offline unless `--live`.
- Phase O wiring (LLM products): `app/budget.py` (per-request token, cost and time limits from
  `RUMBO_BUDGET_*`; the overrun becomes an abstention) and `app/telemetry.py` (spans with
  OpenTelemetry GenAI attribute names and cost, logged as JSON, no dependency); the runbook and
  SLO templates carry the matching entries.
- CI with actions pinned by SHA, a full-history gitleaks scan, the sensor, pre-commit on all
  files (mvp+) and an SBOM from `uv.lock` (production); a nightly with mutation testing and
  live evals in a protected environment, the only place a model credential lives. A PR template
  with the ANCLA checklist and `CODEOWNERS` for the harness files; Dependabot with a cooldown.
- Templates: brief, PRD, ADR (MADR + fitness function), EARS spec with data contracts, C4,
  threat model (STRIDE, lethal trifecta, OWASP LLM/Agentic checklist), runbook, SLOs,
  postmortem, eval plan, decisions log.

The full tree is in `RUMBO.md`, Part A1.

## Hard rules as hooks

Rules are prose until a hook enforces them. The generated `.claude/settings.json` pre-approves
the harness commands (`make check`, pytest, ruff, read-only git) and wires
`scripts/rumbo_guard.py` as Claude Code hooks:

- **Before every `Edit`/`Write`/`Bash` call** it blocks edits to the Makefile, `pyproject.toml`
  (except through `uv add`), CI, `infra/`, the pre-commit config, the sensors and `.env`;
  removing a template mark; writing `[tests-change-approved]`; `--no-verify`; force pushes;
  `git reset --hard`; disabling git hooks; `rm -rf` on the root; shell redirections into
  protected files. The reason reaches the agent, which has to ask you.
- **On `Stop`**, if Python, TOML or JSONL files changed since the last commit, it runs
  `make check-brief` and refuses to finish while it is red.

You are never blocked: edits by hand skip the hook, and `.claude/settings.local.json`
overrides any rule. `rumbo update` refreshes the guard; `CODEOWNERS` plus branch protection
make the same rules binding on GitHub.

## Rigor levels

| | prototype | mvp | production |
|---|---|---|---|
| `make check` | lint, types, test, arch (+evals) | + security, coverage report | same (coverage is informational: mutation testing is the sensor) |
| CI | check + sensor + gitleaks | + pre-commit on all files | + SBOM (CycloneDX from uv.lock) |
| Nightly | — | mutation (+ live evals) | mutation + live evals with k=3 |
| Dependabot | — | weekly, 7-day cooldown | same |
| Gates | PRD, threat model, C4, runbook optional; 20 real eval cases | all required; 50 cases; budget + telemetry wired | + fitness function per ADR, nightly, SBOM, every requirement traced to a test; 100 cases |

What matters is the worst plausible impact, not the size of the code. The full practice table
is in `RUMBO.md`, Part E.

## Security by design

- `make check` never needs a model credential; the real model runs only in the nightly
  `evals-live` job, in a GitHub environment you protect with required reviewers. With the
  `claude-code` provider no API key exists on any machine.
- Secrets: gitleaks over the full history in CI (the pre-commit hook only sees staged files),
  `.env` ignored, `detect-private-key`.
- Supply chain: `rumbo verify-dep` before adding a package (existence, age, repository,
  typosquatting against popular names, OSV), `pip-audit` and `bandit` in `make check`,
  lockfile, Dependabot with cooldown, actions pinned by SHA, SBOM in production.
- LLM products: a threat model template with the lethal trifecta and OWASP LLM 2025 /
  Agentic 2026, typed least-privilege tools, schema validation of everything the model
  returns, no remote links or images in rendered output, a hard budget per request.
- The agent cannot change the sensors on its own (hooks, CODEOWNERS).

## Working within the context limit

There is no safe token threshold: quality degrades from the first few thousand tokens and bug
fixing collapses long before the advertised window (`docs/method.md` §3.1, full report in
`reports/`). The harness keeps units of work small: one spec per fresh session, specs with
explicit data contracts and file ownership, `make check-brief` so only failures reach the
agent's context, `# Compact instructions` in AGENTS.md, decisions in files, and a one-writer
rule with read-only parallel agents. Parallelize at the level of specs (3–5 worktrees, small
PRs merged in series), not inside them. `rumbo metrics` tells you how many sessions each spec
takes; above two, split it.

## Reviewing without drowning: `rumbo tour`

Agent-written changes are cheap to produce and expensive to understand. `rumbo tour` builds,
from the diff of a git range, a per-commit guide for the human: intent and kind (behavioral,
structural, tests, docs), **risk hotspots first** (CI, secrets, dependencies, weakened tests),
a **reading order** (tests first, never alphabetical), a budget of ~300 lines per sitting, the
commands that verify the change and **three explanation-test questions** with the hints folded
away. `/rumbo-tour` asks Claude Code to replace the template questions with real ones written
from the diff; `/rumbo-review` is the independent second review by a fresh session, starting
from the `rumbo trace` table. Design rationale in `docs/method.md`, section 3.

## Updating a project's harness

```bash
rumbo doctor           # warns when the harness is older than the CLI
rumbo update --diff    # what would change: owned files replaced, harness files that drifted
rumbo update           # replaces only framework-owned files (RUMBO.md, scripts/, .claude/commands, 0000 templates)
```

`update` never touches the documents you edited (brief, specs, ADRs…). For `Makefile`,
`pyproject.toml`, CI or `AGENTS.md` it only tells you they differ from the current template;
`--diff` shows how, so you merge by hand.

## Developing the framework

```bash
uv sync
make check   # lint, types and the CLI test suite
make e2e     # mounts real projects in several configurations and requires their `make check`
make review  # guided review of your branch
```

Templates live in `src/rumbo/templates/`: `.tmpl` files are rendered (`{{var}}`,
`{{#flag}}…{{/flag}}`, `{{^flag}}…{{/flag}}`), `__pkg__` becomes the package name and `dot_`
becomes `.`. The canonical sensors (anti-cheat and guard hook) are in `src/rumbo/sensors/`.
The framework's own decisions are ADRs in `docs/adr/`. CI runs on Python 3.11–3.13 (Linux,
macOS, Windows) plus the e2e matrix; a `vX.Y.Z` tag triggers the release workflow (GitHub
Release from the CHANGELOG, optional PyPI trusted publishing). See
[CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## Glossary

- **Harness:** everything that surrounds the agent so it can self-correct: guides (AGENTS.md,
  ADRs, specs) and sensors (tests, linters, contracts, evals, hooks, CI).
- **Sensor / guide:** feedback that catches a mistake / feed-forward that prevents it.
- **ANCLA:** the per-change loop (Anchor, Navigate, Close the loop, Limit, Learn And measure).
- **Gate (G1–G5):** the exit criterion of each phase; automatic checks plus a checklist the
  human signs.
- **Part A / A0:** the one-time setup of the harness (`RUMBO.md`), starting with four questions
  (name, LLM or not, rigor, use case).
- **Template mark:** the first line `<!-- rumbo:template … -->` of every generated document;
  the human deletes it when the document is complete, and `rumbo gate` checks it.
- **Rigor:** `prototype`, `mvp` or `production`; it scales what `make check`, CI and the gates
  demand.
- **Requirement id:** `F-NNNN.Rk`, spec number plus requirement line; a test covers it by
  mentioning the id.
- **Explanation test:** before a merge, the human explains the change in three sentences
  without reading the agent's summary.

## License

[MIT](LICENSE).
