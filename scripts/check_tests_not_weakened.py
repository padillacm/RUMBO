#!/usr/bin/env python3
"""RUMBO anti-cheat sensor: fails when the tests get weaker than the base revision.

Compares the tests directory at the base (``origin/main`` or the PR base commit) against
the working tree and fails if any of these happen:

1. a file under the tests directory is deleted;
2. a test disappears (identified by file + qualified name);
3. a test loses assertions, or the total number of assertions in the tests drops;
4. a test gains always-true asserts (``assert True``, ``assert (x, "msg")``);
5. a test or a test class gains a way of skipping, or its file gains one that reaches the
   tests without touching them;
6. a ``parametrize`` literal list shrinks;
7. a test leaves the place where pytest collects it for sure (ADR-0027): it goes under a
   condition, its class stops being one pytest collects, a ``del`` or a second binding of
   its name appears, ``__test__ = False`` is set where the base had tests, a decorator
   written in the tests directory is added to it, or a ``conftest.py`` (or a plugin module of
   the tests) gains a hook that decides which tests run or how they end.

What is inventoried (ADR-0027): every function whose name starts with ``test``, wherever it
is written (module, class, under an ``if``/``try``/``with``, inside another function), under
its qualified name, plus the tests a class inherits from base classes of the tests
directory. Each carries whether it is under a condition and whether pytest collects its
class: ``Test*``, or a class with a base the sensor cannot see (it may be a ``TestCase``),
or with a collected base. When in doubt a test is inventoried: a test nobody looks at can
be emptied unnoticed.

Assertions are counted the way idiomatic Python writes them (ADR-0007). Inside a test,
one each: ``assert``; every ``with pytest.raises(...)``, ``pytest.warns(...)`` or
``pytest.deprecated_call(...)`` item; ``self.assert*(...)`` and ``self.fail(...)``; the
``unittest.mock`` assertions by exact name on any receiver; ``pytest.fail(...)``. A call to
a helper function defined in the same file, in a ``conftest.py`` of the same directory or
an ancestor, or in a module imported from inside the tests directory counts as the
assertions of that helper's body under the same rules (depth at most 3, cycles count 0).
So does a call ``self.<name>(...)``, ``cls.<name>(...)`` or ``<Class>.<name>(...)`` to a
helper method of the test's class or of a base class defined or imported inside the tests
directory. Anything else counts 0, so a hollow helper never passes for an assertion.

Each test has two counts and a drop in either is reported. The first is every assertion
written in it, as above. The second leaves out the ones that plainly cannot run or cannot
fail (ADR-0027): in a nested function, lambda or class the test never mentions again; under
a ``try`` whose handler catches ``AssertionError``, ``Exception``, ``BaseException`` or
everything and does nothing (``pass``, ``...``, or ``continue`` with no assertion after
it); under ``with suppress(...)`` of one of those; in a generator or coroutine helper whose
call is a statement by itself, so that nothing can run it; in a test that yields.

Skips are counted per test (``skip``/``skipif``/``xfail`` marks, also through a name of the
tests that stands for one, the ``unittest`` skip decorators, ``pytest.param(...,
marks=...)``, ``pytest.skip()``, ``pytest.xfail()``, ``pytest.importorskip()``,
``self.skipTest()``, ``raise SkipTest``, and the skips of the fixtures the test uses, by
parameter or by ``usefixtures``), per test class (its decorators, its ``pytestmark``, its
``setUp``) and per file for what skips the tests of the file without touching them:
module-level statements (``pytestmark``, ``pytest.skip``, ``importorskip``),
``setup_module``/``setup_function`` and autouse fixtures. A new test, a new class and a
new file may carry their own.

What it does NOT see (review these by hand, and let mutation testing catch the rest):
early ``return`` before the asserts, asserts under a condition that is false, a handler
that swallows the failure after doing something, mocks that empty the behavior, mock
assertions on a fresh mock, a widened ``pytest.raises``, helpers built dynamically or
imported from outside the tests directory, and the changes to
``[tool.pytest.ini_options]``.

The tests directory is ``--tests-dir`` on the command line, else ``[tool.rumbo].tests`` in
``pyproject.toml`` (default ``tests``). The harness settings are read from the BASE, not
from the tree under test (ADR-0026): when the working tree names another tests directory,
both are inventoried on both sides, and the change is a problem when it leaves files of the
base out; ``[tool.rumbo].agent_authors`` is the union of the base's list and the tree's. The
``conftest.py`` files above the tests directory are inventoried too: pytest loads them.

The base is the merge base of HEAD with the first of these that resolves: ``--base``,
``RUMBO_BASE_REF`` (the all-zero SHA says "this ref is new" and is skipped),
``origin/<GITHUB_BASE_REF>``, ``origin/<RUMBO_DEFAULT_BRANCH>``, ``origin/main``,
``origin/master``, ``origin/HEAD``, ``main``, ``master``.

The rule can only be bypassed by a HUMAN: either a commit in ``base..HEAD`` whose message
contains ``[tests-change-approved]`` and that no agent took part in (ADR-0021:
its author, its committer and its ``Co-authored-by:`` trailers match no agent identity, that
is ``DEFAULT_AGENT_AUTHORS`` plus ``[tool.rumbo].agent_authors`` in ``pyproject.toml``,
case-insensitive substrings of ``Name <email>``; and its message carries none of
``DEFAULT_AGENT_TRAILERS``), or the ``tests-change-approved`` label on the pull request
(read from ``GITHUB_EVENT_PATH``, only in a ``pull_request*`` run of GitHub Actions). The
agent never writes that mark.

Standard library only: it is copied verbatim into every RUMBO project.
Exit codes: 0 ok (also: nothing to compare on a new ref or in a run nobody pushed for) ·
1 tests weakened · 2 usage error, a base that cannot be read, or (with --strict) no base,
or a base found by default that is HEAD itself with nothing uncommitted to compare.
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
# Where a base that is HEAD itself is somebody's choice, not the lack of a better one.
CHOSEN_BASES = {"explicit base", "RUMBO_BASE_REF", "GITHUB_BASE_REF"}
# GitHub events with no push behind them: on the default branch there is nothing to compare.
UNPUSHED_EVENTS = {"workflow_dispatch", "schedule"}
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
# ADR-0027: pytest's default prefixes (`python_functions`, `python_classes`).
TEST_PREFIX = "test"
CLASS_PREFIX = "Test"
CONFTEST = "conftest.py"
# What pytest imports before the tests of a directory: a new one reaches all of them.
LOADED_FIRST = {CONFTEST, "__init__.py"}
# A decorator whose last name is one of these skips, whatever it hangs from.
SKIP_NAMES = {"skip", "skipif", "xfail", "skipIf", "skipUnless", "expectedFailure"}
# The same marks anywhere else (`pytestmark = ...`, `pytest.param(marks=...)`), by the name
# they have once the imports of the module are followed.
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
# Their last names: what an attribute must end in to be worth resolving.
MARK_ENDS = frozenset(mark.rsplit(".", 1)[-1] for mark in SKIP_MARKS)
CALL_ENDS = frozenset(call.rsplit(".", 1)[-1] for call in SKIP_CALLS)
# What runs before every test of a module or of a class without being asked for.
SETUP_FUNCTIONS = {"setup_module", "setup_function"}
SETUP_METHODS = {"setUp", "setUpClass", "asyncSetUp", "setup_method", "setup_class"}
# Catching one of these catches a failed assertion.
SWALLOWED = {"AssertionError", "Exception", "BaseException"}
# The hooks of a conftest.py that decide which tests run or how they end. Those that only
# configure the run (pytest_addoption, pytest_configure, pytest_plugins...) are not here.
REVIEW_HOOKS = {
    "pytest_collection_modifyitems",
    "pytest_ignore_collect",
    "pytest_collect_file",
    "pytest_pycollect_makeitem",
    "pytest_itemcollected",
    "pytest_deselected",
    "pytest_generate_tests",
    "pytest_runtest_setup",
    "pytest_runtest_call",
    "pytest_runtest_teardown",
    "pytest_runtest_makereport",
    "pytest_runtest_protocol",
    "pytest_runtest_logreport",
    "pytest_pyfunc_call",
    "pytest_report_teststatus",
    "pytest_sessionfinish",
    "pytest_exception_interact",
    "collect_ignore",
    "collect_ignore_glob",
}
# How a decorator written in the tests directory is named in a problem.
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
# git modes of the entries that are not files of this repository.
GITLINK = "160000"  # a submodule: its files belong to another repository
SYMLINK = "120000"  # a symbolic link: its content is the path it points to
# Directories of the working tree that are never tests of this project, and what marks a
# directory as another repository (a submodule) or a virtual environment.
PRUNED = {"__pycache__", ".git"}
FOREIGN = (".git", "pyvenv.cfg")


class SensorError(RuntimeError):
    """The comparison cannot be made: the run stops with exit code 2 instead of passing."""


@dataclass
class TestInfo:
    asserts: int = 0  # the assertions that can run and fail (ADR-0027)
    constant_asserts: int = 0
    skips: int = 0
    param_cases: int | None = None
    decorators: tuple[str, ...] = ()
    lexical: int = 0  # every assertion written, as v0.4.0 counted them
    conditional: bool = False  # defined under an `if`, a `try`, a `with`...
    collected: bool = True  # at module level, or in a class pytest collects


@dataclass
class ClassInfo:
    decorators: tuple[str, ...] = ()
    skips: int = 0  # its skip decorators, its `pytestmark`, its set-up methods


@dataclass
class Inventory:
    files: set[str] = field(default_factory=set)
    tests: dict[str, TestInfo] = field(default_factory=dict)
    asserts: int = 0
    errors: list[str] = field(default_factory=list)
    # file that does not parse → its message in `errors`, and its text
    unparsed: dict[str, str] = field(default_factory=dict)
    unparsed_text: dict[str, str] = field(default_factory=dict)
    # class pytest collects (`path::Qualified.Name`) → its decorators and its skips
    classes: dict[str, ClassInfo] = field(default_factory=dict)
    # file → ways of skipping its tests without touching them
    outside_skips: dict[str, int] = field(default_factory=dict)
    # file → (kind, qualified name) → times: `del`, second bindings, `__test__`, hooks
    flags: dict[str, Counter[tuple[str, str]]] = field(default_factory=dict)
    # symbolic links of the base: one entry in git, whatever they are on disk
    links: set[str] = field(default_factory=set)


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
_DEFINITION = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)


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


def _callee(node: ast.expr) -> ast.expr:
    """What is called, however many times: ``f`` for ``f(1)(2)``."""
    while isinstance(node, ast.Call):
        node = node.func
    return node


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


def _assertions(node: ast.AST) -> int:
    """How many assertions the node itself is: an ``assert``, a call that counts, or the
    pytest contexts of a ``with``."""
    if isinstance(node, ast.Assert):
        return 1
    if isinstance(node, ast.Call):
        return int(_is_assert_call(node))
    if isinstance(node, ast.With | ast.AsyncWith):
        return sum(_is_pytest_context(item) for item in node.items)
    return 0


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


def _inner_statements(stmt: ast.stmt) -> list[ast.stmt]:
    """The statements directly under a compound statement (``if``, ``try``, ``with``...)."""
    found: list[ast.stmt] = []
    for child in ast.iter_child_nodes(stmt):
        if isinstance(child, ast.stmt):
            found.append(child)
        elif isinstance(child, ast.ExceptHandler | ast.match_case):
            found.extend(child.body)
    return found


def _statements(body: Iterable[ast.stmt]) -> Iterator[ast.stmt]:
    """The statements that run in the scope of a module or class body: its own and those
    under its ``if``, ``for``, ``try``, ``with``..., not the bodies of functions and classes."""
    stack: list[ast.stmt] = [*reversed(list(body))]
    while stack:
        stmt = stack.pop()
        yield stmt
        if not isinstance(stmt, _DEFINITION):
            stack.extend(reversed(_inner_statements(stmt)))


def _own_nodes(stmt: ast.stmt) -> Iterator[ast.AST]:
    """The statement and its expressions, without the statements and scopes it holds."""
    yield stmt
    parts = [
        child
        for child in ast.iter_child_nodes(stmt)
        if not isinstance(child, ast.stmt | ast.ExceptHandler | ast.match_case)
    ]
    yield from _walk_scope(parts)


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


def _assigned_value(stmt: ast.stmt, name: str) -> ast.expr | None:
    """The value of ``name = value`` (or ``name: T = value``), else None."""
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target: ast.expr = stmt.targets[0]
        value: ast.expr | None = stmt.value
    elif isinstance(stmt, ast.AnnAssign):
        target, value = stmt.target, stmt.value
    else:
        return None
    return value if isinstance(target, ast.Name) and target.id == name else None


def _bound_names(stmt: ast.stmt) -> list[str]:
    """The names a statement binds in its scope (definitions, imports and assignments)."""
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [stmt.name]
    if isinstance(stmt, ast.Import | ast.ImportFrom):
        return [alias.asname or alias.name.split(".", 1)[0] for alias in stmt.names]
    return _assigned_names(stmt)


def _gags(handler: ast.ExceptHandler, body: list[ast.AST], tried: ast.Try | ast.TryStar) -> bool:
    """True when the handler catches a failed assertion and does nothing with it: ``pass``,
    ``...``, a string, or ``continue`` when no assertion of the function (``body``) comes
    after the ``try``. A handler that sleeps, records, reports or raises is the test's business."""
    kind = handler.type
    kinds = kind.elts if isinstance(kind, ast.Tuple) else [] if kind is None else [kind]
    if kind is not None and not any(_last(_attr_chain(k)) in SWALLOWED for k in kinds):
        return False
    goes_on = False
    for stmt in handler.body:
        if isinstance(stmt, ast.Continue):
            goes_on = True
        elif not (
            isinstance(stmt, ast.Pass)
            or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
        ):
            return False
    if not goes_on:
        return True
    end = tried.end_lineno or tried.lineno
    return not any(
        _assertions(node) and getattr(node, "lineno", 0) > end
        for stmt in body
        for node in ast.walk(stmt)
    )


