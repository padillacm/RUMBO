# Worked example: a conversational agent over the Colombian electricity market (XM)

RUMBO's pilot case, end to end. Recommended start:

```bash
mkdir xm-agent && cd xm-agent && git init
uvx --from git+https://github.com/padillacm/RUMBO rumbo init --yes \
  --name "XM Agent" --package xm_agent --llm --provider claude-code --rigor mvp \
  --case "Conversational agent over the Colombian electricity market data published by XM"
make check && git add -A && git commit -m "chore: initial RUMBO harness"
```

> Rigor: it starts as an MVP, but its figures feed commercial decisions, so for accuracy it is
> treated as an "MVP with production controls" (deterministic numeric evals and pass^3 from
> the start). Provider `claude-code` keeps API keys off the developer's machine; switch to the
> `anthropic` SDK when the workflow is deployed.

## R — Recognize
- **Brief (hypothesis to validate):** analysts at energy retailers lose time navigating
  Sinergox or wiring API calls to answer questions like "how did this week's spot price
  compare with the same week of 2024?". Metric: ≥70 % of real questions answered correctly,
  with the source, in under 15 s.
- **Validation:** 5–8 interviews to gather **50 real questions**, the seed of the eval dataset.
- **Prototype in `spikes/`:** a notebook with the model and `pydataxm`, XM's official library
  (class `ReadDB`, Python ≥3.10). The public API (`servapibi.xm.com.co`, endpoints `hourly`,
  `daily`, `monthly` and `lists`) needs no authentication but limits the range of each call
  (30 days for hourly and daily data, as verified in October 2026). That becomes an NFR:
  paginate and cache.
- **G1:** if the prototype answers ≥60 % of the 50 questions well and the analysts would use
  it, move to U.

## U — Unify
- **PRD:** v1 covers spot price, commercial demand, generation by resource, hydro inflows and
  useful reservoir volume. Non-goals: forecasts, purchase recommendations, non-public data.
- **EARS specs (excerpt):**
  - When the user asks for a metric over a date range, the system shall query XM in blocks that
    respect the API limit and return the value, unit, granularity and query date.
  - If the metric or the date does not exist, then the system shall say so and shall not
    invent values.
  - While the XM API is unavailable, the system shall serve from the cache and warn about its
    age.
  - When the answer includes a calculation, the system shall compute it with deterministic
    code, not with the LLM, and show the formula.
- **NFRs:** p95 < 15 s; cost < 0.02 USD per question (to be calibrated); 99 % availability
  during business hours; the agent has no write or arbitrary-network tools.
- **Eval plan** (graders from `evals/graders/`):
  - Numeric accuracy: `numeric` against the recomputed value.
  - Correct metric and dates: `tool_arguments` on the tool-call arguments.
  - Honesty on missing data: `abstention`.
  - Source attribution: an LLM judge validated against 50 human labels (after the error
    analysis).
  - Adversarial robustness and consistency (pass^k with `--k 3`).
  - Thresholds are set **after** the first error analysis. The smoke suite in `make check`
    runs offline from recorded XM responses; `make evals-live` calls the model.

Example case in `evals/datasets/smoke.jsonl`:

```json
{"id": "price-001", "input": {"question": "What was the average spot price on 2025-03-10?"}, "expected": {"field": "value", "value": 512.34, "rel_tol": 0.001}, "grader": "numeric", "tags": ["spot_price", "real"]}
```

## M — Model
- **ADR-0001, workflow, not agent:** (1) the LLM extracts `{metric, entity, range,
  aggregation}` into a Pydantic schema; (2) deterministic code validates and calls XM through
  an adapter with a DuckDB/Parquet cache; (3) deterministic code computes; (4) the LLM writes
  the answer using only the returned data. Fitness functions: `domain/` does not import the
  LLM SDK (import-linter contract already generated) and every number in the answer comes
  from the results table (test).
- **ADR-0002, closed catalog:** an enum generated from the `lists` endpoint, so the LLM cannot
  ask for metrics that do not exist.
- **ADR-0003:** Langfuse or Phoenix + OTel GenAI, with pinned versions.
- **Threat model:**
  - *Untrusted content:* the questions and, later, documents via RAG (e.g. CREG resolutions).
  - *Private data:* none in v1 (XM data is public). If customer data is added, the second leg
    of the trifecta appears.
  - *External communication:* no email, web or write tools, and the output is rendered
    without links or remote images to prevent exfiltration through markdown. **The trifecta
    is broken by design.**
  - *Applicable OWASP rows:* LLM01, LLM05, LLM06, LLM09 (mitigated by deterministic
    calculation), LLM10 (token and rate limits) and ASI02 (read-only tools with validated
    parameters).

## B — Build with ANCLA
- **Task order:**
  1. XM adapter with cache and pagination, with tests over recorded responses and a Hypothesis
     property: the union of date blocks covers the range exactly, with no overlap.
  2. Intent schema.
  3. Deterministic calculator, with properties like "the average lies within [min, max]".
  4. Answer writing.
  5. FastAPI API (`src/xm_agent/api/http.py`).
  6. Minimal UI (Streamlit or Chainlit).
- **Every PR:** the failing test first, reviewed by the human; then full CI with the smoke
  cases, `make review` for the human, and a second review by another model (`/rumbo-review`).
- **Dependencies:** `rumbo verify-dep pydataxm` and check the official repository
  (`EquipoAnaliticaXM/API_XM`). Exactly the kind of unusual name worth checking against
  slopsquatting.

## O — Operate
- **Deployment:** container (`infra/Dockerfile`) on Fly.io, Render or Cloud Run, with the LLM
  credential in the platform's secret manager.
- **SLOs:** 99 % availability, p95 < 15 s and ≥95 % "online numeric accuracy", measured by
  recomputing a 10 % sample of the answers asynchronously.
- **Traces:** one workflow span per question, with `chat` and `execute_tool` children, tokens
  and cost; a dashboard of cost per day and per question.
- **Cost control:** a small model to extract the intent and a capable model to write only if
  the evals justify it; prompt caching and bounded `max_tokens`; a per-user daily question
  limit; an alert when the daily cost exceeds 2× the moving average; an answer cache for closed
  ranges with a TTL defined in an ADR (XM publishes re-liquidations).
- **Runbook:** if the XM API is down, serve from the cache with a warning; if cost spikes,
  switch to the small model with a flag; if a wrong figure is reported, locate the trace, add
  the case to the dataset and write a postmortem (`rumbo new postmortem`).
- **Weekly ritual:** the human open-codes 20–30 traces; the agent proposes categories; the
  human decides the new eval cases.
