#!/usr/bin/env python3
"""RUMBO verification runner: runs the steps of ``[tool.rumbo.checks]`` without a shell.

The steps live in ``pyproject.toml``, one command string per step, in execution order::

    [tool.rumbo.checks]
    lint = "uv run ruff check ."
    test = "uv run pytest"

Each string is split with ``shlex.split(posix=True)`` and run with ``subprocess.run`` and no
shell, so the harness behaves identically on Windows, macOS and Linux. Shell operators
(``&&``, ``|``, ``;``, redirections) and ``$variables`` are configuration errors: a step that
needs logic becomes a small Python script. The only placeholder is ``{base}``: ``--base`` if
given, else ``origin/main`` when that ref exists, else ``main``.

Usage: ``python scripts/rumbo_check.py [STEP ...] [--brief] [--keep-going] [--base REF]``.
Without step names every step runs in table order and the run stops at the first failing step;
``--keep-going`` runs them all. Named steps run a subset, still in table order. Without
``--brief`` each step streams to the terminal; with it all output goes to ``.rumbo-check.log``
and only a one-line verdict is printed on success, or the last 40 log lines on failure.

Exit codes: 0 green · 1 a step failed (exit 1) · 3 broken environment (executable not found,
a step exited with a code other than 0 or 1, the table is missing or malformed, or an unknown
step was requested). Exit 2 is never used: it means "block" in Claude Code hooks. With
``--keep-going``, 3 wins over 1. All output of the runner itself is ASCII.

Standard library only: it is copied verbatim into every RUMBO project (ADR-0006).
"""

from __future__ import annotations

import argparse
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
TAIL_LINES = 40
SHELL_OPERATORS = frozenset({"&&", "||", "|", ";", ">", "<", ">>", "2>&1"})
LAUNCHERS = frozenset({"uv", "uvx", "run", "python", "python3", "py", "-m"})


class ConfigError(Exception):
    """The step table or the requested steps cannot be used."""


@dataclass(frozen=True)
class StepResult:
    name: str
    status: int  # GREEN, RED or ENV
    detail: str  # "", "exit 1", "pyright exit 3", "ruff: not found"

    @property
    def line(self) -> str:
        if self.status == GREEN:
            return f"ok   {self.name}"
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
    """Split a step into argv (no shell) and substitute ``{base}``."""
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
    return [token.replace("{base}", base) for token in argv]


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


# --------------------------------------------------------------------------- running
def _tool_name(argv: list[str]) -> str:
    """The program a step really runs: ``pyright`` for ``uv run pyright``."""
    for token in argv:
        if token not in LAUNCHERS:
            return Path(token).name
    return Path(argv[0]).name


def run_step(name: str, argv: list[str], root: Path, log: IO[bytes] | None) -> StepResult:
    """Run one step; its output goes to the terminal, or to ``log`` when given."""
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
        return StepResult(name, GREEN, "")
    if code == 1:
        return StepResult(name, RED, "exit 1")
    return StepResult(name, ENV, f"{_tool_name(argv)} exit {code}")


def run_steps(
    steps: dict[str, list[str]], root: Path, log: IO[bytes] | None, keep_going: bool
) -> list[StepResult]:
    results: list[StepResult] = []
    for name, argv in steps.items():
        result = run_step(name, argv, root, log)
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

    if not args.brief:
        results = run_steps(commands, root, None, args.keep_going)
        code, summary = verdict(results)
        print(summary)
        return code

    log_path = root / LOG_NAME
    with log_path.open("wb") as log:
        results = run_steps(commands, root, log, args.keep_going)
    code, summary = verdict(results)
    _report_brief(log_path, code, summary)
    return code


if __name__ == "__main__":
    sys.exit(main())
