# Ejemplo aplicado: agente conversacional sobre el mercado eléctrico colombiano (XM)

Caso piloto de RUMBO de punta a punta. Arranque recomendado:

```bash
mkdir agente-xm && cd agente-xm && git init
uvx --from git+https://github.com/padillacm/RUMBO rumbo init --yes \
  --nombre "Agente XM" --pkg agente_xm --llm --proveedor anthropic --rigor mvp \
  --caso "Agente conversacional sobre datos del mercado eléctrico colombiano (XM)"
make check && git add -A && git commit -m "chore: arnés RUMBO inicial"
```

> Rigor: aunque empiece como MVP, las cifras se usan en decisiones comerciales, así que en
> exactitud se trata como "MVP con controles de producción" (evals numéricos deterministas y
> pass^3 desde el inicio).

## R — Reconocer
- **Brief (hipótesis por validar):** los analistas de comercializadoras pierden tiempo navegando
  Sinergox o montando llamadas a la API para responder preguntas como "¿cómo se comparó el precio
  de bolsa de esta semana con la misma semana de 2024?". Métrica: ≥70 % de las preguntas reales
  respondidas correctamente, con fuente, en menos de 15 s.
- **Validación:** 5–8 entrevistas para reunir **50 preguntas reales**, semilla del dataset de evals.
- **Prototipo en `spikes/`:** un notebook con el modelo y `pydataxm`, la librería oficial de XM
  (clase `ReadDB`, Python ≥3.10). La API pública (`servapibi.xm.com.co`, endpoints `hourly`,
  `daily`, `monthly` y `lists`) no pide autenticación, pero limita el rango de cada llamada
  (máximo 30 días para datos horarios y diarios). Eso se convierte en un RNF: hay que paginar y
  cachear.
- **G1:** si el prototipo responde bien ≥60 % de las 50 preguntas y los analistas lo usarían, se
  pasa a U.

## U — Unificar
- **PRD:** en la v1, precio de bolsa, demanda comercial, generación por recurso, aportes hídricos
  y volumen útil de embalses. No-objetivos: pronósticos, recomendaciones de compra, datos no
  públicos.
- **Specs EARS (extracto):**
  - Cuando el usuario pida una métrica para un rango de fechas, el sistema deberá consultar XM en
    bloques que respeten el límite de la API y devolver el valor, la unidad, la granularidad y la
    fecha de consulta.
  - Si la métrica o la fecha no existen, entonces el sistema deberá indicarlo y no deberá inventar
    valores.
  - Mientras la API de XM no responda, el sistema deberá usar la caché y avisar de su antigüedad.
  - Cuando la respuesta incluya un cálculo, el sistema deberá hacerlo con código determinista, no
    con el LLM, y mostrar la fórmula.
- **RNF:** p95 < 15 s; costo < 0,02 USD por pregunta (por calibrar); 99 % de disponibilidad en
  horario laboral; el agente no tiene herramientas de escritura ni de red arbitraria.
- **Plan de evals** (graders de `evals/graders/`):
  - Exactitud numérica: `numerico` contra el valor recalculado.
  - Selección correcta de métrica y fechas: `argumentos_tool` sobre los argumentos de las tool calls.
  - Honestidad ante datos ausentes: `abstencion`.
  - Atribución de la fuente: juez LLM validado contra 50 etiquetas humanas (después del análisis
    de errores).
  - Robustez adversarial y consistencia (pass^k con `--k 3`).
  - Los umbrales se fijan **después** del primer análisis de errores.

Ejemplo de caso en `evals/datasets/smoke.jsonl`:

```json
{"id": "precio-001", "input": {"pregunta": "¿Cuál fue el precio de bolsa promedio el 2025-03-10?"}, "expected": {"campo": "valor", "valor": 512.34, "rel_tol": 0.001}, "grader": "numerico", "tags": ["precio_bolsa", "real"]}
```