def _suppresses(item: ast.withitem) -> bool:
    """``with suppress(AssertionError)`` (or a wider class): its body cannot fail an assert."""
    call = item.context_expr
    if not isinstance(call, ast.Call) or _last(_attr_chain(call.func)) != "suppress":
        return False
    return any(_last(_attr_chain(arg)) in SWALLOWED for arg in call.args)


def _is_fixture(func: FunctionNode) -> bool:
    return any(_last(_attr_chain(_callee(deco))) == "fixture" for deco in func.decorator_list)


def _parameters(func: FunctionNode) -> list[str]:
    arguments = func.args
    return [arg.arg for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)]


def _usefixtures(marks: Iterable[ast.AST]) -> list[str]:
    """The fixtures named by ``usefixtures("a", "b")`` anywhere in those expressions."""
    names: list[str] = []
    for mark in marks:
        for node in ast.walk(mark):
            if isinstance(node, ast.Call) and _last(_attr_chain(node.func)) == "usefixtures":
                names += [
                    arg.value
                    for arg in node.args
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                ]
    return names


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


def _unused_definitions(body: list[ast.AST]) -> set[ast.AST]:
    """The functions, classes and named lambdas of a function body that the body never
    mentions again: not called, not passed on, not assigned, not returned, not a decorator.
    Nothing can run them. A method goes with its class; a lambda written where it is used
    (an argument, an attribute) is in use, and so is whatever has a decorator of its own,
    which receives it (``@app.route("/x")``)."""
    loads = Counter(
        node.id
        for stmt in body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    )
    unused: set[ast.AST] = set()
    stack: list[tuple[ast.AST, bool]] = [(stmt, False) for stmt in body]
    while stack:
        node, in_class = stack.pop()
        name, target = "", node
        if isinstance(node, _DEFINITION) and not in_class:
            name = "" if node.decorator_list else node.name
        elif isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(node.value, ast.Lambda):
            names = _assigned_names(node)
            if len(names) == 1 and _assigned_value(node, names[0]) is not None:
                name, target = names[0], node.value
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Lambda):
            unused.add(node.value)
            continue
        if name:
            inside = sum(
                isinstance(sub, ast.Name) and sub.id == name and isinstance(sub.ctx, ast.Load)
                for sub in ast.walk(target)
            )
            if loads[name] <= inside:
                unused.add(target)
                continue
        # a method is what its class holds, also under an `if` of the class body
        of_class = isinstance(node, ast.ClassDef) or (in_class and not isinstance(node, _NESTED))
        stack.extend((child, of_class) for child in ast.iter_child_nodes(node))
    return unused


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
    # names of the module scope, also under its `if`/`try`: definitions, and `name = value`
    defined: dict[str, ast.AST] = field(default_factory=dict)
    aliases: dict[str, ast.expr] = field(default_factory=dict)
    # found anywhere in the file: autouse fixtures, and `X.__test__ = <false>` assignments
    autouse: list[FunctionNode] = field(default_factory=list)
    untested: list[ast.Attribute] = field(default_factory=list)


