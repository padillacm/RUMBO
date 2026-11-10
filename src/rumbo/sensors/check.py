#!/usr/bin/env python3
"""RUMBO verification runner: runs the steps of ``[tool.rumbo.checks]`` without a shell.

The steps live in ``pyproject.toml``, one command string per step, in execution order::

    [tool.rumbo.checks]
    lint = "uv run ruff check ."
    test = "uv run pytest"

Each string is split with ``shlex.split(posix=True)`` and run with ``subprocess.run`` and no
shell, so the harness behaves identically on Windows, macOS and Linux. Shell operators
(``&&``, ``|``, ``;``, redirections) and ``$variables`` are configuration errors: a step that
needs logic becomes a small Python script. Two placeholders exist:

* ``{base}``: ``--base`` if given, else ``origin/main`` when that ref exists, else ``main``.
* ``{changed}`` (ADR-0013): the ``.py`` files that differ from the merge base with ``{base}``
  (``git diff --name-only --diff-filter=ACMR <base>...HEAD``) plus the uncommitted changes and
  the untracked files, deduplicated, existing and sorted, one argument per file. Paths under
  ``spikes/`` are never part of it (ADR-0020): prototypes are not checked. It must be an
  argument of its own (``--files={changed}`` is a configuration error). When the list is empty
  the step is not run and is reported ``ok   <name> (no changed files)``. When there is no
  usable git base (not a repository, no commits, unknown ref) it expands to ``.`` and the step
  line says ``(no git base: whole tree)``. ``--full`` always expands it to ``.``.

Usage: ``python scripts/rumbo_check.py [STEP ...] [--brief] [--keep-going] [--base REF]
[--full]``.
Without step names every step runs in table order and the run stops at the first failing step;
``--keep-going`` runs them all. Named steps run a subset, still in table order. Without
``--brief`` each step streams to the terminal; with it all output goes to ``.rumbo-check.log``
and only a one-line verdict is printed on success, or the last 40 log lines on failure.

Exit codes: 0 green · 1 a step failed · 3 broken environment (executable not found, a step
killed by a signal, the table is missing or malformed, or an unknown step was requested).
Exit 2 is never used: it means "block" in Claude Code hooks. With ``--keep-going``, 3 wins
over 1. All output of the runner itself is ASCII.

A step that exits with a code other than 0 or 1 is asked one question (ADR-0018): the same
launcher and tool are run with ``--version`` (``uv run pytest --version`` for ``uv run pytest
--cov``; for a script, ``uv run python --version``). If that works the tool started and what
it refused is the code: the step failed. If it does not, the environment is broken.
Launcher options that take a value must be glued to it (``uv run --group=dev pytest``), or
the value is taken for the tool.

Standard library only: it is copied verbatim into every RUMBO project (ADR-0006).
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tomllib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import IO, NoReturn

GREEN, RED, ENV = 0, 1, 3
LOG_NAME = ".rumbo-check.log"
CHANGED = "{changed}"
WHOLE_TREE = "."
SPIKES = "spikes/"  # ADR-0020: prototypes are never checked, not even when they changed
TAIL_LINES = 40
SHELL_OPERATORS = frozenset({"&&", "||", "|", ";", ">", "<", ">>", "2>&1"})
LAUNCHERS = frozenset({"uv", "uvx", "run", "python", "python3", "py", "-m"})
INLINE_CODE = "-c"  # ``python -c "..."``: what runs is the interpreter, there is no tool
PROBE_TIMEOUT = 60  # seconds


class ConfigError(Exception):
    """The step table or the requested steps cannot be used."""


@dataclass(frozen=True)
class StepResult:
    name: str
    status: int  # GREEN, RED or ENV
    detail: str  # "", "exit 1", "pytest exit 2", "ruff: not found"
    note: str = ""  # shown on green lines: "no changed files", "no git base: whole tree"

    @property
    def line(self) -> str:
        if self.status == GREEN:
            return f"ok   {self.name} ({self.note})" if self.note else f"ok   {self.name}"
        if self.status == RED:
            return f"FAIL {self.name} ({self.detail})"
        return f"ENV  {self.name} ({self.detail})"


# ------------------------------------------------------------------- configuration
def load_steps(root: Path) -> dict[str, str]:
    """The ``[tool.rumbo.checks]`` table, in file order."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        raise ConfigError(f"no pyproject.toml in {root}")
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"pyproject.toml is not valid TOML: {exc}") from exc
    tool = data.get("tool")
    checks = tool.get("rumbo", {}).get("checks") if isinstance(tool, dict) else None
    if checks is None:
        raise ConfigError("[tool.rumbo.checks] is missing from pyproject.toml")
    if not isinstance(checks, dict) or not checks:
        raise ConfigError('[tool.rumbo.checks] must be a non-empty table of step = "command"')
    for name, value in checks.items():
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"[tool.rumbo.checks] {name} must be a non-empty command string")
    return dict(checks)


def parse_command(name: str, command: str, base: str) -> list[str]:
    """Split a step into argv (no shell) and substitute ``{base}``.

    ``{changed}`` stays a token of its own for :func:`plan_steps` to expand lazily.
    """
    try:
        argv = shlex.split(command, posix=True)
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
    if explicit:
        return explicit
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "origin/main"],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return "main"
    return "origin/main" if probe.returncode == 0 else "main"


@dataclass(frozen=True)
class Changed:
    files: list[str]  # what ``{changed}`` expands to
    note: str = ""  # "no git base: whole tree" when git could not tell


def _git_paths(args: list[str], root: Path) -> list[str] | None:
    """NUL-separated paths printed by ``git <args>`` in ``root``; None when git fails."""
    try:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return [os.fsdecode(raw) for raw in done.stdout.split(b"\0") if raw]


