#!/usr/bin/env python3
"""RUMBO verification runner: runs the steps of ``[tool.rumbo.checks]`` without a shell.

The steps live in ``pyproject.toml``, one command string per step, in execution order::

    [tool.rumbo.checks]
    lint = "uv run ruff check ."
    test = "uv run pytest"

Each string is split into words and run with ``subprocess.run`` and no shell, so the harness
behaves identically on Windows, macOS and Linux. Single and double quotes group words
(``"two words"``, ``'a && b'``); nothing else is special: a backslash is an ordinary character
(``python scripts\\hello.py`` on Windows), so a quote cannot be escaped, only wrapped in the
other kind. Shell operators (``&&``, ``|``, ``;``, redirections) and ``$variables`` are
configuration errors: a step that needs logic becomes a small Python script.

The program of a step (its first word) is looked up before it runs. With a directory part
(``tools/lint``, ``.venv/Scripts/python``) it is taken against the project root, never against
the directory the runner was started from, and on Windows ``.exe``, ``.cmd``, ``.bat`` and
``.com`` are tried. A bare name is left to the system, except on Windows when ``PATH`` holds
it only as a ``.cmd``/``.bat`` shim (``npx``, ``pnpm``) outside the project: the shim is run
by its path, still without a shell. A name is never taken from the project: relative
entries of ``PATH`` are not searched, a batch file inside the project root is passed over,
and while a step, a probe or git is started ``NoDefaultCurrentDirectoryInExePath`` is set,
so that Windows does not look in the current directory either (a ``uv.cmd`` or ``uv.exe``
written into the root is not run in place of ``uv``). A batch file is executed by ``cmd.exe``
whatever the caller does, and ``cmd.exe`` parses its command line again, so an argument that
contains one of ``& | < > ^ % "`` or a line break is refused for such a program
(environment, exit 3) instead of being passed. Steps do not read the runner's standard input.

A bare ``python`` is whatever the system finds first, usually not the project's environment
(``python -m pytest`` then ends in "No module named pytest", exit 1, red): write
``uv run python ...``.

Two placeholders exist:

* ``{base}``: ``--base`` if given, else the first of ``origin/main``, ``origin/master``,
  ``origin/HEAD``, ``main``, ``master`` that names a commit (the candidates of the anti-cheat
  sensor), else ``main``.
* ``{changed}`` (ADR-0013): the ``.py`` files that differ from the merge base with ``{base}``
  (``git diff --name-only --diff-filter=ACMR <base>...HEAD``) plus the uncommitted changes and
  the untracked files, deduplicated, existing and sorted, one argument per file. Paths under
  ``spikes/`` are never part of it (ADR-0020): prototypes are not checked. It must be an
  argument of its own (``--files={changed}`` is a configuration error). A file whose name
  starts with ``-`` is passed as ``./name``, so no tool takes it for an option. When the list
  is empty the step is not run and is reported ``ok   <name> (no changed files)``. When there
  is no usable git base (not a repository, no commits, unknown ref) it expands to ``.`` and
  the step line says ``no git base: whole tree``. When the files do not fit one command line
  (30,000 characters on Windows, where the system takes 32,767, and 8,000 when the program is
  a batch file, where ``cmd.exe`` takes 8,191; 120,000 bytes elsewhere) it expands to ``.``
  too and the step line says ``N changed files: too long for one command line, whole
  tree``. Both notes are shown on green, red and environment lines. ``--full``
  always expands it to ``.``. The header of a step names the first three files, then
  ``(+N more)``.

Usage: ``python scripts/rumbo_check.py [STEP ...] [--brief] [--keep-going] [--base REF]
[--full]``.
Without step names every step runs in table order and the run stops at the first failing step;
``--keep-going`` runs them all. Named steps run a subset, still in table order. Without
``--brief`` each step streams to the terminal; with it all output goes to ``.rumbo-check.log``
and only a one-line verdict is printed on success, or the last 40 log lines on failure.

Exit codes: 0 green · 1 a step failed · 3 broken environment (executable not found, a step
that crashed, the table is missing or malformed, an unknown step was requested, the log
cannot be written, or the runner itself hit an error it did not foresee). Exit 2 is never
used: it means "block" in Claude Code hooks. With ``--keep-going``, 3 wins over 1. All output
of the runner itself is ASCII.

A step is red only when its tool ran and judged the code (ADR-0018, refined by ADR-0022).
In order, for a step that did not exit 0:

1. Before it runs: a project script named right after the launchers (``uv run python
   scripts/x.py``) that does not exist is the environment (``scripts/x.py: missing``).
2. A crash is the environment whatever else is true: a negative return code (killed by a
   signal), 129 to 159 (``128 + signal``, how shells and launchers report it) and a Windows
   NTSTATUS failure (``0xC0000000`` and above, also when read as a negative number).
3. A code that only a broken setup produces is the environment (``ENV_EXITS``: ``ruff
   check`` 2 and pyright 3, an invalid configuration).
4. Exit 1 is red, unless the launcher cannot start its interpreter. The question is about
   the launcher, never about the tool: the launcher words of the step up to and including
   ``run``, then ``python --version`` (``uv run --offline python --version`` for ``uv run
   --offline pytest -q``), or the words up to the interpreter the step names (``python
   --version`` for ``python -m compileall``). ``uv run`` exits 1 when it cannot build the
   environment, and then this fails too: the environment is broken. A tool that has no
   ``--version`` is red on 1 like any other. A step with no launcher (a bare tool, ``uvx``,
   a program by its path) is red on 1 and nothing is asked.
5. Any other code is asked the question of ADR-0018: the same launcher and tool are run with
   ``--version`` (``uv run pytest --version`` for ``uv run pytest --cov``). If that works the
   tool started and what it refused is the code: the step failed. If it does not, the
   environment is broken. A program of the project (a path, a ``.py`` file, a file of the
   root) is never run a second time: its launcher is asked instead as in 4, and with no
   launcher (``scripts/test.sh``) such a code is the environment.

Each distinct question is asked once in a run, has 60 seconds, and is silent. The options of
``uv`` that take a value are known (``uv run --group dev pytest`` runs ``pytest``); for an
option this file does not know, the next word is its value unless it is a launcher, a path
or a program on ``PATH``.

Standard library only: it is copied verbatim into every RUMBO project (ADR-0006).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, NoReturn

GREEN, RED, ENV = 0, 1, 3
LOG_NAME = ".rumbo-check.log"
CHANGED = "{changed}"
WHOLE_TREE = "."
SPIKES = "spikes/"  # ADR-0020: prototypes are never checked, not even when they changed
TAIL_LINES = 40
SHELL_OPERATORS = frozenset({"&&", "||", "|", ";", ">", "<", ">>", "2>&1"})
LAUNCHERS = frozenset({"uv", "uvx", "run", "python", "python3", "py", "-m"})
INTERPRETERS = frozenset({"python", "python3", "py"})
UV = frozenset({"uv", "uvx"})
INLINE_CODE = "-c"  # ``python -c "..."``: what runs is the interpreter, there is no tool
# The options of ``uv``, ``uv run`` and ``uvx``, read from ``uv --help``, ``uv run --help`` and
# ``uvx --help`` of uv 0.12.19: those printed with a ``<VALUE>`` take the next word, so that
# ``uv run --group dev pytest`` names ``pytest``. An option in neither tuple (a newer uv) is
# read by the word that follows it: see ``_takes_value``.
UV_VALUE_OPTIONS = (
    "--allow-insecure-host", "--build-constraints", "--cache-dir", "--color", "--config-file",
    "--config-setting", "--config-settings-package", "--constraints", "--default-index",
    "--directory", "--env-file", "--exclude-newer", "--exclude-newer-package", "--extra",
    "--extra-index-url", "--find-links", "--fork-strategy", "--from", "--group", "--index",
    "--index-strategy", "--index-url", "--keyring-provider", "--link-mode",
    "--no-binary-package", "--no-build-isolation-package", "--no-build-package",
    "--no-editable-package", "--no-extra", "--no-group", "--no-sources-package",
    "--only-group", "--overrides", "--package", "--prerelease", "--prerelease-package",
    "--project", "--python", "--python-platform", "--refresh-package", "--reinstall-package",
    "--resolution", "--torch-backend", "--upgrade-group", "--upgrade-package", "--with",
    "--with-editable", "--with-requirements", "-C", "-P", "-b", "-c", "-f", "-i", "-p", "-w",
)  # fmt: skip
UV_FLAGS = (
    "--active", "--all-extras", "--all-groups", "--all-packages", "--compile-bytecode",
    "--exact", "--frozen", "--gui-script", "--help", "--isolated", "--lfs", "--locked",
    "--managed-python", "--module", "--no-binary", "--no-build", "--no-build-isolation",
    "--no-cache", "--no-config", "--no-default-groups", "--no-dev", "--no-editable",
    "--no-env-file", "--no-index", "--no-managed-python", "--no-progress", "--no-project",
    "--no-python-downloads", "--no-sources", "--no-sync", "--offline", "--only-dev", "--quiet",
    "--refresh", "--reinstall", "--script", "--system-certs", "--upgrade", "--verbose",
    "--version", "-U", "-V", "-h", "-m", "-n", "-q", "-s", "-v",
)  # fmt: skip
# ``uv run`` flags that say what the command word is (a module, a script): they do not belong
# in the question ``uv run ... python --version``.
UV_COMMAND_FLAGS = ("-m", "--module", "-s", "--script", "--gui-script")
PYTHON_VALUE_OPTIONS = ("-W", "-X")  # ``python --help``: "-W arg", "-X opt"
PROBE_TIMEOUT = 60  # seconds
WINDOWS = os.name == "nt"
# The candidates of the anti-cheat sensor (DEFAULT_BASES in check_tests_not_weakened.py), in
# its order. The two files are standalone copies: keep the tuples equal by hand.
DEFAULT_BASES = ("origin/main", "origin/master", "origin/HEAD", "main", "master")
FALLBACK_BASE = "main"
# One command line: CreateProcess takes 32,767 characters; elsewhere the limit (ARG_MAX) also
# holds the environment and is far larger. Both values leave a wide margin.
WINDOWS_COMMAND_LINE = 30_000  # characters
BATCH_COMMAND_LINE = 8_000  # characters: cmd.exe, which runs a batch file, takes 8,191
POSIX_COMMAND_LINE = 120_000  # bytes
TOO_LONG_WINERROR = 206  # ERROR_FILENAME_EXCED_RANGE: Python raises it as FileNotFoundError
HEADER_FILES = 3  # files of {changed} named in a step header before "(+N more)"
WINDOWS_SUFFIXES = (".exe", ".cmd", ".bat", ".com")
PATH_SUFFIXES = (".com", ".exe", ".bat", ".cmd")  # the order of the default PATHEXT
# Windows looks for a bare program name in the current directory before PATH, unless this
# variable exists in the environment of the process that starts it (measured: Python 3.13
# on Windows 11; cmd.exe documents it as NeedCurrentDirectoryForExePath).
NO_CURRENT_DIRECTORY = "NoDefaultCurrentDirectoryInExePath"
BATCH_SUFFIXES = (".cmd", ".bat")
BATCH_UNSAFE = frozenset('&|<>^%"\r\n')  # what cmd.exe interprets in a batch file's arguments
SIGNAL_EXIT = 128  # a shell or a launcher reports "killed by signal n" as exit 128 + n
SIGNALS = 32
NTSTATUS_ERROR = 0xC0000000  # Windows: severity "error", what a crashed process returns

# ADR-0022: exit codes that are the environment with no question asked. An entry needs two
# things: the tool's documentation gives the code to a failure of the tool itself (the page is
# in ENV_EXIT_SOURCES; never add a code from memory), and real runs show that code the agent
# wrote does not produce it (the runs are listed in ADR-0022). Keys are the tool, or "<tool>
# <sub-command>" when the meaning depends on it. What stays out, and takes the probe:
# - pytest 3 ("internal error"): code under test that exits at import (an unguarded
#   ``sys.exit(main())``) ends in "mainloop: caught unexpected SystemExit!" and exit 3;
# - pytest 2, 4 (usage error, "including ... a conftest.py that fails to import"), 5 and 6;
# - pyright 2 ("fatal error") and 4 ("illegal command-line parameters"): 4 is also a path on
#   the command line that the change removed, and no run shows that bad code cannot give 2;
# - ``ruff format`` 2: documented like ``ruff check`` 2, but a file with a syntax error also
#   exits 2 (ruff 0.17.4), while ``ruff check`` reports it with 1;
# - mypy: its documentation does not describe its exit codes.
ENV_EXITS: dict[str, dict[int, str]] = {
    "ruff check": {2: "abnormal termination"},
    "pyright": {3: "config file could not be read or parsed"},
}
ENV_EXIT_SOURCES = {
    "ruff check": "https://docs.astral.sh/ruff/linter/#exit-codes",
    "pyright": "https://github.com/microsoft/pyright/blob/main/docs/command-line.md",
}


class ConfigError(Exception):
    """The step table or the requested steps cannot be used."""


class CannotStart(Exception):
    """The program of a step cannot be started; the message is the detail of the step."""


@dataclass(frozen=True)
class StepResult:
    name: str
    status: int  # GREEN, RED or ENV
    detail: str  # "", "exit 1", "pytest exit 2", "ruff: not found"
    note: str = ""  # what {changed} stood for: "no changed files", "no git base: whole tree"

    @property
    def line(self) -> str:
        if self.status == GREEN:
            return f"ok   {self.name} ({self.note})" if self.note else f"ok   {self.name}"
        label = "FAIL" if self.status == RED else "ENV "
        text = f"{self.detail}; {self.note}" if self.note else self.detail
        return f"{label} {self.name} ({text})"


# ------------------------------------------------------------------- configuration
def load_steps(root: Path) -> dict[str, str]:
    """The ``[tool.rumbo.checks]`` table, in file order."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        raise ConfigError(f"no pyproject.toml in {root}")
    try:
        # utf-8-sig: Windows editors and PowerShell write a byte order mark; tomllib refuses it
        data = tomllib.loads(pyproject.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ConfigError(f"cannot read pyproject.toml: {exc.strerror or exc}") from exc
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"pyproject.toml is not valid TOML: {exc}") from exc
    path = ("tool", "rumbo", "checks")
    checks: Any = data
    for depth, key in enumerate(path):
        if not isinstance(checks, dict):
            raise ConfigError(f"[{'.'.join(path[:depth])}] must be a table in pyproject.toml")
        checks = checks.get(key)
        if checks is None:
            raise ConfigError("[tool.rumbo.checks] is missing from pyproject.toml")
    if not isinstance(checks, dict) or not checks:
        raise ConfigError('[tool.rumbo.checks] must be a non-empty table of step = "command"')
    for name, value in checks.items():
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"[tool.rumbo.checks] {name} must be a non-empty command string")
    return dict(checks)


