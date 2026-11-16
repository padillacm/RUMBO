#!/usr/bin/env python3
"""RUMBO anti-cheat sensor: fails when the tests get weaker than the base revision.

Compares the tests directory at the base (``origin/main`` or the PR base commit) against
the working tree and fails if any of these happen:

1. a file under the tests directory is deleted;
2. a test disappears (identified by file + qualified name);
3. a test loses assertions, or the total number of assertions in the tests drops;
4. a test gains always-true asserts (``assert True``, ``assert (x, "msg")``);
5. a test gains a way of skipping, or its file gains one outside the tests;
6. a ``parametrize`` literal list shrinks;
7. something that can keep a test from running appears: a ``del`` or a second binding of its
   name, ``__test__ = False``, a decorator the sensor does not know on an existing test or
   test class, a hook in a ``conftest.py``.

What a test is (ADR-0027): a function whose name starts with ``test`` that is a direct child
of a module, or of a class pytest collects (``Test*`` or a ``unittest`` ``*TestCase``
subclass), including the tests such a class inherits from a base class defined in the tests
directory. A ``test*`` function under an ``if``, a ``try``, a ``with`` or another function is
not one: its assertions only count in the total.

Assertions are counted the way idiomatic Python writes them (ADR-0007). Inside a test,
one each: ``assert``; every ``with pytest.raises(...)``, ``pytest.warns(...)`` or
``pytest.deprecated_call(...)`` item; ``self.assert*(...)`` and ``self.fail(...)``; the
``unittest.mock`` assertions by exact name on any receiver; ``pytest.fail(...)``. A call to
a helper function defined in the same file, in a ``conftest.py`` of the same directory or
an ancestor, or in a module imported from inside the tests directory counts as the
assertions of that helper's body under the same rules (depth at most 3, cycles count 0).
So does a call ``self.<name>(...)``, ``cls.<name>(...)`` or ``<Class>.<name>(...)`` to a
helper method of the test's class or of a base class defined or imported inside the tests
directory, and a call by name to a function defined inside the test. Anything else counts
0, so a hollow helper never passes for an assertion.

An assertion that cannot run or cannot fail counts 0 (ADR-0027): under a ``try`` whose
handler catches ``AssertionError``, ``Exception``, ``BaseException`` or everything and does
not raise again; under ``with suppress(...)`` of one of those; in a nested function, lambda
or class the test does not call by name; in a helper that is a generator (unless it is a
context manager entered with ``with``) or a coroutine that is not awaited; in a test that
yields.

Skips are counted per test (``skip``/``skipif``/``xfail`` marks, the ``unittest`` skip
decorators, ``pytest.param(..., marks=...)``, ``pytest.skip()``, ``pytest.xfail()``,
``pytest.importorskip()``, ``self.skipTest()``, ``raise SkipTest``) and, for everything
outside the tests (module level, ``pytestmark``, fixtures, hooks, class decorators), per
file. A new file may carry its own; a new ``conftest.py`` or ``__init__.py`` may not.

What it does NOT see (review these by hand, and let mutation testing catch the rest):
early ``return`` before the asserts, asserts under a condition that is false, mocks that
empty the behavior, mock assertions on a fresh mock, a widened ``pytest.raises``, helpers
built dynamically or imported from outside the tests directory, a custom
``python_functions``/``python_classes`` and the other changes to
``[tool.pytest.ini_options]``.

The tests directory is ``--tests-dir`` on the command line, else ``[tool.rumbo].tests`` in
``pyproject.toml`` (default ``tests``). The harness settings are read from the BASE, not
from the tree under test (ADR-0026): when the working tree names another tests directory,
both are inventoried on both sides, and the change is a problem when it leaves files of the
base out; ``[tool.rumbo].agent_authors`` is the union of the base's list and the tree's. The
``conftest.py`` files above the tests directory are inventoried too: pytest loads them.

The rule can only be bypassed by a HUMAN: either a commit in ``base..HEAD`` whose message
contains ``[tests-change-approved]`` and that no agent took part in (ADR-0021:
its author, its committer and its ``Co-authored-by:`` trailers match no agent identity, that
is ``DEFAULT_AGENT_AUTHORS`` plus ``[tool.rumbo].agent_authors`` in ``pyproject.toml``,
case-insensitive substrings of ``Name <email>``; and its message carries none of
``DEFAULT_AGENT_TRAILERS``), or the ``tests-change-approved`` label on the pull request
(read from ``GITHUB_EVENT_PATH``, only in a ``pull_request*`` run of GitHub Actions). The
agent never writes that mark.

Standard library only: it is copied verbatim into every RUMBO project.
Exit codes: 0 ok · 1 tests weakened · 2 usage error, a base that cannot be read, or (with
--strict) no base or a default base that is HEAD itself.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import os
import posixpath
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Set
from dataclasses import dataclass, field
from pathlib import Path

APPROVAL_MARK = "[tests-change-approved]"
APPROVAL_LABEL = "tests-change-approved"
TESTS_DIR = "tests"
# The upstream of the branch is not here: after `git push -u` it is HEAD itself.
DEFAULT_BASES = ("origin/main", "origin/master", "origin/HEAD", "main", "master")
NULL_SHA = "0" * 40
# ADR-0021: who counts as an agent in the history. Both tuples hold case-insensitive
# substrings, one entry per line, each followed by `# source: <url>` (the primary source that
# confirms it). Never add an entry from memory.
# Identities, looked for in `Name <email>` of the author and of the committer and in the value
# of every `Co-authored-by:` trailer; a project adds its own in `[tool.rumbo].agent_authors`.
DEFAULT_AGENT_AUTHORS: tuple[str, ...] = (
    "noreply@anthropic.com",  # source: https://code.claude.com/docs/en/settings
    "[bot]",  # GitHub Apps: every bot account ends in [bot]
    "users.noreply.github.com/bot",
    "noreply@openai.com",  # source: https://github.com/openai/codex/pull/11617 (Codex)
    "aider@aider.chat",  # source: https://github.com/Aider-AI/aider/blob/main/aider/repo.py
    "(aider)",  # same source: the suffix Aider appends to the author and committer name
    "amp@ampcode.com",  # source: https://ampcode.com/docs/cli/settings
)
# Marks an agent leaves in the commit message, looked for anywhere in it.
DEFAULT_AGENT_TRAILERS: tuple[str, ...] = (
    "co-authored-by: claude",  # source: https://code.claude.com/docs/en/settings
    "claude-session:",
    "amp-thread-id:",  # source: https://ampcode.com/docs/cli/settings
    # source: https://github.blog/changelog/2026-03-20-trace-any-copilot-coding-agent-commit-to-its-session-logs
    "agent-logs-url:",  # GitHub Copilot coding agent
)
COAUTHOR_TRAILER = "co-authored-by"
# ADR-0027: pytest's default prefixes (`python_functions`, `python_classes`) and the suffix
# by which a unittest class is known whatever its own name.
TEST_PREFIX = "test"
CLASS_PREFIX = "Test"
TESTCASE_SUFFIX = "TestCase"
CONFTEST = "conftest.py"
# What pytest imports before the tests of a directory: a new one reaches all of them.
LOADED_FIRST = {CONFTEST, "__init__.py"}
# A decorator whose last name is one of these skips, whatever it hangs from.
SKIP_NAMES = {"skip", "skipif", "xfail", "skipIf", "skipUnless", "expectedFailure"}
# The same marks anywhere else (`pytestmark = ...`, `pytest.param(marks=...)`, `add_marker`),
# by the name they have once the imports of the module are followed.
SKIP_MARKS = {
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
    "unittest.skip",
    "unittest.skipIf",
    "unittest.skipUnless",
    "unittest.expectedFailure",
}
SKIP_CALLS = {"pytest.skip", "pytest.xfail", "pytest.importorskip", "pytest.exit"}
# Catching one of these catches a failed assertion.
SWALLOWED = {"AssertionError", "Exception", "BaseException"}
CONFTEST_SETTINGS = {"collect_ignore", "collect_ignore_glob", "pytest_plugins"}
# Decorators that leave the test in place: adding one to an existing test is not reported.
# A name here counts only when it does not come from the tests directory itself.
KNOWN_DECORATORS = (
    "pytest.mark",  # skip, skipif and xfail are counted as skips
    "pytest.fixture",
    "pytest.yield_fixture",
    "pytest_asyncio.fixture",
    "unittest.skip",
    "unittest.skipIf",
    "unittest.skipUnless",
    "unittest.expectedFailure",
    "unittest.mock.patch",
    "mock.patch",
    "hypothesis",
    "functools.wraps",
    "staticmethod",
    "classmethod",
    "typing.override",
    "typing.no_type_check",
    "typing_extensions.override",
    "freezegun.freeze_time",
    "time_machine.travel",
    "responses.activate",
    "respx.mock",
    "requests_mock.Mocker",
    "django.test.override_settings",
    "django.test.utils.override_settings",
)
LOCAL_DECORATOR = " [defined in the tests]"
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


class SensorError(RuntimeError):
    """The comparison cannot be made: the run stops with exit code 2 instead of passing."""


@dataclass
class TestInfo:
    asserts: int = 0
    constant_asserts: int = 0
    skips: int = 0
    param_cases: int | None = None
    decorators: tuple[str, ...] = ()


@dataclass
class Inventory:
    files: set[str] = field(default_factory=set)
    tests: dict[str, TestInfo] = field(default_factory=dict)
    asserts: int = 0
    errors: list[str] = field(default_factory=list)
    # file → its message in `errors`
    unparsed: dict[str, str] = field(default_factory=dict)
    # collected class (`path::Qualified.Name`) → its decorators
    classes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # file → ways of skipping outside its tests
    outside_skips: dict[str, int] = field(default_factory=dict)
    # file → what can keep a test from running (`del`, `__test__`, conftest hooks...)
    flags: dict[str, Counter[str]] = field(default_factory=dict)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


# ----------------------------------------------------------------------------- AST
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
# What opens a scope of its own inside a function: its body does not run with the function.
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _attr_chain(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _full_chain(node: ast.AST) -> str:
    """``a.b.c`` for a name or an attribute of a name; ``""`` for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def _last(chain: str) -> str:
    return chain.rsplit(".", 1)[-1]


