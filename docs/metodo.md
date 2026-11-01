# El método RUMBO

RUMBO es un ciclo de cinco fases para construir software con agentes de IA de punta a punta:
**R**econocer el problema, **U**nificar la intención en specs y evals, **M**odelar arquitectura y
riesgos, **B**uild con ANCLA y **O**perar/Observar/Optimizar. Lo atraviesa una **brújula humana**
que protege el criterio técnico de quien dirige el proyecto.

La tesis es poco vistosa: **la IA amplifica la disciplina de ingeniería que ya existe, pero no la
reemplaza**. Por eso RUMBO invierte en verificadores ejecutables, arquitectura legible por
máquinas, lotes pequeños, observabilidad y aprendizaje deliberado, y no en prompts sofisticados ni
en "equipos virtuales" de agentes.

Las instrucciones operativas para el agente están en [`RUMBO.md`](../RUMBO.md). Este documento
explica el **porqué**.

---

## 1. Hallazgos que lo fundamentan (evidencia a octubre de 2026)

### 1.1 La IA amplifica la disciplina; no la sustituye
- **DORA 2025** (≈5.000 profesionales): la adopción de IA se asocia con más *throughput* de
  entrega **y** más inestabilidad. "AI's primary role in software development is that of an
  amplifier". Sin control robusto (tests automáticos, control de versiones maduro, feedback
  rápido) más volumen de cambios produce inestabilidad. *Correlacional, no causal.*
- **METR (RCT, jul. 2025):** desarrolladores expertos tardaron un 19 % más con IA aunque creían
  haber ido un 20 % más rápido. **Lección: la sensación de productividad no es una métrica.**
- **Calidad del código (GitClear, vendor):** cae el código "movido" (refactorización) y sube la
  duplicación. *Señal, no prueba.*
- **Seguridad (Veracode, vendor):** el 45 % de las muestras de código generado falló tests de
  seguridad, sin mejora con el tamaño del modelo.

### 1.2 La deuda cognitiva es real y se puede gestionar
- **Anthropic (RCT, ene. 2026, n=52):** quienes aprendieron una librería con IA sacaron un 17 %
  menos en comprensión, sin ser significativamente más rápidos. La mayor brecha fue en
  *debugging*. Preguntar conceptos y pedir explicaciones protegió el aprendizaje; delegarlo todo
  fue lo más dañino.
- **MIT Media Lab (preprint):** escribir primero sin ayuda y después usar el LLM ("Brain-to-LLM")
  preservó mejor el recuerdo.
- **Thoughtworks Radar v34:** *codebase cognitive debt* = brecha entre la implementación y el
  entendimiento compartido del equipo.

### 1.3 El "arnés" es la unidad de diseño cuando se programa con agentes
- **OpenAI, "Harness engineering" (feb. 2026):** dar al agente "a map, not a 1,000-page
  instruction manual", hacer del repositorio la fuente de verdad, imponer invariantes de
  arquitectura mecánicamente y hacer limpieza continua.
- **Birgitta Böckeler (abr. 2026):** el arnés se compone de **guías** (feedforward: AGENTS.md,
  documentación) y **sensores** (feedback: linters, tests, contratos, revisores), cada uno
  computacional (determinista) o inferencial (un LLM).
- **Kent Beck, "Augmented Coding":** TDD estricto, separar cambios estructurales de los de
  comportamiento (Tidy First) y vigilar tres alarmas: bucles, funcionalidad no pedida y "any
  indication that the genie was cheating, for example by disabling or deleting tests".

### 1.4 Aplicaciones LLM: primero lo simple, evals que salen de los errores, seguridad desde la arquitectura
- **Anthropic, "Building effective agents":** distinguir *workflows* (caminos de código
  predefinidos) de *agentes* (el LLM dirige su propio proceso); empezar por lo más simple e
  invertir en la interfaz agente-computador (ACI).
- **Husain y Shankar (Evals FAQ):** "Error analysis is the most important activity in evals".
  Open coding (≥30 trazas a mano) → axial coding (taxonomía de fallos con conteos) → recién
  entonces evaluadores. "Start with error analysis, not infrastructure."
