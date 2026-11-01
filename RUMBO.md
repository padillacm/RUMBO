# RUMBO — Instrucciones para Claude Code

> **Cómo usar este archivo**
>
> 1. Pon `RUMBO.md` en la raíz de un repositorio nuevo (o del proyecto que quieras ordenar).
>    Si instalaste el CLI, `rumbo init` lo copia por ti junto con todo el arnés.
> 2. Abre Claude Code en esa carpeta y escribe: **"Lee RUMBO.md y ejecuta la Parte A."**
> 3. Claude Code te hará unas preguntas, montará el arnés y se detendrá en cada punto de aprobación.
>
> RUMBO es un framework para construir software con agentes de IA de punta a punta:
> **R**econocer → **U**nificar → **M**odelar → **B**uild con ANCLA → **O**perar/Observar/Optimizar,
> con una "brújula humana" transversal. ANCLA es el bucle de cada cambio:
> **A**cotar en verificadores → **N**avegar con plan → **C**errar el ciclo en sandbox →
> **L**imitar y revisar → **A**prender y medir.
>
> Principio rector: la IA amplifica la disciplina que existe; no la reemplaza. Por eso el foco
> está en **verificadores ejecutables** (tests, propiedades, tipos, contratos de arquitectura,
> evals) y no en prompts.

---

## Instrucciones para Claude (léelas antes de actuar)

- Eres el agente de implementación. El humano (dueño del proyecto) decide el **qué** y el
  **por qué**; tú propones, implementas y verificas.
- Trabaja **por pasos**. Al final de cada paso marcado con 🛑, detente, resume lo que hiciste
  en ≤5 líneas y espera aprobación.
- Nunca borres ni debilites tests para que pasen. Si un test parece incorrecto, detente y pregunta.
- No añadas dependencias que no estén listadas aquí sin pedir permiso y verificar que el paquete
  existe en PyPI (nombre exacto, repositorio oficial, antigüedad): `rumbo verify-dep <paquete>`.
- Usa siempre las versiones estables más recientes de cada herramienta; no copies números de
  versión de memoria. Para pre-commit, ejecuta `pre-commit autoupdate` tras crear la configuración.
- Si algo de este archivo choca con la realidad del entorno (una herramienta no instala, una
  opción cambió), dilo y propón la alternativa más cercana; no lo ocultes.

---

## PARTE A — Montar el arnés (una vez por repositorio)

### A0. Preguntas iniciales 🛑

Pregunta al humano (todas juntas, en un solo mensaje):

1. Nombre del proyecto y del paquete Python (snake_case).
2. ¿Es un producto con LLM? (sí/no). Si sí: ¿qué proveedor de modelo?
3. Nivel de rigor: `prototipo`, `mvp` o `produccion` (ver Parte E).
4. ¿Hay un caso concreto? (por ejemplo: agente conversacional sobre datos del mercado eléctrico
   colombiano de XM).

No continúes sin respuestas. Usa `<pkg>` para referirte al paquete en adelante.

### A1–A7. Ruta rápida: el CLI `rumbo`

Si `uv` está disponible, monta todo el arnés (A1–A7) con un comando, usando las respuestas de A0:

```bash
uvx --from git+https://github.com/padillacm/RUMBO rumbo init --yes \
  --nombre "<proyecto>" --pkg <pkg> --rigor <rigor> \
  [--llm --proveedor anthropic|openai|google|mistral | --no-llm] [--caso "<caso>"]
```

`rumbo init` crea la estructura, instala las dependencias con `uv add` (últimas versiones
estables), ejecuta `pre-commit autoupdate` e `install`, y anota las respuestas de A0 en
`docs/decisiones.md`. Si un archivo ya existía no lo sobrescribe (fusiona `pyproject.toml` y
`.gitignore`) y lo reporta. Revisa la salida, explica al humano lo que se omitió o falló y salta a
**A8**. Si no puedes usar el CLI, sigue A1–A7 a mano.

### A1. Estructura del repositorio

