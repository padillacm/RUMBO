# RUMBO

An end-to-end framework for building software with AI coding agents, with **ANCLA** at its
core.

**R**ecognize → **U**nify → **M**odel → **B**uild with ANCLA → **O**perate/Observe/Optimize,
with a transversal **human compass**. ANCLA is the loop of every change: **A**nchor in
verifiers → **N**avigate with a plan → **C**lose the loop in a sandbox → **L**imit and review →
Learn **A**nd measure.

> AI amplifies the discipline that already exists; it does not replace it. RUMBO therefore
> invests in **executable verifiers** (tests, properties, types, architecture contracts, evals)
> and in keeping the human in charge of the *what* and the *why* — not in prompts.

## Glossary

- **Harness:** everything that surrounds the agent so it can self-correct: guides (AGENTS.md,
  ADRs, specs) and sensors (tests, linters, contracts, evals, CI).
- **Sensor / guide:** feedback that catches a mistake / feed-forward that prevents it.
- **Gate (G1–G5):** the exit criterion of each phase; automatic checks plus a checklist the
  human signs.
- **Part A / A0:** the one-time setup of the harness (`RUMBO.md`), starting with four questions
  (name, LLM or not, rigor, use case).
- **Template mark:** the first line `<!-- rumbo:template … -->` of every generated document;
  the human deletes it when the document is complete, and `rumbo gate` checks it.
- **Rigor:** `prototype`, `mvp` or `production`; it scales what `make check`, CI and the gates
  demand (`RUMBO.md`, Part E).

## What is in this repository

| Piece | Purpose |
|---|---|
| [`RUMBO.md`](RUMBO.md) | Instructions for Claude Code (Parts A–F). Works on its own, without the CLI. |
| `rumbo` CLI | Mounts and updates the harness in any Python project; automates gates, templates, dependency checks and guided reviews. |
| [`docs/method.md`](docs/method.md) | The why: evidence, phases, review design, RACI, stack, rigor, anti-patterns, adoption plan. |
| [`docs/examples/xm-agent.md`](docs/examples/xm-agent.md) | Worked example: an agent over the Colombian electricity market (XM). |

## Quick start

Requirements: Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), git and `make` (on Windows use
Git Bash or WSL).

```bash
mkdir my-project && cd my-project && git init
uvx --from git+https://github.com/padillacm/RUMBO rumbo init      # asks the A0 questions
make check                                                        # must be green (offline)
git add -A && git commit -m "chore: initial RUMBO harness"
```

Then open Claude Code in the project and say *"Read AGENTS.md and RUMBO.md; let's start
phase R"*, or use the commands `/rumbo-r`, `/rumbo-u`, `/rumbo-m`, `/rumbo-ancla`,
`/rumbo-review`, `/rumbo-o`, `/rumbo-gate` and `/rumbo-tour`.

Without the CLI: copy `RUMBO.md` to the repository root and ask Claude Code to *"Read RUMBO.md
and run Part A"*.

Permanent install: `uv tool install git+https://github.com/padillacm/RUMBO`. Pin a release once
one is tagged: `…/RUMBO@v0.2.0`.

### Non-interactive `rumbo init`

```bash
rumbo init --yes --name "XM Agent" --package xm_agent \
  --llm --provider claude-code --rigor mvp --case "agent over XM market data"
```

| Option | Values |
|---|---|
| `--llm` / `--no-llm` | LLM products get `evals/`, `prompts/`, a FastAPI skeleton, the offline guard and the provider SDK |
| `--provider` | `anthropic`, `openai`, `google`, `mistral`, `claude-code` (the Claude Code CLI with your own login: no API key), `other` (with `--sdk-package`, `--sdk-module`, `--sdk-env-key`) |
| `--rigor` | `prototype`, `mvp`, `production` (scales `make check`, CI, nightly, Dependabot and the gates) |
| `--no-install` | write files only; skip `uv add` and pre-commit |
| `--force` | overwrite existing files (keeps `.bak` copies). `pyproject.toml` and `.gitignore` are always merged, never replaced |

In an existing project nothing is overwritten by default: the missing `[tool.*]` sections are
merged into `pyproject.toml`, missing lines into `.gitignore`, and everything skipped is
reported.

