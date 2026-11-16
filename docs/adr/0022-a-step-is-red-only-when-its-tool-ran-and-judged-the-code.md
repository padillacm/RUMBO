<!-- rumbo:template — delete this line when the document is complete. Only the HUMAN removes it; `rumbo gate` checks it. -->
# ADR-0022: A step is red only when its tool ran and judged the code
Status: proposed    Date: 2026-11-16

Refines ADR-0018 (a tool that ran and refused the code is red). It supersedes nothing: the
probe of ADR-0018 stays the rule. The sentences of ADR-0018 that no longer hold are listed
under "Decision and rationale".

## Context
ADR-0018 asks the environment one question (`<launcher> <tool> --version`) when a step exits
with a code other than `0` or `1`, and calls the step red when the tool starts. The second
audit before v0.4.1 (`docs/audits/2026-11-16-pre-0.4.1.md`, R1 and R3) measured what that
leaves out:

| Situation | Exit | ADR-0018 says | It is |
|---|---|---|---|
| `uv run <tool>` cannot build the environment (offline, index down, lock does not resolve) | 1 | red, no probe | environment |
| `python -m <module that is not installed>` | 1 | red, no probe | environment |
| `uv run python scripts/sensor.py`, the script is missing | 2 | red (python starts) | environment |
| A tool crashes on Windows (access violation `0xC0000005`) | 3221225477 | red (the tool starts) | environment |
| A tool is killed under a launcher or a shell | 128 + signal | red (the tool starts) | environment |
| pytest internal error; pyright fatal error or unreadable configuration | 3; 2, 3 | red (the tool starts) | environment |

In every row the `Stop` hook tells the agent to repair something that no change to the code
repairs, the loop ADR-0006 removed. "The tool starts" is necessary for a red step; it is not
sufficient.

## Drivers (non-functional requirements)
The agent is never handed an environment repair, and never excused from a failure of its own
(ADR-0006, ADR-0018). Tools stay black boxes: no parsing of their output. No fact about
another tool is written from memory (the rule of ADR-0021): an exit code enters the runner
only with the page of the tool's documentation that defines it. A code the documentation
gives both to bad code and to a bad setup is not evidence. The red path stays cheap. Exit `1`
of the runner means "the code is red" to the `Stop` hook, to `rumbo gate G4` and to CI: nothing
else may return it.

## Options considered
1. **Leave exit `1` alone.** Rejected: it is the finding; `uv` reports its own failures with `1`.
2. **Parse the output** (`error: No solution found`, `INTERNALERROR>`). Rejected by ADR-0006.
3. **A full per-tool table of red and environment codes.** Rejected by ADR-0018 and still
   rejected: under `uv run` the same code comes from the launcher or from the tool.
4. **Probe every failure, and take out of the probe's hands what a probe cannot know
   (chosen):** a process that died, a script that is not there, and the few codes a tool
   documents as "I could not do my job".

## Decision and rationale
In `src/rumbo/sensors/check.py`, a step that did not exit `0` is classified in this order:

1. **Missing project script, before running.** When the program of the step is a `*.py`
   argument right after the launchers (`uv run python scripts/x.py`) and that file does not
   exist under the project root, the step is not run: environment, `<script>: missing`.
2. **Crash codes are the environment whatever the probe would say.** A negative return code
   (ADR-0018, unchanged); `129` to `159`, the `128 + signal` convention of shells and
   launchers; a Windows NTSTATUS failure, `0xC0000000` and above, also when the code is read
   as a negative 32-bit number. Detail: `<tool> killed by signal <n>` or `<tool> crashed
   (0xC0000005)`. No probe.