- **Seguridad:** OWASP Top 10 LLM 2025 y Agentic Applications 2026 (*least agency*). La
  **trifecta letal** de Simon Willison: datos privados + contenido no confiable + comunicación
  externa; "the only way to stay safe is to avoid that lethal trifecta combination entirely".
  No existe defensa probada contra prompt injection ("The Attacker Moves Second", 2025): las
  defensas son sobre todo arquitectónicas.
- **Observabilidad:** las convenciones GenAI de OpenTelemetry siguen en estado "Development":
  usarlas, pero fijando versiones y aislando la instrumentación.

### 1.5 Prototipar es barato; el riesgo es confundir el prototipo con el producto
- **Cagan/SVPG:** separar *build to learn* de *build to earn*. RUMBO lo adopta con un matiz: **el
  prototipo es la spec, pero nunca el código de producción**.

---

## 2. Las cinco fases

| Fase | Pregunta | Artefactos | Puerta (gate) |
|---|---|---|---|
| **R: Reconocer** | ¿Vale la pena? ¿Para quién? | Brief, árbol de oportunidades, registro de supuestos, prototipos desechables | **G1:** problema validado con ≥5 usuarios o datos reales; métrica de éxito; prototipo fuera de `src/` |
| **U: Unificar** | ¿Qué debe hacer y cómo lo sabremos? | PRD ligero, specs EARS con criterios de aceptación, RNF cuantificados, plan de evals, dataset semilla | **G2:** cada requisito tiene un verificador o un eval planeado; los RNF tienen número |
| **M: Modelar** | ¿Cómo lo estructuramos para cambiarlo con seguridad? | ADRs (MADR), C4 1–2, contratos import-linter, fitness functions, threat model ligero | **G3:** contratos en CI; threat model revisado; decisión workflow vs. agente documentada |
| **B: Build con ANCLA** | ¿Cada cambio es correcto, pequeño y entendido? | Plan por tarea, tests, diffs pequeños, AGENTS.md | **G4 (por PR):** CI verde con evals smoke; revisión humana + segundo modelo; el humano puede explicar el cambio |
| **O: Operar, Observar, Optimizar** | ¿Funciona en producción, cuánto cuesta, cómo evoluciona? | CI/CD, SLOs, trazas LLM, runbook, postmortems, backlog de deuda | **G5:** SLOs y alertas activas; rollback probado; presupuesto de costo con alerta |
| **Brújula humana** (transversal) | ¿Seguimos entendiendo y decidiendo nosotros? | Diario de decisiones, sesiones sin IA, explicación del diff | Si el humano no puede explicar un módulo, no se mergea |

ANCLA (Acotar, Navegar, Cerrar, Limitar, Aprender) **es la fase B**, pero sus principios se
aplican de forma recursiva al resto: en R, "acotar" significa fijar métricas; en M, fijar
contratos.

### 2.1 R — Reconocer
1. **Brief de una página**, escrito primero por el humano: problema, usuario, evidencia, métrica de
   resultado y supuestos más riesgosos.
2. **Árbol de oportunidades** (Teresa Torres): resultado → oportunidades → soluciones →
   experimentos. La IA sintetiza entrevistas, pero **las entrevistas las hace el humano**.
3. **Prototipos para aprender** en `spikes/`, desechables y validados con usuarios o datos reales.
4. **Regla contra el "prototipo zombi":** nada de `spikes/` se mergea a `src/`. Pasa *lo aprendido*,
   que se reimplementa con ANCLA.
5. **En productos LLM:** prueba de factibilidad con 20–30 preguntas reales, "a mano".

### 2.2 U — Unificar
1. **PRD ligero** (1–2 páginas): objetivos, no-objetivos, usuarios, flujos, RNF con números,
   riesgos.
2. **Specs con EARS**, un patrón por requisito: ubicuo ("El sistema deberá…"), evento
   ("Cuando…"), estado ("Mientras…"), no deseado ("Si…, entonces…") u opcional ("Donde…"). Cada
   requisito lleva criterios ejecutables en pytest/Hypothesis.
3. **RNF cuantificados:** latencia p95, costo máximo por solicitud, SLO, privacidad, herramientas
   permitidas al agente, mantenibilidad.
4. **Plan de evals:** dimensiones de calidad, dataset semilla y umbrales provisionales. Los
   evaluadores definitivos *salen* del análisis de errores.
