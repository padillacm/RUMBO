<!-- rumbo:template — delete this line when the document is complete. Only the HUMAN removes it; `rumbo gate` checks it. -->
# F-0002: One verification runner, offline, on three platforms (`rumbo check`)
Story: As a developer working with a coding agent I want one command that runs the project's
verification steps the same way on Windows, macOS and Linux, without a shell, `make` or network,
so that the hooks, the gates and CI all read the same verdict and a broken environment is never
handed to the agent as a failure to fix.

Scope: `scripts/rumbo_check.py` (canonical source `src/rumbo/sensors/check.py`) and the `check`
subcommand of the CLI (`cmd_check` in `src/rumbo/cli.py`), which runs the same module.
Decisions: ADR-0003, ADR-0006, ADR-0011 (the table grows with rigor), ADR-0013 (`{changed}`,
`--full`), ADR-0018, ADR-0020 (`spikes/`).

This spec is retroactive: it describes what the code does on 2026-11-09, not a plan.

## Requirements (EARS)
<!-- Ubiquitous: The system shall… · Event: When…, the system shall… · State: While…, the system shall…
     Unwanted: If…, then the system shall… · Optional: Where…, the system shall… -->
- R1 (ubiquitous): The runner shall read the steps from the `[tool.rumbo.checks]` table of the `pyproject.toml` in the project root (`--dir`, default the current directory) and run the steps named on the command line, or every step when none is named, in the order of the table whatever the order of the names, streaming each step's output to the terminal and printing one line per step (`ok   <name>`) and a final line `rumbo check: green (N steps)` with exit code 0 when every step exited 0.
- R2 (ubiquitous): The runner shall run each step without a shell: it splits the command string with POSIX `shlex` rules and starts the resulting argument list directly, with the project root as working directory, so that a quoted argument arrives as one argument and `*`, `~` and `%VAR%` arrive unexpanded.
- R3 (unwanted): If the command of a step that is about to run has, once split, a token equal to `&&`, `||`, `|`, `;`, `>`, `<`, `>>` or `2>&1`, a token that begins with `$`, or a token that contains `{changed}` next to other text, then the runner shall run no step, print `rumbo check: environment broken (step <name>: <reason>)` and exit with code 3.
- R4 (unwanted): If `pyproject.toml` is missing or is not valid TOML, `[tool.rumbo.checks]` is missing, empty or not a table, a step is not a non-empty string, a step named on the command line is not in the table, or a command-line option of the script is not recognised, then the runner shall run no step and exit with code 3, never with argparse's 2.
- R5 (ubiquitous): The runner shall replace `{base}` in a step, also inside a longer argument (`--base={base}`), with the value of `--base` when it is given, otherwise with `origin/main` when `git rev-parse --verify -q origin/main` succeeds in the project root, otherwise with `main`.
- R6 (ubiquitous): The runner shall expand a `{changed}` argument to the `.py` files under the project root that were added, copied, modified or renamed between the merge base of `{base}` and `HEAD`, that differ between the working tree and the index, or that are untracked and not ignored, keeping those that exist, without duplicates, sorted, as paths relative to the project root and one argument per file; it shall resolve the list once per run and only when a step that runs uses `{changed}`; and when the list is empty it shall not run the step and shall report it green as `ok   <name> (no changed files)`.
- R7 (ubiquitous): The runner shall never put in `{changed}` a path that begins with `spikes/` relative to the project root, whether the file is committed, modified or untracked; a `spikes` directory deeper in the tree and a file whose name only begins with `spikes` stay in the list.
- R8 (optional): Where `--full` is given, the runner shall expand `{changed}` to `.` (the whole tree), run the step even when no file changed, and leave the steps without `{changed}` as they are.
- R9 (unwanted): If git cannot give a base for `{changed}` (git is not installed, the project root is not inside a repository, the repository has no commit, or `{base}` is not a known ref), then the runner shall expand `{changed}` to `.`, add `(no git base: whole tree)` to the step's `ok` line and keep the exit code the steps deserve; `{base}` shall still default to `main` (R5).
- R10 (optional): Where `--keep-going` is given, the runner shall run every step, name every failing step in the final line and let a broken environment win over a failure (exit code 3 rather than 1); without it, the runner shall stop at the first step that is not green.
- R11 (ubiquitous): The runner shall classify each step by how it ended and use three exit codes, never 2: a step that exits 0 is green; a step that exits 1 is red (`FAIL <name> (exit 1)`, final line `rumbo check: red (<names>)`, exit code 1); a step whose executable is not found (`ENV  <name> (<executable>: not found)`) or whose return code is negative (`<tool> killed by signal <n>`, no probe) is a broken environment (final line `rumbo check: environment broken (<names>)`, exit code 3).
- R12 (event): When a step exits with a code above 1, the runner shall run the step's launchers and tool once with `--version` (`uv run pytest --version` for `uv run pytest --cov`; the interpreter alone, `uv run python --version`, when the tool is a `.py` script or inline `-c` code), silently and with a 60-second timeout, and shall report the step red as `FAIL <name> (<tool> exit <code>)` when that probe exits 0, and as a broken environment `ENV  <name> (<tool> exit <code>)` when the probe exits non-zero, cannot be started, does not answer in time, or there is nothing to ask.
- R13 (optional): Where `--brief` is given, the runner shall send the step headers, the output of every step (stdout and stderr) and the step lines to `.rumbo-check.log` in the project root instead of the terminal, and shall print exactly one line, `rumbo check: green (N log lines in .rumbo-check.log)`, when the run is green, or otherwise the last 40 lines of the log with every non-ASCII character replaced by `?`, followed by the final line.
- R14 (ubiquitous): The runner shall be one file that imports only the Python standard library and no networking module, shall run as `python scripts/rumbo_check.py` from the project root with no package installed, and the `rumbo check` subcommand shall pass its step names and options (`--brief`, `--keep-going`, `--base`, `--full`, `--dir`) to the same module and return its exit code.

