# F-0002: One verification runner, offline, on three platforms (`rumbo check`)
Story: As a developer working with a coding agent I want one command that runs the project's
verification steps the same way on Windows, macOS and Linux, without a shell, `make` or network,
so that the hooks, the gates and CI all read the same verdict and a broken environment is never
handed to the agent as a failure to fix.

Scope: `scripts/rumbo_check.py` (canonical source `src/rumbo/sensors/check.py`) and the `check`
subcommand of the CLI (`cmd_check` in `src/rumbo/cli.py`), which runs the same module.
Decisions: ADR-0003, ADR-0006, ADR-0011 (the table grows with rigor), ADR-0013 (`{changed}`,
`--full`), ADR-0018, ADR-0020 (`spikes/`), ADR-0022 (what is red, refining ADR-0018).

This spec is retroactive: it describes what the code does on 2026-11-21, not a plan.

## Requirements (EARS)
<!-- Ubiquitous: The system shall… · Event: When…, the system shall… · State: While…, the system shall…
     Unwanted: If…, then the system shall… · Optional: Where…, the system shall… -->
- R1 (ubiquitous): The runner shall read the steps from the `[tool.rumbo.checks]` table of the `pyproject.toml` in the project root (`--dir`, default the current directory; a byte order mark is accepted) and run the steps named on the command line, or every step when none is named, in the order of the table whatever the order of the names, streaming each step's output to the terminal and printing a header before each step that is not skipped (`--- <name>: <command words>`), one line per step (`ok   <name>`) and a final line `rumbo check: green (N steps)` with exit code 0 when every step is green, with `?` in place of every non-ASCII character of the headers and the step lines.
- R2 (ubiquitous): The runner shall run each step without a shell, in the project root and with no standard input: it splits the command string into words at white space, single and double quotes grouping words and nothing escaping anything (a backslash is an ordinary character), and starts the resulting argument list directly, so that a quoted argument arrives as one argument and `*`, `~` and `%VAR%` arrive unexpanded. The runner shall look up the program of a step (its first word) before starting it: with a directory part (`tools/lint`) the program is taken against the project root, not the directory the runner was started from, trying on Windows `.exe`, `.cmd`, `.bat` and `.com` before the name itself unless the name ends in one of them; a bare name is left to the system's search, except that on Windows a name that the absolute entries of `PATH` hold only as a `.cmd` or `.bat` file is run by the path of the first such file outside the project root, still without a shell; and while a step or a question of R11 and R12 is started on Windows the runner shall keep `NoDefaultCurrentDirectoryInExePath` set, so that the system takes no program from the current directory.
- R3 (unwanted): If the command of a step that is about to run has a quote left open or, once split, a token equal to `&&`, `||`, `|`, `;`, `>`, `<`, `>>` or `2>&1`, a token that begins with `$`, or a token that contains `{changed}` next to other text, then the runner shall run no step, print `rumbo check: environment broken (step <name>: <reason>)` and exit with code 3; and if the program of a step is, once looked up (R2), a `.cmd` or `.bat` file on Windows and its path or an argument of the step holds one of `& | < > ^ % "` or a line break, then the runner shall not start that step and shall report `ENV  <name> (<file>: a batch file, and cmd.exe would interpret <character> in <word> (steps run without a shell: call it from a small Python script))`.
- R4 (unwanted): If `pyproject.toml` is missing or is not valid TOML, `tool` or `tool.rumbo` is not a table, `[tool.rumbo.checks]` is missing, empty or not a table, a step is not a non-empty string, a step named on the command line is not in the table, or the log of `--brief` cannot be opened, then the runner shall run no step, print one line, `rumbo check: environment broken (<reason>)`, and exit with code 3; if a command-line option of the script is not recognised, then the runner shall print the usage and that line on standard error and exit with code 3, never with argparse's 2; and if any other exception is raised during the run (an interrupt is not caught), then the runner shall print one line, `rumbo check: environment broken (internal error: <type>: <message>)`, and exit with code 3, never 1.
- R5 (ubiquitous): The runner shall replace `{base}` in a step, also inside a longer argument (`--base={base}`), with the value of `--base` when it is given, otherwise with the first of `origin/main`, `origin/master`, `origin/HEAD`, `main` and `master` for which `git rev-parse --verify -q <candidate>^{commit}` succeeds in the project root, otherwise with `main`.
- R6 (ubiquitous): The runner shall expand a `{changed}` argument to the `.py` files under the project root that were added, copied, modified or renamed between the merge base of `{base}` and `HEAD`, or between `HEAD` and the working tree (staged or not), or that are untracked and not ignored, keeping those that exist, without duplicates, sorted, as paths relative to the project root, one argument per file and a path that begins with `-` as `./<path>`; it shall resolve the list once per run and only when a step that runs uses `{changed}`; when the list is empty it shall not run the step and shall report it green as `ok   <name> (no changed files)`; when the command with the files in it does not fit one command line (30,000 characters on Windows, 8,000 when the program is a `.cmd` or `.bat` file, 120,000 bytes elsewhere) it shall expand `{changed}` to `.` and add the note `N changed files: too long for one command line, whole tree` to the step line as in R9; and when more than three files are passed the step header shall name the first three, then `(+N more)`.
- R7 (ubiquitous): The runner shall never put in `{changed}` a path that begins with `spikes/` relative to the project root, whether the file is committed, modified or untracked; a `spikes` directory deeper in the tree and a file whose name only begins with `spikes` stay in the list.
- R8 (optional): Where `--full` is given, the runner shall expand `{changed}` to `.` (the whole tree), run the step even when no file changed, and leave the steps without `{changed}` as they are.
- R9 (unwanted): If git cannot give a base for `{changed}` (git cannot be started, the project root is not inside a repository, the repository has no commit, or `{base}` is not a known ref), then the runner shall expand `{changed}` to `.`, add the note `no git base: whole tree` to the step line, be it green, red or environment (`ok   <name> (<note>)`, `FAIL <name> (<detail>; <note>)`, `ENV  <name> (<detail>; <note>)`), and keep the exit code the steps deserve; `{base}` shall keep the value of R5 (`main` without git, outside a repository or without a commit).
- R10 (optional): Where `--keep-going` is given, the runner shall run every step, name every red step in the final line and let a broken environment win over a failure (exit code 3 rather than 1, the final line then naming the steps whose environment is broken); without it, the runner shall stop at the first step that is not green.
- R11 (ubiquitous): The runner shall classify each step by how it ended and use three exit codes, never 2: a step that exits 0 is green; a red step is `FAIL <name> (<detail>)`, final line `rumbo check: red (<names>)`, exit code 1; a broken environment is `ENV  <name> (<detail>)`, final line `rumbo check: environment broken (<names>)`, exit code 3. Asking nothing, the runner shall report as a broken environment a step whose program, right after its launchers and their options (not after `-m`), is a `.py` path that is not a file under the project root (`scripts/x.py: missing` for `uv run python scripts/x.py`; nothing is started), a step whose program is not found (`<program>: not found`) or whose command line the system refuses (`<tool>: command line too long for the system`), and a step whose return code says that the process died: a negative code (`<tool> killed by signal <n>`, or `<tool> crashed (0x<code as 32 bits>)` from -128 down), 129 to 159 (`<tool> killed by signal <n> (exit <code>)`) or 0xC0000000 and above (`<tool> crashed (0xC0000005)`). The runner shall report a step that exits 1 as red, `FAIL <name> (exit 1)`, unless its launcher cannot start its interpreter: it asks the launcher, never the tool, by running the words of the step up to and including the interpreter it names (`python`, `python3`, `py`), then `--version` (`python --version` for `python -m compileall`), or else, behind `uv` and `run`, those launchers and their options, then `python --version` (`uv run --offline python --version` for `uv run --offline pytest -q`; uv's `-m`, `--module`, `-s`, `--script` and `--gui-script` are left out), and when that does not exit 0 the step is `ENV  <name> (<tool> exit 1: <words asked> fails too)`; a step with no such launcher (a bare tool, `uvx`, a program run by its path) is red on exit 1 and nothing is asked.
- R12 (event): When a step exits with a code above 1 that is not a crash code of R11, the runner shall report a broken environment, asking nothing, `ENV  <name> (<tool> exit <code>: <reason>)`, when `ENV_EXITS` holds the code for the tool, or for the tool and the argument right after it (`ruff check` 2, `abnormal termination`; `pyright` 3, `config file could not be read or parsed`); otherwise it shall run the step's launchers and tool once with `--version` (`uv run pytest --version` for `uv run pytest --cov`) and report the step red, `FAIL <name> (<tool> exit <code>)`, when that exits 0, and a broken environment, `ENV  <name> (<tool> exit <code>)`, when it does not. The runner shall never run a second time a tool that is a path, a `.py` file, a file of the project root or inline `-c` code: it asks the launcher as in R11, and with no launcher to ask such a code is a broken environment. To find the tool the runner shall skip the launchers and their options, with the values of uv's options listed in `UV_VALUE_OPTIONS` (`uv run --group dev pytest` runs `pytest`) and of Python's `-W` and `-X`; after an option of uv it does not know, the next word is a value unless it begins with `-` or is a launcher, a path, a `.py` file or a program on `PATH`. Each question of R11 and R12 shall run in the project root, silently, with no standard input, for at most 60 seconds and once per distinct command in a run; a question that cannot be started or does not answer in time counts as failed.
- R13 (optional): Where `--brief` is given, the runner shall send the step headers, the output of every step (stdout and stderr) and the step lines to `.rumbo-check.log` in the project root instead of the terminal, and shall print exactly one line, `rumbo check: green (N log lines in .rumbo-check.log)`, when the run is green, or otherwise the last 40 lines of the log with every non-ASCII character replaced by `?`, followed by the final line; a log that cannot be opened is the case of R4.
- R14 (ubiquitous): The runner shall be one file that imports only the Python standard library and no networking module, shall run as `python scripts/rumbo_check.py` from the project root with no package installed, and the `rumbo check` subcommand shall pass its step names and options (`--brief`, `--keep-going`, `--base`, `--full`, `--dir`) to the same module and return its exit code.

## Acceptance criteria
<!-- pytest tests / Hypothesis properties that verify them (path::test). A test covers a requirement
     by mentioning its full id, e.g. `F-000N.R1`, in its docstring; `rumbo trace` builds the table. -->
In `tests/test_check.py`; each test carries its requirement id in a comment. `uv run rumbo trace` prints which tests cover each requirement.

End to end: `scripts/e2e.sh` runs `uv run python scripts/rumbo_check.py`, `make check` and
`rumbo check --dir .` in a generated project, requires exit 3 for an unknown step, and runs the
runner with `--brief` with the four model credentials unset. The CI job `check` runs
`uv run python scripts/rumbo_check.py` on Linux, macOS and Windows.

## Data contracts and ownership
<!-- What two sessions (or two agents) must agree on BEFORE writing code. Omitting explicit data
     structures is what breaks parallel work (docs/method.md, section 3.1). -->
- Types / schemas: `[tool.rumbo.checks]` in `pyproject.toml`, a non-empty table `step name = "command"` whose file order is the execution order; the placeholders `{base}` (replaced anywhere in a token) and `{changed}` (a token of its own); the log `.rumbo-check.log` in the project root, written as bytes (the runner's own lines encoded as ASCII, the steps' output as they emitted it); the step header `--- <name>: <command words>`; the step lines `ok   <name>`, `ok   <name> (<note>)`, `FAIL <name> (<detail>)`, `ENV  <name> (<detail>)` and, with a note, `FAIL <name> (<detail>; <note>)`, `ENV  <name> (<detail>; <note>)`; the notes `no changed files`, `no git base: whole tree`, `N changed files: too long for one command line, whole tree`; the final lines `rumbo check: green (N steps)`, `rumbo check: red (<names>)`, `rumbo check: environment broken (<names or reason>)` and, with `--brief`, `rumbo check: green (N log lines in .rumbo-check.log)`; `ENV_EXITS: dict[str, dict[int, str]]` (tool, or tool and sub-command, to code to reason), every key with its page in `ENV_EXIT_SOURCES`; `DEFAULT_BASES`, the candidates of R5, the same tuple as in the anti-cheat sensor (two standalone files, kept equal by hand).
- Signatures: `main(argv: list[str] | None = None) -> int` with `[STEP ...] [--brief] [--keep-going] [--base REF] [--full] [--dir DIR]`; `load_steps(root) -> dict[str, str]`, `parse_command(name, command, base) -> list[str]` and `select_steps(steps, requested) -> dict[str, str]`, which raise `ConfigError`; `split_command(command) -> list[str]`; `resolve_base(explicit, root) -> str`; `resolve_changed(base, root, full) -> Changed(files, note)`; `plan_steps(commands, base, root, full) -> dict[str, Step]`, with `Step(argv, note, skip, shown)`; `run_step(name, argv, root, log, note="", *, shown=None, probes=None) -> StepResult(name, status, detail, note)`; `run_steps(steps, root, log, keep_going) -> list[StepResult]`, which holds the answers of a run (`probes`); `verdict(results) -> tuple[int, str]`; `cmd_check(args) -> int` in `src/rumbo/cli.py`.
- Shared invariants: `GREEN, RED, ENV = 0, 1, 3`, and 2 is never a verdict because it means "block" in Claude Code hooks; exit 1 is returned for a red step and for nothing else (an error of the runner itself is 3); a configuration error of the table or of the requested steps prints one line on stdout and runs nothing; with `--keep-going` 3 wins over 1; a question is asked only after a step ended with a code that is not 0, a crash code or a code of `ENV_EXITS`, at most one per failing step and one per distinct command per run, and it never runs a program of the project; the script is identical in every project (`rumbo update` refreshes the copy in `scripts/`).
- Files owned by this spec (one writer): `src/rumbo/sensors/check.py`, `tests/test_check.py`, `cmd_check` and the `check` subparser in `src/rumbo/cli.py`.
- Interfaces touched that other specs depend on: the exit codes and the `--brief` output read by the `Stop` hook (F-0001); the `sensor` step, which receives `{base}` (F-0003); the table rendered by rigor and by adoption, and the copy of the script made by `init` and `update` (F-0004); gate G4, which runs the script and reads its exit code (F-0005).

## Evals (LLM products)
<!-- case IDs, threshold -->
Not applicable: the runner calls no model.

## NFR
0 network modules imported and 0 dependencies (R14) · questions: none after a green step, at
most one extra process per failing step and one per distinct command per run, timeout 60 s
(R11, R12) · `--brief` prints at most 41 lines (R13) · cost 0 USD · platforms: the CI job
`check` runs the runner on Linux (Python 3.11, 3.12, 3.13), macOS (3.12) and Windows (3.13).
No latency target is set for the runner: its duration is that of the steps.

## Out of scope
- Which steps the table holds: the steps by rigor and kind (ADR-0011) and the `{changed}` steps
  written by adoption (ADR-0013) are rendered by the scaffold (F-0004, `tests/test_scaffold.py`).
- What the steps do, network included: the runner starts what the table says. The `audit` step of
  the `mvp` table runs `pip-audit`, which needs the network; "offline" here means the runner
  itself and no model credential (ADR-0003).
- What the `Stop` hook and gate G4 do with the exit code (F-0001, F-0005).
- Parsing the output of a tool: tools are black boxes read by exit code (ADR-0006, ADR-0018,
  ADR-0022). So, as ADR-0022 accepts: a tool that exits 1 is red whatever made it fail, a broken
  configuration or a missing network included, when its launcher answers or it has none (`uvx`,
  a bare tool); the codes left to the question of R12 (pytest 2 to 6, pyright 2 and 4,
  `ruff format` 2) are red when the tool answers; a tool that uses 129 to 159 for its own
  findings is read as killed by a signal.
- Which program a bare name is: outside the cases of R2 the system decides, among the entries
  of `PATH`. A bare `python` is the first one found, usually not the project's environment;
  steps are written with `uv run python`.
- Read in the code and not verified by a test, so not stated as requirements: a `pyproject.toml`
  that cannot be read or is not UTF-8 is a configuration error; any `OSError` other than
  "not found" when a step starts is a broken environment; a configuration error with `--brief`
  leaves the previous `.rumbo-check.log` untouched; `NoDefaultCurrentDirectoryInExePath` is
  also set while git is started.

## Questions found while writing the spec (for the human)
- R3 validates only the steps that are about to run: `rumbo check lint` is green even when
  another step of the table holds `&&`. Operators glued to a word (`pytest>out.txt`, `a&&b`) are
  not detected; they reach the program as literal arguments.
- R4: only the script maps a usage error to 3. `rumbo check --no-such-flag` is refused by the
  CLI's own parser and exits 2.
- R1 covers the headers, the step lines and the one-line errors; the final line is printed as
  it is. A failing step whose name has a non-ASCII character (a quoted TOML key) is named so in
  `rumbo check: red (<names>)`, and on a console that cannot encode it the run ends with
  `internal error: UnicodeEncodeError` and exit code 3 instead of 1 (seen with
  `PYTHONIOENCODING=ascii`). ADR-0006 and the module docstring say all output is ASCII.
- No decision records the splitting and the lookup of R2, the candidates of R5 or the limits of
  R6: ADR-0006 still says `shlex.split(value, posix=True)` and "`origin/main` [...] else `main`"
  (so does the help of `--base` in `src/rumbo/cli.py`), and ADR-0013 names `git diff` without
  `HEAD` for the uncommitted changes.
- The PRD measures "Platforms" as Linux, macOS, Windows × Python 3.11–3.13; the CI matrix runs
  the three versions on Linux only, 3.12 on macOS and 3.13 on Windows.