5. **IA sin delegar el juicio:** el agente *interroga* la spec; el humano decide y firma.

### 2.3 M — Modelar
1. **Monolito modular** con arquitectura hexagonal ligera: `domain/` (puro), `app/` (casos de
   uso), `adapters/` (APIs, LLM, BD) y `api/`. Así el agente toca un solo módulo por tarea.
2. **ADRs MADR con una fitness function ejecutable**: el ADR deja de ser documentación y se
   convierte en sensor.
3. **C4 niveles 1–2** en Mermaid dentro del repo.
4. **Decisión workflow vs. agente** en un ADR: llamada única con retrieval → workflow → agente
   autónomo solo si los evals demuestran que hace falta.
5. **Diseño de herramientas (ACI):** pocas, parámetros tipados con Pydantic, enums y rangos
   validados, salidas acotadas.
6. **Threat model en 1 hora:** flujo de datos, STRIDE por frontera, OWASP LLM/Agentic y prueba de
   la trifecta: ¿qué pata eliminamos?

### 2.4 B — Build con ANCLA

| Paso | Guías (feedforward) | Sensores (feedback) | Quién |
|---|---|---|---|
| **A**cotar en verificadores | Spec EARS; tests revisados por el humano antes del código | pytest en rojo; Hypothesis; pyright estricto en `domain/` | Humano aprueba; agente redacta los tests |
| **N**avegar con plan | AGENTS.md ("mapa, no manual"); ADRs enlazados | Revisión del plan antes de codificar | Agente propone; humano aprueba |
| **C**errar en sandbox | Comandos de verificación documentados | ruff, pyright, pytest, import-linter, bandit, pip-audit, evals de regresión | Agente itera hasta verde |
| **L**imitar y revisar | Diffs <~300 líneas; estructura separada del comportamiento; un solo agente escritor | Revisión humana + segundo modelo; alarmas de Beck; verificación de dependencias nuevas (slopsquatting) | Humano decide el merge |
| **A**prender y medir | Hallazgos convertidos en reglas | Métricas DORA, mutmut en módulos críticos, PRs revertidos, pass^k | Ambos |

**Regla de oro del arnés:** si el agente comete el mismo error dos veces, se añade un sensor
(test, regla de lint, contrato) o una guía, además de corregir el código.

### 2.5 O — Operar, Observar, Optimizar
1. **CI/CD:** trunk-based con ramas cortas; despliegue automático a staging y aprobación manual a
   producción hasta que evals y SLOs den confianza.
2. **Despliegue y rollback:** feature flags para funciones LLM nuevas, imágenes inmutables
   (tag = SHA) y rollback de un comando, **ensayado** antes del lanzamiento.
3. **Observabilidad clásica:** OpenTelemetry con 2–3 SLIs/SLOs y presupuesto de error. Si se
   agota, se congelan las features.
4. **Observabilidad LLM:** spans `gen_ai.*` con modelo, tokens, herramienta y costo hacia Langfuse
   o Phoenix. Los prompts se versionan en el repo y su hash queda en la traza. Las trazas de
   producción se convierten en casos de eval.
5. **Incidentes:** runbook por modo de fallo y postmortems sin culpables que respondan "¿qué
   sensor faltó?".
6. **Mantenimiento:** Dependabot/Renovate semanal; limpieza quincenal con refactors Tidy First;
   detector de clones; documentación actualizada en el mismo PR.

### 2.6 Brújula humana
- **Modo conceptual al aprender:** pedir explicaciones y usar la IA para comprobar lo que uno
  entiende, no para delegar.
- **Primero yo, luego la IA** en el diseño: el humano esboza el ADR, el modelo de datos o el
  prompt antes de pedírselo al agente.
- **Prueba de explicación:** antes de un merge relevante, el humano explica el cambio en 3 frases
  sin mirar el resumen del agente.
- **Una sesión semanal de *debugging* sin IA.**
- **Cuándo NO usar agentes:** cuando se aprende algo que se debe dominar; en decisiones
  irreversibles; en código de seguridad sin tests previos; cuando el contexto no está en el repo;
  cuando especificar la tarea toma más tiempo que hacerla.

---

## 3. RACI humano vs. agente

A = aprueba/responde, R = realiza, C = consultado.