@dataclass(frozen=True, eq=False)
class _Class:
    """A class and the module that defines it."""

    module: _Module
    node: ast.ClassDef


@dataclass(frozen=True, eq=False)
class _Helper:
    """A function whose body is counted; ``owner`` is the class of its ``self``, if any."""

    module: _Module
    func: FunctionNode
    owner: _Class | None = None


@dataclass(frozen=True, eq=False)
class _CallSite:
    """A call that may be a helper's."""

    node: ast.Call
    live: bool  # not where an assertion cannot run or cannot fail
    discarded: bool  # a statement by itself: nothing takes what the call returns


@dataclass
class _Scan:
    """What the body of one function holds, with its nested functions and classes."""

    lexical: int = 0  # every assertion written
    effective: int = 0  # those that can run and fail
    constant: int = 0
    skips: int = 0
    generator: bool = False
    sites: list[_CallSite] = field(default_factory=list)


@dataclass(frozen=True, eq=False)
class _Definition:
    """A ``test*`` function, where it is written."""

    qualname: str
    module: _Module
    func: FunctionNode
    owner: _Class | None
    conditional: bool
    collected: bool


def _index_module(path: str, tree: ast.Module) -> _Module:
    module = _Module(path, tree)
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            module.functions[stmt.name] = stmt
        elif isinstance(stmt, ast.ClassDef):
            module.classes[stmt.name] = stmt
    for stmt in _statements(tree.body):
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            module.defined[stmt.name] = stmt
        for name in _assigned_names(stmt):
            value = _assigned_value(stmt, name)
            if value is not None:
                module.aliases[name] = value
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
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.decorator_list and _is_autouse(node):
                module.autouse.append(node)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            module.untested += [
                target for target in _falsy_dunder_test(node) if isinstance(target, ast.Attribute)
            ]
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