## Acceptance criteria
<!-- pytest tests / Hypothesis properties that verify them (path::test). A test covers a requirement
     by mentioning its full id, e.g. `F-000N.R1`, in its docstring; `rumbo trace` builds the table. -->
All in `tests/test_check.py`; each test carries its requirement id in a comment.

| Requirement | Tests |
|---|---|
| R1 | `test_runs_every_step_in_table_order`, `test_step_output_streams_to_the_terminal`, `test_named_steps_run_a_subset_in_table_order` |
| R2 | `test_runs_every_step_in_table_order` (working directory), `test_quoted_operators_are_plain_arguments`, `test_arguments_reach_the_program_without_shell_expansion` |
| R3 | `test_shell_operators_are_configuration_errors`, `test_changed_must_be_an_argument_of_its_own` |
| R4 | `test_missing_or_malformed_table_is_an_environment_problem`, `test_no_pyproject_is_an_environment_problem`, `test_unknown_step_is_an_environment_problem`, `test_usage_errors_never_exit_2` |
| R5 | `test_base_placeholder_takes_the_flag`, `test_base_placeholder_is_replaced_inside_tokens`, `test_base_defaults_to_origin_main_when_it_exists` |
| R6 | `test_changed_expands_to_the_changed_python_files_sorted`, `test_changed_is_relative_to_the_given_base`, `test_changed_paths_are_relative_to_a_subdirectory_root`, `test_changed_with_nothing_changed_skips_the_step`, `test_only_spikes_changed_means_no_changed_files`, `test_changed_note_reaches_the_brief_log`, `test_changed_is_resolved_once_and_only_when_used` |
| R7 | `test_changed_never_includes_spikes`, `test_only_spikes_changed_means_no_changed_files` |
| R8 | `test_full_replaces_changed_with_the_whole_tree`, `test_full_runs_even_when_nothing_changed`, `test_full_does_nothing_for_tables_without_changed` |
| R9 | `test_changed_without_commits_falls_back_to_the_whole_tree`, `test_changed_with_an_unknown_base_falls_back_to_the_whole_tree`, `test_changed_fallback_keeps_the_failure_exit_codes`, `test_without_git_the_base_is_main_and_changed_is_the_whole_tree`, `test_outside_a_repository_the_base_is_main_and_changed_is_the_whole_tree` |
| R10 | `test_stops_at_the_first_failure`, `test_keep_going_runs_everything_and_environment_wins`, `test_keep_going_reports_every_failing_step` |
| R11 | `test_stops_at_the_first_failure`, `test_missing_executable_is_an_environment_problem`, `test_a_step_killed_by_a_signal_is_env_and_is_not_probed` |
| R12 | `test_a_tool_that_ran_and_exited_2_is_red`, `test_an_environment_that_cannot_launch_the_tool_is_env`, `test_exit_code_other_than_0_or_1_is_an_environment_problem`, `test_the_probe_stops_at_the_interpreter_for_a_script`, `test_a_probe_that_does_not_answer_in_60_seconds_is_env`, `test_the_probe_is_silent_in_the_brief_log_too`, `test_tool_name_skips_launchers`, `test_tool_name_skips_launcher_options_and_launchers_given_by_path` |
| R13 | `test_brief_success_prints_one_line_and_logs_the_rest`, `test_brief_failure_prints_the_last_40_log_lines_then_the_verdict`, `test_brief_captures_stderr_and_keeps_the_terminal_ascii`, `test_changed_note_reaches_the_brief_log` |
| R14 | `test_the_runner_imports_only_the_standard_library_and_no_network_module`, `test_the_runner_runs_as_a_single_file_with_no_package_installed`, `test_rumbo_check_passes_its_arguments_to_the_runner_and_returns_its_exit_code` |

