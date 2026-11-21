# ADR-0023: The files that shadow `pyproject.toml` and the Makefile are protected, and so is `.git/`
Status: accepted    Date: 2026-11-16

Completes ADR-0005 (the hard rules as hooks) and ADR-0019 (`pyproject.toml` is protected except
its dependency tables). Both stand; spec F-0001 (R1, R3, R4, R7) needs the wider list.

## Context
The guard protects the files that define "green": the Makefile, `pyproject.toml`, the sensors,
CI. The audit before v0.4.1 found two ways around the list that need no forbidden file at all.

1. **A tool takes its configuration from another file** (finding G2). Each tool of the checks
   has a file of its own besides its table in `pyproject.toml`: ruff (`ruff.toml`,
   `.ruff.toml`), pytest (`pytest.ini`, `tox.ini`, `setup.cfg`), mypy (`mypy.ini`, `.mypy.ini`),
   pyright (`pyrightconfig.json`), coverage (`.coveragerc`), import-linter (`.importlinter`),
   uv (`uv.toml`); make reads `GNUmakefile` and `makefile` before `Makefile`. Measured by the
   audit: a new `ruff.toml` with `ignore = ["E", "F"]` turned a red lint step green, and a
   `pytest.ini` deselected the failing test. None of those names was protected: the edit tool,
   the check after a shell command and the git hook all let them pass.
2. **`.git/` was not protected** (finding G3). An edit tool could write `.git/hooks/pre-commit`
   or `.git/config` (`core.hooksPath`, `rumbo.human`); in the shell only `rm` on the hooks was
   matched. `git status` never shows that directory, so no later layer saw it either.

`conftest.py` shadows nothing by itself but can skip or rewrite tests through pytest hooks.

## Drivers (non-functional requirements)
The verification contract has one owner, the human. A rule the agent meets must be cheap to
explain ("that name configures a tool: ask"). An agent must keep writing fixtures without asking.
The edit rule stays pure logic (no subprocess, F-0001.R12).

## Options considered
1. **Make every tool ignore the other files** (`--config pyproject.toml`, `-c pyproject.toml`
   in each check). Rejected: not every tool has the option, adopted projects keep their own
   commands, and an editor or a plain `ruff check` would still read the shadow file.
2. **Protect the names at the project root only.** Rejected: ruff takes the configuration file
   nearest to each file it checks, and pytest looks for its file from the directory of its
   arguments upwards, so `tests/ruff.toml` can work as well as one at the root. (Not measured
   by the audit, which placed its files at the root: the human confirms or narrows this.)
3. **Protect the names in any directory, and `.git/` (chosen).**
4. **Protect `conftest.py` too.** Rejected: it is where fixtures live.

## Decision and rationale
- `SHADOW_CONFIGS` in the guard: `ruff.toml`, `.ruff.toml`, `pytest.ini`, `tox.ini`,
  `setup.cfg`, `mypy.ini`, `.mypy.ini`, `pyrightconfig.json`, `.coveragerc`, `.importlinter`,
  `uv.toml`. A file with one of these names is protected **in any directory**. `GNUmakefile`
  and `makefile` join the Makefile at the project root.
- `.git/` is a protected prefix: the edit tools cannot write into it (also when the git
  directory is above the project root or belongs to a worktree), and shell commands that write,
  move or delete inside it are blocked before they run. `.github/`, `.gitignore` and
  `.gitattributes` are other names: the first was already protected, the other two stay free.
- Names are compared without case and without the spellings Windows opens as the same file
  (`Makefile.`, `Makefile::$DATA`): on Windows and macOS `Ruff.toml` is `ruff.toml`.
- All three layers apply the same list: the edit tool (blocked), the check after a shell
  command and at Stop (reported; after a command also when an ignore rule hides from `git
  status` a file that appeared or changed while it ran, ADR-0024), and the `pre-commit` hook
  (refused for an agent, except a path staged exactly as it is in `MERGE_HEAD` while a merge is
  concluded: that change comes from the merged branch).
- `conftest.py` stays editable. What it can do to the tests (new `pytest_*` hooks,
  `collect_ignore`) is the anti-cheat sensor's to report (audit finding S4).
- The exception of ADR-0019 does not grow: `uv add` writes none of these files.

## Consequences (+/−)
+ A red check can no longer be turned green by adding a configuration file next to
  `pyproject.toml`, nor the hooks disabled by writing into `.git/`.
+ One rule for the agent: tool configuration belongs to the human, whatever the file.
− An adopted project that keeps its configuration in `setup.cfg` or `tox.ini` asks the human
  for every change to it, as it already did for `pyproject.toml`.
− A fixture file that happens to be named `setup.cfg` or `tox.ini` under `tests/` is protected
  too; the human edits it or the override in `.claude/settings.local.json` is used.
− The list needs maintenance when a tool gains a configuration file.
− The shell rules for `.git/` are heuristics: a script that writes there is seen only through
  its effect, when HEAD moves (ADR-0024).

## Fitness function
`tests/test_guard.py::test_files_that_shadow_pyproject_are_protected`,
`::test_a_new_shadow_file_is_caught_after_the_command_and_at_commit`,
`::test_the_git_directory_is_protected_and_its_neighbours_are_not`.

## Reversibility: high
