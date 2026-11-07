# ADR-0007: The sensor counts idiomatic assertions and resolves helpers inside the tests directory
Status: accepted    Date: 2026-11-07

## Context
`scripts/check_tests_not_weakened.py` compares the test tree at the merge base with the working
tree and fails when a test loses `assert` statements. It only counts `ast.Assert`. Two things go
wrong in idiomatic Python: deleting `with pytest.raises(ValueError):` is invisible, and extracting
four repeated asserts into a clean `assert_valid(x)` helper is punished as weakening. False
positives train the human to sign `[tests-change-approved]` without reading (the
wemake-python-styleguide lesson); false negatives are the cheating genie's door. The architecture
test of the generated project has the same dogmatism problem: it forbids importing `pathlib` and
`io` at all in `domain/`, which blocks pure in-memory types, while never noticing the builtin
`open(...)`, which does disk I/O with no import.

## Drivers (non-functional requirements)
No new evasion: a rule that counts a construct must make it impossible to satisfy with a hollow
construct; refactoring tests without changing what they verify must pass; standard library only,
static analysis only (the sensor runs on both snapshots without importing test code); the tests
directory configurable for brownfield layouts.

## Options considered
1. Count more syntactic forms, including any call named `assert_*` (cheap; `def assert_ok(x):
   pass` defeats it).
2. Count the standard forms syntactically and resolve user helpers statically within the tests
   directory, counting a helper call as the assertions in its body.
3. Only require that no test drops to zero assertions (lets 4 → 1 through).
4. Status quo plus mutation testing (mutmut needs `os.fork`: no Windows; and the false positives
   remain).

## Decision and rationale
Option 2. The precedent is pytest itself: it rewrites `assert` only in test modules, `conftest.py`
and modules registered with `register_assert_rewrite`, so "helper inside the tests tree" is
already a first-class notion. The rules:

**Counted syntactically**, one each, inside a test function (nested defs included, as today):
1. `assert` statements (constant asserts still flagged).
2. `with pytest.raises(...)`, `pytest.warns(...)`, `pytest.deprecated_call(...)`: one per
   `withitem`.
3. On `self`: any method whose name starts with `assert`, and `self.fail(...)`.
4. Mock assertions by exact attribute name on any receiver: `assert_called`,
   `assert_called_once`, `assert_called_with`, `assert_called_once_with`, `assert_any_call`,
   `assert_has_calls`, `assert_not_called`, `assert_awaited`, `assert_awaited_once`,
   `assert_awaited_with`, `assert_awaited_once_with`, `assert_any_await`, `assert_has_awaits`,
   `assert_not_awaited`.
5. `pytest.fail(...)`.

**Counted by resolution.** A call to a bare name, or to `module.name`, resolves to a
`FunctionDef` when that function is defined in the same file, in a `conftest.py` of the same
directory or an ancestor inside the tests directory, or in a module imported from inside the
tests directory (`from tests.helpers import x`, `from .helpers import x`, `from helpers import x`,
`import helpers`). A resolved call counts as the assertions of the helper's body under these same
rules, recursively, depth at most 3, a cycle counting 0. Unresolvable calls count 0. Resolution
runs on each snapshot independently, so both sides of the comparison follow one rule.

**Unchanged.** Skip/xfail detection, parametrize shrinkage, deleted tests and files, the
per-file total, the human approval mark and its agent-authorship check.

**Tests directory.** Read from `[tool.rumbo].tests` in `pyproject.toml` (default `tests`), with
`--tests-dir` overriding on the command line.

**Purity of `domain/`** (the generated `tests/architecture/test_architecture.py`): `import pathlib`
and `import io` stay forbidden, but `from pathlib import PurePath, PurePosixPath,
PureWindowsPath` and `from io import StringIO, BytesIO` are allowed by exact name; and any call to
the builtin `open` that is not shadowed by a local definition or import named `open` is a
violation.

## Consequences (+/−)
+ Extracting or inlining assertion helpers no longer triggers the sensor; removing a
  `pytest.raises` block does.
+ Hollowing a helper lowers every caller at once, which the sensor reports.
+ In-memory pure types are usable in the domain; disk I/O through `open` is caught.
− Resolution is static: helpers built dynamically or imported from outside the tests tree count 0.
− The sensor grows by a resolver (bounded depth, cycle guard) that has to stay stdlib-only.

## Fitness function
`tests/test_sensor.py`: each counted form, helper extraction accepted, hollowed helper rejected,
deleted `pytest.raises` rejected, cycle and depth bounds, `[tool.rumbo].tests`;
`tests/test_scaffold.py`: the generated architecture test accepts `PurePath`/`StringIO` and rejects
`open(` and `import pathlib` in `domain/`.

## Reversibility: high
The syntactic list and the resolver are two functions; removing them returns to `ast.Assert` only.