def _skip_call(module: _Module, node: ast.AST) -> int:
    """1 when the node skips where it runs: a call that skips, or ``raise SkipTest``."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr == "skipTest":
                return 1
            if func.attr not in CALL_ENDS:
                return 0
        elif not (isinstance(func, ast.Name) and func.id in module.imports):
            return 0  # a plain name skips only when it was imported as one that does
        return int(_canonical(module, _full_chain(func)) in SKIP_CALLS)
    if isinstance(node, ast.Raise) and node.exc is not None:
        name = _canonical(module, _full_chain(_callee(node.exc)))
        return int(_last(name) == "SkipTest" or name.startswith(("pytest.skip.", "pytest.xfail.")))
    return 0


def _skip_form(module: _Module, node: ast.AST) -> int:
    """1 when the node is a way of skipping or of expecting a failure, else 0."""
    if isinstance(node, ast.Attribute):
        if node.attr not in MARK_ENDS:
            return 0
        return int(_canonical(module, _full_chain(node)) in SKIP_MARKS)
    if isinstance(node, ast.Name):
        if node.id not in module.imports:
            return 0
        return int(_canonical(module, node.id) in SKIP_MARKS)
    return _skip_call(module, node)


def _skips_in(module: _Module, nodes: Iterable[ast.AST]) -> int:
    return sum(_skip_form(module, node) for root in nodes for node in ast.walk(root))


def _decorator_skips(module: _Module, deco: ast.expr) -> int:
    named = _last(_attr_chain(_callee(deco))) in SKIP_NAMES
    return max(_skips_in(module, [deco]), int(named))


def _scan(module: _Module, root: ast.AST) -> _Scan:
    """Count the assertions of a function twice: all of them, and those that can run and
    fail (ADR-0027). Its nested functions and classes are part of it, as they were in
    v0.4.0; the ones it never mentions again only count in the first number."""
    scan = _Scan()
    body = _body(root)
    unused: set[ast.AST] | None = None  # worked out when the first nested definition shows up
    # (node, where an assertion can run and fail, in the function's own scope, discarded)
    stack: list[tuple[ast.AST, bool, bool, bool]] = [
        (node, True, True, False) for node in reversed(body)
    ]
    while stack:
        node, live, own, discarded = stack.pop()
        scan.skips += _skip_form(module, node)
        found = _assertions(node)
        scan.lexical += found
        scan.effective += found if live else 0
        children: list[tuple[ast.AST, bool, bool, bool]]
        if isinstance(node, _NESTED):
            if unused is None:
                unused = _unused_definitions(body)
            inside = live and node not in unused
            children = [(child, inside, False, False) for child in ast.iter_child_nodes(node)]
        elif isinstance(node, ast.Try | ast.TryStar):
            inside = live and not any(_gags(handler, body, node) for handler in node.handlers)
            children = [(child, inside, own, False) for child in node.body]
            rest = [*node.handlers, *node.orelse, *node.finalbody]
            children += [(child, live, own, False) for child in rest]
        elif isinstance(node, ast.With | ast.AsyncWith):
            inside = live and not any(_suppresses(item) for item in node.items)
            children = [(item.context_expr, live, own, False) for item in node.items]
            children += [(child, inside, own, False) for child in node.body]
        else:
            if isinstance(node, ast.Yield | ast.YieldFrom):
                scan.generator = scan.generator or own
            elif isinstance(node, ast.Assert):
                scan.constant += _is_constant_assert(node)
            elif isinstance(node, ast.Call) and not found:
                scan.sites.append(_CallSite(node, live, discarded))
            alone = isinstance(node, ast.Expr)
            children = [(child, live, own, alone) for child in ast.iter_child_nodes(node)]
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
        self._scans: dict[ast.AST, _Scan] = {}
        self._collected: dict[ast.ClassDef, bool] = {}
        self._skips: dict[ast.AST, int] = {}  # fixture → the ways of skipping in its body

    def scan(self, module: _Module, node: ast.AST) -> _Scan:
        found = self._scans.get(node)
        if found is None:
            found = self._scans[node] = _scan(module, node)
        return found

    def count(
        self, module: _Module, func: FunctionNode, owner: _Class | None = None
    ) -> tuple[int, int, int, int]:
        """(assertions that can fail, assertions written, always-true ones, skips) of a test
        defined in ``module``; ``owner`` is the class that runs it, if any."""
        effective, lexical, constant, skips = self._count(
            _Helper(module, func, owner), 0, frozenset({func})
        )
        if self.scan(module, func).generator and not _is_fixture(func):
            effective = 0  # pytest does not run a test function that yields
        return effective, lexical, constant, skips

    def _count(
        self, scope: _Helper, depth: int, active: frozenset[ast.AST]
    ) -> tuple[int, int, int, int]:
        scan = self.scan(scope.module, scope.func)
        effective, lexical, constant, skips = (
            scan.effective,
            scan.lexical,
            scan.constant,
            scan.skips,
        )
        if depth >= MAX_HELPER_DEPTH:
            return effective, lexical, constant, skips
        for site in scan.sites:
            helper = self._resolve_call(scope, site.node.func)
            if helper is None or helper.func in active:
                continue  # unresolved, or a cycle: counts 0
            self.reached.add(helper.func)
            e, w, c, s = self._count(helper, depth + 1, active | {helper.func})
            lexical, constant, skips = lexical + w, constant + c, skips + s
            if site.live and not (site.discarded and self._is_lazy(helper)):
                effective += e
        return effective, lexical, constant, skips

    def _is_lazy(self, helper: _Helper) -> bool:
        """Calling it only builds a generator or a coroutine: something has to run it."""
        if isinstance(helper.func, ast.AsyncFunctionDef):
            return True
        return self.scan(helper.module, helper.func).generator

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
        """pytest may collect the class: its name starts with ``Test``, or one of its bases
        is out of sight (it may be a ``unittest.TestCase``: when in doubt, yes), or is a
        class of the tests directory that pytest may collect."""
        known = self._collected.get(owner.node)
        if known is not None:
            return known
        self._collected[owner.node] = False  # a class that inherits from itself, in a loop
        answer = owner.node.name.startswith(CLASS_PREFIX)
        for base in owner.node.bases:
            chain = _attr_chain(base)
            if answer or chain == "object":
                continue
            resolved = self._resolve_class(owner.module, chain.split("."))
            answer = resolved is None or self.is_collected(resolved)
        self._collected[owner.node] = answer
        return answer

    def inherited_tests(self, owner: _Class) -> dict[str, tuple[_Module, FunctionNode]]:
        """The tests the class inherits from the classes of the tests directory, the nearest
        definition first. A name a nearer class binds to something else hides them."""
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
                found.setdefault(name, None if current is owner else value)
        return {name: value for name, value in found.items() if value is not None}

    def _origin(
        self, module: _Module, chain: str, depth: int = 0
    ) -> tuple[_Module, ast.AST] | None:
        """Where a dotted name leads inside the tests directory: the module and the function,
        class, lambda or other expression the name stands for there. None when it leaves the
        tests directory (a library, the package under test) or cannot be followed."""
        if depth > 4 or not chain:
            return None
        head, _, rest = chain.partition(".")
        if head in module.defined:
            return module, module.defined[head]
        value = module.aliases.get(head)
        if value is not None:
            called = _callee(value)
            if isinstance(called, ast.Lambda):
                return module, called
            return self._origin(module, _full_chain(called), depth + 1) or (module, value)
        imported = module.imports.get(head)
        if imported is None:
            return None
        parts = [part for part in imported.module.split(".") if part]
        if imported.name is not None:
            parts.append(imported.name)
        parts.extend(part for part in rest.split(".") if part)
        # the longest prefix that is a module of the tests directory, then the name in it:
        # `from hypothesis import given` next to a `tests/hypothesis/` that defines no
        # `given` is still the library
        for cut in range(len(parts) - 1, 0, -1):
            target = self._find_module(module, ".".join(parts[:cut]), imported.level)
            if target is not None:
                return self._origin(target, ".".join(parts[cut:]), depth + 1)
        return None

    def _alias(self, module: _Module, deco: ast.expr) -> tuple[_Module, ast.expr]:
        """The decorator, or the expression a name of the tests stands for
        (``slow = pytest.mark.slow``), with the module whose imports it is read with."""
        origin = self._origin(module, _full_chain(_callee(deco)))
        if origin is None:
            return module, deco
        home, meant = origin
        if not isinstance(meant, ast.expr) or isinstance(meant, ast.Lambda):
            return module, deco
        return home, meant

    def decorators(self, module: _Module, node: FunctionNode | ast.ClassDef) -> tuple[str, ...]:
        """The decorators by name. One written in the tests directory (a function, a class or
        a lambda: what the author of a change can write to replace a test) carries
        ``LOCAL_DECORATOR``; one that comes from anywhere else is named as imported."""
        names: list[str] = []
        for deco in node.decorator_list:
            called = _callee(deco)
            chain = _full_chain(called)
            origin = self._origin(module, chain)
            if isinstance(called, ast.Lambda):
                names.append("<lambda>" + LOCAL_DECORATOR)
            elif not chain:
                names.append("<expression>")
            elif origin is not None and isinstance(origin[1], _NESTED):
                names.append(chain + LOCAL_DECORATOR)
            else:
                names.append(_canonical(module, chain))
        return tuple(names)

    def decorator_skips(self, module: _Module, decorators: Iterable[ast.expr]) -> int:
        total = 0
        for deco in decorators:
            home, meant = self._alias(module, deco)
            total += max(_decorator_skips(module, deco), _decorator_skips(home, meant))
        return total

    def decorator_info(self, module: _Module, func: FunctionNode) -> tuple[int, int | None]:
        """(skip-like markers, parametrize literal case count or None)."""
        cases: int | None = None
        for deco in func.decorator_list:
            _, meant = self._alias(module, deco)
            if not isinstance(meant, ast.Call) or _last(_attr_chain(meant.func)) != "parametrize":
                continue
            named = [keyword.value for keyword in meant.keywords if keyword.arg == "argvalues"]
            values = meant.args[1] if len(meant.args) >= 2 else named[0] if named else None
            n = _literal_cases(values) if isinstance(values, ast.List | ast.Tuple) else None
            if n is not None:
                cases = n if cases is None else cases * n
        return self.decorator_skips(module, func.decorator_list), cases

    def _fixture(self, module: _Module, name: str, owner: _Class | None) -> _Helper | None:
        """The fixture a test of ``module`` gets under that name: of its class, of its module,
        or of the nearest ``conftest.py`` above it."""
        places: list[tuple[_Module, ast.AST | None]] = []
        if owner is not None:
            method = self._resolve_method(owner, name)
            if method is not None:
                places.append((method.module, method.func))
        places.append((module, module.defined.get(name)))
        for directory in _ancestors(posixpath.dirname(module.path)):
            conftest = self.modules.get(posixpath.join(directory, CONFTEST))
            if conftest is not None and conftest is not module:
                places.append((conftest, conftest.defined.get(name)))
        for home, node in places:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_fixture(node):
                return _Helper(home, node)
        return None

    def fixture_skips(self, module: _Module, func: FunctionNode, owner: _Class | None) -> int:
        """The ways of skipping of the fixtures the test uses: those it takes as parameters,
        those ``usefixtures`` names on it, on its class or in the ``pytestmark`` of its module,
        and the ones they use in turn. A fixture skips whoever uses it: the skip is theirs."""
        marks = [self._alias(module, deco)[1] for deco in func.decorator_list]
        bodies = [module.tree.body]
        if owner is not None:
            marks += [self._alias(owner.module, deco)[1] for deco in owner.node.decorator_list]
            bodies.append(owner.node.body)
        for stmt in (stmt for body in bodies for stmt in body):
            mark = _assigned_value(stmt, "pytestmark")
            if mark is not None:
                marks.append(mark)
        wanted = [(module, name) for name in (*_parameters(func), *_usefixtures(marks))]
        seen: set[ast.AST] = set()
        total = 0
        for _ in range(MAX_HELPER_DEPTH):
            following: list[tuple[_Module, str]] = []
            for home, name in wanted:
                fixture = self._fixture(home, name, owner)
                if fixture is None or fixture.func in seen:
                    continue
                seen.add(fixture.func)
                if fixture.func not in self._skips:
                    self._skips[fixture.func] = _skips_in(fixture.module, fixture.func.body)
                total += self._skips[fixture.func]
                following += [(fixture.module, used) for used in _parameters(fixture.func)]
            wanted = following
        return total

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


@dataclass
class _Found:
    """What one module holds: its tests, where they are written, and its classes."""

    tests: list[_Definition] = field(default_factory=list)
    # (qualified name, class, pytest may collect it)
    classes: list[tuple[str, _Class, bool]] = field(default_factory=list)


def _find(module: _Module, resolver: _Resolver) -> _Found:
    """Every ``test*`` function of the module, in the order it is written, and every class."""
    found = _Found()
    # (statements, prefix of the names, class of `self`, under a condition, pytest collects)
    stack: list[tuple[list[ast.stmt], str, _Class | None, bool, bool]] = [
        (module.tree.body, "", None, False, True)
    ]
    while stack:
        body, prefix, owner, conditional, collected = stack.pop()
        for stmt in body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                name = f"{prefix}{stmt.name}"
                if stmt.name.startswith(TEST_PREFIX):
                    found.tests.append(
                        _Definition(name, module, stmt, owner, conditional, collected)
                    )
                else:  # a test in a function is not one pytest collects, but it is one
                    stack.append((stmt.body, f"{name}.", None, conditional, False))
            elif isinstance(stmt, ast.ClassDef):
                inner = _Class(module, stmt)
                inside = collected and resolver.is_collected(inner)
                found.classes.append((f"{prefix}{stmt.name}", inner, inside))
                stack.append((stmt.body, f"{prefix}{stmt.name}.", inner, conditional, inside))
            else:
                under = _inner_statements(stmt)
                if under:
                    stack.append((under, prefix, owner, True, collected))
    found.tests.sort(key=lambda test: (test.func.lineno, test.func.col_offset))
    return found


def _merged(first: TestInfo, second: TestInfo) -> TestInfo:
    """Two definitions under one name (the branches of an ``if``, or one that shadows the
    other): the weaker of each number, so that weakening either is seen."""
    cases = [n for n in (first.param_cases, second.param_cases) if n is not None]
    return TestInfo(
        asserts=min(first.asserts, second.asserts),
        constant_asserts=max(first.constant_asserts, second.constant_asserts),
        skips=max(first.skips, second.skips),
        param_cases=min(cases) if cases else None,
        decorators=second.decorators,
        lexical=min(first.lexical, second.lexical),
        conditional=first.conditional and second.conditional,
        collected=first.collected or second.collected,
    )


def _class_skips(module: _Module, node: ast.ClassDef, resolver: _Resolver) -> int:
    """What skips every test of the class without touching one: its decorators, its
    ``pytestmark`` and its set-up methods."""
    total = resolver.decorator_skips(module, node.decorator_list)
    for stmt in _statements(node.body):
        mark = _assigned_value(stmt, "pytestmark")
        if mark is not None:
            total += _skips_in(module, [mark])
        elif isinstance(stmt, _FUNCTION) and stmt.name in SETUP_METHODS:
            total += _skips_in(module, stmt.body)
    return total


def _collect(
    module: _Module, found: _Found, resolver: _Resolver
) -> tuple[dict[str, TestInfo], dict[str, ClassInfo], int]:
    """(tests, the classes pytest collects, assertions written in the tests) of a module."""
    tests: dict[str, TestInfo] = {}
    classes: dict[str, ClassInfo] = {}
    written = 0

    def info(home: _Module, func: FunctionNode, owner: _Class | None) -> TestInfo:
        effective, lexical, constant, skip_calls = resolver.count(home, func, owner)
        marks, cases = resolver.decorator_info(home, func)
        skips = marks + skip_calls + resolver.fixture_skips(home, func, owner)
        return TestInfo(effective, constant, skips, cases, resolver.decorators(home, func), lexical)

    for test in found.tests:
        new = info(module, test.func, test.owner)
        new.conditional, new.collected = test.conditional, test.collected
        written += new.lexical
        old = tests.get(test.qualname)
        tests[test.qualname] = new if old is None else _merged(old, new)
    for qualname, owner, collected in found.classes:
        if not collected:
            continue
        decorators = resolver.decorators(module, owner.node)
        classes[qualname] = ClassInfo(decorators, _class_skips(module, owner.node, resolver))
        for name, (home, func) in resolver.inherited_tests(owner).items():
            if f"{qualname}.{name}" not in tests:
                tests[f"{qualname}.{name}"] = info(home, func, owner)
    return tests, classes, written


def _residual_asserts(module: _Module, counted: Set[ast.AST]) -> int:
    """Assertions outside the tests that no test reaches through a helper call: module level,
    class bodies, and the functions that are neither a test nor a helper a test counted."""
    total = 0
    stack: list[ast.AST] = [module.tree]
    while stack:
        node = stack.pop()
        if node in counted:
            continue
        total += _assertions(node)
        stack.extend(ast.iter_child_nodes(node))
    return total


def _outside_skips(module: _Module) -> int:
    """Ways of skipping the tests of the file without touching them: what the module does
    when it is imported (``pytestmark``, ``pytest.skip(...)``, ``importorskip``), its
    ``setup_module``/``setup_function`` and the autouse fixtures. A fixture a test has to ask
    for, a helper or a marker kept in a name do not count: a test has to change to meet them."""
    total = 0
    for stmt in _statements(module.tree.body):
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            if stmt.name in SETUP_FUNCTIONS:
                total += _skips_in(module, stmt.body)
        elif not isinstance(stmt, ast.ClassDef):
            mark = _assigned_value(stmt, "pytestmark")
            if mark is not None:
                total += _skips_in(module, [mark])
            else:
                total += sum(_skip_call(module, node) for node in _own_nodes(stmt))
    return total + sum(_skips_in(module, fixture.body) for fixture in module.autouse)


def _scope_flags(
    body: list[ast.stmt], names: Mapping[str, str], classes: Set[str]
) -> Counter[tuple[str, str]]:
    """``del`` and second bindings of the tests of one module or class body.

    ``names``: name in that scope → qualified name, for its tests and collected classes;
    ``classes``: the collected classes whose test attributes can be assigned from here.
    """
    flags: Counter[tuple[str, str]] = Counter()
    bound: Counter[str] = Counter()
    for stmt in _statements(body):
        bound.update(name for name in _bound_names(stmt) if name in names)
        if isinstance(stmt, ast.Delete):
            for target in stmt.targets:
                chain = _full_chain(target)
                if chain in names:
                    flags["del", names[chain]] += 1
                elif "." in chain and _last(chain).startswith(TEST_PREFIX):
                    flags["del", chain] += 1
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                owner, _, name = _full_chain(target).rpartition(".")
                if owner in classes and name.startswith(TEST_PREFIX):
                    flags["bound", f"{owner}.{name}"] += 1
    for name, times in bound.items():
        if times > 1:
            flags["bound", names[name]] += times - 1
    return flags


def _falsy_dunder_test(stmt: ast.AST) -> list[ast.expr]:
    """The targets of an assignment of anything but a true constant (or a dict: the doctest
    table of a module) to ``__test__``."""
    if isinstance(stmt, ast.Assign):
        targets, value = stmt.targets, stmt.value
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        targets, value = [stmt.target], stmt.value
    else:
        return []
    if isinstance(value, ast.Dict) or (isinstance(value, ast.Constant) and value.value):
        return []
    return [
        target
        for target in targets
        if (getattr(target, "id", None) or getattr(target, "attr", None)) == "__test__"
    ]


def _review_flags(module: _Module, found: _Found) -> Counter[tuple[str, str]]:
    """What can keep a test of the file from running without touching its body (ADR-0027):
    (kind, qualified name of the test or class, ``""`` for the module) → times."""
    flags: Counter[tuple[str, str]] = Counter()
    collected = [(qualname, owner) for qualname, owner, inside in found.classes if inside]
    names = {
        stmt.name: stmt.name
        for stmt in module.tree.body
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
        and stmt.name.startswith(TEST_PREFIX)
    }
    top = {qualname for qualname, _ in collected if "." not in qualname}
    flags.update(_scope_flags(module.tree.body, {**names, **{c: c for c in top}}, top))
    for qualname, owner in collected:
        own = {
            stmt.name: f"{qualname}.{stmt.name}"
            for stmt in owner.node.body
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
            and stmt.name.startswith(TEST_PREFIX)
        }
        flags.update(_scope_flags(owner.node.body, own, set()))
    # `__test__ = False`: in the module, in a class, or on a class from anywhere
    scopes = [("", module.tree.body)]
    scopes += [(qualname, owner.node.body) for qualname, owner, _ in found.classes]
    for qualname, body in scopes:
        for stmt in _statements(body):
            flags["untested", qualname] += sum(
                isinstance(target, ast.Name) for target in _falsy_dunder_test(stmt)
            )
    for target in module.untested:
        flags["untested", _full_chain(target.value)] += 1
    # hooks: the functions of any module (one that `pytest_plugins` names is a plugin), and
    # the `collect_ignore` lists that only a conftest.py has
    in_conftest = posixpath.basename(module.path) == CONFTEST
    for stmt in _statements(module.tree.body):
        hooks = _assigned_names(stmt) if in_conftest else []
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            hooks = [stmt.name]
        for hook in hooks:
            flags["hook", hook] += hook in REVIEW_HOOKS
    return +flags  # without the zeros


def analyze(files: Mapping[str, str | None]) -> Inventory:
    """``files``: path → content (``None`` when it is not a readable .py file)."""
    inv = Inventory()
    modules: dict[str, _Module] = {}
    for path, content in files.items():
        inv.files.add(path)
        if content is None or not path.endswith(".py"):
            continue
        text = content.removeprefix("﻿")
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            inv.unparsed[path] = f"{path}: cannot parse ({exc.msg}, line {exc.lineno})"
        except (ValueError, RecursionError, MemoryError) as exc:
            inv.unparsed[path] = f"{path}: cannot parse ({type(exc).__name__})"
        else:
            modules[path] = _index_module(path, tree)
            continue
        inv.unparsed_text[path] = text
    inv.errors.extend(inv.unparsed.values())
    resolver = _Resolver(modules)
    found = {path: _find(module, resolver) for path, module in modules.items()}
    for path, module in modules.items():
        tests, classes, written = _collect(module, found[path], resolver)
        inv.tests.update({f"{path}::{name}": info for name, info in tests.items()})
        inv.classes.update({f"{path}::{name}": info for name, info in classes.items()})
        inv.asserts += written
    counted: set[ast.AST] = {test.func for tests in found.values() for test in tests.tests}
    counted |= resolver.reached  # once every test of every module has been counted
    for path, module in modules.items():
        inv.asserts += _residual_asserts(module, counted)
        inv.outside_skips[path] = _outside_skips(module)
        inv.flags[path] = _review_flags(module, found[path])
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
    repository starts. A base that cannot be read raises: it is not "no tests". A submodule
    is skipped (its files are not this repository's) and a symbolic link is one entry."""
    directories = _directories(tests_dir)
    paths = [*directories, *_outer_conftests(directories)]
    listing = _git(["ls-tree", "-r", "-z", base, "--", *paths], root)
    if listing.returncode != 0:
        why = listing.stderr.strip() or f"git ls-tree exit {listing.returncode}"
        raise SensorError(f"cannot list the tests at the base {base[:12]}: {why}")
    files: dict[str, str | None] = {}
    links: set[str] = set()
    for entry in filter(None, listing.stdout.split("\0")):
        meta, _, path = entry.partition("\t")
        mode = meta.split(" ", 1)[0]
        if mode == GITLINK:
            continue
        content = None
        if mode == SYMLINK:
            links.add(path)
        elif path.endswith(".py"):
            shown = _git(["show", f"{base}:./{path}"], root)
            if shown.returncode != 0:
                why = shown.stderr.strip() or f"git show exit {shown.returncode}"
                raise SensorError(f"cannot read {path} at the base {base[:12]}: {why}")
            content = shown.stdout
        files[path] = content
    inventory = analyze(files)
    inventory.links = links
    return inventory


