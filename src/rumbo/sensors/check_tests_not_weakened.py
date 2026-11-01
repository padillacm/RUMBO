#!/usr/bin/env python3
"""RUMBO anti-cheat sensor: fails when the tests get weaker than the base revision.

Compares ``tests/`` at the base (``origin/main`` or the PR base commit) against the
working tree and fails if any of these happen:

1. a file under ``tests/`` is deleted;
2. a ``test_*`` function disappears (identified by file + qualified name);
3. a test loses ``assert`` statements, or the total number of asserts in ``tests/`` drops;
4. a test gains always-true asserts (``assert True``, ``assert (x, "msg")``);
5. a test gains ``skip``/``skipif``/``xfail`` markers or ``pytest.skip()`` calls;
6. a ``parametrize`` literal list shrinks.

What it does NOT see (review these by hand, and let mutation testing catch the rest):
early ``return`` before the asserts, mocks that empty the behavior, asserts moved into
helpers, and changes to ``[tool.pytest.ini_options]`` or ``conftest.py``.

The rule can only be bypassed by a HUMAN: either a commit in ``base..HEAD`` whose message
contains ``[tests-change-approved]`` and whose author is not an agent (no
``noreply@anthropic.com`` / ``*[bot]`` author, no ``Co-Authored-By: Claude`` trailer on
that commit), or the ``tests-change-approved`` label on the pull request (read from
``GITHUB_EVENT_PATH`` in GitHub Actions). The agent never writes that mark.

Standard library only: it is copied verbatim into every RUMBO project.
Exit codes: 0 ok · 1 tests weakened · 2 usage error / base not found (with --strict).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

APPROVAL_MARK = "[tests-change-approved]"
APPROVAL_LABEL = "tests-change-approved"
TESTS_DIR = "tests"
DEFAULT_BASES = ("origin/main", "origin/master", "origin/HEAD", "@{upstream}", "main", "master")
NULL_SHA = "0" * 40
AGENT_EMAIL_MARKERS = ("noreply@anthropic.com", "[bot]", "users.noreply.github.com/bot")
AGENT_TRAILERS = ("co-authored-by: claude", "claude-session:")
SKIP_NAMES = {"skip", "skipif", "xfail"}


@dataclass
class TestInfo:
    asserts: int = 0
    constant_asserts: int = 0
    skips: int = 0
    param_cases: int | None = None


@dataclass
class Inventory:
    files: set[str] = field(default_factory=set)
    tests: dict[str, TestInfo] = field(default_factory=dict)
    asserts: int = 0
    errors: list[str] = field(default_factory=list)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


# ----------------------------------------------------------------------------- AST
def _attr_chain(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_constant_assert(node: ast.Assert) -> bool:
    test = node.test
    if isinstance(test, ast.Constant):
        return bool(test.value)
    return isinstance(test, ast.Tuple) and bool(test.elts)


def _decorator_info(func: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int | None]:
    """(skip-like markers, parametrize literal case count or None)."""
    skips = 0
    cases: int | None = None
    for deco in func.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        chain = _attr_chain(target)
        last = chain.rsplit(".", 1)[-1]
        if last in SKIP_NAMES:
            skips += 1
        if last == "parametrize" and isinstance(deco, ast.Call) and len(deco.args) >= 2:
            values = deco.args[1]
            if isinstance(values, ast.List | ast.Tuple):
                n = len(values.elts)
                cases = n if cases is None else cases * n
    return skips, cases


def _body_stats(func: ast.AST) -> tuple[int, int, int]:
    """(asserts, constant asserts, skip calls) inside a function, nested defs included."""
    asserts = constant = skip_calls = 0
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            asserts += 1
            if _is_constant_assert(node):
                constant += 1
        elif isinstance(node, ast.Call):
            chain = _attr_chain(node.func)
            if chain.rsplit(".", 1)[-1] in {"skip", "xfail"} and chain.startswith("pytest"):
                skip_calls += 1
    return asserts, constant, skip_calls


def _collect(tree: ast.AST) -> tuple[dict[str, TestInfo], int]:
    tests: dict[str, TestInfo] = {}
    total_asserts = sum(isinstance(n, ast.Assert) for n in ast.walk(tree))

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = f"{prefix}{child.name}"
                if child.name.startswith("test_"):
                    asserts, constant, skip_calls = _body_stats(child)
                    skips, cases = _decorator_info(child)
                    tests[qualname] = TestInfo(asserts, constant, skips + skip_calls, cases)
                else:
                    visit(child, qualname + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return tests, total_asserts


def analyze(files: Mapping[str, str | None]) -> Inventory:
    """``files``: path → content (``None`` when it is not a readable .py file)."""
    inv = Inventory()
    for path, content in files.items():
        inv.files.add(path)
        if content is None or not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError as exc:
            inv.errors.append(f"{path}: cannot parse ({exc.msg}, line {exc.lineno})")
            continue
        tests, asserts = _collect(tree)
        inv.tests.update({f"{path}::{name}": info for name, info in tests.items()})
        inv.asserts += asserts
    return inv


# ------------------------------------------------------------------------ inventories
def base_inventory(base: str, root: Path) -> Inventory:
    listing = _git(["ls-tree", "-r", "--name-only", base, "--", TESTS_DIR], root)
    files: dict[str, str | None] = {}
    for path in filter(None, listing.stdout.splitlines()):
        content = None
        if path.endswith(".py"):
            shown = _git(["show", f"{base}:{path}"], root)
            content = shown.stdout if shown.returncode == 0 else None
        files[path] = content
    return analyze(files)


def worktree_inventory(root: Path) -> Inventory:
    files: dict[str, str | None] = {}
    folder = root / TESTS_DIR
    if folder.is_dir():
        for path in sorted(folder.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                rel = path.relative_to(root).as_posix()
                files[rel] = (
                    path.read_text(encoding="utf-8", errors="replace")
                    if path.suffix == ".py"
                    else None
                )
    return analyze(files)


def compare(base: Inventory, current: Inventory) -> list[str]:
    problems: list[str] = []
    for path in sorted(base.files - current.files):
        problems.append(f"test file deleted: {path}")
    for name in sorted(base.tests.keys() - current.tests.keys()):
        problems.append(f"test deleted or renamed: {name}")
    for name in sorted(base.tests.keys() & current.tests.keys()):
        old, new = base.tests[name], current.tests[name]
        if new.asserts < old.asserts:
            problems.append(f"{name}: asserts dropped from {old.asserts} to {new.asserts}")
        if new.constant_asserts > old.constant_asserts:
            problems.append(f"{name}: gained always-true asserts (assert True / tuple)")
        if new.skips > old.skips:
            problems.append(f"{name}: gained skip/skipif/xfail markers or pytest.skip() calls")
        if (
            old.param_cases is not None
            and new.param_cases is not None
            and new.param_cases < old.param_cases
        ):
            problems.append(
                f"{name}: parametrize cases dropped from {old.param_cases} to {new.param_cases}"
            )
    if current.asserts < base.asserts:
        problems.append(f"total asserts in tests/ dropped from {base.asserts} to {current.asserts}")
    problems.extend(f"parse error: {e}" for e in current.errors)
    return problems


# --------------------------------------------------------------------------- base
def resolve_base(explicit: str | None, root: Path) -> tuple[str | None, list[str]]:
    """Returns (merge-base commit or None, warnings)."""
    warnings: list[str] = []
    candidates: list[str] = []
    for value in (explicit, os.environ.get("RUMBO_BASE_REF")):
        if value and value.strip() and value.strip() != NULL_SHA:
            candidates.append(value.strip())
    gh_base = os.environ.get("GITHUB_BASE_REF")
    if gh_base:
        candidates.append(f"origin/{gh_base}")
    candidates.extend(DEFAULT_BASES)
    for candidate in candidates:
        rp = _git(["rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"], root)
        if rp.returncode != 0:
            if candidate == (explicit or "").strip():
                warnings.append(f"explicit base {candidate!r} is not reachable; trying defaults")
            continue
        mb = _git(["merge-base", "HEAD", candidate], root)
        resolved = mb.stdout.strip() if mb.returncode == 0 and mb.stdout.strip() else candidate
        head = _git(["rev-parse", "HEAD"], root).stdout.strip()
        if resolved == head and candidate in DEFAULT_BASES:
            warnings.append(
                f"base {candidate!r} is HEAD itself: nothing to compare "
                "(set origin/main or pass --base)"
            )
        return resolved, warnings
    return None, warnings


# ----------------------------------------------------------------------- approval
def _is_agent_commit(author_email: str, committer_email: str, message: str) -> bool:
    emails = f"{author_email} {committer_email}".lower()
    if any(marker in emails for marker in AGENT_EMAIL_MARKERS):
        return True
    lowered = message.lower()
    return any(trailer in lowered for trailer in AGENT_TRAILERS)


def human_approval(base: str, root: Path) -> str | None:
    """Reason string when a human approved the change, else None."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).is_file():
        try:
            event = json.loads(Path(event_path).read_text(encoding="utf-8"))
            labels = [lb.get("name") for lb in event.get("pull_request", {}).get("labels", [])]
            if APPROVAL_LABEL in labels:
                return f"pull request label '{APPROVAL_LABEL}'"
        except (json.JSONDecodeError, AttributeError):
            pass
    log = _git(["log", "--format=%H%x1f%ae%x1f%ce%x1f%B%x1e", f"{base}..HEAD"], root)
    for record in log.stdout.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) != 4:
            continue
        sha, author, committer, message = parts
        if APPROVAL_MARK not in message:
            continue
        if _is_agent_commit(author, committer, message):
            print(f"  (ignoring {APPROVAL_MARK} in {sha[:10]}: commit authored by an agent)")
            continue
        return f"commit {sha[:10]} by {author}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="RUMBO anti-cheat sensor: fails when tests get weaker than the base."
    )
    parser.add_argument("--base", help="base ref or SHA (default: origin/main or the PR base)")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--strict", action="store_true", help="exit 2 when no base can be resolved")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    base, warnings = resolve_base(args.base, root)
    for warning in warnings:
        print(f"WARNING: {warning}")
    if base is None:
        print("WARNING: no base to compare against (no origin/main?).")
        return 2 if args.strict else 0

    problems = compare(base_inventory(base, root), worktree_inventory(root))
    if not problems:
        print(f"OK: no test weakened relative to {base[:12]}.")
        return 0

    print(f"Tests weakened relative to {base[:12]}:")
    for problem in problems:
        print(f"  - {problem}")
    approval = human_approval(base, root)
    if approval:
        print(f"Approved by a human: {approval}.")
        return 0
    print(
        "If the change is legitimate, a HUMAN approves it by adding "
        f"{APPROVAL_MARK} to a commit message of their own (or the '{APPROVAL_LABEL}' "
        "label to the pull request). The agent never writes that mark."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