```
.
├── AGENTS.md                 # contenido de la Parte B
├── CLAUDE.md                 # una sola línea: @AGENTS.md
├── RUMBO.md                  # este archivo
├── README.md
├── Makefile
├── pyproject.toml
├── .pre-commit-config.yaml
├── .gitignore
├── .github/workflows/ci.yml
├── .github/workflows/nightly.yml     # rigor mvp o produccion
├── .github/dependabot.yml            # rigor mvp o produccion
├── .claude/commands/rumbo-*.md       # /rumbo-r, /rumbo-u, /rumbo-m, /rumbo-ancla, /rumbo-o…
├── docs/
│   ├── product/brief.md
│   ├── product/prd.md
│   ├── adr/0000-plantilla.md
│   ├── architecture/c4.md
│   ├── security/threat-model.md
│   ├── ops/runbook.md
│   ├── ops/slos.md
│   ├── postmortems/0000-plantilla.md
│   ├── evals/plan.md         # solo si hay LLM
│   └── decisiones.md         # diario de decisiones humanas (brújula)
├── specs/0000-plantilla.md
├── src/<pkg>/
│   ├── __init__.py
│   ├── domain/__init__.py    # lógica pura: sin I/O, sin SDKs de LLM, sin HTTP
│   ├── app/__init__.py       # casos de uso; depende solo de domain
│   ├── adapters/__init__.py  # APIs externas, BD, LLM
│   ├── prompts/__init__.py   # prompts versionados (solo si hay LLM)
│   └── api/__init__.py       # FastAPI / CLI
├── tests/
│   ├── unit/
│   ├── property/
│   ├── integration/
│   └── architecture/
├── evals/                    # solo si hay LLM
│   ├── datasets/smoke.jsonl
│   ├── graders/__init__.py
│   ├── error_analysis/.gitkeep
│   └── run_evals.py
├── scripts/check_tests_not_weakened.py
├── spikes/README.md          # "Código desechable. Nunca se importa desde src/."
└── infra/Dockerfile
```

Los documentos de `docs/` y `specs/` empiezan con la marca `<!-- rumbo:plantilla … -->`. El humano
la borra cuando el documento está completo; `rumbo gate` lo verifica.

### A2. Entorno y herramientas (uv)

- Inicializa con `uv init --package` (o adapta si ya existe) y añade dependencias de desarrollo:
  `ruff`, `pyright`, `pytest`, `pytest-cov`, `hypothesis`, `import-linter`, `pre-commit`,
  `pip-audit`, `bandit`, `mutmut`.
- Si hay LLM, añade (runtime): `pydantic`, `fastapi`, `httpx` y el SDK oficial del proveedor
  elegido. Observabilidad (`opentelemetry-sdk`, Langfuse o Phoenix) se añade en la fase O, no ahora.

En `pyproject.toml` configura como mínimo:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "S", "RUF"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]