| Actividad | Humano | Agente | Verifica el humano | Verifica el agente o sensor |
|---|---|---|---|---|
| Problema y métrica de éxito | **A/R** | C (reta supuestos) | Evidencia de usuarios | — |
| Entrevistas | **A/R** | C (guiones, síntesis) | Fidelidad de la síntesis | — |
| Prototipos de discovery | A | **R** | Qué se aprendió | Que funcione para demostrar |
| PRD y specs EARS | **A** | R (borrador, ambigüedades) | Cada requisito y no-objetivo | Consistencia, casos borde |
| RNF y presupuestos | **A/R** | C | Números realistas | Estimación de tokens y costo |
| Análisis de errores y evals | **A/R** (open coding a mano) | R (axial preliminar, datos sintéticos) | Taxonomía final, etiquetas gold | TPR/TNR del juez LLM |
| ADRs y arquitectura | **A** (esboza primero) | R (opciones, fitness functions) | Trade-offs, reversibilidad | Contratos en CI |
| Threat model | **A/R** | C (enumera amenazas) | Prioridades, riesgos aceptados | Tests adversariales, SAST |
| Tests de aceptación | **A** | R | Que prueben la intención | Que fallen antes del cambio |
| Implementación | C | **R** | Diff y prueba de explicación | CI completo |
| Revisión de PR | **A** | R (segundo modelo) | Diseño, seguridad, tests intactos | Lint, tipos, arquitectura, SAST |
| Despliegue | **A** (go/no-go) | R (pipeline) | Plan de rollback | Smoke tests, evals post-despliegue |
| Incidentes y postmortem | **A/R** | C/R (diagnóstico, borrador) | Causa raíz, nuevo sensor | Correlación de trazas |
| Deuda y mantenimiento | **A** (prioriza) | R (refactor, upgrades) | Que la estructura mejore | Tests, mutation score, duplicación |

**Reparto permanente:** el humano es dueño del *qué* y del *por qué*: problema, specs, decisiones
irreversibles, riesgos aceptados, merge, go/no-go y entendimiento del sistema. El agente es dueño
de los borradores, las alternativas, la implementación verificada y la vigilancia (revisión,
análisis de trazas, limpieza). Ninguna decisión del agente llega a producción sin pasar por un
sensor o por el humano.

---

## 4. Stack recomendado para Python

| Capa | Recomendado | Alternativas | Nota |
|---|---|---|---|
| Entorno | **uv** | Poetry | Lockfile reproducible; comandos idempotentes para el agente |
| Lint/format | **ruff** | black + flake8 | Rápido en pre-commit y en el bucle del agente |
| Tipos | **pyright** (estricto en `domain/`) o mypy | basedpyright | Sensor barato contra APIs alucinadas |
| Tests | **pytest + Hypothesis** | — | Tests-first y PBT |
| Mutation | **mutmut** (nightly, módulos críticos) | cosmic-ray | Detecta tests débiles o tramposos |
| Arquitectura | **import-linter** | pytestarch, tach | Fitness functions para los ADRs |
| Hooks/CI | **pre-commit + GitHub Actions** | lefthook, GitLab CI | Los mismos sensores para humano y agente |
| SAST | **semgrep + bandit** | CodeQL | Según Veracode, el prompting no basta |
| Dependencias | **pip-audit + Dependabot/Renovate** | OSV-Scanner, Socket | Supply chain y slopsquatting |
| Supply chain avanzado | SBOM CycloneDX, OpenSSF Scorecard, attestations SLSA | — | MVP o superior |
| Secretos | Gestor de la plataforma + **gitleaks** | trufflehog | El agente nunca ve secretos de producción |
| API | **FastAPI + Pydantic v2** | Litestar | Validación en la frontera |
| LLM | SDK oficial directo o **PydanticAI** | LangGraph solo si hace falta un grafo | Empezar con las APIs directas |
| Evals | pytest + JSONL versionado; **Inspect AI** o **promptfoo** | DeepEval | Evals en CI como tests |
| Observabilidad LLM | **Langfuse** o **Arize Phoenix** | LangSmith, Logfire | Ingieren OTel GenAI; vigilar licencias |
| Observabilidad general | **OpenTelemetry** → Grafana Cloud | Honeycomb | Estándar abierto |
| Despliegue | **Fly.io, Railway, Render o Cloud Run** | VPS + Docker Compose, Modal | Sin Kubernetes para 1–3 personas |
| Datos | **Postgres** (+pgvector) o **DuckDB/Parquet** | SQLite | |
| Flags | Variables de entorno; **Unleash/GrowthBook** después | LaunchDarkly | Proporcional al tamaño |

