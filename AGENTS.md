# AGENTS.md — the RUMBO framework repository

This repository builds the `rumbo` CLI and the templates it generates. Method: docs/method.md ·
Instructions for projects: RUMBO.md · Contributing rules: CONTRIBUTING.md.

## Map
- CLI: src/rumbo/cli.py · scaffold/update: src/rumbo/scaffold.py · template engine: src/rumbo/render.py
- Gates: src/rumbo/gates.py · project state: src/rumbo/project.py · guided review: src/rumbo/tour.py
- Traceability: src/rumbo/trace.py · process metrics: src/rumbo/metrics.py
- Canonical sensors copied into every project: src/rumbo/sensors/check_tests_not_weakened.py (anti-cheat) and
  src/rumbo/sensors/guard.py (Claude Code hook, becomes scripts/rumbo_guard.py)
- Decisions about this framework: docs/adr/ (each with a fitness function in tests/)
- Templates: src/rumbo/templates/ (`.tmpl` is rendered, `__pkg__` → package, `dot_` → `.`)
- Tests: tests/ · end-to-end: scripts/e2e.sh (`make e2e`)

## Before declaring a task done
`make check` green; if you touched templates, `make e2e` too.

## Hard rules
- The CLI uses the standard library only: no runtime dependencies.
- Every Python file in the templates must pass ruff, ruff format and pyright in the generated project.
- Never copy version numbers from memory; project dependencies are resolved by `uv add`.
- Never delete or weaken tests. If a test looks wrong, stop and ask.
- Changes to RUMBO.md or the templates: keep RUMBO.md, README.md and docs/method.md consistent.
- English everywhere; Conventional Commits; structural and behavioral commits separated.
