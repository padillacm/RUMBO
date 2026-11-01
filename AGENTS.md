# AGENTS.md — repositorio del framework RUMBO

Este repo construye el CLI `rumbo` y las plantillas que genera. Método: docs/metodo.md · Instrucciones para proyectos: RUMBO.md.

## Mapa
- CLI: src/rumbo/cli.py · scaffold: src/rumbo/scaffold.py · render: src/rumbo/render.py
- Gates: src/rumbo/gates.py · sensor anti-trampa canónico: src/rumbo/sensors/
- Plantillas: src/rumbo/templates/ (`.tmpl` se renderiza, `__pkg__` → paquete, `dot_` → `.`)
- Tests: tests/ · prueba extremo a extremo: scripts/e2e.sh

## Antes de declarar una tarea terminada
`make check` en verde; si tocaste plantillas, también `make e2e`.

## Reglas duras
- El CLI solo usa la biblioteca estándar: ninguna dependencia de runtime.
- Todo Python de las plantillas debe pasar ruff, ruff format y pyright en el proyecto generado.
- No copies números de versión de memoria; las dependencias de los proyectos las resuelve `uv add`.
- Nunca borres ni debilites tests. Si un test parece incorrecto, detente y pregunta.
- Cambios en RUMBO.md o en las plantillas: mantén coherentes RUMBO.md, README.md y docs/metodo.md.