def _is_constant_assert(node: ast.Assert) -> bool:
    test = node.test
    if isinstance(test, ast.Constant):
        return bool(test.value)
    return isinstance(test, ast.Tuple) and bool(test.elts)


def _is_assert_call(call: ast.Call) -> bool:
    """One of the calls that count as an assertion (ADR-0007)."""
    chain = _attr_chain(call.func)
    head, _, last = chain.rpartition(".")
    if chain in {"pytest.fail", "self.fail"}:
        return True
    if head == "self" and last.startswith("assert"):
        return True
    return bool(head) and last in MOCK_ASSERT_NAMES


def _is_pytest_context(item: ast.withitem) -> bool:
    call = item.context_expr
    return isinstance(call, ast.Call) and _attr_chain(call.func) in PYTEST_CONTEXT_ASSERTS


def _body(node: ast.AST) -> list[ast.AST]:
    """The statements of a module, class or function; the expression of a lambda."""
    body = getattr(node, "body", [])
    return list(body) if isinstance(body, list) else [body]


def _walk_scope(nodes: Iterable[ast.AST]) -> Iterator[ast.AST]:
    """Those nodes and what is under them, without entering a nested function or class."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, _NESTED):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _statements(body: Iterable[ast.stmt]) -> Iterator[ast.stmt]:
    """The statements that run in the scope of a module or class body: its own and those
    under its ``if``, ``for``, ``try``, ``with``..., not the bodies of functions and classes."""
    stack: list[ast.AST] = [*reversed(list(body))]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.stmt):
            yield node
        if isinstance(node, _NESTED):
            continue
        blocks = [
            child
            for child in ast.iter_child_nodes(node)
            if isinstance(child, ast.stmt | ast.ExceptHandler | ast.match_case)
        ]
        stack.extend(reversed(blocks))


def _assigned_names(stmt: ast.stmt) -> list[str]:
    """The names an assignment statement binds."""
    if isinstance(stmt, ast.Assign):
        targets = stmt.targets
    elif isinstance(stmt, ast.AnnAssign | ast.AugAssign):
        targets = [stmt.target]
    else:
        return []
    return [
        node.id
        for target in targets
        for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    ]


def _bound_names(stmt: ast.stmt) -> list[str]:
    """The names a statement binds in its scope (definitions, imports and assignments)."""
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [stmt.name]
    if isinstance(stmt, ast.Import | ast.ImportFrom):
        return [alias.asname or alias.name.split(".", 1)[0] for alias in stmt.names]
    return _assigned_names(stmt)


def _swallows(handler: ast.ExceptHandler) -> bool:
    """True when the handler catches a failed assertion and lets the test go on."""
    kind = handler.type
    kinds = kind.elts if isinstance(kind, ast.Tuple) else [] if kind is None else [kind]
    if kind is not None and not any(_last(_attr_chain(k)) in SWALLOWED for k in kinds):
        return False
    for node in _walk_scope(handler.body):
        if isinstance(node, ast.Raise):
            return False
        if isinstance(node, ast.Call) and _attr_chain(node.func) in {"pytest.fail", "self.fail"}:
            return False
    return True


def _suppresses(item: ast.withitem) -> bool:
    """``with suppress(AssertionError)`` (or a wider class): its body cannot fail an assert."""
    call = item.context_expr
    if not isinstance(call, ast.Call) or _last(_attr_chain(call.func)) != "suppress":
        return False
    return any(_last(_attr_chain(arg)) in SWALLOWED for arg in call.args)


def _is_context_manager(func: FunctionNode) -> bool:
    names = {_last(_attr_chain(deco)) for deco in func.decorator_list}
    return bool(names & {"contextmanager", "asynccontextmanager"})


def _is_autouse(func: FunctionNode) -> bool:
    """``@fixture(autouse=...)`` with anything but a literal false value."""
    for deco in func.decorator_list:
        if isinstance(deco, ast.Call) and _last(_attr_chain(deco.func)) == "fixture":
            for keyword in deco.keywords:
                value = keyword.value
                off = isinstance(value, ast.Constant) and not value.value
                if keyword.arg == "autouse" and not off:
                    return True
    return False


def _literal_cases(values: ast.expr | None) -> int | None:
    """Cases of a ``parametrize`` literal; ``*()`` adds none, an unknown ``*name`` adds one."""
    if not isinstance(values, ast.List | ast.Tuple | ast.Set):
        return None
    total = 0
    for element in values.elts:
        inner = _literal_cases(element.value) if isinstance(element, ast.Starred) else None
        total += 1 if inner is None else inner
    return total


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
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)
    imports: dict[str, _Import] = field(default_factory=dict)
    assigned: set[str] = field(default_factory=set)


@dataclass(frozen=True, eq=False)
class _Class:
    """A class and the module that defines it."""

    module: _Module
    node: ast.ClassDef


@dataclass(frozen=True, eq=False)
class _Helper:
    """A function whose body is counted; ``owner`` is the class of its ``self``, if any, and
    ``visible`` the functions defined in the functions that enclose it."""

    module: _Module
    func: FunctionNode
    owner: _Class | None = None
    visible: Mapping[str, FunctionNode] = field(default_factory=dict)


@dataclass(frozen=True, eq=False)
class _CallSite:
    """A call that may be a helper's, and how the test uses what it returns."""

    node: ast.Call
    driven: bool  # awaited, or handed to `asyncio.run` / `run_until_complete`
    entered: bool  # the context expression of a `with`


