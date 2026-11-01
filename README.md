# RUMBO

Framework de punta a punta para construir software con agentes de IA, con **ANCLA** como núcleo.

**R**econocer → **U**nificar → **M**odelar → **B**uild con ANCLA → **O**perar/Observar/Optimizar,
con una **brújula humana** transversal. ANCLA es el bucle de cada cambio: **A**cotar en
verificadores → **N**avegar con plan → **C**errar el ciclo en sandbox → **L**imitar y revisar →
**A**prender y medir.

> La IA amplifica la disciplina que existe; no la reemplaza. Por eso RUMBO invierte en
> **verificadores ejecutables** (tests, propiedades, tipos, contratos de arquitectura, evals) y no
> en prompts.

## Qué contiene

| Pieza | Para qué |
|---|---|
| [`RUMBO.md`](RUMBO.md) | Instrucciones para Claude Code (Partes A–F). Funciona solo, sin el CLI. |
| CLI `rumbo` | Monta el arnés en cualquier proyecto Python y automatiza gates y plantillas. |
| [`docs/metodo.md`](docs/metodo.md) | El porqué: evidencia, fases, RACI, stack, rigor, anti-patrones, adopción. |
| [`docs/ejemplos/agente-xm.md`](docs/ejemplos/agente-xm.md) | Caso aplicado: agente sobre el mercado eléctrico colombiano (XM). |

## Uso rápido

Requisitos: [uv](https://docs.astral.sh/uv/) y git.

```bash
mkdir mi-proyecto && cd mi-proyecto && git init
uvx --from git+https://github.com/padillacm/RUMBO rumbo init      # pregunta lo de A0
make check                                                        # debe quedar en verde
git add -A && git commit -m "chore: arnés RUMBO inicial"
```

Luego abre Claude Code en el proyecto y escribe: *"Lee AGENTS.md y RUMBO.md; empecemos la fase R"*
(o usa los comandos `/rumbo-r`, `/rumbo-u`, `/rumbo-m`, `/rumbo-ancla`, `/rumbo-revisar`,
`/rumbo-o`, `/rumbo-gate`).

Sin el CLI: copia `RUMBO.md` a la raíz del repositorio y pide a Claude Code *"Lee RUMBO.md y
ejecuta la Parte A"*.

Instalación permanente: `uv tool install git+https://github.com/padillacm/RUMBO`.

### `rumbo init` no interactivo

```bash
rumbo init --yes --nombre "Agente XM" --pkg agente_xm \
  --llm --proveedor anthropic --rigor mvp --caso "agente sobre datos de XM"
```

| Opción | Valores |
|---|---|
| `--llm` / `--no-llm` | con LLM añade `evals/`, `prompts/`, API FastAPI y el SDK del proveedor |
| `--proveedor` | `anthropic`, `openai`, `google`, `mistral`, `otro` (con `--sdk-paquete`/`--sdk-modulo`) |
| `--rigor` | `prototipo`, `mvp`, `produccion` (ajusta `make check`, CI, nightly y Dependabot) |
| `--no-install` | solo escribe archivos; no ejecuta `uv add` ni pre-commit |
| `--force` | sobrescribe archivos existentes (por defecto se omiten y se reportan) |

En un proyecto existente no sobrescribe nada: fusiona las secciones `[tool.*]` que falten en
`pyproject.toml` y las líneas que falten en `.gitignore`, y reporta lo omitido.

## Comandos

```bash
rumbo init                          # monta el arnés (Parte A)
rumbo new adr "Workflow vs agente"  # docs/adr/0001-workflow-vs-agente.md
rumbo new spec "Consulta de precio" # specs/0001-consulta-de-precio.md (EARS)
rumbo new postmortem "Cifra errónea"
rumbo decision "Usar DuckDB" --porque "series de tiempo"   # docs/decisiones.md
rumbo gate G1                       # G1..G5: sensores automáticos + checklist humano
rumbo doctor                        # estructura completa, AGENTS.md ≤ 100 líneas
rumbo check-tests                   # sensor anti-trampa (tests no debilitados)
rumbo verify-dep pydataxm           # existe en PyPI, antigüedad, repositorio
```

Los documentos generados empiezan con la marca `<!-- rumbo:plantilla … -->`; el humano la borra
cuando el documento está completo y `rumbo gate` lo verifica.

## Qué genera en el proyecto

- `AGENTS.md` (mapa ≤100 líneas), `CLAUDE.md` (`@AGENTS.md`), `RUMBO.md`.
- `Makefile` con `make check` como único comando de verificación: ruff, pyright (estricto en
  `domain/`), pytest + Hypothesis, import-linter, bandit + pip-audit y evals smoke según el caso.
- Capas hexagonales `src/<pkg>/{domain,app,adapters,api}` con contratos de import-linter y un test
  de arquitectura que impide I/O en `domain/` e importar `spikes/`.
- `scripts/check_tests_not_weakened.py`: falla si se borran tests o archivos de `tests/` o bajan
  los `assert`, salvo `[tests-change-approved]` en el commit (lo pone el humano).
- `evals/` (si hay LLM): JSONL, graders deterministas (numérico con tolerancia, igualdad de campos,
  argumentos de tool calls, abstención) y `run_evals.py` con pass@1 y pass^k.
- CI (acciones fijadas por SHA), nightly con mutmut y evals completas, Dependabot, pre-commit con
  gitleaks.
- Plantillas: brief, PRD, ADR (MADR + fitness function), spec EARS, C4, threat model (STRIDE,
  OWASP, trifecta letal), runbook, SLOs, postmortem, plan de evals, diario de decisiones.
- Comandos de Claude Code en `.claude/commands/`.

## Desarrollo del framework

```bash
uv sync
make check   # lint, tipos y tests del CLI
make e2e     # monta proyectos reales en varias configuraciones y exige `make check` en verde
```

Las plantillas viven en `src/rumbo/templates/`: los archivos `.tmpl` se renderizan (`{{var}}`,
`{{#flag}}…{{/flag}}`, `{{^flag}}…{{/flag}}`), `__pkg__` se reemplaza por el paquete y `dot_` por
`.`. El sensor anti-trampa canónico está en `src/rumbo/sensors/`.
