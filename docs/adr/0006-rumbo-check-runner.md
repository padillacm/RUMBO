# ADR-0006: `rumbo check` is the verification runner; `make` becomes an optional alias
Status: accepted    Date: 2026-11-07

## Context
The generated harness verifies itself through a Makefile whose recipes use POSIX tools
(`command -v`, `find`, `grep`, `tail -40`, `date`), and the Claude Code `Stop` hook
(`scripts/rumbo_guard.py`) and gate G4 call `make check-brief` through a subprocess. A large part of
the Python community (data teams, corporate desktops, Windows without WSL) has neither `make` nor a
POSIX shell. On such a machine the hook silently reports green when `make` is missing: the worst of
both worlds. The upstream uv task-runner proposal (astral-sh/uv#5903) is still open; its design
discussion converged on one lesson that poethepoet already applies: portability dies in the shell.

## Drivers (non-functional requirements)
Identical behaviour on Windows (PowerShell), macOS and Linux; standard library only, no network, no
`rumbo` installation required inside a generated project; the agent sees at most the failing tail;
tools treated as black boxes by exit code, never by parsing their output (the FlakeHell lesson);
a verification failure and a broken environment are told apart, because an agent that is told to
"fix it" when the environment is broken destroys working code trying.

## Options considered
1. Keep `make` and document WSL/Git Bash for Windows (status quo, excludes a large audience).
2. Depend on a task runner (poethepoet, pdm scripts, just): a runtime dependency of every project
   and one more tool to learn; contradicts ADR-0001's spirit for the harness.
3. A standard-library runner, `scripts/rumbo_check.py`, copied into every project like the sensor
   and the guard (ADR-0005), reading ordered steps from `[tool.rumbo.checks]`; `rumbo check` in
   the CLI imports the same module; the Makefile shrinks to an optional alias.

## Decision and rationale
Option 3. The contract below is what every other component builds against.

**Configuration.** One table, one string per step, in execution order:

```toml
[tool.rumbo.checks]
lint   = "uv run ruff check ."
format = "uv run ruff format --check ."
types  = "uv run pyright"
test   = "uv run pytest"
arch   = "uv run lint-imports"
sensor = "uv run python scripts/check_tests_not_weakened.py --base {base}"
```

Each string is split with `shlex.split(value, posix=True)` and run with `subprocess.run(argv,
shell=False, cwd=<project root>)`. No shell: a token that is a shell operator (`&&`, `||`, `|`,
`;`, `>`, `<`, `>>`, `2>&1`) or starts with `$` is a configuration error. The only placeholder is
`{base}`, replaced (by `str.replace`, never `str.format`) with `--base` if given, else
`origin/main` when `git rev-parse --verify -q origin/main` succeeds, else `main`.

**Invocation.** `python scripts/rumbo_check.py [STEP ...] [--brief] [--keep-going] [--base REF]`;
`rumbo check` accepts the same arguments plus `--dir`. Without step names every step runs in table
order and the run stops at the first failing step; `--keep-going` runs them all. By default each
step's output is streamed to the terminal. With `--brief` all output goes to `.rumbo-check.log`
(already ignored by git); on success one line is printed, `rumbo check: green (N log lines in
.rumbo-check.log)`, on failure the last 40 lines of the log and the summary line. Output is ASCII
only, so it survives any Windows console code page.

**Exit codes.** `0` green. `1` a verification failure: a step exited with `1`. `3` a broken
environment: a step's executable was not found, a step exited with any code other than `0` or `1`
(ruff `2`; pyright `2`, `3`, `4`; pytest `2`, `3`, `4`, `5`), `[tool.rumbo.checks]` is missing or
malformed, or an unknown step name was requested. With `--keep-going`, `3` wins over `1`. Exit `2`
is deliberately unused because it means "block" in Claude Code hooks.

**Summary lines**, one per step, ASCII: `ok   lint`, `FAIL lint (exit 1)`, `ENV  types (pyright
exit 3)`, `ENV  lint (ruff: not found)`; and a final `rumbo check: green (6 steps)`, `rumbo check:
red (lint)` or `rumbo check: environment broken (types)`.

**Consumers.** The `Stop` hook runs `[sys.executable, "scripts/rumbo_check.py", "--brief"]`: exit
`1` blocks the agent (exit 2, the failing tail as the reason) exactly as today; exit `3` returns 0
with a JSON `{"decision": "block", "reason": ..., "systemMessage": ...}` whose reason tells the
agent to report the failure to the human and not to repair the environment, and whose
`systemMessage` tells the human; `stop_hook_active` keeps it to one round. If the script is
missing (a project generated before this ADR and not yet updated) the hook falls back to `make
check-brief`, and if that is missing too it reports an environment problem the same way, with
"run `rumbo update`" in the message. Gate G4 and the CI workflow call the script directly. The
Makefile keeps its targets as one-line aliases (`check: ; uv run python scripts/rumbo_check.py`).

## Consequences (+/−)
+ The whole harness (hooks, gates, CI) runs identically on the three platforms; CI regains a
  Windows job that runs the script without `make`.
+ A broken environment is reported to the human instead of being hidden or handed to the agent.
+ The step list lives in `pyproject.toml`, protected by the guard like the Makefile was.
− One more file copied into every project and refreshed by `rumbo update`.
− No shell means no inline conditionals; a step that needs logic becomes a small Python script.
− The `mutation` target stays a Makefile/nightly concern (mutmut needs `os.fork`, POSIX only).

## Fitness function
`tests/test_check.py` (table parsing, shell-operator rejection, `{base}`, stop-at-first vs
`--keep-going`, the exit-code classes, `--brief` log and tail), `tests/test_guard.py` (`Stop` maps
exit `1` to a block and exit `3` to the report-to-human JSON, with the `make` fallback),
`tests/test_scaffold.py` (the generated CI has a Windows job that runs `python
scripts/rumbo_check.py` and never `make`), and the e2e script.

## Reversibility: high
The Makefile aliases keep `make check` working; dropping the runner means restoring the recipes.