@dataclass
class _Scan:
    """What the body of one function holds, without its nested functions and classes."""

    asserts: int = 0
    constant: int = 0
    skips: int = 0
    generator: bool = False
    calls: list[_CallSite] = field(default_factory=list)
    nested: dict[str, FunctionNode] = field(default_factory=dict)
    inner: list[ast.AST] = field(default_factory=list)


def _index_module(path: str, tree: ast.Module) -> _Module:
    module = _Module(path, tree)
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            module.functions[stmt.name] = stmt
        elif isinstance(stmt, ast.ClassDef):
            module.classes[stmt.name] = stmt
    for stmt in _statements(tree.body):
        module.assigned.update(_assigned_names(stmt))
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


def _canonical(module: _Module, chain: str) -> str:
    """The dotted name with its first part replaced by what the module imported it as:
    ``mark.skip`` after ``from pytest import mark`` is ``pytest.mark.skip``."""
    head, dot, rest = chain.partition(".")
    imported = module.imports.get(head)
    if imported is None or imported.level:
        return chain
    origin = imported.module if imported.name is None else f"{imported.module}.{imported.name}"
    return f"{origin}{dot}{rest}"


def _skip_form(module: _Module, node: ast.AST) -> int:
    """1 when the node is a way of skipping or of expecting a failure, else 0."""
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "skipTest":
            return 1
        return int(_canonical(module, _full_chain(node.func)) in SKIP_CALLS)
    if isinstance(node, ast.Raise) and node.exc is not None:
        raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        name = _canonical(module, _full_chain(raised))
        return int(_last(name) == "SkipTest" or name.startswith(("pytest.skip.", "pytest.xfail.")))
    if isinstance(node, ast.Attribute | ast.Name):
        return int(_canonical(module, _full_chain(node)) in SKIP_MARKS)
    return 0


def _skips_in(module: _Module, nodes: Iterable[ast.AST]) -> int:
    return sum(_skip_form(module, node) for root in nodes for node in ast.walk(root))


def _decorator_skips(module: _Module, deco: ast.expr) -> int:
    target = deco.func if isinstance(deco, ast.Call) else deco
    named = _last(_attr_chain(target)) in SKIP_NAMES
    return max(_skips_in(module, [deco]), int(named))


def _decorator_info(module: _Module, func: FunctionNode) -> tuple[int, int | None]:
    """(skip-like markers, parametrize literal case count or None)."""
    skips = 0
    cases: int | None = None
    for deco in func.decorator_list:
        skips += _decorator_skips(module, deco)
        if not isinstance(deco, ast.Call) or _last(_attr_chain(deco.func)) != "parametrize":
            continue
        named = [keyword.value for keyword in deco.keywords if keyword.arg == "argvalues"]
        values = deco.args[1] if len(deco.args) >= 2 else named[0] if named else None
        n = _literal_cases(values) if isinstance(values, ast.List | ast.Tuple) else None
        if n is not None:
            cases = n if cases is None else cases * n
    return skips, cases