def split_command(command: str) -> list[str]:
    """The words of a step. Quotes group; a backslash is an ordinary character.

    ``shlex.split`` takes a backslash for an escape and turned ``scripts\\hello.py`` into
    ``scriptshello.py``. Without escapes a Windows path is written as it is; the price is
    that a quote cannot be escaped, only wrapped in the other kind of quote.
    """
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)


def parse_command(name: str, command: str, base: str) -> list[str]:
    """Split a step into argv (no shell) and substitute ``{base}``.

    ``{changed}`` stays a token of its own for :func:`plan_steps` to expand lazily.
    """
    try:
        argv = split_command(command)
    except ValueError as exc:
        raise ConfigError(f"step {name}: cannot parse command ({exc})") from exc
    if not argv:
        raise ConfigError(f"step {name}: empty command")
    for token in argv:
        if token in SHELL_OPERATORS or token.startswith("$"):
            raise ConfigError(
                f"step {name}: {token!r} needs a shell; steps run without one "
                "(move the logic into a small Python script)"
            )
        if CHANGED in token and token != CHANGED:
            raise ConfigError(
                f"step {name}: {CHANGED} must be an argument of its own, not part of {token!r}"
            )
    return [token if token == CHANGED else token.replace("{base}", base) for token in argv]


def select_steps(steps: dict[str, str], requested: list[str]) -> dict[str, str]:
    """The requested subset, kept in table order; every step when none is requested."""
    unknown = [name for name in requested if name not in steps]
    if unknown:
        raise ConfigError(
            f"unknown step {', '.join(repr(n) for n in unknown)}; "
            f"[tool.rumbo.checks] defines: {', '.join(steps)}"
        )
    if not requested:
        return dict(steps)
    wanted = set(requested)
    return {name: cmd for name, cmd in steps.items() if name in wanted}