End to end: `scripts/e2e.sh` runs `uv run python scripts/rumbo_check.py`, `make check` and
`rumbo check --dir .` in a generated project, requires exit 3 for an unknown step, and runs the
runner with `--brief` with the four model credentials unset. The CI job `check` runs
`uv run python scripts/rumbo_check.py` on Linux, macOS and Windows.

## Data contracts and ownership
<!-- What two sessions (or two agents) must agree on BEFORE writing code. Omitting explicit data
     structures is what breaks parallel work (docs/method.md, section 3.1). -->
- Types / schemas: `[tool.rumbo.checks]` in `pyproject.toml`, a non-empty table `step name = "command"` whose file order is the execution order; the placeholders `{base}` (replaced anywhere in a token) and `{changed}` (a token of its own); the log `.rumbo-check.log` in the project root, written as bytes (the runner's own lines encoded as ASCII, the steps' output as they emitted it); the step lines `ok   <name>`, `ok   <name> (<note>)`, `FAIL <name> (<detail>)`, `ENV  <name> (<detail>)`; the final lines `rumbo check: green (N steps)`, `rumbo check: red (<names>)`, `rumbo check: environment broken (<names or reason>)` and, with `--brief`, `rumbo check: green (N log lines in .rumbo-check.log)`.
- Signatures: `main(argv: list[str] | None = None) -> int` with `[STEP ...] [--brief] [--keep-going] [--base REF] [--full] [--dir DIR]`; `load_steps(root) -> dict[str, str]`, `parse_command(name, command, base) -> list[str]` and `select_steps(steps, requested) -> dict[str, str]`, which raise `ConfigError`; `resolve_base(explicit, root) -> str`; `resolve_changed(base, root, full) -> Changed(files, note)`; `plan_steps(commands, base, root, full) -> dict[str, Step]`; `run_step(name, argv, root, log, note="") -> StepResult(name, status, detail, note)`; `verdict(results) -> tuple[int, str]`; `cmd_check(args) -> int` in `src/rumbo/cli.py`.
- Shared invariants: `GREEN, RED, ENV = 0, 1, 3`, and 2 is never a verdict because it means "block" in Claude Code hooks; a configuration error of the table or of the requested steps prints one line on stdout and runs nothing; with `--keep-going` 3 wins over 1; the probe runs only after a step exited with a code above 1; the script is identical in every project (`rumbo update` refreshes the copy in `scripts/`).
- Files owned by this spec (one writer): `src/rumbo/sensors/check.py`, `tests/test_check.py`, `cmd_check` and the `check` subparser in `src/rumbo/cli.py`.
- Interfaces touched that other specs depend on: the exit codes and the `--brief` output read by the `Stop` hook (F-0001); the `sensor` step, which receives `{base}` (F-0003); the table rendered by rigor and by adoption, and the copy of the script made by `init` and `update` (F-0004); gate G4, which runs the script and reads its exit code (F-0005).

