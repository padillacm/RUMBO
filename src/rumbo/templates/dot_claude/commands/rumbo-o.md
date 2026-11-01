---
description: Fase O (Operar, Observar, Optimizar) de RUMBO
---
Sigue la fase **O — Operar, Observar, Optimizar** de `RUMBO.md` (Parte C). Foco: $ARGUMENTS

- Despliegue con imagen inmutable (`infra/Dockerfile`, tag = SHA) y **rollback ensayado**.
- OpenTelemetry + 2–3 SLOs en `docs/ops/slos.md`. Si hay LLM: trazas GenAI (modelo, tokens, costo, herramienta) hacia Langfuse o Phoenix, con versiones fijadas; aísla la instrumentación en un adaptador.
- Presupuesto de costo por solicitud, límites duros y alertas.
- `docs/ops/runbook.md`: una entrada por modo de fallo. Postmortems sin culpables con `rumbo new postmortem` que respondan "¿qué sensor faltó?".
- Ritual semanal: el humano revisa 20–30 trazas (open coding); tú propones categorías; los fallos nuevos se vuelven casos de eval.

Toda dependencia nueva (OTel, Langfuse…) se verifica con `rumbo verify-dep` y la aprueba el humano. Termina con `rumbo gate G5` y detente.
