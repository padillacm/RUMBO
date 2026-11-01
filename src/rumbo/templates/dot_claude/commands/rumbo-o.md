---
description: RUMBO phase O (Operate, Observe, Optimize)
argument-hint: [focus]
---
Follow phase **O — Operate, Observe, Optimize** of `RUMBO.md` (Part C). Focus: $ARGUMENTS

- Deployment with an immutable image (`infra/Dockerfile`, tag = SHA) and a **rehearsed rollback**.
- OpenTelemetry + 2–3 SLOs in `docs/ops/slos.md`. LLM products: GenAI traces (model, tokens, cost, tool) to Langfuse or Phoenix, with pinned versions; isolate the instrumentation in an adapter.
- Cost budget per request, hard limits and alerts.
- `docs/ops/runbook.md`: one entry per failure mode. Blameless postmortems with `make rumbo ARGS='new postmortem "<title>"'` that answer "which sensor was missing?".
- Weekly ritual: the human reviews 20–30 traces (open coding); you propose categories; new failures become eval cases.

Every new dependency (OTel, Langfuse…) is verified with `rumbo verify-dep` and approved by the human. Finish with `make rumbo ARGS="gate G5"` and stop.
