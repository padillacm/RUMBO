<!-- rumbo:template — delete this line when the document is complete. Only the HUMAN removes it; `rumbo gate` checks it. -->
# ADR-0027: What counts as a test that runs
Status: proposed    Date: 2026-11-16

Completes ADR-0007 (what counts as an assertion). ADR-0007 stands: its counted forms and its
helper resolution are unchanged; this ADR says which functions are tests, which assertions can
actually fail, and where a skip is looked for.

## Context
The sensor reads the tests without running them. Until now a test was any `test_*` function found
anywhere in the syntax tree, an assertion was any counted form found anywhere under it, and a skip
was a mark or a `pytest.skip()` call on the test itself. The audit of 2026-11-16 reproduced, each
with a green sensor and a green pytest on broken code:

- **S3** a test that stays in the tree but that pytest never runs: moved under
  `if TYPE_CHECKING:`, followed by `del test_x`, in a class given `__test__ = False`, or under a
  decorator that replaces it;
- **S4** skips placed elsewhere: an autouse fixture or a `pytest_collection_modifyitems` hook in
  `conftest.py`, `pytest_pyfunc_call` returning `True`, a module-level `pytest.skip`,
  `from pytest import skip`, `self.skipTest()`, `raise unittest.SkipTest`,
  `pytest.param(..., marks=pytest.mark.skip)`, a `parametrize` list of `*()`;
- **S6, S10** assertions that keep their count and cannot fail: in a helper turned generator or
  coroutine, under `try/except AssertionError: pass`, under `contextlib.suppress`, in a nested
  function nobody calls;
- **S7** `testAdd`, which pytest collects and the sensor ignored (its prefix was `test_`);
- **S8** tests inherited from a base class: deleting `class TestCalc(Contract)` lost its tests
  unnoticed.

