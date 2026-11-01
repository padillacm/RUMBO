---
description: Fase B — una tarea con el bucle ANCLA (Acotar, Navegar, Cerrar, Limitar, Aprender)
---
Implementa esta tarea con **ANCLA** (`RUMBO.md`, Parte C, fase B): $ARGUMENTS

1. **Acotar:** lee la spec y los ADRs relevantes. Escribe primero los tests de aceptación (Hypothesis para invariantes del dominio). Ejecútalos y muestra que **fallan**. 🛑 Detente: el humano aprueba los tests.
2. **Navegar:** propone un plan corto (archivos, tests, riesgos, dependencias nuevas). 🛑 Espera aprobación.
3. **Cerrar:** código mínimo para pasar, luego refactor. Itera hasta que `make check` esté verde.
4. **Limitar:** diff ≤ ~300 líneas; commits estructurales separados de los de comportamiento. Verifica dependencias nuevas con `rumbo verify-dep`. Ejecuta `make sensor`.
5. **Aprender:** si un error se repitió, propone un sensor o una línea en AGENTS.md.

Cierra con: resumen en 3 frases, dependencias nuevas, salida resumida de `make check` y el checklist de `rumbo gate G4`. Recuerda al humano pedir una segunda revisión (`/rumbo-revisar` en una sesión limpia u otro modelo) y hacer la prueba de explicación.