def _walk(folder: Path) -> Iterator[Path]:
    """The files under a directory, and the directories that are symbolic links (one entry
    each, as in git). Not entered: another repository (a submodule), a virtual environment,
    ``__pycache__``."""
    for current, dirnames, filenames in os.walk(folder):
        here = Path(current)
        kept: list[str] = []
        for name in sorted(dirnames):
            child = here / name
            if child.is_symlink():
                yield child
            elif name not in PRUNED and not any((child / mark).exists() for mark in FOREIGN):
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            yield here / name


def worktree_inventory(root: Path, tests_dir: str | Iterable[str] = TESTS_DIR) -> Inventory:
    directories = _directories(tests_dir)
    found: list[Path] = []
    for directory in directories:
        folder = root / directory
        if folder.is_dir():
            found.extend(_walk(folder))
    found.extend(root / conftest for conftest in _outer_conftests(directories))
    files: dict[str, str | None] = {}
    for path in found:
        rel = path.relative_to(root).as_posix()
        if path.is_file():
            files[rel] = (
                path.read_text(encoding="utf-8-sig", errors="replace")
                if path.suffix == ".py"
                else None
            )
        elif path.is_symlink():
            files[rel] = None
    return analyze(files)


def _added_decorators(key: str, old: tuple[str, ...], new: tuple[str, ...]) -> list[str]:
    added = Counter(new) - Counter(old)
    return [
        f"{key}: decorator added: review (@{name})"
        for name in sorted(added)
        if name.endswith(LOCAL_DECORATOR)
    ]


