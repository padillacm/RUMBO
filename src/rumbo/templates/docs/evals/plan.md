<!-- rumbo:plantilla — borra esta línea cuando el documento esté completo (lo verifica `rumbo gate`) -->
# Plan de evals

## Comportamientos objetivo

## Taxonomía de fallos (con conteos del análisis de errores)
| Fallo | Conteo | Evaluador (código / juez LLM con TPR/TNR) |
|---|---|---|

## Dataset
Fuentes, cómo se actualiza (las trazas de producción se vuelven casos).

## Umbrales de CI
- smoke por PR: pass^1 ≥ …
- completa nightly: pass^k ≥ … (k = 3 para consistencia)

## Métricas online

## Cadencia del análisis de errores
Ritual semanal: el humano revisa 20–30 trazas (open coding); el agente propone categorías.