`rumbo init` instala el núcleo (uv, ruff, pyright, pytest, Hypothesis, pytest-cov, import-linter,
pre-commit, pip-audit, bandit, mutmut, gitleaks). El resto se añade en su fase, verificado con
`rumbo verify-dep` y aprobado por el humano.

---

## 5. Escalar el rigor según el riesgo

| Práctica | Prototipo/hackathon | MVP | Producción crítica |
|---|---|---|---|
| Producto | Brief de 5 líneas | Brief + PRD ligero | PRD revisado con stakeholders |
| Specs | Lista de criterios | EARS en features núcleo | EARS en todo, con trazabilidad requisito→test |
| ADRs | 0–1 | Decisiones irreversibles | Todas las significativas + fitness function |
| Tests | Smoke | Unit + integración + Hypothesis | + mutation, contract tests con APIs externas |
| Evals | 20 preguntas a mano | Análisis de errores + 50–100 casos + smoke en CI | + juez validado, pass^k, red-teaming OWASP, evals online |
| Seguridad | Sin secretos en el código | Threat model, semgrep, pip-audit, gitleaks | + SBOM, Scorecard, SLSA, red team, revisión de privacidad |
| Observabilidad | Logs | OTel + Langfuse/Phoenix + alertas de costo | SLOs con error budget, runbooks completos |
| Despliegue | Local | PaaS con rollback ensayado | Canary/flags por cohorte |
| Revisión | Informal | Humano + segundo modelo | + dos pares de ojos en zonas críticas |
| Brújula humana | Opcional | Prueba de explicación | Obligatoria + documentar los modelos mentales |

**Regla de decisión:** manda el **peor impacto plausible**, no el tamaño del código. Un agente que
da cifras usadas en decisiones comerciales es, como mínimo, un "MVP con controles de producción"
en lo que respecta a exactitud.

---

## 6. Anti-patrones a lo largo del ciclo

| Fase | Anti-patrón | Antídoto |
|---|---|---|
| R | **Prototipo zombi:** el spike del fin de semana termina siendo producción | `spikes/` separado; reimplementar con ANCLA |
| R | **Construir porque es barato** | Brief con métrica; G1 |
| U | **Spec-teatro:** specs largas generadas por IA que nadie lee | EARS cortos + verificadores; firma humana |
| U | **Evals genéricos** ("helpfulness 4,2/5") | Análisis de errores primero |
| M | **Agente por defecto** con 15 herramientas | ADR workflow vs. agente |
| M | **ADR muerto** que el agente viola | Una fitness function por ADR en CI |
| M | **Microservicios de una sola persona** | Monolito modular |
| B | **El genio tramposo:** tests borrados o asserts debilitados | Regla en AGENTS.md + sensor en CI + mutmut |
| B | **AGENTS.md enciclopedia** | "Mapa, no manual" (≤100 líneas) |
| B | **Varios agentes escribiendo a la vez** | Un escritor; los demás solo revisan |
| B | **Copiar en vez de reutilizar** | Detector de clones; limpieza quincenal |
| O | **Sin rollback probado** | Ensayarlo antes del lanzamiento |
| O | **Costos invisibles** | Presupuesto por solicitud, alertas, límites duros |
| O | **Trazas que nadie mira** | Ritual semanal de 30 min |
| Humano | **Deuda cognitiva:** "no sé cómo funciona, pero pasa CI" | Prueba de explicación; sesión sin IA |
| Humano | **Productividad percibida** | Medir (en METR la percepción se equivocó por ~40 puntos) |

---

## 7. Plan de adopción

### Semana 1: el arnés y la brújula