## Commands

```bash
rumbo init                           # mount the harness (Part A)
rumbo update                         # refresh RUMBO.md, .claude/commands, scripts/ and the 0000 templates
rumbo new adr "Workflow vs agent"    # docs/adr/0001-workflow-vs-agent.md
rumbo new spec "Spot price query"    # specs/0001-spot-price-query.md (EARS)
rumbo new postmortem "Wrong figure"
rumbo decision "Use DuckDB" --why "time series"     # docs/decisions.md
rumbo gate G1                        # G1..G5: automatic sensors (by rigor) + the human checklist
rumbo doctor                         # structure complete, AGENTS.md ≤ 100 lines, harness up to date
rumbo check-tests                    # anti-cheat sensor (tests not weakened)
rumbo verify-dep pydataxm            # exists on PyPI, age, repository, typosquatting, OSV
rumbo tour origin/main               # guided, pedagogical review of the branch
```

Inside a generated project the CLI is available as `make rumbo ARGS="gate G4"` and the guided
review as `make review`.

### Reviewing without drowning: `rumbo tour`

Agent-written changes are cheap to produce and expensive to understand. `rumbo tour` builds,
from the diff of a git range, a per-commit guide for the human: intent and kind (behavioral,
structural, tests, docs), **risk hotspots first** (CI, secrets, dependencies, weakened tests),
a **reading order** (tests first, never alphabetical), a budget of ~300 lines per sitting, the
commands that verify the change and **three explanation-test questions** with the hints folded
away. The design follows the evidence on reviewer attention and on learning with AI; see
`docs/method.md`, section 3. `/rumbo-tour` asks Claude Code to replace the template questions
with real ones written from the diff.

## What `rumbo init` generates

- `AGENTS.md` (a map, ≤100 lines), `CLAUDE.md` (`@AGENTS.md`), `RUMBO.md`.
- A `Makefile` whose `make check` is the single, **offline** verification command: ruff,
  pyright (strict in `domain/`), pytest + Hypothesis, import-linter, bandit + pip-audit and the
  smoke evals, scaled by rigor.
- Hexagonal layers `src/<pkg>/{domain,app,adapters,api}` with import-linter contracts and an
  architecture test that forbids I/O in `domain/` and imports from `spikes/`.
- `scripts/check_tests_not_weakened.py`: fails when tests or files are deleted, asserts drop,
  always-true asserts or skip markers appear, or `parametrize` lists shrink — unless a human
  approves it (`[tests-change-approved]` in a human-authored commit, or the PR label).
- `evals/` (LLM products): JSONL datasets, deterministic graders (numeric with tolerance, field
  equality, tool arguments, abstention) and `run_evals.py` with pass@1 and pass^k, offline
  unless `--live`.
- CI with actions pinned by SHA, a full-history gitleaks scan, the sensor, pre-commit on all
  files (mvp+) and an SBOM from `uv.lock` (production); a nightly with mutation testing and
  live evals in a protected environment — the only place a model credential lives.
- Templates: brief, PRD, ADR (MADR + fitness function), EARS spec, C4, threat model (STRIDE,
  lethal trifecta, OWASP LLM/Agentic checklist), runbook, SLOs, postmortem, eval plan,
  decisions log, `.env.example`.
- Claude Code commands in `.claude/commands/`.

## Updating a project's harness

```bash
rumbo doctor    # warns when the harness is older than the CLI
rumbo update    # replaces only framework-owned files; lists the others that differ
```

`update` never touches the documents you edited (brief, specs, ADRs…). For `Makefile`,
`pyproject.toml`, CI or `AGENTS.md` it only tells you they differ from the current template:
compare and merge by hand.

## Developing the framework

```bash
uv sync
make check   # lint, types and the CLI test suite
make e2e     # mounts real projects in several configurations and requires their `make check`
make review  # guided review of your branch
```

Templates live in `src/rumbo/templates/`: `.tmpl` files are rendered (`{{var}}`,
`{{#flag}}…{{/flag}}`, `{{^flag}}…{{/flag}}`), `__pkg__` becomes the package name and `dot_`
becomes `.`. The canonical anti-cheat sensor is in `src/rumbo/sensors/`. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE).