def _drives(module: _Module, node: ast.AST) -> bool:
    """A call that runs the coroutines it is given."""
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Attribute) and node.func.attr == "run_until_complete":
        return True
    return _canonical(module, _full_chain(node.func)) == "asyncio.run"


def _scan(module: _Module, root: ast.AST) -> _Scan:
    """Count what runs with the body of ``root`` and can fail (ADR-0027)."""
    scan = _Scan()
    # (node, under something that swallows a failed assertion, awaited, entered by a `with`)
    stack: list[tuple[ast.AST, bool, bool, bool]] = [
        (node, False, False, False) for node in reversed(_body(root))
    ]
    while stack:
        node, muted, driven, entered = stack.pop()
        if isinstance(node, _NESTED):
            scan.inner.append(node)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                scan.nested[node.name] = node
            continue
        scan.skips += _skip_form(module, node)
        children: list[tuple[ast.AST, bool, bool, bool]]
        if isinstance(node, ast.Try | ast.TryStar):
            inside = muted or any(_swallows(handler) for handler in node.handlers)
            children = [(child, inside, False, False) for child in node.body]
            rest = [*node.handlers, *node.orelse, *node.finalbody]
            children += [(child, muted, False, False) for child in rest]
        elif isinstance(node, ast.With | ast.AsyncWith):
            if not muted:
                scan.asserts += sum(_is_pytest_context(item) for item in node.items)
            inside = muted or any(_suppresses(item) for item in node.items)
            children = [(item.context_expr, muted, False, True) for item in node.items]
            children += [(child, inside, False, False) for child in node.body]
        else:
            if isinstance(node, ast.Yield | ast.YieldFrom):
                scan.generator = True
            elif isinstance(node, ast.Assert) and not muted:
                scan.asserts += 1
                scan.constant += _is_constant_assert(node)
            elif isinstance(node, ast.Call) and not muted:
                if _is_assert_call(node):
                    scan.asserts += 1
                else:
                    scan.calls.append(_CallSite(node, driven, entered))
            below = driven or isinstance(node, ast.Await) or _drives(module, node)
            children = [(child, muted, below, False) for child in ast.iter_child_nodes(node)]
        stack.extend(reversed(children))
    return scan


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
        self.reached: set[ast.AST] = set()  # helpers whose body a test counted
        self.collected: set[ast.AST] = set()  # the functions that are tests
        self._scans: dict[ast.AST, _Scan] = {}

    def scan(self, module: _Module, node: ast.AST) -> _Scan:
        found = self._scans.get(node)
        if found is None:
            found = self._scans[node] = _scan(module, node)
        return found

    def count(
        self, module: _Module, func: FunctionNode, owner: _Class | None = None
    ) -> tuple[int, int, int]:
        """Stats of a test defined in ``module``; ``owner`` is the class that runs it, if any."""
        self.collected.add(func)
        if self.scan(module, func).generator:
            # pytest does not run a test function that yields: nothing in it can fail
            return 0, 0, _skips_in(module, func.body)
        return self._count(_Helper(module, func, owner), 0, frozenset({func}))

    def _count(
        self, scope: _Helper, depth: int, active: frozenset[ast.AST]
    ) -> tuple[int, int, int]:
        scan = self.scan(scope.module, scope.func)
        asserts, constant, skips = scan.asserts, scan.constant, scan.skips
        called: set[ast.AST] = set()
        if depth < MAX_HELPER_DEPTH:
            for site in scan.calls:
                helper = self._resolve_site(scope, scan, site)
                if helper is None or helper.func in active:
                    continue  # unresolved, or a cycle: counts 0
                if not self._runs(helper, site):
                    continue
                called.add(helper.func)
                self.reached.add(helper.func)
                a, c, s = self._count(helper, depth + 1, active | {helper.func})
                asserts, constant, skips = asserts + a, constant + c, skips + s
        # a skip hidden in a nested function the test never calls by name still counts
        skips += _skips_in(scope.module, [node for node in scan.inner if node not in called])
        return asserts, constant, skips

    def _runs(self, helper: _Helper, site: _CallSite) -> bool:
        """False when the call only builds a generator or a coroutine: its body never runs."""
        if self.scan(helper.module, helper.func).generator:
            return site.entered and _is_context_manager(helper.func)
        return site.driven or not isinstance(helper.func, ast.AsyncFunctionDef)

    def _resolve_site(self, scope: _Helper, scan: _Scan, site: _CallSite) -> _Helper | None:
        func = site.node.func
        local = {**scope.visible, **scan.nested}  # a function defined here or around here
        if isinstance(func, ast.Name) and func.id in local:
            return _Helper(scope.module, local[func.id], scope.owner, local)
        return self._resolve_call(scope, func)

    def _resolve_call(self, scope: _Helper, func: ast.expr) -> _Helper | None:
        if isinstance(func, ast.Name):
            return self._resolve_name(scope.module, func.id)
        if isinstance(func, ast.Attribute):
            *prefix, name = _attr_chain(func).split(".")
            if not prefix or not prefix[0]:
                return None
            if scope.owner is not None and prefix in (["self"], ["cls"]):
                return self._resolve_method(scope.owner, name)
            target = self._resolve_module(scope.module, prefix)
            if target is not None and name in target.functions:
                return _Helper(target, target.functions[name])
            owner = self._resolve_class(scope.module, prefix)
            if owner is not None:
                return self._resolve_method(owner, name)
        return None

    def _resolve_name(self, module: _Module, name: str) -> _Helper | None:
        if name in module.functions:
            return _Helper(module, module.functions[name])
        imported = module.imports.get(name)
        if imported is not None and imported.name is not None:
            target = self._find_module(module, imported.module, imported.level)
            if target is not None and imported.name in target.functions:
                return _Helper(target, target.functions[imported.name])
        for directory in _ancestors(posixpath.dirname(module.path)):
            conftest = self.modules.get(posixpath.join(directory, CONFTEST))
            if conftest is not None and conftest is not module and name in conftest.functions:
                return _Helper(conftest, conftest.functions[name])
        return None

    def _lineage(self, owner: _Class) -> Iterator[_Class]:
        """The class, then its bases depth first, left to right, as far as they are classes
        of the tests directory."""
        pending = [owner]
        seen: set[ast.ClassDef] = set()
        while pending:
            current = pending.pop(0)
            if current.node in seen:
                continue
            seen.add(current.node)
            yield current
            bases = [
                self._resolve_class(current.module, _attr_chain(base).split("."))
                for base in current.node.bases
            ]
            pending[:0] = [base for base in bases if base is not None]

    def _resolve_method(self, owner: _Class, name: str) -> _Helper | None:
        """The method ``name`` of a class: its own, else the first one found in its bases.

        The helper keeps ``owner``: its ``self`` is still an instance of it.
        """
        for current in self._lineage(owner):
            methods = {
                stmt.name: stmt
                for stmt in current.node.body
                if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            if name in methods:
                return _Helper(current.module, methods[name], owner)
        return None

    def is_collected(self, owner: _Class) -> bool:
        """pytest collects the class: ``Test*``, or a ``unittest`` test case whatever its name."""
        if owner.node.name.startswith(CLASS_PREFIX):
            return True
        return any(
            _last(_attr_chain(base)).endswith(TESTCASE_SUFFIX)
            for current in self._lineage(owner)
            for base in current.node.bases
        )

    def test_methods(self, owner: _Class) -> dict[str, tuple[_Module, FunctionNode]]:
        """The tests the class runs: its own and those it inherits, the nearest definition
        first. A name a nearer class binds to something else hides the inherited test."""
        found: dict[str, tuple[_Module, FunctionNode] | None] = {}
        for current in self._lineage(owner):
            local: dict[str, tuple[_Module, FunctionNode] | None] = {}
            for stmt in current.node.body:
                if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                    if stmt.name.startswith(TEST_PREFIX):
                        local[stmt.name] = (current.module, stmt)
                    continue
                for name in _bound_names(stmt):
                    if name.startswith(TEST_PREFIX):
                        local.setdefault(name, None)
            for name, value in local.items():
                found.setdefault(name, value)
        return {name: value for name, value in found.items() if value is not None}

    def decorators(self, module: _Module, node: FunctionNode | ast.ClassDef) -> tuple[str, ...]:
        """The decorators by the name their import gives them; one that comes from the tests
        directory, or that is not a plain name, can never pass for a known one."""
        names: list[str] = []
        for deco in node.decorator_list:
            chain = _full_chain(deco.func if isinstance(deco, ast.Call) else deco)
            if not chain:
                names.append("<expression>")
            elif self._is_local(module, chain.split(".", 1)[0]):
                names.append(chain + LOCAL_DECORATOR)
            else:
                names.append(_canonical(module, chain))
        return tuple(names)

    def _is_local(self, module: _Module, name: str) -> bool:
        """The name is defined in the module, or imported from inside the tests directory."""
        if name in module.functions or name in module.classes or name in module.assigned:
            return True
        imported = module.imports.get(name)
        if imported is None:
            return False
        if imported.level:
            return True
        dotted = [imported.module]
        if imported.name is not None:
            dotted.append(f"{imported.module}.{imported.name}")
        return any(self._find_module(module, path, 0) is not None for path in dotted)

    def _resolve_class(self, module: _Module, chain: list[str]) -> _Class | None:
        """The class a (dotted) name means: defined in the module or imported into it."""
        *prefix, name = chain
        if prefix:
            target = self._resolve_module(module, prefix)
        elif name in module.classes:
            target = module
        else:
            imported = module.imports.get(name)
            if imported is None or imported.name is None:
                return None
            target = self._find_module(module, imported.module, imported.level)
            name = imported.name
        if target is not None and name in target.classes:
            return _Class(target, target.classes[name])
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


def _collected_classes(module: _Module, resolver: _Resolver) -> list[tuple[str, ast.ClassDef]]:
    """(qualified name, class) of the classes pytest collects in the module: those of the
    module itself and, inside a collected class, the collected ones it holds."""
    found: list[tuple[str, ast.ClassDef]] = []
    pending = [("", stmt) for stmt in reversed(module.tree.body) if isinstance(stmt, ast.ClassDef)]
    while pending:
        prefix, node = pending.pop()
        if not resolver.is_collected(_Class(module, node)):
            continue
        qualname = f"{prefix}{node.name}"
        found.append((qualname, node))
        pending.extend(
            (f"{qualname}.", child)
            for child in reversed(node.body)
            if isinstance(child, ast.ClassDef)
        )
    return found


def _collect(
    module: _Module, resolver: _Resolver
) -> tuple[dict[str, TestInfo], dict[str, tuple[str, ...]]]:
    """(tests, collected classes with their decorators) of a module (ADR-0027)."""
    tests: dict[str, TestInfo] = {}
    classes: dict[str, tuple[str, ...]] = {}

    def add(qualname: str, home: _Module, func: FunctionNode, owner: _Class | None) -> None:
        asserts, constant, skip_calls = resolver.count(home, func, owner)
        skips, cases = _decorator_info(home, func)
        decorators = resolver.decorators(home, func)
        tests[qualname] = TestInfo(asserts, constant, skips + skip_calls, cases, decorators)

    for stmt in module.tree.body:
        is_function = isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
        if is_function and stmt.name.startswith(TEST_PREFIX):
            add(stmt.name, module, stmt, None)
    for qualname, node in _collected_classes(module, resolver):
        owner = _Class(module, node)
        classes[qualname] = resolver.decorators(module, node)
        for name, (home, func) in resolver.test_methods(owner).items():
            add(f"{qualname}.{name}", home, func, owner)
    return tests, classes


def _residual_asserts(module: _Module, resolver: _Resolver) -> int:
    """Assertions outside tests that no test reaches through a helper call.

    Every scope of the module is counted once (module level, class bodies, functions that
    are neither a test nor a helper a test counted, and the nested functions, lambdas and
    classes a test does not call), under the same rules as a test.
    """
    total = 0
    stack: list[ast.AST] = [module.tree]
    while stack:
        node = stack.pop()
        scan = resolver.scan(module, node)
        if node not in resolver.collected and node not in resolver.reached:
            total += scan.asserts
        stack.extend(scan.inner)
    return total


def _outside_skips(module: _Module, counted: Set[ast.AST]) -> int:
    """Ways of skipping anywhere in the file but in its tests and in the helpers they call
    (``counted``): each test already counts those as its own."""
    total = 0
    stack: list[ast.AST] = [module.tree]
    while stack:
        node = stack.pop()
        if node in counted:
            continue
        decorators: list[ast.expr] = getattr(node, "decorator_list", [])
        total += sum(_decorator_skips(module, deco) for deco in decorators)
        total += _skip_form(module, node)
        stack.extend(child for child in ast.iter_child_nodes(node) if child not in decorators)
    return total


def _scope_flags(body: list[ast.stmt], names: Mapping[str, str], classes: Set[str]) -> Counter[str]:
    """``del`` and second bindings of the tests of one module or class body.

    ``names``: name in that scope → qualified name, for its tests and collected classes;
    ``classes``: the collected classes whose test attributes can be assigned from here.
    """
    flags: Counter[str] = Counter()
    bound: Counter[str] = Counter()
    for stmt in _statements(body):
        bound.update(name for name in _bound_names(stmt) if name in names)
        if isinstance(stmt, ast.Delete):
            for target in stmt.targets:
                chain = _full_chain(target)
                if chain in names:
                    flags[f"test removed with del: {names[chain]}"] += 1
                elif "." in chain and _last(chain).startswith(TEST_PREFIX):
                    flags[f"test removed with del: {chain}"] += 1
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                owner, _, name = _full_chain(target).rpartition(".")
                if owner in classes and name.startswith(TEST_PREFIX):
                    flags[f"test name bound again: {owner}.{name}"] += 1
    for name, times in bound.items():
        if times > 1:
            flags[f"test name bound again: {names[name]}"] += times - 1
    return flags


def _review_flags(module: _Module, resolver: _Resolver) -> Counter[str]:
    """What can keep a test of the file from running without touching its body (ADR-0027)."""
    flags: Counter[str] = Counter()
    classes = _collected_classes(module, resolver)
    names = {
        stmt.name: stmt.name
        for stmt in module.tree.body
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
        and stmt.name.startswith(TEST_PREFIX)
    }
    top = {qualname for qualname, _ in classes if "." not in qualname}
    flags.update(_scope_flags(module.tree.body, {**names, **{c: c for c in top}}, top))
    for qualname, node in classes:
        own = {
            stmt.name: f"{qualname}.{stmt.name}"
            for stmt in node.body
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
            and stmt.name.startswith(TEST_PREFIX)
        }
        flags.update(_scope_flags(node.body, own, set()))
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Dict) or (isinstance(value, ast.Constant) and value.value):
            continue  # a true value, or the doctest table of a module
        for target in targets:
            name = getattr(target, "id", None) or getattr(target, "attr", None)
            if name == "__test__":
                flags["__test__ set to a false value"] += 1
    if posixpath.basename(module.path) == CONFTEST:
        for stmt in _statements(module.tree.body):
            hooks = [name for name in _assigned_names(stmt) if name in CONFTEST_SETTINGS]
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                if stmt.name.startswith("pytest_"):
                    hooks.append(stmt.name)
                elif _is_autouse(stmt):
                    hooks.append(f"autouse fixture {stmt.name}")
            for hook in hooks:
                flags[f"conftest hook added: review ({hook})"] += 1
    return flags