## Drivers (non-functional requirements)
The sensor runs on every pull request of every project: a false alarm costs a human approval, so
each new rule must stay silent on ordinary test code (measured on this repository's own tests).
No import of the tests, no pytest at run time, standard library only. Both sides of the comparison
are analysed with the same rules, so a rule that counts less never reports by itself.

## Options considered
1. **Ask pytest** (`pytest --collect-only` at the base and at the head). The exact answer, but it
   imports the code under test twice, needs the base's environment, and the tree under test
   controls the collection (conftest, plugins, `addopts`). Rejected.
2. **Keep the syntactic sensor and list the gaps as accepted risks.** Rejected: the reproductions
   are one-line edits an agent finds by itself.
3. **Model pytest's defaults syntactically and report what cannot be modelled (chosen).**

## Decision and rationale
**A test** is a function, plain or `async`, whose name starts with `test` (pytest's default
prefix) and that is a direct child of
- a module, or
- a class pytest collects: its name starts with `Test`, or one of its bases (followed through the
  classes of the tests directory) has a name that ends in `TestCase`; a collected class inside a
  collected class counts too.

A collected class also runs the tests it **inherits** from base classes defined or imported
inside the tests directory; each is inventoried under the class that runs it
(`path::TestCalc.test_add`), so a deleted subclass, a removed base or a name the subclass binds
to something else shows as "test deleted or renamed". A `test*` function under an `if`, a `try`,
a `with`, another function or a class pytest does not collect is not a test: at the head it does
not count as present. Its assertions still count in the total of the directory.

**Reported for review when the change adds them** (they are compared per file or per test with
the base, and a file that is new is exempt unless pytest loads it before the others:
`conftest.py`, `__init__.py`):
- `del` of a test or of a collected class, and a second binding of its name (assignment, import,
  definition under a statement, `Class.test_x = ...`), at module or class level;
- `__test__` set to anything but a true constant (a dict is the doctest table and is left alone);
- a decorator added to an existing test or test class that is not a known one: `pytest.mark.*`,
  `pytest.fixture`, `unittest.mock.patch*` / `mock.patch*`, the `unittest` skip decorators,
  `hypothesis.*`, `functools.wraps`, `staticmethod`, `classmethod`, `typing.override`, and a
  short list of third-party decorators that run the test (`KNOWN_DECORATORS` in the code). Names
  are compared after following the module's imports, and a name defined in the tests directory
  never passes for a known one. Worded "decorator added: review";
- in any `conftest.py`: a `pytest_*` function, `collect_ignore`, `collect_ignore_glob`,
  `pytest_plugins`, or an autouse fixture. Worded "conftest hook added: review".

**Skips.** Per test, as before, now also: the `unittest` decorators (`skipIf`, `skipUnless`,
`expectedFailure`), skip marks inside the decorators (`pytest.param(..., marks=...)`),
`pytest.importorskip`, `pytest.exit`, `self.skipTest(...)`, `raise SkipTest`, and `skip`/`xfail`
under whatever name the module imported them. Per file, the same forms everywhere outside the
tests (module level, `pytestmark`, fixtures, hooks, `setUp`, class decorators): a file whose count
grows is a problem. A new test, and a new file, may carry their own skips, as before.
A `parametrize` literal counts the cases it really has: `*()` adds none.

**Assertions that cannot run or cannot fail count 0**: under a `try` with a handler for
`AssertionError`, `Exception`, `BaseException` or everything that neither raises nor fails the
test; under `with suppress(...)` of one of those; in a nested function, lambda or class that the
test does not call by name (a nested function it does call counts like a helper, and sees its
siblings); in a helper that is a generator, unless it is a `contextmanager` entered by `with`; in
an `async` helper that is neither awaited nor handed to `asyncio.run`/`run_until_complete`; in a
test that yields. What is not counted for a test still counts in the total, except what a handler
swallows and the body of a test that yields.

**The base** (S5). `--strict` no longer passes when there is nothing to compare: a base found
among the defaults that is `HEAD` itself exits 2, unless `--base`, `RUMBO_BASE_REF` or
`GITHUB_BASE_REF` named it, or `RUMBO_BASE_REF` is the all-zero SHA (a new branch or tag: nothing
came before it). The upstream of the branch is no longer a default base. An unreachable
`RUMBO_BASE_REF` earns a warning.

**The approval** (S9, S12). The log is read with NUL separators, so a commit message cannot hold
the record of another commit. The pull-request label counts only in a `pull_request*` run of
GitHub Actions (`GITHUB_ACTIONS=true`).

## Consequences (+/−)
+ Every reproduction of S1 to S12 turns the sensor red; none of the new rules reports on this
  repository's tests, whose inventory is the same under the old rules and the new ones.
+ `unittest`-style suites (`testAdd`, `CalcTest(TestCase)`) are protected like pytest ones.
− More messages that say "review": a new autouse fixture or hook in `conftest.py`, a custom
  decorator on an existing test, a fixture that starts to skip. Each costs one approval.
− A project that changes pytest's prefixes (`python_functions`, `python_classes`) has its tests
  outside `Test*`/`*TestCase` classes protected by the total only.
− Counts differ from v0.4.0 (a nested function counts per call, an uncalled one counts for the
  total only). Both sides use the same rules, so the first run after the update reports nothing.
− A generator helper consumed by a `for`, and an `async` helper started by another runner
  (`anyio`, `trio`), count 0: turning a helper into one of those reports a drop.
− **Not closed inside the sensor: the push after the push.** On a `push` event the base is the
  commit before the push; if a weakening reached the default branch with a red sensor, the next
  push compares with the weakened commit and is green. The sensor has one base per run; the fix
  belongs to the workflow (compare pushes to the default branch with the last green commit) or to
  the repository (protected default branch, changes through pull requests).
− **Out of scope, inherent to reading syntax (S13)**: an empty helper named `assert_ok` that the
  test stops calling for real, assertions under a condition that is false at run time
  (`if SLOW:`), mock assertions on a mock nobody uses, a widened `pytest.raises(Exception)`, an
  early `return`, dynamic tricks (`globals()`, `setattr`, `delattr`). They stay accepted risks in
  the threat model; mutation testing is the control that sees them.

## Fitness function
In `tests/test_sensor.py`:
`test_a_test_moved_where_pytest_does_not_collect_it_is_reported_as_deleted`,
`test_unbinding_a_test_after_its_definition_is_reported`,
`test_a_decorator_added_to_an_existing_test_is_reported_for_review`,
`test_a_known_decorator_added_to_an_existing_test_is_accepted`,
`test_the_prefix_of_a_test_is_test_as_in_pytest_with_or_without_the_underscore`,
`test_tests_inherited_from_a_base_class_are_inventoried_for_each_collected_class`,
`test_every_way_of_skipping_from_inside_a_test_is_counted`,
`test_a_skip_outside_the_tests_is_counted_for_its_file`,
`test_a_new_test_or_a_new_file_may_carry_its_own_skips`,
`test_a_hook_added_to_a_conftest_is_reported_for_review`,
`test_an_assert_that_cannot_fail_counts_zero`,
`test_a_helper_whose_body_never_runs_counts_zero`,
`test_asserts_in_a_nested_function_count_only_when_the_test_calls_it`,
`test_strict_refuses_a_default_base_that_is_head_itself`,
`test_a_commit_message_cannot_forge_the_commit_of_a_human`.

## Reversibility: high
