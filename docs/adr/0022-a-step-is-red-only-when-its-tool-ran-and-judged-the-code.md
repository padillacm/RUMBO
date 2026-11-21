# ADR-0022: A step is red only when its tool ran and judged the code
Status: proposed    Date: 2026-11-21

Refines ADR-0018 (a tool that ran and refused the code is red). It supersedes nothing: the
probe of ADR-0018 stays the rule for every exit code other than `0` and `1`. The sentences of
ADR-0018 that no longer hold are listed under "Decision and rationale".

## Context
ADR-0018 asks the environment one question (`<launcher> <tool> --version`) when a step exits
with a code other than `0` or `1`, and calls the step red when the tool starts. The second
audit before v0.4.1 (`docs/audits/2026-11-16-pre-0.4.1.md`, R1 and R3) measured what that
leaves out:

| Situation | Exit | ADR-0018 says | It is |
|---|---|---|---|
| `uv run <tool>` cannot build the environment (offline, index down, lock does not resolve) | 1 | red, no probe | environment |
| `uv run python scripts/sensor.py`, the script is missing | 2 | red (python starts) | environment |
| A tool crashes on Windows (access violation `0xC0000005`) | 3221225477 | red (the tool starts) | environment |
| A tool is killed under a launcher or a shell | 128 + signal | red (the tool starts) | environment |
| `ruff check` with an invalid configuration; pyright with an unreadable configuration file | 2; 3 | red (the tool starts) | environment |

In every row the `Stop` hook tells the agent to repair something that no change to the code
repairs, the loop ADR-0006 removed. "The tool starts" is necessary for a red step; it is not
sufficient.