def resolve_base(explicit: str | None, root: Path) -> str:
    """``--base``, else the first of ``DEFAULT_BASES`` that names a commit, else ``main``."""
    if explicit:
        return explicit
    for candidate in DEFAULT_BASES:
        probe = _git(["rev-parse", "--verify", "-q", f"{candidate}^{{commit}}"], root)
        if probe is None:
            break
        if probe.returncode == 0:
            return candidate
    return FALLBACK_BASE


@dataclass(frozen=True)
class Changed:
    files: list[str]  # what ``{changed}`` expands to
    note: str = ""  # "no git base: whole tree" when git could not tell


def _git(args: list[str], root: Path) -> subprocess.CompletedProcess[bytes] | None:
    """``git <args>`` in ``root``, its output as bytes; None when git cannot be started."""
    try:
        return subprocess.run(
            ["git", *args], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, check=False
        )
    except OSError:
        return None


def _git_paths(args: list[str], root: Path) -> list[str] | None:
    """NUL-separated paths printed by ``git <args>`` in ``root``; None when git fails."""
    done = _git(args, root)
    if done is None or done.returncode != 0:
        return None
    return [os.fsdecode(raw) for raw in done.stdout.split(b"\0") if raw]


def resolve_changed(base: str, root: Path, full: bool) -> Changed:
    """The files ``{changed}`` stands for: ``.`` with ``--full`` or without a usable git base.

    A file named on a command line bypasses the tool's own exclusions, so ``spikes/`` is
    dropped here (ADR-0020). A name that starts with ``-`` would be read as an option: it is
    passed as ``./name``.
    """
    if full:
        return Changed([WHOLE_TREE])
    fallback = Changed([WHOLE_TREE], "no git base: whole tree")
    merge_base = _git(["merge-base", base, "HEAD"], root)
    if merge_base is None or merge_base.returncode != 0:
        return fallback
    diff = ["diff", "-z", "--relative", "--name-only", "--diff-filter=ACMR"]
    queries = [
        [*diff, f"{base}...HEAD"],
        [*diff, "HEAD"],  # uncommitted: staged AND unstaged (plain `git diff` misses the index)
        ["ls-files", "-z", "--others", "--exclude-standard"],
    ]
    found: set[str] = set()
    for query in queries:
        paths = _git_paths(query, root)
        if paths is None:
            return fallback
        found.update(paths)
    return Changed(
        sorted(
            f"./{p}" if p.startswith("-") else p
            for p in found
            if p.endswith(".py") and not p.startswith(SPIKES) and (root / p).is_file()
        )
    )


