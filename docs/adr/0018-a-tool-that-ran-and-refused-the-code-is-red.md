# ADR-0018: A tool that ran and refused the code is red; "environment" means the tool could not start
Status: accepted    Date: 2026-11-09

Supersedes the exit-code paragraph of ADR-0006 ("a step exited with any code other than `0` or
`1`" is a broken environment). Everything else in ADR-0006 stands.

## Context
ADR-0006 classifies a step by its exit code alone: `0` green, `1` red, anything else a broken
environment (exit `3`). An audit of v0.4.0 measured what the tools really do:

| Situation | Exit |
|---|---|
| pytest, `ImportError` or `SyntaxError` while collecting a test file | 2 |
| pytest, no test collected | 5 |
| mypy, syntax error | 2 |
| `ruff format --check`, syntax error | 2 |
| `ruff check` and pyright, syntax error | 1 |

So the agent's own broken import is reported as "the verification environment, not your code".
Two consequences: the `Stop` hook tells the agent NOT to repair what it broke, and `rumbo gate
G4` records the runner as unmeasured (⚠️) and exits `0`. Lint and types run first and catch
what is visible statically, so the exposed cases are a failure only visible at import time and
an adopted project, where `{changed}` hides the unchanged test that imports a renamed name.

## Drivers (non-functional requirements)
Tools stay black boxes: no parsing of their output (ADR-0006, the FlakeHell lesson). No table
of per-tool exit codes to maintain. The agent must never be handed an environment repair
(ADR-0006), and must never be excused from a failure that is its own. No cost on the green path.

## Options considered
1. **A per-tool table** (pytest 2 and 5 are red, mypy 2 is red…). Rejected: `uv run pytest`
   exits `2` both when pytest refuses the code and when `uv` cannot build the environment; the
   code alone cannot tell them apart, and the table rots with every tool release.
2. **Every non-zero exit is red.** Rejected: a missing interpreter or a lock that does not
   resolve would become a repair job for the agent, the loop ADR-0006 removed.
3. **Ask the environment one question (chosen).** When a step exits with a code other than `0`
   or `1`, the runner runs the same launcher and tool with `--version`. If that works, the tool
   started and what it rejected is the code: red. If it does not, the environment is broken.

## Decision and rationale
In `run_step` (`src/rumbo/sensors/check.py`):

- `0` → green; `1` → red; executable not found or `OSError` → environment. Unchanged.
- A negative return code (the process was killed by a signal) → environment.
- Any other code → **probe**. The probe is the step's command up to and including the tool,
  followed by `--version`: `uv run pytest --cov …` → `uv run pytest --version`. Option tokens
  before the tool (`uv run --frozen pytest`) are kept and are not mistaken for the tool. When
  the tool is a script (`uv run python scripts/x.py …`) the probe stops at the interpreter:
  `uv run python --version`. The probe is silent (its output is discarded), has a timeout of
  60 seconds and runs only on this path.
  - probe exits `0` → the step is **red**, detail `"<tool> exit <code>"`.
  - anything else (non-zero, not found, timeout) → **environment**, as before.
- A tool without `--version` keeps today's behaviour (environment): the failure mode of the
  probe is the old classification, never a new one.

In `rumbo gate G4` (`src/rumbo/gates.py`): when the run was requested (no `--no-run`), a runner
that exits `3`, or a runner or sensor that could not be executed, is a **failed** check (❌),
with the detail saying it is the environment and that the human repairs it. A gate cannot be
green on a verification that did not happen. `--no-run` still reports "skipped" as unmeasured.

The `Stop` hook is unchanged: exit `3` still means "report to the human and stop"; it is now
true when it says so.

## Consequences (+/−)
+ An import error, a collection error, an empty test run and a blocking type-checker error
  reach the agent as red, with the failing tail, like any other failure.
+ G4 can no longer exit `0` on an aborted test run.
− One extra process on an already failing step.
− A red step whose real cause is configuration in a protected file (a bad `addopts`) is handed
  to the agent, which cannot edit that file: it stops and asks, which is the intended rule.

## Fitness function
`tests/test_check.py::test_a_tool_that_ran_and_exited_2_is_red`,
`::test_an_environment_that_cannot_launch_the_tool_is_env`,
`::test_the_probe_stops_at_the_interpreter_for_a_script`;
`tests/test_gates.py::test_g4_fails_when_the_check_could_not_be_measured`.

## Reversibility: high
