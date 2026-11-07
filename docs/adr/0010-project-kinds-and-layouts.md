# ADR-0010: Project kinds and layouts; the LLM capability is orthogonal to both
Status: accepted    Date: 2026-11-07

## Context
`Config` pivots on one boolean, `llm`. Nineteen template paths are conditioned on it, so
`--no-llm` drops the HTTP skeleton together with the model code; `evals/` exists only for LLM
products although data pipelines need datasets and deterministic graders just the same;
`pyproject.toml` hard-codes `src/<pkg>/{domain,app,adapters,api}` in import-linter, pyright and
mutmut; G3 demands two contracts and G5 a Dockerfile, a runbook and SLOs for every project. A
library, a CLI or a pipeline cannot be generated or audited, and RUMBO itself (a flat CLI) is the
first counter-example. The guard rail from the expert review and the community study: keep the
four A0 questions, expose the rest as flags and `[tool.rumbo]` keys, and never let a scaffolder
grow into a twenty-option form (the Cookiecutter lesson).

## Drivers (non-functional requirements)
Four questions by default; the artefact type separated from the LLM capability; every existing
project loads unchanged (`kind = service`, `layout = hexagonal` inferred, never an error); each
kind gets only what it needs, and the gates ask only for what that kind can have; architecture
contracts stay few and coarse (the gauge-sh/tach lesson); standard library only.

## Options considered
1. Keep one archetype and document how to delete what does not apply (status quo).
2. One template tree per kind: four copies of the harness to keep in sync.
3. Two orthogonal axes on the same template tree: `kind` decides what the project is
   (service, library, cli, data) and `layout` how its package is organised (hexagonal, layered,
   flat), with `llm` as a capability on top; conditions and gates read the axes.

## Decision and rationale
Option 3. The contract:

**Axes.** `[tool.rumbo].kind ∈ {service, library, cli, data}`, default `service`.
`[tool.rumbo].layout ∈ {hexagonal, layered, flat}`, default derived from the kind: `service →
hexagonal`, `library`, `cli`, `data → flat`. `[tool.rumbo].layers` is a list of snake_case
package names from the outermost to the innermost, required (≥ 2) when `layout = layered`,
forbidden otherwise. `llm` stays a boolean; an LLM product forces `layout = hexagonal` because
the LLM skeleton lives in `app/` and `adapters/` (a `cli` or `data` project with `--llm` is
therefore hexagonal). `evals` is a derived property: `llm or kind == "data"`. `arch` is a
derived property: `layout != "flat"`. The innermost layer (`domain` for hexagonal, the last
entry of `layers` for layered) is the pure one: pyright strict, mutmut `only_mutate`, the
import-linter "pure" contract and the architecture test's purity rules apply to it.

**A0.** Question 2 becomes "What are you building? service / library / cli / data" (default
`service`); "Does it call an LLM?" is its sub-question, as the provider already is. `layout` and
`layers` are never asked: `--layout`, `--layers a,b,c` or the keys in `[tool.rumbo]`. Interactive
`rumbo init` still asks four numbered questions.

**What each kind generates and requires.**

| | service | library | cli | data |
|---|---|---|---|---|
| Package | layout dirs | `__init__` with `__all__`, `__version__` | same + `__main__.py` + `[project.scripts]` | layout dirs |
| HTTP | `api/http.py` + `tests/integration/test_http.py` always (health endpoint; JSON logging only with `llm`); `fastapi`, `uvicorn`, `httpx` as runtime deps | – | – | – |
| Evals | with `llm` | – | with `llm` | always (`evals/`, numeric graders; `judge.py` only with `llm`) |
| Ops | `infra/Dockerfile`, `docs/ops/runbook.md`, `docs/ops/slos.md` | `CHANGELOG.md`, `.github/workflows/release.yml` (Trusted Publishing, no token), CI matrix on ≥ 2 Python versions | same as library | `infra/Dockerfile`, `docs/ops/runbook.md` |
| G5 | Dockerfile, runbook, SLOs, dependency updates, (llm) budget + telemetry, (production) SBOM | CHANGELOG with an `Unreleased` section, release workflow without a PyPI token secret, CI matrix, dependency updates, (production) SBOM | same as library | runbook, Dockerfile (recommended, not required), dependency updates, datasets present (`evals/datasets/*.jsonl` beyond the examples), (production) SBOM |

G1, G2 and G4 do not change, except that G2 counts eval cases whenever `evals` is on. G3 asks
for the two import-linter contracts only when `arch` is on; for `flat` it asks that
`tests/architecture/test_architecture.py` exists (the spikes rule still applies). The "workflow
vs. agent" ADR and the OWASP checklist stay `llm`-only.

**What each layout generates.** `hexagonal`: `src/<pkg>/{domain,app,adapters,api}/__init__.py`,
the layers contract with the four fixed layers, the purity contract on `domain`.
`layered`: `src/<pkg>/<layer>/__init__.py` for each entry of `layers` (added by `plan_files`,
not by templates), the layers contract with those entries in order, the purity contract on the
last one. `flat`: `src/<pkg>/__init__.py` only; no import-linter section, no `arch` step in
`[tool.rumbo.checks]`, `import-linter` not installed. The architecture test keeps the spikes
rule for every layout and the purity rules (ADR-0007) only when `arch` is on. Templates receive
precomputed strings (`layers_toml`, `inner_layer`, flags `service`, `library`, `cli`, `data`,
`hexagonal`, `layered`, `flat`, `evals`, `arch`), since the template engine has no lists.

**Migration.** `Project.load` reads `kind`, `layout`, `layers` with the defaults above and never
fails on their absence; `rumbo update` writes the missing keys into `[tool.rumbo]`; `rumbo
doctor` prints them in its summary line.

## Consequences (+/−)
+ A library, a CLI or a pipeline gets a harness that fits, and the gates stop asking for a
  Dockerfile it will never have.
+ `--no-llm` keeps the HTTP skeleton for a service; `data` gets evals without a model.
+ Still four questions; everything else is a flag or a key.
− `layered` relies on the user naming the layers; a wrong order gives a wrong contract.
− `llm` forcing `hexagonal` closes the door to an LLM skeleton in a flat package; revisit when
  the skeleton no longer imports `app/` and `adapters/`.
− Monorepos (`uv` workspaces, `packages/*`) are not covered: they break the one-pyproject,
  one-package assumption of `Project` and need their own ADR.

## Fitness function
`tests/test_scaffold.py`: for every kind, the plan contains exactly what the table says and
nothing of the other columns (no `infra/` or `api/` for a library, no CHANGELOG for a service,
`evals/` for `data` without `llm`, `[project.scripts]` for `cli`); for every layout, the
generated `pyproject.toml` has the expected import-linter contracts or none, the expected
`strict`/`only_mutate` target, and `[tool.rumbo.checks]` has `arch` only when `arch` is on;
`layers` validation (≥ 2, snake_case, only with `layered`); `llm` forces `hexagonal`.
`tests/test_cli.py`: `Project.load` without the keys infers service/hexagonal; `rumbo update`
writes them; a generated `--kind cli --layout flat --no-llm --rigor production` project passes
`rumbo doctor` and `rumbo gate G1…G5 --no-run` without a failed result; G5 per kind. The e2e
matrix gains `--kind library --no-llm --rigor mvp` and `--kind cli --layout flat --no-llm --rigor
production`.

## Reversibility: medium
The axes are additive and default to today's archetype; removing them means deleting the
conditions and the three extra templates, and projects keep working with the defaults.
