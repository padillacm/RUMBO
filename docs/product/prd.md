# PRD — RUMBO

## Context
The brief (`brief.md`, G1: iterate) states the problem: a small team working with a coding agent
cannot stop the agent from cheating its way to green, because rules written in prompts are not
sensors. RUMBO is a standard-library Python CLI that mounts a harness around a project (guides
the agent reads, sensors the agent cannot bypass, gates the human signs) and a method
(R·U·M·B·O with the ANCLA loop) that tells both what to do at each phase. This PRD covers the
CLI and the generated harness at v0.4; the method's prose lives in `RUMBO.md` and
`docs/method.md`.

## Goals and metrics
| Goal | Metric | Target |
|---|---|---|
| Retention in the pilots | pilots still running the check four weeks after `rumbo init` | ≥ 2 of 3 |
| The agent closes its own loop | agent PRs that reach review with the check green, no human intervention | ≥ 80 % |
| Fast start | time from `rumbo init` to the first green check on a fresh machine | < 1 hour |
| Cheats are caught mechanically | weakened tests, protected-file edits and red Stops blocked before merge | indicator (count) |

## Non-goals
Frontend, mobile and non-Python stacks; monorepos with uv workspaces; an agent SDK or agent
orchestration; replacing FastAPI, the model SDKs or the CI provider; teams larger than three per
repository; a hosted service or a GitHub App; a baseline file of accepted lint findings
(ADR-0013); `ty` as a type checker until it leaves preview (ADR-0015).

## Users
- **The solo builder**: one person, one agent, several small Python projects; wants the agent to
  iterate alone without reviewing every keystroke.
- **The small team (2–3)**: shares a repository and at least two agents; needs the same rules
  for everyone and for every agent (Claude Code, Cursor, Codex, Gemini CLI).
- **The adopter of an existing codebase**: has thousands of lines before RUMBO; needs the harness
  without a rewrite (`--adopt`).
- **The maintainer of RUMBO**: runs the framework on itself.

## Flows
1. **Start**: `rumbo init` (four questions) → harness written, tools installed, hooks wired →
   first green check → first commit.
2. **Adopt**: `rumbo init --adopt` on an existing project → detection, harness only, strict
   checks on changed files → adoption commit by the human.
3. **Build**: `/rumbo-ancla` or `/rumbo-fix` → tests first → the agent iterates until the runner
   is green; the guard blocks forbidden edits and a red finish → `rumbo gate G4` → guided review.
4. **Grow**: `rumbo promote` raises the rigor; `rumbo update` refreshes framework-owned files.
5. **Audit**: `rumbo doctor`, `rumbo gate G1…G5`, `rumbo trace`, `rumbo metrics`.

## Requirements
<!-- IDs linking to specs/: F-0001, F-0002… -->
Every feature has its spec. The code predated them (the project adopted its own method at
v0.3); F-0002 to F-0007 were written from the code on 2026-11-16, and each one records where
the code and the decisions disagree.

| ID | Feature | Spec |
|---|---|---|
| F-0001 | The agent cannot cheat its way to green (the guard: Claude Code and git hooks) | `specs/0001-…` |
| F-0002 | One verification runner, offline, on three platforms (`rumbo check`) | `specs/0002-…` |
| F-0003 | Tests cannot be weakened unnoticed (the anti-cheat sensor) | `specs/0003-…` |
| F-0004 | Mount, adopt, update and promote the harness (`init`, `--adopt`, `update`, `promote`) | `specs/0004-…` |
| F-0005 | Gates and traceability (`gate`, `trace`, `doctor`, `metrics`) | `specs/0005-…` |
| F-0006 | Guided review of a change (`tour`) | `specs/0006-…` |
| F-0007 | Dependencies verified before they are added (`verify-dep`) | `specs/0007-…` |

## Non-functional requirements (with numbers)
| NFR | Target | How it is measured |
|---|---|---|
| Guard latency (PreToolUse/PostToolUse logic, excluding interpreter start) | p95 < 100 ms | timing test on `guard.main` (to write with F-0001) |
| `rumbo init --no-install` | < 2 s | pytest duration of the scaffold tests |
| Framework test suite on a laptop | < 120 s | `pytest` wall time (60 s today) |
| Runtime dependencies of the CLI | 0 | `tests/test_fitness.py::test_cli_has_no_runtime_dependencies` |
| Platforms | Linux, macOS, Windows × Python 3.11–3.13, all green | CI matrix (`check`, `e2e`) |
| The check is offline | 0 network calls, 0 model credentials in CI | e2e with every provider variable unset |
| Generated `AGENTS.md` | ≤ 100 lines | `rumbo doctor` |
| Cost per request | 0 USD: the CLI never calls a model | by construction (no SDK import) |
| Availability | not applicable: no service is operated | — |
| Maintenance of RUMBO's own harness | < 8 hours a month | maintainer's log in `docs/decisions.md` |

## Risks
- **Process fatigue**: the full cycle is heavy for small tools. Mitigation: rigor levels, the fix
  lane, five dev tools at prototype.
- **Guard evasion**: an agent can unset its marker or set `rumbo.human`. Mitigation: the Claude
  Code guard blocks both; CI is the binding layer; documented as a limit (ADR-0008).
- **Template drift**: four kinds × three layouts × two type checkers. Mitigation: the e2e matrix
  and the Linux container run before every PR.
- **Single maintainer**: bus factor one. Mitigation: zero lock-in (the generated project never
  imports `rumbo`); ADRs with fitness functions.
- **Windows specifics**: console encodings, virtualised AppData, missing wheels. Mitigation: a
  Windows job in the framework's CI since v0.4.

## Launch (flags)
No feature flags: the CLI is installed from PyPI (`uv tool install rumbo==X.Y.Z`). v0.4.0 was
cut on 2026-11-09 and published the same day by Trusted Publishing, before the five
interviews (one recorded); the pilots install it from there.

## Open questions
- Does the guard need live hooks for Cursor once its hook protocol is stable (ADR-0008)?
- Who are the three pilots, and which one is brownfield?