[tool.pyright]
include = ["src", "tests"]
typeCheckingMode = "standard"
strict = ["src/<pkg>/domain"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"

[tool.importlinter]
root_package = "<pkg>"
include_external_packages = true

[[tool.importlinter.contracts]]
name = "Capas hexagonales"
type = "layers"
layers = ["<pkg>.api", "<pkg>.adapters", "<pkg>.app", "<pkg>.domain"]

[[tool.importlinter.contracts]]
name = "El dominio es puro"
type = "forbidden"
source_modules = ["<pkg>.domain"]
forbidden_modules = ["httpx", "requests", "fastapi"]   # añade aquí el SDK del LLM (p. ej. "anthropic")
```

(Ajusta las claves si la versión instalada de una herramienta las cambió; verifica ejecutándola.)

### A3. Makefile: un único comando de verificación

```make
.PHONY: check test lint types arch security evals mutation

lint:      ; uv run ruff check . && uv run ruff format --check .
types:     ; uv run pyright
test:      ; uv run pytest
arch:      ; uv run lint-imports
security:  ; uv run bandit -q -r src && uv run pip-audit --skip-editable
evals:     ; uv run python evals/run_evals.py --suite smoke
mutation:  ; uv run mutmut run && uv run mutmut results

check: lint types test arch security
```

Si el proyecto tiene LLM, añade `evals` a `check`. Ajusta `check` al rigor (Parte E).

### A4. pre-commit

Hooks: `ruff-check` y `ruff-format` (astral-sh/ruff-pre-commit), `gitleaks`,
`check-added-large-files`, `end-of-file-fixer`, y un hook local que ejecute `uv run lint-imports`.
Luego `pre-commit autoupdate` y `pre-commit install`.

### A5. CI (GitHub Actions)

- `ci.yml` (en cada push y PR): checkout con `fetch-depth: 0` → `astral-sh/setup-uv` →
  `uv sync --locked` → `make check` → `uv run python scripts/check_tests_not_weakened.py --strict`.
  Acciones fijadas por SHA.
- `nightly.yml` (cron diario): `make mutation` sobre `src/<pkg>/domain` y, si hay LLM, la suite
  completa de evals.

### A6. Sensor anti-trampa: `scripts/check_tests_not_weakened.py`

Script que, comparando contra `origin/main` (o el commit base del PR):

- falla si se eliminan funciones `def test_` o archivos en `tests/`;
- falla si disminuye el número total de `assert` en `tests/`;
- permite saltarse la regla solo si el mensaje del último commit contiene
  `[tests-change-approved]` (que pone el humano, nunca tú).

Tiene sus propios tests (`tests/unit/test_check_tests_not_weakened.py`).

### A7. Esqueleto de evals (solo si hay LLM)

`evals/run_evals.py`:

- lee casos JSONL (`{"id", "input", "expected", "grader", "tags"}`) desde
  `evals/datasets/<suite>.jsonl`;
- ejecuta el sistema real (función de entrada en `src/<pkg>/app`, por defecto
  `<pkg>.app.entrypoint:responder`) para cada caso, `k` veces (`--k`, por defecto 1; usa 3 para
  medir consistencia);
- aplica graders **deterministas** de `evals/graders/` (exactitud numérica con tolerancia,
  igualdad de campos, aserciones sobre argumentos de tool calls, abstención cuando falta el dato);
- reporta `pass@1` y `pass^k` (un caso pasa solo si las `k` ejecuciones pasan) y sale con código
  ≠ 0 si `pass^k` cae por debajo del umbral (`--min-pass`).

Los jueces LLM se añaden **después** del primer análisis de errores y solo validados contra
etiquetas humanas.

### A8. Verificación final de la Parte A 🛑

Ejecuta `make check` y muestra el resultado. Debe quedar en verde con un test trivial de ejemplo
en cada carpeta de `tests/`. Ejecuta `rumbo doctor` si está disponible. Haz un commit:
`chore: arnés RUMBO inicial`. Detente y resume.

---

## PARTE B — Contenido de `AGENTS.md`

`rumbo init` genera `AGENTS.md` con este esquema (ajustado a `<pkg>`, al rigor y a si hay LLM):

```markdown
# AGENTS.md — mapa del proyecto (léeme antes de cada tarea)

## Mapa
- Producto: docs/product/brief.md, docs/product/prd.md
- Specs de features (EARS): specs/
- Decisiones de arquitectura: docs/adr/  (léelas antes de cualquier decisión estructural)
- Capas: src/<pkg>/{domain,app,adapters,api}; contratos en pyproject.toml [tool.importlinter]
- Seguridad: docs/security/threat-model.md
- Evals: evals/ (solo productos LLM)

## Antes de declarar una tarea terminada
make check   # debe estar en verde; pega la salida resumida

## Flujo por tarea (ANCLA)
1. Lee la spec y los ADRs relevantes.
2. Propón un plan corto (archivos, tests, riesgos) y espera aprobación.
3. Escribe primero los tests (deben fallar), luego el código mínimo, luego refactoriza.
4. Itera hasta que `make check` pase.
5. Resume el cambio en 3 frases y lista dependencias nuevas (si las hay).

## Reglas duras
- Nunca borres, saltes ni debilites tests. Si un test parece incorrecto, detente y pregunta.
- Separa commits estructurales (refactor) de commits de comportamiento.
- Diffs pequeños (~300 líneas máximo). Si la tarea es más grande, propón dividirla.
- `domain/` es puro: sin I/O, sin HTTP, sin SDKs de LLM.
- Los cálculos numéricos que se muestran al usuario se hacen con código, nunca con el LLM.
- Prompts solo en src/<pkg>/prompts/, con identificador de versión.
- Ninguna dependencia nueva sin aprobación y verificación en PyPI.
- No toques infra/, secretos ni workflows de CI sin pedirlo.
- Nada de spikes/ se importa desde src/.
- Si cometes el mismo error dos veces, propón un test, regla de lint o contrato que lo atrape.
```

Mantén este archivo por debajo de ~100 líneas (`rumbo doctor` lo verifica). Es un mapa, no un
manual.

---

## PARTE C — El ciclo RUMBO (cómo trabajamos después del arnés)

Cuando el humano diga **"fase R"**, **"fase U"**, etc. (o use `/rumbo-r`, `/rumbo-u`, `/rumbo-m`,
`/rumbo-ancla`, `/rumbo-o`), sigue la sección correspondiente. Cada gate se evalúa con
`rumbo gate G1..G5` (chequeos automáticos + checklist que firma el humano).

### R — Reconocer (¿vale la pena construirlo?)

- El humano escribe primero `docs/product/brief.md`. Tú lo criticas: supuestos débiles, métrica
  ausente, alternativas actuales.
- Propón guiones de entrevista; el humano hace las entrevistas.
- Prototipos **solo en `spikes/`**. Lo que pasa a la siguiente fase es lo aprendido, no el código.
- En productos LLM: prueba de factibilidad con 20–30 preguntas reales, ejecutadas "a mano" con el
  modelo y los datos.
- 🛑 **Gate G1:** problema validado con usuarios o datos reales (≥5), métrica de éxito y criterio
  de abandono escritos.

### U — Unificar (¿qué debe hacer y cómo sabremos que lo hace?)

- PRD ligero con no-objetivos y requisitos no funcionales **con números** (latencia p95, costo por
  solicitud, disponibilidad).
- Specs en `specs/` (`rumbo new spec "<nombre>"`) con patrones EARS:
  - Ubicuo: "El sistema deberá…"
  - Evento: "Cuando…, el sistema deberá…"
  - Estado: "Mientras…, el sistema deberá…"
  - No deseado: "Si…, entonces el sistema deberá…"
  - Opcional: "Donde…, el sistema deberá…"
- Tu rol: interrogar la spec (ambigüedades, casos borde, contradicciones) y proponer tests de
  aceptación. El humano firma.
- Si hay LLM: dataset semilla de preguntas **reales** en `evals/datasets/` y plan de evals.
- 🛑 **Gate G2:** cada requisito tiene un test o eval planeado.

### M — Modelar (¿cómo lo estructuramos para cambiarlo con seguridad?)

- Monolito modular por defecto. ADRs en `docs/adr/` (`rumbo new adr "<decisión>"`, formato MADR,
  Parte D); **cada ADR con una fitness function** (contrato import-linter, test de arquitectura o
  umbral en CI).
- El humano esboza primero las decisiones irreversibles; tú completas opciones y trade-offs.
- Si hay LLM: ADR explícito **workflow vs. agente** (empezar por lo más simple: llamada única →
  workflow → agente solo si los evals lo exigen). Herramientas pocas, tipadas con Pydantic, de
  solo lectura salvo necesidad.
- Threat model de 1 hora: STRIDE por frontera, mapeo a OWASP Top 10 LLM 2025 y Agentic 2026, y
  prueba de la **trifecta letal** (datos privados + contenido no confiable + comunicación
  externa): documenta qué pata se elimina.
- 🛑 **Gate G3:** contratos en CI, threat model revisado, C4 niveles 1–2 en
  `docs/architecture/c4.md`.

### B — Build con ANCLA (cada PR)

- **A**cotar: tests de aceptación primero (Hypothesis para invariantes de dominio). El humano los
  aprueba.
- **N**avegar: plan corto antes de codificar.
- **C**errar: itera en sandbox hasta `make check` verde.
- **L**imitar: diff pequeño; revisión humana + una segunda revisión con sesión limpia o con otro
  modelo (`/rumbo-revisar`).
- **A**prender: errores repetidos → nuevo sensor o línea en AGENTS.md.
- 🛑 **Gate G4 (por PR):** CI verde, ningún test debilitado, dependencias verificadas, y el humano
  puede explicar el cambio en 3 frases sin leer tu resumen.

**Regla de oro del arnés:** si el agente comete el mismo error dos veces, se añade un sensor
(test, regla de lint, contrato) o una guía, además de corregir el código.

### O — Operar, Observar, Optimizar

- Despliegue en una PaaS (Fly.io, Railway, Render o Cloud Run) con imagen inmutable
  (`infra/Dockerfile`, tag = SHA) y **rollback ensayado**.
- OpenTelemetry + 2–3 SLOs en `docs/ops/slos.md`. Si hay LLM: trazas GenAI (modelo, tokens,
  costo, herramienta) hacia Langfuse o Phoenix, con versiones fijadas porque las convenciones aún
  están en desarrollo.
- Presupuesto de costo por solicitud, límites duros y alertas.
- `docs/ops/runbook.md` con una entrada por modo de fallo; postmortems sin culpables
  (`rumbo new postmortem "<título>"`) que respondan "¿qué sensor faltó?".
- Ritual semanal: el humano revisa 20–30 trazas (open coding); tú propones categorías; los fallos
  nuevos se vuelven casos de eval.
- Mantenimiento: Dependabot/Renovate semanal; limpieza quincenal con refactors Tidy First;
  documentación actualizada en el mismo PR.
- 🛑 **Gate G5:** rollback probado, SLOs y alertas activas, límites de costo configurados.

### Brújula humana (siempre)

- Ante decisiones de diseño, pide al humano su versión primero.
- Cuando el humano esté aprendiendo algo nuevo, explica y pregunta en lugar de resolver todo.
- No propongas usar agentes para: decisiones irreversibles, código de seguridad sin tests previos,
  o temas que el humano necesite dominar (p. ej., su tesis).
- Anota en `docs/decisiones.md` las decisiones que tomó el humano y por qué
  (`rumbo decision "<título>" --porque "<razón>"`).

---

## PARTE D — Plantillas

`rumbo init` crea estas plantillas; `rumbo new adr|spec|postmortem "<título>"` crea documentos
numerados a partir de ellas.

**`docs/product/brief.md`**

```
# Brief
Problema: <quién> no puede <qué> porque <causa>; hoy usa <alternativa>.
Evidencia: <entrevistas/datos, n=>
Métrica de resultado:
Supuestos riesgosos (valor / usabilidad / factibilidad / viabilidad):
Experimentos y criterio de éxito/fracaso:
No-objetivos:
Decisión G1: construir / iterar / abandonar
```

**`docs/adr/0000-plantilla.md`** (MADR)

```
# ADR-000N: <decisión>
Estado: propuesto | aceptado | reemplazado por ADR-XXXX    Fecha:
## Contexto
## Drivers (requisitos no funcionales)
## Opciones consideradas
## Decisión y razón
## Consecuencias (+/-)
## Fitness function
<contrato / test / umbral en CI que la hace cumplir>
## Reversibilidad: alta | media | baja
```

Los ADRs aceptados son inmutables: para cambiar una decisión se escribe uno nuevo que reemplaza al
anterior. Si la reversibilidad es baja, el humano decide sin un borrador previo del agente.

**`specs/0000-plantilla.md`**

```
# F-000N: <nombre>
Historia: Como <usuario> quiero <capacidad> para <beneficio>.
## Requisitos (EARS)
- R1 (evento): Cuando …, el sistema deberá …
- R2 (no deseado): Si …, entonces el sistema deberá …
## Criterios de aceptación
<tests pytest / propiedades Hypothesis que los verifican>
## Evals (si hay LLM): <IDs de casos, umbral>
## RNF: p95 < X s · costo < Y USD
## Fuera de alcance
```

**`docs/security/threat-model.md`**

```
# Threat model
Flujo de datos (diagrama Mermaid):
Activos:
Fronteras de confianza:
STRIDE por frontera:
OWASP LLM 2025 / Agentic 2026 aplicables:
Trifecta letal: ¿datos privados? ¿contenido no confiable? ¿comunicación externa?
Mitigaciones (cada una con su test):
Riesgos aceptados (firmados por el humano):
```

**`docs/ops/runbook.md`**

```
# Runbook
Dashboards:
## <Alerta / modo de fallo>
Síntoma · Diagnóstico (consulta de trazas) · Mitigación (flag / rollback) · Escalamiento
```

**`docs/postmortems/AAAA-MM-DD-titulo.md`**

```
Resumen · Impacto · Línea de tiempo · Causa raíz
Sensor o guía que faltó:
Acciones (dueño, fecha):
Test o caso de eval añadido:
```

---

## PARTE E — Escalar el rigor

| Práctica | prototipo | mvp | produccion |
|---|---|---|---|
| Producto | Brief de 5 líneas | Brief + PRD | PRD revisado |
| Specs | Lista de criterios | EARS en el núcleo | EARS en todo |
| ADRs | 0–1 | Decisiones irreversibles | Todas + fitness function |
| Tests | Smoke | Unit + integración + Hypothesis | + mutation testing |
| Evals (LLM) | 20 casos a mano | Análisis de errores + 50–100 casos en CI | + jueces validados, pass^k, red teaming |
| Seguridad | Sin secretos en código | Threat model + bandit + pip-audit + gitleaks | + SBOM, revisión de privacidad |
| Observabilidad | Logs | OTel + trazas LLM + alertas de costo | SLOs con error budget |
| Despliegue | Local | PaaS + rollback ensayado | Canary / feature flags |
| Revisión | Informal | Humano + segundo modelo | + dos pares de ojos en zonas críticas |
| Brújula humana | Opcional | Prueba de explicación | Obligatoria + documentar modelos mentales |

Lo que genera `rumbo init` según el rigor:

| | prototipo | mvp | produccion |
|---|---|---|---|
| `make check` | lint, types, test, arch (+evals) | + security | + cobertura ≥ 80 % |
| CI | check + sensor | + pre-commit/gitleaks | + SBOM CycloneDX |
| Nightly | — | mutation (+ evals completas) | mutation + evals con k=3 |
| Dependabot | — | sí | sí |

Manda el **peor impacto plausible**, no el tamaño del código. Un agente que da cifras usadas en
decisiones comerciales es, como mínimo, un "MVP con controles de producción" en lo que respecta a
exactitud. Ajusta `make check` y CI al nivel elegido en A0.

---

## PARTE F — Perfiles de caso (opcional)

Ejemplos de cómo aplicar RUMBO a un caso concreto. El completo está en
`docs/ejemplos/agente-xm.md` del repositorio de RUMBO.

### Agente conversacional sobre el mercado eléctrico colombiano (XM)

- Fuente de datos: librería oficial `pydataxm` (verifica en PyPI y en el repositorio
  `EquipoAnaliticaXM/API_XM` antes de instalar: `rumbo verify-dep pydataxm`). La API pública
  limita el rango por llamada: el adaptador debe paginar por bloques y cachear (DuckDB/Parquet).
- Arquitectura (ADR-0001): **workflow, no agente**:
  1. el LLM extrae `{métrica, entidad, rango, agregación}` en un esquema Pydantic con un enum
     cerrado de métricas;
  2. código determinista valida y consulta XM vía el adaptador;
  3. código determinista calcula;
  4. el LLM redacta usando solo los datos devueltos, mostrando unidad, fuente y fecha de consulta.
- Propiedades Hypothesis iniciales: la unión de bloques de fechas cubre exactamente el rango sin
  solapes; agregaciones diarias consistentes con los datos horarios; conversiones de unidades
  invertibles; zona horaria `America/Bogota`.
- Evals deterministas: exactitud numérica con tolerancia, argumentos correctos de las
  herramientas, abstención ante datos inexistentes; consistencia con pass^3.
- Seguridad: sin herramientas de escritura ni de red arbitraria, salida sin enlaces ni imágenes
  remotas → trifecta rota por diseño.
- Primera tarea ANCLA: el adaptador XM con caché y paginación.