@dataclass(frozen=True)
class Step:
    argv: list[str]
    note: str = ""
    skip: bool = False  # nothing to run: reported green with ``note``
    shown: list[str] | None = None  # argv for the header when ``argv`` names many files


def _fits_one_command_line(argv: list[str], root: Path) -> bool:
    """Whether the system can start ``argv``, with a wide margin (see the limits)."""
    if not WINDOWS:
        return sum(len(os.fsencode(arg)) + 1 for arg in argv) <= POSIX_COMMAND_LINE
    try:
        program = _launch_argv(argv[:1], root)[0]
    except CannotStart:
        program = argv[0]
    batch = program.lower().endswith(BATCH_SUFFIXES)
    limit = BATCH_COMMAND_LINE if batch else WINDOWS_COMMAND_LINE
    return len(subprocess.list2cmdline([program, *argv[1:]])) <= limit


def _expand(argv: list[str], files: list[str]) -> list[str]:
    expanded: list[str] = []
    for token in argv:
        expanded.extend(files if token == CHANGED else [token])
    return expanded


def plan_steps(
    commands: dict[str, list[str]], base: str, root: Path, full: bool
) -> dict[str, Step]:
    """Expand ``{changed}``; git is consulted once, and only if some step uses it."""
    changed: Changed | None = None
    plan: dict[str, Step] = {}
    for name, argv in commands.items():
        if CHANGED not in argv:
            plan[name] = Step(argv)
            continue
        if changed is None:
            changed = resolve_changed(base, root, full)
        if not changed.files:
            plan[name] = Step(argv, "no changed files", skip=True)
            continue
        files, note, shown = changed.files, changed.note, None
        if not _fits_one_command_line(_expand(argv, files), root):
            # the whole tree rather than chunks: one run, one verdict, and the line says so
            note = f"{len(files)} changed files: too long for one command line, whole tree"
            files = [WHOLE_TREE]
        elif len(files) > HEADER_FILES:
            more = f"(+{len(files) - HEADER_FILES} more)"
            shown = _expand(argv, [*files[:HEADER_FILES], more])
        plan[name] = Step(_expand(argv, files), note, shown=shown)
    return plan


