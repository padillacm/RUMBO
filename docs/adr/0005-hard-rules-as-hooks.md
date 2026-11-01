# ADR-0005: The hard rules of AGENTS.md are enforced by Claude Code hooks
Status: accepted    Date: 2026-11-01

## Context
AGENTS.md forbids the agent to edit the Makefile, the CI, the sensors, to remove the template
mark or to write the test-change approval mark. A prompt is a guide, not a sensor: under
pressure to go green, agents do exactly those things (Beck's "cheating genie").

## Drivers (non-functional requirements)
Deterministic enforcement; the human never blocked; no new dependency; the rule list visible
in one file; a blocked action explains itself to the agent.

## Options considered
1. Keep the rules in prose and rely on review (status quo).
2. Git pre-commit hooks: late (after the edit), bypassable with `--no-verify`.
3. Claude Code `PreToolUse`/`Stop` hooks running `scripts/rumbo_guard.py`: block before the
   edit or command happens, return the reason, and refuse to finish with `make check` red.
4. Permission deny rules only in `settings.json`: no content inspection (template mark,
   approval mark), opaque to the agent.

## Decision and rationale
Option 3, with option 4's deny list as a second layer. The guard is a stdlib script copied into
every project and refreshed by `rumbo update`; the human bypasses it by editing by hand or in
`.claude/settings.local.json`, so it constrains the agent and nobody else.

## Consequences (+/−)
+ The most common cheats are impossible from inside Claude Code; the agent is told to ask.
+ `Stop` running `make check-brief` closes the loop without the human asking.
− Only Claude Code honours these hooks; other agents need their own wiring.
− `uv add` edits `pyproject.toml` legitimately, so Bash rules are regex-based and conservative.

## Fitness function
`tests/test_guard.py` (protected files, template mark, dangerous commands, Stop behaviour) and
the e2e script, which exercises the three hook paths in a generated project.

## Reversibility: high