def analyze(files: Mapping[str, str | None]) -> Inventory:
    """``files``: path → content (``None`` when it is not a readable .py file)."""
    inv = Inventory()
    modules: dict[str, _Module] = {}
    for path, content in files.items():
        inv.files.add(path)
        if content is None or not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content.removeprefix("﻿"), filename=path)
        except SyntaxError as exc:
            inv.unparsed[path] = f"{path}: cannot parse ({exc.msg}, line {exc.lineno})"
        except (ValueError, RecursionError, MemoryError) as exc:
            inv.unparsed[path] = f"{path}: cannot parse ({type(exc).__name__})"
        else:
            modules[path] = _index_module(path, tree)
    inv.errors.extend(inv.unparsed.values())
    resolver = _Resolver(modules)
    for path, module in modules.items():
        tests, classes = _collect(module, resolver)
        inv.tests.update({f"{path}::{name}": info for name, info in tests.items()})
        inv.classes.update({f"{path}::{name}": decos for name, decos in classes.items()})
        inv.asserts += sum(info.asserts for info in tests.values())
    for path, module in modules.items():  # once every test of every module is known
        inv.asserts += _residual_asserts(module, resolver)
        inv.outside_skips[path] = _outside_skips(module, resolver.collected | resolver.reached)
        inv.flags[path] = _review_flags(module, resolver)
    return inv


