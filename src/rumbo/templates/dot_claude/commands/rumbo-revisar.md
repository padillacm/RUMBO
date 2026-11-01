---
description: Segunda revisión de un diff (sesión limpia u otro modelo) — paso L de ANCLA
---
Eres el **segundo revisor**. No escribiste este código. Revisa el diff contra la rama base (`git diff origin/main...HEAD`, o lo que indique el humano: $ARGUMENTS).

Busca, en este orden:
1. Señales del "genio tramposo": tests borrados, saltados (`skip`, `xfail`), asserts debilitados, mocks que vacían el comportamiento, umbrales relajados.
2. Funcionalidad no pedida por la spec o fuera del plan.
3. Violaciones de capas (domain impuro, prompts fuera de `prompts/`, cálculos numéricos hechos por el LLM).
4. Dependencias nuevas sin justificar (`rumbo verify-dep`).
5. Seguridad: entradas no validadas, secretos, herramientas con más privilegio del necesario, trifecta letal.
6. Diff demasiado grande o que mezcla refactor con comportamiento.

No modifiques archivos. Entrega una lista priorizada de hallazgos con archivo:línea y una recomendación: aprobar / pedir cambios.
