# spikes/

Código desechable. **Nunca se importa desde src/.**

Aquí viven los prototipos de la fase R (aprender, no ganar). Lo que pasa a la siguiente
fase es lo aprendido —anotado en docs/product/brief.md o en un ADR—, no el código: se
reimplementa con ANCLA. Ruff no revisa esta carpeta y un test de arquitectura impide
importarla desde `src/`.