def _governs(path: str, base: Inventory, anywhere: bool = False) -> bool:
    """True when the ``conftest.py`` (or ``__init__.py``) is above a Python file the base
    already had: in a new directory, or with the first tests of a project, it reaches no
    test that could be weakened. ``anywhere``: a plugin module, which reaches every test."""
    directory = "" if anywhere else posixpath.dirname(path)
    return any(
        other != path
        and other.endswith(".py")
        and posixpath.basename(other) not in LOADED_FIRST
        and _under(other, directory)
        for other in base.files
    )


def _flag_problems(path: str, base: Inventory, current: Inventory, reaches: bool) -> list[str]:
    """The flags of a file that the change adds, where they can weaken a test of the base.
    ``reaches``: the file existed at the base, or pytest loads it before tests that did."""
    before = base.flags.get(path, Counter())
    after = current.flags.get(path, Counter())
    in_conftest = posixpath.basename(path) == CONFTEST
    problems: list[str] = []
    for kind, name in sorted(after):
        if after[kind, name] <= before[kind, name]:
            continue
        if kind == "hook":  # in a conftest.py, or in a module that `pytest_plugins` can load
            if _governs(path, base, anywhere=not in_conftest):
                where = "conftest" if in_conftest else "pytest"
                problems.append(f"{path}: {where} hook added: review ({name})")
            continue
        if not reaches:
            continue
        key = f"{path}::{name}"
        inside = f"{key}." if name else key  # `name` is "" for the module: any test of the file
        had = any(test == key or test.startswith(inside) for test in base.tests)
        if kind == "untested" and had:
            problems.append(f"{path}: __test__ set to a false value")
        elif kind == "del" and (had or key in base.classes):
            problems.append(f"{path}: test removed with del: {name}")
        elif kind == "bound" and (had or key in base.classes):
            problems.append(f"{path}: test name bound again: {name}")
    return problems


