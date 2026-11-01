---
description: RUMBO phase M (Model) — ADRs with fitness functions, C4 and the threat model
argument-hint: <decision or focus>
---
Follow phase **M — Model** of `RUMBO.md` (Part C, "The RUMBO cycle").

1. Ask the human for their version of the decision first (human compass). Then complete options and trade-offs.
2. Create the ADR with `make rumbo ARGS='new adr "<decision>"'`. **Every ADR carries an executable fitness function** (import-linter contract, test in `tests/architecture/` or CI threshold); implement it in the same change.
3. LLM products: an explicit **workflow vs. agent** ADR (single call → workflow → agent only if the evals demand it). Few tools, typed with Pydantic, read-only.
4. Update `docs/architecture/c4.md` (levels 1–2 in Mermaid).
5. One-hour threat model in `docs/security/threat-model.md`: STRIDE per boundary, the OWASP LLM 2025 / Agentic 2026 checklist, lethal trifecta (which leg is removed, and which sensor proves it).
6. Finish with `make rumbo ARGS="gate G3"` and stop.

Decision or focus: $ARGUMENTS