A first draft of this ADR went too far in two places, and an audit of that draft measured it.
It asked the *tool* for `--version` on exit `1`: a failing `python -m unittest`, a failing
`python -m compileall` and `uv run --group dev pytest` (the option and its value unglued, as
uv's documentation writes them) became "environment", because the tool, or the word taken
for it, does not answer `--version`. And it took pytest's exit `3` ("internal error") for the
environment: code under test that exits at import (an unguarded `sys.exit(main())`) makes
pytest print "mainloop: caught unexpected SystemExit!" and exit `3`, so the agent was told
not to fix its own bug. This text is the corrected decision.

## Drivers (non-functional requirements)
The agent is never excused from a failure of its own; when in doubt a step is red, because
the agent stops and asks when it cannot repair it (ADR-0018). The agent is not handed an
environment repair when the runner can tell (ADR-0006). Tools stay black boxes: no parsing of
their output. No fact about another tool is written from memory (the rule of ADR-0021): an
exit code enters the runner only with the page that documents it and with real runs showing
that code the agent writes does not produce it. A program of the project is never run a
second time to be asked a question. The red path stays cheap. Exit `1` of the runner means
"the code is red" to the `Stop` hook, to `rumbo gate G4` and to CI: nothing else may return it.

## Options considered
1. **Leave exit `1` alone.** Rejected: it is the finding; `uv` reports its own failures with `1`.
2. **Parse the output** (`error: No solution found`, `INTERNALERROR>`). Rejected by ADR-0006.
3. **Ask the tool for `--version` on exit `1` too.** Rejected after measuring it: a tool with
   no `--version` that ran and failed was called the environment.
4. **A table of every code a tool documents as its own failure.** Rejected after measuring
   it: pytest 3 and pyright 4 are produced by the agent's own change.
5. **On exit `1` ask the launcher, not the tool; keep the tool probe for the other codes; and
   take out of the probe's hands only what a probe cannot know (chosen):** a process that
   died, a script that is not there, and two codes that only a broken setup produces.

## Decision and rationale
In `src/rumbo/sensors/check.py`, a step that did not exit `0` is classified in this order:

1. **Missing project script, before running.** When the program of the step is a `*.py`
   argument right after the launchers (`uv run python scripts/x.py`) and that file does not
   exist under the project root, the step is not run: environment, `<script>: missing`.
2. **Crash codes are the environment whatever a probe would say.** A negative return code
   (ADR-0018, unchanged); `129` to `159`, the `128 + signal` convention of shells and
   launchers; a Windows NTSTATUS failure, `0xC0000000` and above, also when the code is read
   as a negative 32-bit number. Detail: `<tool> killed by signal <n>` or `<tool> crashed
   (0xC0000005)`. No probe.
3. **Two environment codes.** `ENV_EXITS` holds a code when the tool's documentation gives
   it to a failure of the tool itself (`ENV_EXIT_SOURCES` holds the page; a test refuses an
   entry without one) **and** real runs show that bad code does not produce it. No probe.

   | Tool | Code | Documented meaning | Source | Evidence (ruff 0.17.4, pyright 1.1.414, 2026-11-21) |
   |---|---|---|---|---|
   | `ruff check` | 2 | "Ruff terminates abnormally due to invalid configuration, invalid CLI options, or an internal error" | https://docs.astral.sh/ruff/linter/#exit-codes | 2 for an invalid `[tool.ruff]` value and for an unknown rule selector; 0 or 1 for a lint error, a syntax error, a non-UTF-8 file, NUL bytes, a broken or empty notebook, a bad `noqa`, a missing path, a directory named `a.py`, 3,000 nested parentheses |
   | pyright | 3 | "Config file could not be read or parsed" | https://github.com/microsoft/pyright/blob/main/docs/command-line.md | 3 for a `pyrightconfig.json` that is not JSON; 0 or 1 for a type error, a syntax error, a missing import, a non-UTF-8 file, no sources, an `include` that does not exist, a wrong `[tool.pyright]` value, 400 nested brackets, a 20,000-term expression |

   The configuration files that produce both codes (`pyproject.toml`, `ruff.toml`,
   `pyrightconfig.json`) are protected from the agent by the guard (ADR-0019, ADR-0023). Left
   out, and decided by the probe of point 5:
   - **pytest 3** ("Internal error happened while executing tests, or a plugin raised while
     importing"): also code under test that exits at import, and a `conftest.py` hook that
     raises (measured with pytest 9.2.0).
   - **pytest 4** ("command line usage error, including a plugin that cannot be found or a
     `conftest.py` that fails to import"), **pytest 2** (documented "interrupted by the
     user"; measured for a collection error), **pytest 5** ("No tests were collected") and
     **pytest 6**.
   - **pyright 4** ("Illegal command-line parameters specified"): measured for a path on
     the command line that does not exist, which a change that removes `tests/` produces.
   - **pyright 2** ("Fatal error occurred with no errors or warnings reported"): no run
     produced it, so nothing shows that bad code cannot.
   - **`ruff format` 2**: documented with the words of `ruff check` 2, but a file with a
     syntax error also exits 2. The key is the sub-command right after the tool:
     `ruff --quiet check` is not recognised and takes the probe.
   - **mypy**: its documentation (`command_line.html`, `running_mypy.html`) does not describe
     its exit codes.
4. **Exit `1` is red, unless the launcher cannot start its interpreter.** The question is
   about the launcher, never about the tool: the launcher words of the step up to and
   including `run`, with their options, then `python --version` (`uv run --offline python
   --version` for `uv run --offline pytest -q`; uv's flags that say what the next word is,
   `-m`, `--module`, `-s`, `--script` and `--gui-script`, are left out); or, when the step
   names the interpreter (`uv run python -m unittest`, `python -m compileall`), the words up
   to and including it, then `--version`. It exits `0` → the tool ran: red, `exit 1`.
   Anything else (non-zero, not
   found, 60 seconds) → environment, `<tool> exit 1: uv run python --version fails too`. A
   step with no such launcher (a bare tool, `uvx`, a program by its path) is red on `1` and
   nothing is asked. Measured with uv 0.12.19: `uv run --offline python -c pass` in a project
   whose dependency cannot be resolved exits `1`, and so does `uv run --offline python
   --version`.
5. **Every other code takes the probe of ADR-0018**: `<launchers> <tool> --version` exits
   `0` → red, `<tool> exit <code>`; anything else → environment. One exception protects the
   project's own programs: when the tool is a path, a `.py` file, a file of the project root
   or inline code (`python -c`), it is not run a second time; its launcher is asked as in
   point 4, and with no launcher (`scripts/test.sh`, `tools/lint`) such a code is the
   environment. `uv` and the interpreters are launchers also when named by a path
   (`.venv/bin/python`); `run` and `-m` only as bare words.

Each distinct question is asked once in a run. To name the tool, the runner knows the options
of `uv`, `uv run` and `uvx` that take a value (`UV_VALUE_OPTIONS`, read from the `--help` of
uv 0.12.19), so `uv run --group dev pytest` runs `pytest`; glued forms (`--group=dev`) keep
working. For an option of uv that it does not know, the next word is the option's value
unless it is a launcher, a path, a `.py` file or a program found on `PATH`.

Sentences of ADR-0018 that no longer hold:
- Drivers: "No table of per-tool exit codes to maintain." There is one, of two entries, each
  with its source and its evidence. The rejection of a table of red codes stands.
- Decision: "`0` → green; `1` → red; […] Unchanged." Exit `1` is the environment when the
  launcher of the step cannot start its interpreter.
- Decision: "Any other code → probe." Crash codes and the two codes of `ENV_EXITS` do not
  reach the probe.
- Decision: the probe "runs only on this path". A second question, about the launcher, is
  asked on exit `1`.
- Decision: "When the tool is a script […] the probe stops at the interpreter". It holds, and
  now for every program of the project; with no interpreter in the step (`uv run tools/x.py`)
  the question is `uv run python --version`, not `uv run --version`.
- Consequences: "a blocking type-checker error" reaches the agent as red. Not when pyright
  says that its configuration file cannot be read (3).

"A tool without `--version` keeps today's behaviour (environment)" still holds, for the codes
ADR-0018 wrote it for; on exit `1` such a tool is red, as it was before ADR-0018.

`rumbo gate G4` and the `Stop` hook are unchanged: they read the exit code of the runner, and
exit `3` already fails the gate (ADR-0018) and already tells the agent to stop and report.

## Consequences (+/−)
+ An environment that `uv` cannot build, a missing sensor script, a crashed tool and a
  broken ruff or pyright configuration stop the agent with "report to the human" instead of a
  repair it cannot make.
+ A tool that ran and exited `1` is red whether or not it has a `--version`.
+ No program of the project is run twice by the runner.
− One extra process on the first red step of a run, the same for every step behind the same
  launcher. Measured: five red steps under `uv run`, 2.85 s against 2.66 s without the
  question.
− Red although the agent cannot repair it (it stops and asks, the intended rule of ADR-0018):
  `pip-audit` with the network cut (exit 1); bandit with an invalid configuration (exit 1);
  pytest 4 for a bad `addopts`; `ruff format` 2 for an invalid configuration; pyright 2 and
  4; pytest 3 for a real internal error; a module that is not installed (`python -m x`
  exits 1).
− A bare `python` in a step is the interpreter the system finds first, usually not the
  project's environment: `python -m pytest` is then "No module named pytest", exit 1, red.
  Steps are written `uv run python …`.
− `uvx` steps are red on exit 1 even when `uvx` could not fetch the tool: there is no
  interpreter to ask.
− A tool that uses `129` to `159` for its own findings is misread as killed by a signal.
− Both tables need maintenance: `ENV_EXITS` when a tool changes its codes, the option list
  when uv adds an option that takes a value (until then the next word decides).
− A probe that does not answer is abandoned after 60 seconds, but on Windows a process
  started by its launcher can outlive it: the runner kills the launcher, not its children.

## Fitness function
`tests/test_check.py::test_exit_1_from_an_environment_that_cannot_start_the_tool_is_env`,
`::test_a_failing_tool_that_has_no_version_option_is_red`,
`::test_the_question_on_exit_1_is_about_the_launcher`,
`::test_a_launcher_option_and_its_value_are_not_the_tool`,
`::test_the_launcher_is_asked_once_for_every_red_step_of_a_run`,
`::test_a_project_program_run_by_its_path_is_never_run_a_second_time`,
`::test_a_crash_code_is_env_whatever_the_probe_would_say`,
`::test_a_missing_project_script_is_env_before_anything_runs`,
`::test_an_exit_code_the_tool_documents_as_not_a_verdict_is_env`,
`::test_an_ambiguous_or_undocumented_code_keeps_the_probe_rule`,
`::test_code_under_test_that_exits_at_import_is_red`,
`::test_every_environment_exit_code_names_its_source`.

## Reversibility: high