def compare(base: Inventory, current: Inventory) -> list[str]:
    problems: list[str] = []
    skipped = "gained skip/skipif/xfail markers or pytest.skip() calls"
    for path in sorted(base.files - current.files):
        problems.append(f"test file deleted: {path}")
    for name in sorted(base.tests.keys() - current.tests.keys()):
        problems.append(f"test deleted or renamed: {name}")
    for name in sorted(base.tests.keys() & current.tests.keys()):
        old, new = base.tests[name], current.tests[name]
        if new.asserts < old.asserts:
            problems.append(f"{name}: asserts dropped from {old.asserts} to {new.asserts}")
        elif new.lexical < old.lexical:
            problems.append(
                f"{name}: asserts dropped from {old.lexical} to {new.lexical}, "
                "counting those that cannot fail"
            )
        if new.constant_asserts > old.constant_asserts:
            problems.append(f"{name}: gained always-true asserts (assert True / tuple)")
        if new.skips > old.skips:
            problems.append(f"{name}: {skipped}")
        if (
            old.param_cases is not None
            and new.param_cases is not None
            and new.param_cases < old.param_cases
        ):
            problems.append(
                f"{name}: parametrize cases dropped from {old.param_cases} to {new.param_cases}"
            )
        if new.conditional and not old.conditional:
            problems.append(f"{name}: moved under a condition: review")
        if old.collected and not new.collected:
            problems.append(f"{name}: no longer where pytest collects it: review")
        problems.extend(_added_decorators(name, old.decorators, new.decorators))
    for name in sorted(base.classes.keys() & current.classes.keys()):
        before, after = base.classes[name], current.classes[name]
        if after.skips > before.skips:
            problems.append(f"{name}: {skipped}")
        problems.extend(_added_decorators(name, before.decorators, after.decorators))
    if current.asserts < base.asserts:
        problems.append(
            f"total asserts in the tests directory dropped from {base.asserts} to {current.asserts}"
        )
    for path in sorted(current.flags.keys() | current.outside_skips.keys()):
        # A new file weakens no test of the base, unless pytest loads it before tests that
        # the base already had.
        first = posixpath.basename(path) in LOADED_FIRST
        reaches = _governs(path, base) if first else path in base.files
        problems.extend(_flag_problems(path, base, current, reaches))
        old_skips, new_skips = base.outside_skips.get(path, 0), current.outside_skips.get(path, 0)
        if reaches and new_skips > old_skips:
            problems.append(
                f"{path}: skip/xfail forms outside the tests grew from {old_skips} to {new_skips}"
            )
    for path, error in current.unparsed.items():
        if path not in base.unparsed:
            problems.append(f"parse error: {error}")
        elif current.unparsed_text.get(path) != base.unparsed_text.get(path):
            problems.append(
                f"parse error: {error}: changed, and this Python cannot parse it at the base "
                "either; run the sensor with the project's Python"
            )
    return problems