# --------------------------------------------------------------------------- running
def _launcher_name(token: str) -> str:
    """``uv`` for ``C:/tools/uv.exe``, ``python`` for ``.venv/bin/python``."""
    return Path(token).name.lower().removesuffix(".exe")


def _is_launcher(token: str) -> bool:
    """``uv``, ``run``, ``python``, ``-m``; uv and python also by path (``.venv/bin/python``)."""
    return token in LAUNCHERS or _launcher_name(token) in UV | INTERPRETERS


def _is_path(word: str) -> bool:
    """A word with a directory part; a backslash counts on every system, to stay cautious."""
    return "/" in word or "\\" in word


def _takes_value(option: str, following: str | None, owner: str) -> bool:
    """Whether the word after ``option`` is its value and not the program of the step.

    ``owner`` is whose option it is: ``uv`` or ``python``. An option of uv that this file
    does not know is decided by the next word: a launcher, a path, a ``.py`` file or a
    program found on ``PATH`` is the program; anything else is the value.
    """
    if following is None or "=" in option or option == "--":
        return False
    if owner == "python":
        return option in PYTHON_VALUE_OPTIONS
    if owner != "uv":
        return False
    if option in UV_VALUE_OPTIONS:
        return True
    if option in UV_FLAGS or following.startswith("-") or _is_launcher(following):
        return False
    program = _is_path(following) or following.endswith(".py") or shutil.which(following)
    return not program


@dataclass(frozen=True)
class _Command:
    """A step read as launchers, then the program they run, then its arguments."""

    prefix: list[str]  # the launchers and their options: ``uv run --frozen``
    tool: str | None  # the program they run; None for inline code or nothing but launchers
    interpreter: int | None  # the index in ``prefix`` of ``python``, when the step names it
    runs: bool  # ``uv run``: the launcher starts a program of the environment


def _read_command(argv: list[str]) -> _Command:
    owner = ""  # whose options follow: "uv" or "python"
    interpreter: int | None = None
    runs = False
    index = 0
    while index < len(argv):
        token = argv[index]
        following = argv[index + 1] if index + 1 < len(argv) else None
        if _is_launcher(token):
            name = _launcher_name(token)
            if name in UV:
                owner = "uv"
            elif name in INTERPRETERS:
                owner = "python"
                interpreter = index if interpreter is None else interpreter
            elif name == "run":
                runs = runs or owner == "uv"
            index += 1
        elif not token.startswith("-"):
            return _Command(argv[:index], token, interpreter, runs)
        elif token == INLINE_CODE and owner != "uv":
            return _Command(argv[:index], None, interpreter, runs)
        else:
            index += 2 if _takes_value(token, following, owner) else 1
    return _Command(list(argv), None, interpreter, runs)