## M — Modelar
- **ADR-0001, workflow y no agente:** (1) el LLM extrae `{métrica, entidad, rango, agregación}` en
  un esquema Pydantic; (2) código determinista valida y llama a XM a través de un adaptador con
  caché en DuckDB/Parquet; (3) código determinista calcula; (4) el LLM redacta usando solo los
  datos devueltos. Fitness functions: `domain/` no importa el SDK del LLM (contrato import-linter
  ya generado) y todo número de la respuesta proviene de la tabla de resultados (test).
- **ADR-0002, catálogo cerrado:** un enum generado desde el endpoint `lists`, para que el LLM no
  pueda pedir métricas inexistentes.
- **ADR-0003:** Langfuse o Phoenix + OTel GenAI, con versiones fijadas.
- **Threat model:**
  - *Contenido no confiable:* las preguntas y, en el futuro, documentos vía RAG (p. ej.,
    resoluciones CREG).
  - *Datos privados:* no los hay en la v1 (los datos de XM son públicos). Si se agregan datos de
    clientes, aparece la segunda pata de la trifecta.
  - *Comunicación externa:* sin herramientas de email, web ni escritura; la salida se renderiza
    sin enlaces ni imágenes remotas para evitar exfiltración vía markdown. **La trifecta queda
    rota por diseño.**
  - *OWASP aplicables:* LLM01, LLM05, LLM06, LLM09 (mitigado con cálculo determinista), LLM10
    (límites de tokens y de tasa) y ASI02 (herramientas de solo lectura con parámetros validados).

## B — Build con ANCLA
- **Orden de tareas:**
  1. Adaptador XM con caché y paginación, con tests sobre respuestas grabadas y una propiedad
     Hypothesis: la unión de los bloques de fechas cubre exactamente el rango, sin solapamientos.
  2. Esquema de intención.
  3. Calculadora determinista, con propiedades como "el promedio está dentro de [min, max]".
  4. Redacción.
  5. API FastAPI (`src/agente_xm/api/http.py`).
  6. UI mínima (Streamlit o Chainlit).
- **Propiedades Hypothesis iniciales:** cobertura exacta de bloques de fechas; agregaciones
  diarias consistentes con los datos horarios; conversiones de unidades invertibles; zona horaria
  `America/Bogota`.
- **En cada PR:** primero el test en rojo, revisado por el humano; luego CI completo con los casos
  smoke y revisión del humano + un segundo modelo (`/rumbo-revisar`).
- **Dependencias:** `rumbo verify-dep pydataxm` y comprobar el repositorio oficial
  (`EquipoAnaliticaXM/API_XM`). Es justo el tipo de nombre poco común que conviene comprobar por el
  riesgo de slopsquatting.

## O — Operar
- **Despliegue:** contenedor (`infra/Dockerfile`) en Fly.io, Render o Cloud Run, con la API key
  del LLM en el gestor de secretos de la plataforma.
- **SLOs:** 99 % de disponibilidad, p95 < 15 s y ≥95 % de "exactitud numérica online", medida
  recalculando de forma asíncrona una muestra del 10 % de las respuestas.
- **Trazas:** un span de workflow por pregunta, con hijos `chat` y `execute_tool`, tokens y costo;
  un dashboard de costo por día y por pregunta.
- **Control de costos:** modelo pequeño para extraer la intención y modelo capaz para redactar
  solo si los evals lo justifican; prompt caching y `max_tokens` acotado; límite de preguntas por
  usuario y día; alerta si el costo diario supera 2× la media móvil; caché de respuestas para
  rangos cerrados con un TTL definido en un ADR (XM publica reliquidaciones).
- **Runbook:** si la API de XM cae, se sirve desde la caché con aviso; si el costo se dispara, se
  pasa al modelo pequeño con un flag; si se reporta una cifra errónea, se localiza la traza, el
  caso se añade al dataset y se escribe un postmortem (`rumbo new postmortem`).
- **Ritual semanal:** el humano hace open coding sobre 20–30 trazas; el agente propone categorías
  y el humano decide los nuevos casos de eval.
