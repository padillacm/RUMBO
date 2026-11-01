---
description: Fase M (Modelar) de RUMBO — ADRs con fitness function, C4 y threat model
---
Sigue la fase **M — Modelar** de `RUMBO.md` (Parte C).

1. Pide primero al humano su versión de la decisión (brújula humana). Luego completa opciones y trade-offs.
2. ADR con `rumbo new adr "<decisión>"`. **Cada ADR lleva una fitness function** ejecutable (contrato import-linter, test en `tests/architecture/` o umbral en CI) y la implementas en el mismo cambio.
3. Si hay LLM: ADR explícito **workflow vs. agente** (llamada única → workflow → agente solo si los evals lo exigen). Herramientas pocas, tipadas con Pydantic, de solo lectura.
4. Actualiza `docs/architecture/c4.md` (niveles 1–2 en Mermaid).
5. Threat model de 1 hora en `docs/security/threat-model.md`: STRIDE por frontera, OWASP LLM 2025 / Agentic 2026, trifecta letal (¿qué pata se elimina?).
6. Termina con `rumbo gate G3` y detente.

Decisión o foco: $ARGUMENTS
