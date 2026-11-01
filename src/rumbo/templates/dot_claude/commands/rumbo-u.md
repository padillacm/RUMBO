---
description: Fase U (Unificar) de RUMBO — specs EARS, RNF con números y plan de evals
---
Sigue la fase **U — Unificar** de `RUMBO.md` (Parte C).

1. Revisa `docs/product/prd.md`: no-objetivos explícitos y RNF **con números** (p95, costo por solicitud, disponibilidad).
2. Para cada feature, crea la spec con `rumbo new spec "<nombre>"` (o copia `specs/0000-plantilla.md`) usando patrones EARS.
3. Tu rol es **interrogar** la spec: ambigüedades, casos borde, contradicciones, RNF ausentes. Propón tests de aceptación por requisito.
4. Si hay LLM: propone el dataset semilla con preguntas **reales** en `evals/datasets/` y completa `docs/evals/plan.md`. Los umbrales se fijan después del análisis de errores.
5. El humano firma. Termina con `rumbo gate G2` y detente.

Feature o foco: $ARGUMENTS