# ------------------------------------------------------------------------ inventories
def _normalize_dir(value: str) -> str:
    return value.strip().replace("\\", "/").strip("/")


def _setting(pyproject: str | None, key: str) -> object:
    """``[tool.rumbo].<key>`` of that pyproject.toml text; None without the table or the key."""
    if pyproject is None:
        return None
    try:
        data = tomllib.loads(pyproject)
    except ValueError:  # not TOML
        return None
    tool = data.get("tool")
    rumbo = tool.get("rumbo") if isinstance(tool, dict) else None
    return rumbo.get(key) if isinstance(rumbo, dict) else None


def _rumbo_setting(root: Path, key: str) -> object:
    """``[tool.rumbo].<key>`` of the working tree; None without the file, the table or the key."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        return _setting(pyproject.read_text(encoding="utf-8"), key)
    except (OSError, ValueError):  # unreadable or not UTF-8
        return None


def _base_setting(base: str, root: Path, key: str) -> object:
    """``[tool.rumbo].<key>`` as the base commit has it (ADR-0026)."""
    shown = _git(["show", f"{base}:./pyproject.toml"], root)
    return _setting(shown.stdout if shown.returncode == 0 else None, key)


def _directory(value: object) -> str:
    if isinstance(value, str) and _normalize_dir(value):
        return _normalize_dir(value)
    return TESTS_DIR


def tests_directory(root: Path, override: str | None = None) -> str:
    """``--tests-dir`` if given, else ``[tool.rumbo].tests`` of the working tree, else ``tests``."""
    if override and _normalize_dir(override):
        return _normalize_dir(override)
    return _directory(_rumbo_setting(root, "tests"))


def tests_directories(root: Path, base: str, override: str | None = None) -> list[str]:
    """The directories to inventory on both sides (ADR-0026).

    ``--tests-dir`` alone when given. Else the directory the BASE configured, followed by the
    one of the working tree when the change under test names another.
    """
    if override and _normalize_dir(override):
        return [_normalize_dir(override)]
    configured = _directory(_base_setting(base, root, "tests"))
    current = tests_directory(root)
    return [configured] if current == configured else [configured, current]


def _own_authors(value: object) -> list[str]:
    extra = value if isinstance(value, list) else []
    return [item for item in extra if isinstance(item, str) and item.strip()]


def agent_authors(root: Path, base: str | None = None) -> tuple[str, ...]:
    """``DEFAULT_AGENT_AUTHORS`` plus the strings of ``[tool.rumbo].agent_authors``: those of
    the base commit, when one is given, and those of the working tree (ADR-0026)."""
    authors = list(DEFAULT_AGENT_AUTHORS)
    if base is not None:
        authors += _own_authors(_base_setting(base, root, "agent_authors"))
    known = set(authors[len(DEFAULT_AGENT_AUTHORS) :])
    authors += [
        item for item in _own_authors(_rumbo_setting(root, "agent_authors")) if item not in known
    ]
    return tuple(authors)


def _under(path: str, directory: str) -> bool:
    return directory in ("", ".") or path == directory or path.startswith(f"{directory}/")


def _directories(tests_dir: str | Iterable[str]) -> list[str]:
    return [tests_dir] if isinstance(tests_dir, str) else list(tests_dir)


def _outer_conftests(directories: Iterable[str]) -> list[str]:
    """``conftest.py`` of every directory above the tests directories, up to the root: pytest
    loads them as well, so a hook there reaches every test."""
    found: list[str] = []
    for directory in directories:
        if directory in ("", ".") or ".." in directory.split("/"):
            continue
        for ancestor in _ancestors(posixpath.dirname(directory)):
            path = posixpath.join(ancestor, CONFTEST)
            if path not in found:
                found.append(path)
    return found


def base_inventory(base: str, root: Path, tests_dir: str | Iterable[str] = TESTS_DIR) -> Inventory:
    """The inventory at the base commit; paths are relative to ``root``, wherever the
    repository starts. A base that cannot be read raises: it is not "no tests"."""
    directories = _directories(tests_dir)
    paths = [*directories, *_outer_conftests(directories)]
    listing = _git(["ls-tree", "-r", "-z", "--name-only", base, "--", *paths], root)
    if listing.returncode != 0:
        why = listing.stderr.strip() or f"git ls-tree exit {listing.returncode}"
        raise SensorError(f"cannot list the tests at the base {base[:12]}: {why}")
    files: dict[str, str | None] = {}
    for path in filter(None, listing.stdout.split("\0")):
        content = None
        if path.endswith(".py"):
            shown = _git(["show", f"{base}:./{path}"], root)
            if shown.returncode != 0:
                why = shown.stderr.strip() or f"git show exit {shown.returncode}"
                raise SensorError(f"cannot read {path} at the base {base[:12]}: {why}")
            content = shown.stdout
        files[path] = content
    return analyze(files)


def worktree_inventory(root: Path, tests_dir: str | Iterable[str] = TESTS_DIR) -> Inventory:
    directories = _directories(tests_dir)
    found: list[Path] = []
    for directory in directories:
        folder = root / directory
        if folder.is_dir():
            found.extend(sorted(folder.rglob("*")))
    found.extend(root / conftest for conftest in _outer_conftests(directories))
    files: dict[str, str | None] = {}
    for path in found:
        if path.is_file() and "__pycache__" not in path.parts:
            rel = path.relative_to(root).as_posix()
            files[rel] = (
                path.read_text(encoding="utf-8-sig", errors="replace")
                if path.suffix == ".py"
                else None
            )
    return analyze(files)


def _is_known_decorator(name: str) -> bool:
    return any(name == known or name.startswith(f"{known}.") for known in KNOWN_DECORATORS)


def _added_decorators(key: str, old: tuple[str, ...], new: tuple[str, ...]) -> list[str]:
    added = Counter(new) - Counter(old)
    return [
        f"{key}: decorator added: review (@{name})"
        for name in sorted(added)
        if not _is_known_decorator(name)
    ]


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
        problems.extend(_added_decorators(name, old.decorators, new.decorators))
    for name in sorted(base.classes.keys() & current.classes.keys()):
        problems.extend(_added_decorators(name, base.classes[name], current.classes[name]))
    if current.asserts < base.asserts:
        problems.append(
            f"total asserts in the tests directory dropped from {base.asserts} to {current.asserts}"
        )
    for path in sorted(current.flags.keys() | current.outside_skips.keys()):
        # A new file weakens no test of the base, unless pytest loads it before the others.
        if path not in base.files and posixpath.basename(path) not in LOADED_FIRST:
            continue
        before = base.flags.get(path, Counter())
        after = current.flags.get(path, Counter())
        problems.extend(f"{path}: {flag}" for flag in sorted(after) if after[flag] > before[flag])
        old_skips, new_skips = base.outside_skips.get(path, 0), current.outside_skips.get(path, 0)
        if new_skips > old_skips:
            problems.append(
                f"{path}: skip/xfail forms outside the tests grew from {old_skips} to {new_skips}"
            )
    problems.extend(
        f"parse error: {error}"
        for path, error in current.unparsed.items()
        if path not in base.unparsed
    )
    return problems


def notes(base: Inventory, current: Inventory) -> list[str]:
    """What is worth saying and is not a problem: a file that never parsed (a fixture)."""
    return [
        f"{error}: it did not parse at the base either"
        for path, error in current.unparsed.items()
        if path in base.unparsed
    ]


# --------------------------------------------------------------------------- base
def _resolve_base(explicit: str | None, root: Path) -> tuple[str | None, list[str], bool]:
    """(merge-base commit or None, warnings, the base is HEAD and nobody chose it).

    A base named by ``--base``, ``RUMBO_BASE_REF`` or ``GITHUB_BASE_REF`` is somebody's
    choice, HEAD included. So is the all-zero ``RUMBO_BASE_REF``: the way a CI run says the
    ref is new, with nothing before it to compare with.
    """
    warnings: list[str] = []
    candidates: list[tuple[str, str]] = []  # (ref, where it comes from; "" for a default)
    pushed = (os.environ.get("RUMBO_BASE_REF") or "").strip()
    for value, source in ((explicit, "explicit base"), (pushed, "RUMBO_BASE_REF")):
        if value and value.strip() and value.strip() != NULL_SHA:
            candidates.append((value.strip(), source))
    gh_base = os.environ.get("GITHUB_BASE_REF")
    if gh_base:
        candidates.append((f"origin/{gh_base}", "GITHUB_BASE_REF"))
    candidates.extend((ref, "") for ref in DEFAULT_BASES)
    for candidate, source in candidates:
        rp = _git(["rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"], root)
        if rp.returncode != 0:
            if source:
                warnings.append(f"{source} {candidate!r} is not reachable; trying defaults")
            continue
        mb = _git(["merge-base", "HEAD", candidate], root)
        resolved = mb.stdout.strip() if mb.returncode == 0 and mb.stdout.strip() else candidate
        head = _git(["rev-parse", "HEAD"], root).stdout.strip()
        unchosen = resolved == head and not source
        if unchosen:
            warnings.append(
                f"base {candidate!r} is HEAD itself: nothing to compare "
                "(set origin/main or pass --base)"
            )
        return resolved, warnings, unchosen and pushed != NULL_SHA
    return None, warnings, False


def resolve_base(explicit: str | None, root: Path) -> tuple[str | None, list[str]]:
    """Returns (merge-base commit or None, warnings)."""
    base, warnings, _ = _resolve_base(explicit, root)
    return base, warnings


# ----------------------------------------------------------------------- approval
def _coauthors(message: str) -> list[str]:
    """The values of the ``Co-authored-by:`` trailers of a commit message."""
    values: list[str] = []
    for line in message.splitlines():
        key, colon, value = line.partition(":")
        if colon and key.strip().lower() == COAUTHOR_TRAILER:
            values.append(value.strip())
    return values


def _is_agent_commit(author: str, committer: str, message: str, authors: tuple[str, ...]) -> bool:
    """``author`` and ``committer`` are ``Name <email>``; ``authors`` the agent identities."""
    identities = [who.lower() for who in (author, committer, *_coauthors(message))]
    if any(marker.lower() in who for marker in authors for who in identities):
        return True
    lowered = message.lower()
    return any(trailer.lower() in lowered for trailer in DEFAULT_AGENT_TRAILERS)


def _label_approval() -> str | None:
    """The label of the pull request, only where GitHub Actions wrote the event file: anywhere
    else a JSON file and one variable would approve the change."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    if not os.environ.get("GITHUB_EVENT_NAME", "").startswith("pull_request"):
        return None
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not Path(event_path).is_file():
        return None
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        labels = [lb.get("name") for lb in event.get("pull_request", {}).get("labels", [])]
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    return f"pull request label '{APPROVAL_LABEL}'" if APPROVAL_LABEL in labels else None