| Día | Humano | Agente |
|---|---|---|
| 1 | Elige el piloto y escribe el brief a mano | Critica el brief y propone preguntas para las entrevistas |
| 2 | Agenda 3–5 conversaciones y reúne preguntas reales | `rumbo init`: esqueleto, uv, pre-commit y CI; el humano revisa cada archivo |
| 3 | Redacta 3 specs EARS | Lista ambigüedades y propone tests de aceptación |
| 4 | Escribe el ADR-0001 con su opinión primero | Completa opciones y propone el contrato de import-linter |
| 5 | Threat model de 1 hora | Enumera amenazas y propone tests adversariales |
| 6–7 | Primera tarea ANCLA completa y prueba de explicación | Implementa con TDD en sandbox; otra sesión actúa como segundo revisor |

**Gate de la semana 1:** CI en verde (ruff, pyright, pytest, lint-imports, gitleaks, pip-audit);
AGENTS.md de ≤100 líneas; un ADR con fitness function; ≥30 preguntas reales en el dataset (si hay
LLM).

### Mes 1
- **Semana 2:** núcleo de dominio y evals deterministas. Primer análisis de errores sobre ~50
  trazas.
- **Semana 3:** API, UI mínima, observabilidad (OTel + Langfuse/Phoenix), alertas de costo,
  semgrep y una suite de prompt injection.
- **Semana 4:** despliegue con rollback ensayado, SLOs, runbook, 3–5 usuarios beta detrás de un
  flag y una retro: ¿qué sensor faltó? ¿qué sobra en AGENTS.md?
- **Métricas desde el día 1:** las cuatro de DORA (frecuencia de despliegue, lead time, tasa de
  fallos de cambio, tiempo de recuperación); costo por solicitud y exactitud online; porcentaje de
  PRs con la prueba de explicación superada; horas semanales sin IA.

---

## 8. Frameworks existentes: qué toma RUMBO de cada uno

| Framework | Fortalezas | Debilidades | Qué toma RUMBO |
|---|---|---|---|
| BMAD Method | Cubre análisis a dev/QA con personas y escala el proceso | Ceremonia, mucho texto; riesgo de spec-teatro; sin sensores computacionales | Escalar el proceso según la complejidad; handoff brief→PRD→arquitectura |
| Spec Kit / Kiro / OpenSpec / Tessl | La spec como artefacto; EARS (Kiro) | Evidencia modesta; specs no ejecutables envejecen | EARS enlazado a tests y evals |
| Product model / dual-track (Cagan, Torres) | Discovery continuo; prototype as spec | No cubre la ingeniería con agentes | Fase R |
| Lean Startup / Shape Up | Experimentos, criterio de abandono, apuestas con "appetite" | Riesgo de "MVP = código malo" | Registro de supuestos; ciclos de 2–4 semanas |
| XP | TDD, CI, lotes pequeños, refactor | El pair programming humano aplica poco | Núcleo de ANCLA |
| Harness engineering (OpenAI, Böckeler) | Guías + sensores, invariantes mecánicas, limpieza | Centrado en la fase B | Fase B y "error repetido → sensor" |
| DORA AI Capabilities Model | Respaldo estadístico | Pensado para organizaciones | Lotes pequeños, control de versiones, foco en el usuario, plantilla de repo como "plataforma" |

---

## 9. Preguntas abiertas y límites de la evidencia

1. **No hay estudios controlados del ciclo de vida completo con agentes.** RUMBO es una síntesis
   razonada, no un método validado: trátalo como hipótesis y mide.
2. **La productividad real con los agentes de 2026 es desconocida.** METR declaró poco fiables sus
   propios datos de 2026.
3. **El RCT de Anthropic midió el aprendizaje de una librería nueva**, no la erosión de habilidades
   consolidadas. Las prácticas de la brújula humana son prudentes, pero su eficacia no está
   probada.
4. **Las cifras de GitClear y Veracode provienen de vendors.** La dirección es consistente; las
   magnitudes no son generalizables.
5. **Prompt injection no tiene una solución robusta conocida.** Las defensas son sobre todo
   arquitectónicas; los detectores son una capa más, no una garantía. La "Agents Rule of Two" de
   Meta (oct. 2025) pide no más de dos de: procesar entradas no confiables, acceder a sistemas
   sensibles o datos privados, y cambiar estado o comunicarse externamente.
6. **OTel GenAI sigue en "Development":** aislar la instrumentación en un módulo y fijar versiones.
7. **Escala:** el framework está calibrado para 1–3 personas.