def _split_tool(argv: list[str]) -> tuple[list[str], str | None]:
    """(launchers and their options, the program they run): ``uv run --frozen`` and ``pytest``.

    The program is None when there is none to name: inline code or nothing but launchers.
    """
    command = _read_command(argv)
    return command.prefix, command.tool


def _tool_name(argv: list[str]) -> str:
    """The program a step really runs: ``pyright`` for ``uv run pyright``."""
    _, tool = _split_tool(argv)
    return Path(tool or argv[0]).name


def _launcher_probe(argv: list[str]) -> list[str] | None:
    """The question about the launcher of a step (ADR-0022): can it start its interpreter?

    The words up to the interpreter the step names, or up to ``run`` and then ``python``;
    then ``--version``. None when the step has no launcher to ask (a bare tool, ``uvx``, a
    program by its path): the program of the step is never part of this question.
    """
    command = _read_command(argv)
    if command.interpreter is not None:
        return [*command.prefix[: command.interpreter + 1], "--version"]
    if command.runs:
        # without the flags that say what the next word is: `uv run -m python` is no question
        kept = [word for word in command.prefix if word not in UV_COMMAND_FLAGS]
        return [*kept, "python", "--version"]
    return None


def _probe_argv(argv: list[str], root: Path | None = None) -> list[str] | None:
    """The step's command up to and including the tool, then ``--version`` (ADR-0018).

    A program of the project (a path, a ``.py`` file, a file of ``root``) and inline code are
    never run a second time: the question goes to the launcher. None when nobody is left to
    ask.
    """
    prefix, tool = _split_tool(argv)
    if tool is None or _is_path(tool) or tool.endswith(".py"):
        return _launcher_probe(argv)
    if root is not None and (root / tool).is_file():
        return _launcher_probe(argv)
    return [*prefix, tool, "--version"]


def _missing_script(argv: list[str], root: Path) -> str | None:
    """The project script a step runs (``uv run python scripts/x.py``) when it is not there."""
    prefix, tool = _split_tool(argv)
    if tool is None or not prefix or prefix[-1] == "-m" or not tool.endswith(".py"):
        return None
    return None if (root / tool).is_file() else tool


def _outside(path: Path, root: Path) -> bool:
    """Whether ``path`` is absolute and not the project root nor anything under it."""
    if not path.is_absolute():
        return False
    try:
        resolved, base = path.resolve(), root.resolve()
    except OSError:
        return False
    return resolved != base and base not in resolved.parents


def _shim_on_path(program: str, root: Path) -> str | None:
    """The batch shim that a bare ``program`` stands for on Windows, when it has one.

    ``PATH`` is read here and not by ``shutil.which``, which looks in the current directory
    first: relative entries are skipped and a batch file inside the project is passed over.
    None when ``PATH`` also holds the name as an ``.exe`` or ``.com``: the system finds that
    one itself, as it always did, and its arguments need no care about ``cmd.exe``.
    """
    known = Path(program).suffix.lower() in PATH_SUFFIXES
    names = [program] if known else [f"{program}{suffix}" for suffix in PATH_SUFFIXES]
    shim: str | None = None
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        directory = Path(entry)
        if not entry or not directory.is_absolute():
            continue
        for name in names:
            candidate = directory / name
            if not candidate.is_file():
                continue
            if not name.lower().endswith(BATCH_SUFFIXES):
                return None
            if shim is None and _outside(candidate, root):
                shim = str(candidate)
    return shim


def _launch_argv(argv: list[str], root: Path) -> list[str]:
    """``argv`` with its program made findable; ``argv`` itself when the system finds it.

    A program with a directory part is taken against ``root`` (CreateProcess would take it
    against the runner's own directory, and adds no extension to a path). A bare name is
    rewritten only on Windows and only when ``PATH`` holds it as nothing but a batch shim,
    outside the project: CreateProcess does not look for one. Raises :class:`CannotStart`.
    """
    program = argv[0]
    if "/" in program or (WINDOWS and "\\" in program):
        path = root / program  # an absolute program stays what it is
        runnable = path.suffix.lower() in WINDOWS_SUFFIXES
        # `node_modules/.bin/eslint` sits next to `eslint.cmd`: on Windows the shim is the one
        suffixed = [Path(f"{path}{suffix}") for suffix in WINDOWS_SUFFIXES]
        candidates = [*suffixed, path] if WINDOWS and not runnable else [path]
        found = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
        if found is None:
            raise CannotStart(f"{path.name}: not found")
    else:
        found = _shim_on_path(program, root) if WINDOWS else None
        if found is None:
            return argv
    launched = [found, *argv[1:]]
    if WINDOWS and found.lower().endswith(BATCH_SUFFIXES):
        for word in launched:  # the path of the batch file is on that command line too
            unsafe = sorted(BATCH_UNSAFE.intersection(word))
            if unsafe:
                raise CannotStart(
                    f"{Path(found).name}: a batch file, and cmd.exe would interpret "
                    f"{unsafe[0]!a} in {word!a} (steps run without a shell: call it from a "
                    "small Python script)"
                )
    return launched