3. **Documented environment codes.** `ENV_EXITS` holds, per tool, the codes its documentation
   reserves for a failure of the tool itself; `ENV_EXIT_SOURCES` holds the page that says so,
   and a test refuses an entry without one. No probe. Read on 2026-11-16:

   | Tool | Code | Documented meaning | Source |
   |---|---|---|---|
   | pytest | 3 | "Internal error happened while executing tests, or a plugin raised while importing" | https://docs.pytest.org/en/stable/reference/exit-codes.html |
   | `ruff check` | 2 | "Ruff terminates abnormally due to invalid configuration, invalid CLI options, or an internal error" | https://docs.astral.sh/ruff/linter/#exit-codes |
   | pyright | 2 | "Fatal error occurred with no errors or warnings reported" | https://github.com/microsoft/pyright/blob/main/docs/command-line.md |
   | pyright | 3 | "Config file could not be read or parsed" | same |
   | pyright | 4 | "Illegal command-line parameters specified" | same |

   Left to the probe, because the code is ambiguous or undocumented:
   - **pytest 4**: "command line usage error, including a plugin that cannot be found or a
     `conftest.py` that fails to import". A bad `addopts` and the agent's own broken import in
     `conftest.py` share the code (measured with pytest 9.2.0: both exit 4).
   - **pytest 2** (documented "interrupted by the user"; ADR-0018 measured it for a collection
     error), **pytest 5** ("No tests were collected": a fact about the code, red as in
     ADR-0018) and **pytest 6** (warnings limit).
   - **`ruff format` 2**: documented with the same words as `ruff check` 2, but a file with a
     syntax error also exits 2 (measured with ruff 0.17.4; `ruff check` reports it with 1).
     The key is the sub-command right after the tool: `ruff --quiet check` is not recognised
     and takes the probe.
   - **mypy**: its documentation (`command_line.html`, `running_mypy.html`) does not describe
     its exit codes, so nothing separates a crash from a finding. It has no entry.
4. **Every other code, `1` included, takes the probe of ADR-0018.** Probe exits `0` → red
   (`exit 1`, or `<tool> exit <code>`); anything else → environment (`<tool> exit <code>`).
   The probe runs once per distinct probe command in a run: three red steps under
   `uv run ruff` cost one extra process. A step with nothing to ask (a script run by its
   path, no launcher in front) is red on `1` and the environment on any other code.

Measured with uv on 2026-11-16: `uv run --offline python -c pass` in a project whose
dependency cannot be resolved exits `1`, and so does its probe; the step is now `ENV`.

Sentences of ADR-0018 that no longer hold:
- Drivers: "No table of per-tool exit codes to maintain." There is one, of environment codes
  only, each with its source. The rejection of option 1 stands for red codes.
- Decision: "`0` → green; `1` → red; […] Unchanged." Exit `1` is red only when the probe
  answers.
- Decision: "Any other code → probe." Crash codes and documented environment codes do not
  reach the probe.
- Decision: the probe "runs only on this path". It also runs on exit `1`, once per command.
- Decision: "A tool without `--version` keeps today's behaviour (environment): the failure
  mode of the probe is the old classification, never a new one." For exit `1` it is a new
  one: such a tool was red and is now the environment.
- Consequences: "a blocking type-checker error" reaches the agent as red. Not when pyright
  says the error is its own (2, 3, 4).

`rumbo gate G4` and the `Stop` hook are unchanged: they read the exit code of the runner, and
exit `3` already fails the gate (ADR-0018) and already tells the agent to stop and report.

## Consequences (+/−)
+ An environment that `uv` cannot build, a missing module, a missing sensor script and a
  crashed tool stop the agent with "report to the human" instead of a repair it cannot make.
+ `rumbo check` exits `1` only for a step whose tool ran and judged the code.
− One extra process on the first red step of each tool in a run (the common red path).
− A tool that does not answer `--version` with exit `0` can never be red: its step is the
  environment on every failure. Measured on 2026-11-16: ruff, pyright, pytest, bandit and
  pip-audit answer it; `lint-imports` declares it in its source (`click.version_option` in
  `src/importlinter/cli.py`, main branch) and mypy was not measured: both are to be checked
  in a generated project before release. A project wraps any other tool in a small script.
− A hook in the project's `conftest.py` that raises makes pytest exit 3 (`INTERNALERROR`,
  measured with pytest 9.2.0): the agent's own mistake is reported as the environment. The
  agent stops and reports; G4 fails.
− pytest 4, `ruff format` 2 and every failure of mypy keep the ADR-0018 rule: a broken
  configuration in a protected file is still handed to the agent, which stops and asks.
− A tool that uses `129` to `159` for its own findings is misread as killed by a signal.
− The table needs maintenance when a tool changes its documented codes.

## Fitness function
`tests/test_check.py::test_exit_1_from_an_environment_that_cannot_start_the_tool_is_env`,
`::test_the_probe_runs_once_per_distinct_command_in_a_run`,
`::test_a_crash_code_is_env_whatever_the_probe_would_say`,
`::test_a_missing_project_script_is_env_before_anything_runs`,
`::test_an_exit_code_the_tool_documents_as_not_a_verdict_is_env`,
`::test_an_ambiguous_or_undocumented_code_keeps_the_probe_rule`,
`::test_every_environment_exit_code_names_its_source`.

## Reversibility: high
