---
description: RUMBO phase U (Unify) — EARS specs, NFRs with numbers and the eval plan
argument-hint: <feature or focus>
---
Follow phase **U — Unify** of `RUMBO.md` (Part C).

1. Review `docs/product/prd.md`: explicit non-goals and NFRs **with numbers** (p95, cost per request, availability).
2. For each feature, create the spec with `make rumbo ARGS='new spec "<name>"'` (or copy `specs/0000-template.md`) using EARS patterns.
3. Your role is to **interrogate** the spec: ambiguities, edge cases, contradictions, missing NFRs. Propose acceptance tests per requirement.
4. LLM products: propose the seed dataset with **real** questions in `evals/datasets/` and complete `docs/evals/plan.md`. Thresholds are set after the error analysis.
5. The human signs. Finish with `make rumbo ARGS="gate G2"` and stop.

Feature or focus: $ARGUMENTS