## Evals (LLM products)
<!-- case IDs, threshold -->
Not applicable: the runner calls no model.

## NFR
0 network modules imported and 0 dependencies (R14) · probe: at most one extra process, on a step
that already failed, timeout 60 s (R12) · `--brief` prints at most 41 lines (R13) · cost 0 USD ·
platforms: the CI job `check` runs the runner on Linux (Python 3.11, 3.12, 3.13), macOS (3.12)
and Windows (3.13). No latency target is set for the runner: its duration is that of the steps.

## Out of scope
- Which steps the table holds: the steps by rigor and kind (ADR-0011) and the `{changed}` steps
  written by adoption (ADR-0013) are rendered by the scaffold (F-0004, `tests/test_scaffold.py`).
- What the steps do, network included: the runner starts what the table says. The `audit` step of
  the `mvp` table runs `pip-audit`, which needs the network; "offline" here means the runner
  itself and no model credential (ADR-0003).
- What the `Stop` hook and gate G4 do with the exit code (F-0001, F-0005).
- Parsing the output of a tool: tools are black boxes read by exit code (ADR-0006, ADR-0018).
- Read in the code and not verified by a test, so not stated as requirements: an unbalanced
  quote in a command is a configuration error; any `OSError` other than "not found" when a step
  starts is a broken environment; a configuration error with `--brief` leaves the previous
  `.rumbo-check.log` untouched; a launcher option that takes a separate value
  (`uv run --group dev pytest`) makes the probe take the value for the tool.

## Questions found while writing the spec (for the human)
- R3 validates only the steps that are about to run: `rumbo check lint` is green even when
  another step of the table holds `&&`. Operators glued to a word (`pytest>out.txt`, `a&&b`) are
  not detected; they reach the program as literal arguments.
- R6: a change that is staged and not committed (`git add` of a new or modified file) is not in
  `{changed}`: the second query is `git diff` without `--cached`. ADR-0013 names that command and
  calls it "the uncommitted changes".
- R4: only the script maps a usage error to 3. `rumbo check --no-such-flag` is refused by the
  CLI's own parser and exits 2.
- R13 covers the tail of the log. The runner's own lines on the terminal (step header, step
  line, final line, configuration error) are printed as they are: a step name or a changed file
  name with a non-ASCII character is not replaced, and on a console that cannot encode it the
  script ends with a traceback and exit code 1 (seen with `PYTHONIOENCODING=ascii`). ADR-0006
  says the output is ASCII only.
- R11: a negative return code exists on POSIX only; what a crashed process returns on Windows is
  a positive code, which takes the probe route of R12.
- This spec describes the canonical file. On this branch the repository's own copy,
  `scripts/rumbo_check.py` (what its CI, `make check` and `Stop` hook run), is older: it has
  neither the probe of R12 nor the `spikes/` rule of R7, until the human runs `rumbo update`.
- The PRD measures "Platforms" as Linux, macOS, Windows × Python 3.11–3.13; the CI matrix runs
  the three versions on Linux only, 3.12 on macOS and 3.13 on Windows.
