---
description: RUMBO phase O (Operate, Observe, Optimize)
argument-hint: [focus]
---
Follow phase **O — Operate, Observe, Optimize** of `RUMBO.md` (Part C, "The RUMBO cycle"). Focus: $ARGUMENTS

- Deployment with an immutable image (`infra/Dockerfile`, tag = SHA) and a **rehearsed rollback**.
- 2–3 SLOs in `docs/ops/slos.md`. LLM products: every request already runs inside `app/telemetry.span` (OTel GenAI attribute names, cost) and a `app/budget.Meter`; charge every model call to the meter and keep the spans when you replace the skeleton. Shipping the records to Langfuse, Phoenix or an OTLP collector is an adapter (`opentelemetry-sdk`, pinned); call sites in app/ do not change.
- Cost budget per request: RUMBO_BUDGET_* in the environment; alerts on `abstain_reason=budget`.
- `docs/ops/runbook.md`: one entry per failure mode. Blameless postmortems with `make rumbo ARGS='new postmortem "<title>"'` that answer "which sensor was missing?".
- Weekly ritual: the human reviews 20–30 traces (open coding); you propose categories; new failures become eval cases with `make rumbo ARGS='eval add ...'`.
- `make rumbo ARGS="metrics"` gives the process numbers (specs closed, sessions per spec, deploys and incidents logged with `rumbo decision`).

Every new dependency (OTel, Langfuse…) is verified with `rumbo verify-dep` and approved by the human. Finish with `make rumbo ARGS="gate G5"` and stop.