def resolve_changed(base: str, root: Path, full: bool) -> Changed:
    """The files ``{changed}`` stands for: ``.`` with ``--full`` or without a usable git base.

    A file named on a command line bypasses the tool's own exclusions, so ``spikes/`` is
    dropped here (ADR-0020).
    """
    if full:
        return Changed([WHOLE_TREE])
    fallback = Changed([WHOLE_TREE], "no git base: whole tree")
    try:
        merge_base = subprocess.run(
            ["git", "merge-base", base, "HEAD"], cwd=root, capture_output=True, check=False
        )
    except OSError:
        return fallback
    if merge_base.returncode != 0:
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
            p
            for p in found
            if p.endswith(".py") and not p.startswith(SPIKES) and (root / p).is_file()
        )
    )


@dataclass(frozen=True)
class Step:
    argv: list[str]
    note: str = ""
    skip: bool = False  # nothing to run: reported green with ``note``


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
        expanded: list[str] = []
        for token in argv:
            expanded.extend(changed.files if token == CHANGED else [token])
        plan[name] = Step(expanded, changed.note)
    return plan


# --------------------------------------------------------------------------- running
def _is_launcher(token: str) -> bool:
    """``uv``, ``run``, ``python``, ``-m``; also by path (``.venv/bin/python``, ``uv.exe``)."""
    return token in LAUNCHERS or Path(token).name.lower().removesuffix(".exe") in LAUNCHERS


def _split_tool(argv: list[str]) -> tuple[list[str], str | None]:
    """(launchers and their options, the program they run): ``uv run --frozen`` and ``pytest``.

    The program is None when there is none to name: inline code or nothing but launchers.
    """
    for index, token in enumerate(argv):
        if token == INLINE_CODE:
            return argv[:index], None
        if not token.startswith("-") and not _is_launcher(token):
            return argv[:index], token
    return list(argv), None


def _tool_name(argv: list[str]) -> str:
    """The program a step really runs: ``pyright`` for ``uv run pyright``."""
    _, tool = _split_tool(argv)
    return Path(tool or argv[0]).name


def _probe_argv(argv: list[str]) -> list[str] | None:
    """The step's command up to and including the tool, then ``--version`` (ADR-0018).

    A script or inline code is the project's own: the probe stops at the interpreter. None
    when nothing is left to ask.
    """
    prefix, tool = _split_tool(argv)
    if tool is not None and not tool.endswith(".py"):
        return [*prefix, tool, "--version"]
    return [*prefix, "--version"] if prefix else None


def _tool_starts(argv: list[str], root: Path) -> bool:
    """Ask the environment one silent question: can it start the tool of this step?

    Any doubt (no probe, not found, timeout, non-zero) answers no: the old classification.
    """
    probe = _probe_argv(argv)
    if probe is None:
        return False
    try:
        done = subprocess.run(
            probe,
            cwd=root,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def run_step(
    name: str, argv: list[str], root: Path, log: IO[bytes] | None, note: str = ""
) -> StepResult:
    """Run one step; its output goes to the terminal, or to ``log`` when given.

    Exit 0 is green and 1 is red. Any other code is red only when the tool can start
    (ADR-0018): a tool that ran and refused the code is not a broken environment.
    """
    header = f"--- {name}: {' '.join(argv)}\n"
    if log is None:
        sys.stdout.write(header)
        sys.stdout.flush()
    else:
        log.write(header.encode("ascii", "replace"))
        log.flush()
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            shell=False,
            stdout=log,
            stderr=subprocess.STDOUT if log is not None else None,
            check=False,
        )
    except FileNotFoundError:
        return StepResult(name, ENV, f"{Path(argv[0]).name}: not found")
    except OSError as exc:
        return StepResult(name, ENV, f"{Path(argv[0]).name}: {exc.strerror or exc}")
    code = completed.returncode
    if code == 0:
        return StepResult(name, GREEN, "", note)
    if code == 1:
        return StepResult(name, RED, "exit 1")
    tool = _tool_name(argv)
    if code < 0:
        return StepResult(name, ENV, f"{tool} killed by signal {-code}")
    return StepResult(name, RED if _tool_starts(argv, root) else ENV, f"{tool} exit {code}")


def run_steps(
    steps: dict[str, Step], root: Path, log: IO[bytes] | None, keep_going: bool
) -> list[StepResult]:
    results: list[StepResult] = []
    for name, step in steps.items():
        if step.skip:
            result = StepResult(name, GREEN, "", step.note)
        else:
            result = run_step(name, step.argv, root, log, step.note)
        results.append(result)
        line = result.line + "\n"
        if log is None:
            sys.stdout.write(line)
            sys.stdout.flush()
        else:
            log.write(line.encode("ascii", "replace"))
            log.flush()
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
        "--base", help="value of {base} (default: origin/main if it exists, else main)"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="expand {changed} to the whole tree (.) instead of the changed .py files",
    )
    parser.add_argument("--dir", default=".", help="project root (default: current directory)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.dir).resolve()
    try:
        steps = select_steps(load_steps(root), args.steps)
        base = resolve_base(args.base, root)
        commands = {name: parse_command(name, cmd, base) for name, cmd in steps.items()}
    except ConfigError as exc:
        print(f"rumbo check: environment broken ({exc})")
        return ENV
    plan = plan_steps(commands, base, root, args.full)

    if not args.brief:
        results = run_steps(plan, root, None, args.keep_going)
        code, summary = verdict(results)
        print(summary)
        return code

    log_path = root / LOG_NAME
    with log_path.open("wb") as log:
        results = run_steps(plan, root, log, args.keep_going)
    code, summary = verdict(results)
    _report_brief(log_path, code, summary)
    return code


if __name__ == "__main__":
    sys.exit(main())
