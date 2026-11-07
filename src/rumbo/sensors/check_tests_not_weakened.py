#!/usr/bin/env python3
"""RUMBO anti-cheat sensor: fails when the tests get weaker than the base revision.

Compares the tests directory at the base (``origin/main`` or the PR base commit) against
the working tree and fails if any of these happen:

1. a file under the tests directory is deleted;
2. a ``test_*`` function disappears (identified by file + qualified name);
3. a test loses assertions, or the total number of assertions in the tests drops;
4. a test gains always-true asserts (``assert True``, ``assert (x, "msg")``);
5. a test gains ``skip``/``skipif``/``xfail`` markers or ``pytest.skip()`` calls;
6. a ``parametrize`` literal list shrinks.

Assertions are counted the way idiomatic Python writes them (ADR-0007). Inside a test,
one each: ``assert``; every ``with pytest.raises(...)``, ``pytest.warns(...)`` or
``pytest.deprecated_call(...)`` item; ``self.assert*(...)`` and ``self.fail(...)``; the
``unittest.mock`` assertions by exact name on any receiver; ``pytest.fail(...)``. A call to
a helper function defined in the same file, in a ``conftest.py`` of the same directory or
an ancestor, or in a module imported from inside the tests directory counts as the
assertions of that helper's body under the same rules (depth at most 3, cycles count 0).
Anything else counts 0, so a hollow helper never passes for an assertion.

What it does NOT see (review these by hand, and let mutation testing catch the rest):
early ``return`` before the asserts, mocks that empty the behavior, helpers built
dynamically or imported from outside the tests directory, and changes to
``[tool.pytest.ini_options]``.

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
import posixpath
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
PYTEST_CONTEXT_ASSERTS = {"pytest.raises", "pytest.warns", "pytest.deprecated_call"}
MOCK_ASSERT_NAMES = {
    "assert_called",
    "assert_called_once",
    "assert_called_with",
    "assert_called_once_with",
    "assert_any_call",
    "assert_has_calls",
    "assert_not_called",
    "assert_awaited",
    "assert_awaited_once",
    "assert_awaited_with",
    "assert_awaited_once_with",
    "assert_any_await",
    "assert_has_awaits",
    "assert_not_awaited",
}
MAX_HELPER_DEPTH = 3


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


FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _syntactic_call(call: ast.Call) -> str | None:
    """``"assert"`` / ``"skip"`` when the call is one of the counted forms, else None."""
    chain = _attr_chain(call.func)
    if not chain:
        return None
    head, _, last = chain.rpartition(".")
    if chain in {"pytest.fail", "self.fail"}:
        return "assert"
    if head == "self" and last.startswith("assert"):
        return "assert"
    if head and last in MOCK_ASSERT_NAMES:
        return "assert"
    if last in {"skip", "xfail"} and chain.startswith("pytest"):
        return "skip"
    return None


def _is_pytest_context(item: ast.withitem) -> bool:
    call = item.context_expr
    return isinstance(call, ast.Call) and _attr_chain(call.func) in PYTEST_CONTEXT_ASSERTS


def _syntactic_stats(func: FunctionNode) -> tuple[int, int, int]:
    """(asserts, constant asserts, skip calls) counted without resolving helper calls."""
    asserts = constant = skip_calls = 0
    for stmt in func.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Assert):
                asserts += 1
                if _is_constant_assert(node):
                    constant += 1
            elif isinstance(node, ast.With | ast.AsyncWith):
                asserts += sum(_is_pytest_context(item) for item in node.items)
            elif isinstance(node, ast.Call):
                kind = _syntactic_call(node)
                asserts += kind == "assert"
                skip_calls += kind == "skip"
    return asserts, constant, skip_calls


@dataclass(frozen=True)
class _Import:
    """``from <module> import <name>`` (``name`` set) or ``import <module>`` (``name`` None)."""

    module: str
    name: str | None
    level: int


@dataclass
class _Module:
    path: str
    tree: ast.Module
    functions: dict[str, FunctionNode] = field(default_factory=dict)
    imports: dict[str, _Import] = field(default_factory=dict)


def _index_module(path: str, tree: ast.Module) -> _Module:
    module = _Module(path, tree)
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            module.functions[stmt.name] = stmt
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                module.imports[bound] = _Import(alias.name if alias.asname else bound, None, 0)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    module.imports[alias.asname or alias.name] = _Import(
                        node.module or "", alias.name, node.level
                    )
    return module


def _ancestors(directory: str) -> list[str]:
    """``"a/b/c"`` → ``["a/b/c", "a/b", "a", ""]``."""
    chain = [directory]
    while chain[-1]:
        chain.append(posixpath.dirname(chain[-1]))
    return chain


class _Resolver:
    """Static resolution of helper calls inside one snapshot of the tests directory."""

    def __init__(self, modules: Mapping[str, _Module]) -> None:
        self.modules = modules
        self.reached: set[tuple[str, str]] = set()

    def count(self, module: _Module, func: FunctionNode) -> tuple[int, int, int]:
        return self._count(module, func, 0, frozenset({(module.path, func.name)}))

    def _count(
        self, module: _Module, func: FunctionNode, depth: int, active: frozenset[tuple[str, str]]
    ) -> tuple[int, int, int]:
        asserts, constant, skip_calls = _syntactic_stats(func)
        if depth >= MAX_HELPER_DEPTH:
            return asserts, constant, skip_calls
        for stmt in func.body:
            for node in ast.walk(stmt):
                if not isinstance(node, ast.Call) or _syntactic_call(node) is not None:
                    continue
                resolved = self._resolve_call(module, node.func)
                if resolved is None:
                    continue
                target, helper = resolved
                key = (target.path, helper.name)
                if key in active:
                    continue  # a cycle counts 0
                self.reached.add(key)
                a, c, s = self._count(target, helper, depth + 1, active | {key})
                asserts, constant, skip_calls = asserts + a, constant + c, skip_calls + s
        return asserts, constant, skip_calls

    def _resolve_call(self, module: _Module, func: ast.expr) -> tuple[_Module, FunctionNode] | None:
        if isinstance(func, ast.Name):
            return self._resolve_name(module, func.id)
        if isinstance(func, ast.Attribute):
            chain = _attr_chain(func).split(".")
            if len(chain) >= 2 and chain[0]:
                target = self._resolve_module(module, chain[:-1])
                if target is not None and chain[-1] in target.functions:
                    return target, target.functions[chain[-1]]
        return None

    def _resolve_name(self, module: _Module, name: str) -> tuple[_Module, FunctionNode] | None:
        if name in module.functions:
            return module, module.functions[name]
        imported = module.imports.get(name)
        if imported is not None and imported.name is not None:
            target = self._find_module(module, imported.module, imported.level)
            if target is not None and imported.name in target.functions:
                return target, target.functions[imported.name]
        for directory in _ancestors(posixpath.dirname(module.path)):
            conftest = self.modules.get(posixpath.join(directory, "conftest.py"))
            if conftest is not None and conftest is not module and name in conftest.functions:
                return conftest, conftest.functions[name]
        return None

    def _resolve_module(self, module: _Module, prefix: list[str]) -> _Module | None:
        imported = module.imports.get(prefix[0])
        if imported is None:
            return None
        parts = [part for part in imported.module.split(".") if part]
        if imported.name is not None:
            parts.append(imported.name)
        parts.extend(prefix[1:])
        return self._find_module(module, ".".join(parts), imported.level)

    def _find_module(self, module: _Module, dotted: str, level: int) -> _Module | None:
        directory = posixpath.dirname(module.path)
        if level:
            for _ in range(level - 1):
                directory = posixpath.dirname(directory)
            bases = [directory]
        else:
            bases = _ancestors(directory)
        relative = dotted.replace(".", "/")
        for base in bases:
            for candidate in (f"{relative}.py", f"{relative}/__init__.py"):
                found = self.modules.get(posixpath.join(base, candidate))
                if found is not None:
                    return found
        return None


def _collect(module: _Module, resolver: _Resolver) -> dict[str, TestInfo]:
    tests: dict[str, TestInfo] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = f"{prefix}{child.name}"
                if child.name.startswith("test_"):
                    asserts, constant, skip_calls = resolver.count(module, child)
                    skips, cases = _decorator_info(child)
                    tests[qualname] = TestInfo(asserts, constant, skips + skip_calls, cases)
                else:
                    visit(child, qualname + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(module.tree, "")
    return tests


def _residual_asserts(module: _Module, reached: set[tuple[str, str]]) -> int:
    """Assertions outside tests that no test reaches through a helper call.

    Module-level statements and non-test functions are counted syntactically; helpers
    reached from a test are skipped because their callers already count them.
    """
    total = 0

    def visit(node: ast.AST) -> None:
        nonlocal total
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                if child.name.startswith("test_") or (module.path, child.name) in reached:
                    continue
                total += _syntactic_stats(child)[0]
            elif isinstance(child, ast.Assert):
                total += 1
            elif isinstance(child, ast.With | ast.AsyncWith):
                total += sum(_is_pytest_context(item) for item in child.items)
                visit(child)
            elif isinstance(child, ast.Call):
                total += _syntactic_call(child) == "assert"
                visit(child)
            else:
                visit(child)

    visit(module.tree)
    return total


def analyze(files: Mapping[str, str | None]) -> Inventory:
    """``files``: path → content (``None`` when it is not a readable .py file)."""
    inv = Inventory()
    modules: dict[str, _Module] = {}
    for path, content in files.items():
        inv.files.add(path)
        if content is None or not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError as exc:
            inv.errors.append(f"{path}: cannot parse ({exc.msg}, line {exc.lineno})")
            continue
        modules[path] = _index_module(path, tree)
    resolver = _Resolver(modules)
    for path, module in modules.items():
        tests = _collect(module, resolver)
        inv.tests.update({f"{path}::{name}": info for name, info in tests.items()})
        inv.asserts += sum(info.asserts for info in tests.values())
    for module in modules.values():
        inv.asserts += _residual_asserts(module, resolver.reached)
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
        problems.append(
            f"total asserts in the tests directory dropped from {base.asserts} to {current.asserts}"
        )
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
        prog="rumbo check-tests",
        description="RUMBO anti-cheat sensor: fails when tests get weaker than the base.",
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