def notes(base: Inventory, current: Inventory) -> list[str]:
    """What is worth saying and is not a problem: a file that never parsed and did not
    change (a sample that is broken on purpose)."""
    return [
        f"{error}: it did not parse at the base either"
        for path, error in current.unparsed.items()
        if path in base.unparsed and current.unparsed_text.get(path) == base.unparsed_text.get(path)
    ]


# --------------------------------------------------------------------------- base
def _resolve_base(explicit: str | None, root: Path) -> tuple[str | None, list[str], bool]:
    """(merge-base commit or None, warnings, the base is HEAD and nobody chose it).

    A base named by ``--base``, ``RUMBO_BASE_REF`` or ``GITHUB_BASE_REF`` is somebody's
    choice, HEAD included. ``RUMBO_DEFAULT_BRANCH`` only says where to look.
    """
    warnings: list[str] = []
    candidates: list[tuple[str, str]] = []  # (ref, where it comes from; "" for a default)
    pushed = os.environ.get("RUMBO_BASE_REF")
    for value, source in ((explicit, "explicit base"), (pushed, "RUMBO_BASE_REF")):
        if value and value.strip() and value.strip() != NULL_SHA:
            candidates.append((value.strip(), source))
    for variable in ("GITHUB_BASE_REF", "RUMBO_DEFAULT_BRANCH"):
        branch = (os.environ.get(variable) or "").strip()
        if branch:
            candidates.append((f"origin/{branch}", variable))
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
        unchosen = resolved == head and source not in CHOSEN_BASES
        if unchosen:
            warnings.append(
                f"base {candidate!r} is HEAD itself: nothing to compare "
                "(set origin/main or pass --base)"
            )
        return resolved, warnings, unchosen
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


NO_BASE_HELP = (
    "Pass --base, or set RUMBO_BASE_REF (a commit) or RUMBO_DEFAULT_BRANCH (the default "
    "branch of the repository, when it is neither main nor master). In CI the history has "
    "to be there: `fetch-depth: 0` in actions/checkout."
)
LOST_BASE_HELP = (
    "A base that was named is not in this clone: either the clone is shallow (use "
    "`fetch-depth: 0` in actions/checkout) or the commit is gone after a force push, and "
    "then the change can only be compared in a pull request."
)


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
    # The all-zero SHA is how a CI run says "this ref is new": nothing came before it.
    new_ref = (os.environ.get("RUMBO_BASE_REF") or "").strip() == NULL_SHA
    event = os.environ.get("GITHUB_EVENT_NAME", "")

    base, warnings, unchosen_head = _resolve_base(args.base, root)
    for warning in warnings:
        print(f"WARNING: {warning}")
    lost = any("is not reachable" in warning for warning in warnings)
    if base is None:
        print("WARNING: no base to compare against (no origin/main?).")
        if new_ref:
            print("RUMBO_BASE_REF is all zeros: a new ref, with nothing before it to compare.")
            return 0
        print(LOST_BASE_HELP if lost else NO_BASE_HELP)
        return 2 if args.strict else 0

    directories = tests_directories(root, base, args.tests_dir)
    try:
        before = base_inventory(base, root, directories)
    except SensorError as exc:
        print(f"ERROR: {exc}")
        print("A base that cannot be read is not a base without tests: nothing was compared.")
        return 2
    after = worktree_inventory(root, directories)
    after.files |= {path for path in before.links if os.path.lexists(root / path)}
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
        # The base is HEAD because nothing better was found: only work that is not committed
        # was compared. That is all there is on a new ref and in a run nobody pushed for.
        if unchosen_head and args.strict and not new_ref:
            if event not in UNPUSHED_EVENTS:
                print(
                    "ERROR: nothing to compare: the base found by default is HEAD itself. "
                    "Pass --base or set RUMBO_BASE_REF to the commit to compare with."
                )
                if lost:
                    print(LOST_BASE_HELP)
                return 2
            print(f"NOTE: nothing to compare: a {event} run on the base itself.")
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