def human_approval(base: str, root: Path) -> str | None:
    """Reason string when a human approved the change, else None."""
    label = _label_approval()
    if label:
        return label
    authors = agent_authors(root, base)
    # NUL between the five fields and (-z) between the commits: no name, email or message can
    # hold one, so a message cannot pass for the record of another commit.
    fields = "%H%x00%ae%x00%an <%ae>%x00%cn <%ce>%x00%B"
    log = _git(["log", "-z", f"--format={fields}", f"{base}..HEAD"], root)
    values = log.stdout.split("\0")
    for start in range(0, len(values) - 4, 5):
        sha, email, author, committer, message = values[start : start + 5]
        if APPROVAL_MARK not in message:
            continue
        if _is_agent_commit(author, committer, message, authors):
            print(f"  (ignoring {APPROVAL_MARK} in {sha[:10]}: commit authored by an agent)")
            continue
        return f"commit {sha[:10]} by {email}"
    return None


def _lenient_output() -> None:
    """A file name the console cannot encode is printed escaped instead of raising."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        with contextlib.suppress(OSError, ValueError):
            reconfigure(errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rumbo check-tests",
        description="RUMBO anti-cheat sensor: fails when tests get weaker than the base.",
    )
    parser.add_argument("--base", help="base ref or SHA (default: origin/main or the PR base)")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 2 when no base can be resolved, or when a default base is HEAD itself",
    )
    parser.add_argument(
        "--tests-dir",
        help="tests directory (default: [tool.rumbo].tests in pyproject.toml, or tests)",
    )
    args = parser.parse_args(argv)
    _lenient_output()
    root = Path(args.root).resolve()

    base, warnings, unchosen_head = _resolve_base(args.base, root)
    for warning in warnings:
        print(f"WARNING: {warning}")
    if base is None:
        print("WARNING: no base to compare against (no origin/main?).")
        return 2 if args.strict else 0
    if unchosen_head and args.strict:
        print(
            "ERROR: nothing to compare: the base found by default is HEAD itself. "
            "Pass --base or set RUMBO_BASE_REF to the commit to compare with."
        )
        return 2

    directories = tests_directories(root, base, args.tests_dir)
    try:
        before = base_inventory(base, root, directories)
    except SensorError as exc:
        print(f"ERROR: {exc}")
        print("A base that cannot be read is not a base without tests: nothing was compared.")
        return 2
    after = worktree_inventory(root, directories)
    problems = compare(before, after)
    for note in notes(before, after):
        print(f"NOTE: {note}")
    if len(directories) > 1:  # ADR-0026: the change under test names another tests directory
        configured, current = directories
        change = f"[tool.rumbo].tests changed from {configured!r} to {current!r}"
        left = sorted(
            path for path in before.files if _under(path, configured) and not _under(path, current)
        )
        if left:
            problems.insert(
                0,
                f"{change}: {len(left)} file(s) of the base leave the tests directory "
                f"(first: {left[0]})",
            )
        else:
            print(f"NOTE: {change}; no file of the base leaves the tests directory.")
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