@contextlib.contextmanager
def _not_from_the_current_directory() -> Iterator[None]:
    """While a process is started, Windows does not look for it in the current directory.

    The variable is read by the process that starts another one, so it is set in the runner's
    own environment, for the time of the call, and inherited by what it starts.
    """
    if not WINDOWS:
        yield
        return
    before = os.environ.get(NO_CURRENT_DIRECTORY)
    os.environ[NO_CURRENT_DIRECTORY] = "1"
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(NO_CURRENT_DIRECTORY, None)
        else:
            os.environ[NO_CURRENT_DIRECTORY] = before


def _start(argv: list[str], root: Path, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    """Run a step or its probe: in the root, without a shell, with no standard input."""
    launched = _launch_argv(argv, root)
    with _not_from_the_current_directory():
        return subprocess.run(
            launched, cwd=root, shell=False, stdin=subprocess.DEVNULL, check=False, **kwargs
        )


def _answers(probe: list[str], root: Path, probes: dict[tuple[str, ...], bool]) -> bool:
    """Ask the environment one silent question: does ``probe`` start and exit 0?

    Any doubt (not found, timeout, non-zero) answers no. ``probes`` remembers the answers of
    a run, so that several failing steps ask the same question once.
    """
    key = tuple(probe)
    if key not in probes:
        try:
            done = _start(
                probe,
                root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PROBE_TIMEOUT,
            )
        except (CannotStart, OSError, subprocess.TimeoutExpired):
            probes[key] = False
        else:
            probes[key] = done.returncode == 0
    return probes[key]


def _crash(code: int) -> str | None:
    """How the process died, when its return code says that it died (ADR-0022); else None."""
    if code < 0:
        if code > -SIGNAL_EXIT:
            return f"killed by signal {-code}"
        return f"crashed (0x{code & 0xFFFFFFFF:08X})"  # a Windows status read as signed
    if code >= NTSTATUS_ERROR:
        return f"crashed (0x{code:08X})"
    if SIGNAL_EXIT < code < SIGNAL_EXIT + SIGNALS:
        return f"killed by signal {code - SIGNAL_EXIT} (exit {code})"
    return None


def _env_exit(argv: list[str], code: int) -> str | None:
    """What the tool's documentation says of ``code`` when it is not a verdict on the code."""
    prefix, tool = _split_tool(argv)
    if tool is None:
        return None
    name = _launcher_name(tool)
    arguments = argv[len(prefix) + 1 :]
    keys = [f"{name} {arguments[0]}", name] if arguments else [name]
    return next((ENV_EXITS[key][code] for key in keys if code in ENV_EXITS.get(key, {})), None)


def _classify(
    argv: list[str], code: int, root: Path, probes: dict[tuple[str, ...], bool]
) -> tuple[int, str]:
    """(status, detail) of a step that ended with ``code``: ADR-0018 as refined by ADR-0022."""
    if code == 0:
        return GREEN, ""
    tool = _tool_name(argv)
    crash = _crash(code)
    if crash is not None:
        return ENV, f"{tool} {crash}"
    reason = _env_exit(argv, code)
    if reason is not None:
        return ENV, f"{tool} exit {code}: {reason}"
    if code == 1:
        # the tool ran unless its launcher cannot start anything: only the launcher is asked
        launcher = _launcher_probe(argv)
        if launcher is None or _answers(launcher, root, probes):
            return RED, "exit 1"
        asked = " ".join([Path(launcher[0]).name, *launcher[1:]])
        return ENV, f"{tool} exit 1: {asked} fails too"
    probe = _probe_argv(argv, root)
    if probe is not None and _answers(probe, root, probes):
        return RED, f"{tool} exit {code}"
    return ENV, f"{tool} exit {code}"


def _emit(text: str, log: IO[bytes] | None) -> None:
    """A line of the runner itself, in ASCII: to the terminal, or to the log when given."""
    if log is None:
        sys.stdout.write(_ascii(text))
        sys.stdout.flush()
    else:
        log.write(text.encode("ascii", "replace"))
        log.flush()


def run_step(
    name: str,
    argv: list[str],
    root: Path,
    log: IO[bytes] | None,
    note: str = "",
    *,
    shown: list[str] | None = None,
    probes: dict[tuple[str, ...], bool] | None = None,
) -> StepResult:
    """Run one step; its output goes to the terminal, or to ``log`` when given.

    Exit 0 is green. Anything else is red only when the tool ran and judged the code
    (ADR-0018, ADR-0022): see :func:`_classify`. ``shown`` replaces ``argv`` in the header;
    ``probes`` is the memory of the run's probes.
    """
    _emit(f"--- {name}: {' '.join(shown or argv)}\n", log)
    missing = _missing_script(argv, root)
    if missing is not None:
        return StepResult(name, ENV, f"{missing}: missing", note)
    try:
        completed = _start(
            argv, root, stdout=log, stderr=subprocess.STDOUT if log is not None else None
        )
    except CannotStart as exc:
        return StepResult(name, ENV, str(exc), note)
    except FileNotFoundError as exc:
        if getattr(exc, "winerror", None) == TOO_LONG_WINERROR:
            detail = f"{_tool_name(argv)}: command line too long for the system"
            return StepResult(name, ENV, detail, note)
        return StepResult(name, ENV, f"{Path(argv[0]).name}: not found", note)
    except OSError as exc:
        return StepResult(name, ENV, f"{Path(argv[0]).name}: {exc.strerror or exc}", note)
    status, detail = _classify(argv, completed.returncode, root, {} if probes is None else probes)
    return StepResult(name, status, detail, note)


def run_steps(
    steps: dict[str, Step], root: Path, log: IO[bytes] | None, keep_going: bool
) -> list[StepResult]:
    results: list[StepResult] = []
    probes: dict[tuple[str, ...], bool] = {}  # ADR-0022: one probe per distinct command
    for name, step in steps.items():
        if step.skip:
            result = StepResult(name, GREEN, "", step.note)
        else:
            result = run_step(
                name, step.argv, root, log, step.note, shown=step.shown, probes=probes
            )
        results.append(result)
        _emit(result.line + "\n", log)
        if result.status != GREEN and not keep_going:
            break
    return results


def verdict(results: list[StepResult]) -> tuple[int, str]:
    """(exit code, final summary line); environment problems win over failures."""
    env = [r.name for r in results if r.status == ENV]
    if env:
        return ENV, f"rumbo check: environment broken ({', '.join(env)})"
    red = [r.name for r in results if r.status == RED]
    if red:
        return RED, f"rumbo check: red ({', '.join(red)})"
    return GREEN, f"rumbo check: green ({len(results)} steps)"


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def _report_brief(log_path: Path, code: int, summary: str) -> None:
    total = 0
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            total += 1
            tail.append(line.rstrip("\r\n"))
    if code == GREEN:
        print(f"rumbo check: green ({total} log lines in {LOG_NAME})")
        return
    for line in tail:
        print(_ascii(line))
    print(summary)


# ------------------------------------------------------------------------------ main
class _Parser(argparse.ArgumentParser):
    """A usage error is a configuration error (exit 3), never argparse's exit 2."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"rumbo check: environment broken ({message})", file=sys.stderr)
        raise SystemExit(ENV)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="rumbo check",
        description="Run the verification steps of [tool.rumbo.checks] without a shell.",
    )
    parser.add_argument("steps", nargs="*", metavar="STEP", help="steps to run (default: all)")
    parser.add_argument(
        "--brief",
        action="store_true",
        help=f"send step output to {LOG_NAME}; print one line, or the failing tail",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="run every step instead of stopping at the first failure",
    )
    parser.add_argument(
        "--base",
        help="value of {base} (default: the first that exists of origin/main, origin/master, "
        "origin/HEAD, main, master)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="expand {changed} to the whole tree (.) instead of the changed .py files",
    )
    parser.add_argument("--dir", default=".", help="project root (default: current directory)")
    return parser


def _run(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    steps = select_steps(load_steps(root), args.steps)
    base = resolve_base(args.base, root)
    commands = {name: parse_command(name, cmd, base) for name, cmd in steps.items()}
    plan = plan_steps(commands, base, root, args.full)

    if not args.brief:
        results = run_steps(plan, root, None, args.keep_going)
        code, summary = verdict(results)
        print(summary)
        return code

    log_path = root / LOG_NAME
    try:
        log = log_path.open("wb")
    except OSError as exc:
        raise ConfigError(f"cannot write {LOG_NAME}: {exc.strerror or exc}") from exc
    with log:
        results = run_steps(plan, root, log, args.keep_going)
    code, summary = verdict(results)
    _report_brief(log_path, code, summary)
    return code


def main(argv: list[str] | None = None) -> int:
    """Exit 0, 1 or 3. Exit 1 says "the code is red" to the Stop hook, the gate and CI, so
    nothing but a red step returns it: an error of the runner itself is exit 3, in one line.
    An interrupt (Ctrl+C) is not caught: Python ends the process as it always did."""
    args = build_parser().parse_args(argv)
    try:
        with _not_from_the_current_directory():  # git too is found on PATH, not in the root
            return _run(args)
    except ConfigError as exc:
        print(_ascii(f"rumbo check: environment broken ({exc})"))
    except Exception as exc:  # the last resort: whatever it is, it is not a red step
        text = " ".join(f"{type(exc).__name__}: {exc}".split())
        print(_ascii(f"rumbo check: environment broken (internal error: {text})"))
    return ENV


if __name__ == "__main__":
    sys.exit(main())
