# ADR-0027: What counts as a test that runs
Status: accepted    Date: 2026-11-21

Completes ADR-0007 (what counts as an assertion). ADR-0007 stands: its counted forms and its
helper resolution are unchanged; this ADR says which functions are inventoried, which changes of
place are reported, which assertions plainly cannot fail, and where a skip is looked for.

## Context
The sensor reads the tests without running them. In v0.4.0 a test was any `test_*` function found
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

A first version of this decision modelled pytest's collection strictly (a test is a direct child
of a module or of a `Test*`/`*TestCase` class) and counted 0 whatever the test did not call by
name. A second audit (2026-11-21) measured what that cost. Tests that pytest runs dropped out of
the inventory: a class whose base lives in the package under test (`class LoginTests(BaseTest)`),
and every test under `if sys.platform...` or in the `else` of a `try`; replacing their asserts
with `assert True` passed. Asserts in a callback, a thread target, a `side_effect` or a retry loop
counted 0, so emptying them passed. And 38 of 157 honest changes were reported by the new rules
alone, among them one of this repository's own commits. A sensor that reports honest work is
approved without being read.

## Drivers (non-functional requirements)
The sensor runs on every pull request of every project. Two failures cost the same: a weakening
it does not see, and an honest change it reports (each one trains the human to approve without
reading). Whatever v0.4.0 reported is still reported: a new rule may add a report, never remove
one. Each new rule is as narrow as the attack it was written for. No import of the tests, no
pytest at run time, standard library only. Both sides are analysed with the same rules.

## Options considered
1. **Ask pytest** (`pytest --collect-only` at the base and at the head). The exact answer, but it
   imports the code under test twice, needs the base's environment, and the tree under test
   controls the collection (conftest, plugins, `addopts`). Rejected.
2. **Model pytest's collection and inventory only what it collects.** The first version. Rejected
   after the second audit: the model cannot see outside the tests directory (a base class, a
   condition that holds at run time), and whatever it leaves out can be emptied unnoticed.
3. **Inventory everything, as before; report the moves and the plain gags on top (chosen).**

## Decision and rationale
**What is inventoried.** Every function, plain or `async`, whose name starts with `test` (pytest's
default prefix), wherever it is written: at module level, in any class, under an `if`, a `try`, a
`with`, inside another function. Its key is the file and its qualified name, as in v0.4.0. When
in doubt a test is inventoried. A class also runs the tests it **inherits** from base classes
defined or imported inside the tests directory: each is inventoried under the class that runs it
(`path::TestCalc.test_add`), so a deleted subclass, a removed base or a name the subclass binds to
something else shows as "test deleted or renamed". Two definitions under one name (the branches
of an `if`) are compared by the weaker of each number.

Each test carries two facts about its place:
- **conditional**: it is under a statement of its module or class (`if`, `try`, `with`, `for`...);
- **collected**: it is at module level, or in a class pytest may collect: its name starts with
  `Test`, or one of its bases is out of the sensor's sight (it may be a `unittest.TestCase`), or
  is a class of the tests directory that may be collected. A test inside a function is not.

**Reported when a test of the base changes place** (a test that keeps its place is compared and
nothing else):
- unconditional at the base, conditional at the head: "moved under a condition: review";
- collected at the base, not at the head: "no longer where pytest collects it: review";
- moved into a function or another class: its qualified name changes, "test deleted or renamed".

**Reported when the change adds it and the base had tests there:**
- `del` of a test or of a collected class, and a second binding of its name (assignment, import,
  definition under a statement, `Class.test_x = ...`), at module or class level;
- `__test__` set to anything but a true constant, in a module or on a class that had tests at the
  base (on a helper class without tests it is how pytest is told to leave it alone; a dict is the
  doctest table);
- a decorator **written in the tests directory** (a function, a class or a lambda, also through a
  name that stands for one) added to an existing test or test class: "decorator added: review".
  It is what the author of a change can write to replace a test. A decorator that comes from a
  library or from the package under test is not reported; a name that stands for a pytest mark
  (`slow = pytest.mark.slow`) is the mark; an import is followed into the tests directory only
  when the module found there defines the name (`from hypothesis import given` next to a
  `tests/hypothesis/` is still the library);
- in a `conftest.py` above at least one Python file the base already had: a hook that decides
  which tests run or how they end (`REVIEW_HOOKS` in the code: `pytest_collection_modifyitems`,
  `pytest_ignore_collect`, `pytest_collect_file`, `pytest_pycollect_makeitem`,
  `pytest_itemcollected`, `pytest_deselected`, `pytest_generate_tests`, the `pytest_runtest_*`
  hooks, `pytest_pyfunc_call`, `pytest_report_teststatus`, `pytest_sessionfinish`,
  `pytest_exception_interact`, `collect_ignore`, `collect_ignore_glob`): "conftest hook added:
  review". `pytest_addoption`, `pytest_configure`, `pytest_plugins` and an autouse fixture that
  does not skip are not reported. The same hook functions in any other module of the tests
  directory (a plugin that `pytest_plugins` can load) are reported as "pytest hook added:
  review" when the base had tests.

**Skips.** Per test, as before, now also: the `unittest` decorators (`skipIf`, `skipUnless`,
`expectedFailure`), skip marks inside the decorators (`pytest.param(..., marks=...)`) or behind a
name of the tests (`later = pytest.mark.skip`), `pytest.importorskip`, `pytest.exit`,
`self.skipTest(...)`, `raise SkipTest`, and `skip`/`xfail` under whatever name the module imported
them. A fixture skips whoever uses it, so its skips count for the tests that use it: by parameter,
by `usefixtures` on the test, on its class or in the `pytestmark` of its module, and through the
fixtures it uses in turn (the fixture of the test's class, of its module, or of the nearest
`conftest.py`). Making an existing fixture skip, or shadowing it with one that does, is reported
for each test of the base that uses it; a new fixture for a new test is not.
Per test class that exists on both sides: its decorators, its `pytestmark`, its set-up
methods (`setUp`, `setUpClass`, `asyncSetUp`, `setup_method`, `setup_class`). Per file, only what
skips the tests of the base without touching them: the statements of the module (`pytestmark`,
`pytest.skip(...)`, `importorskip`, `raise SkipTest`), `setup_module`/`setup_function`, and
autouse fixtures. A helper and a marker kept in a name do not count for the file: an existing
test has to change to meet them, and that change is judged where it happens.
A new test, a new class and a new file carry their own skips freely; a new `conftest.py` or
`__init__.py` does not when it is above a Python file the base already had. A `parametrize`
literal counts the cases it really has: `*()` adds none.

**Assertions: two counts per test, and a drop in either is reported.**
- *Written*: every counted form in the test, its nested functions included, plus the helpers it
  calls (ADR-0007). It is v0.4.0's count, kept as it was: on this repository's tests (636 on the
  day of this decision) it gives the same number as v0.4.0 for every test and the same total
  (3165).
- *Able to fail*: the same without the assertions that plainly cannot run or cannot fail:
  - in a nested function, class or named lambda that the test never mentions again (not called,
    not passed on, not assigned, not returned, not used as a decorator, and with no decorator
    of its own to receive it). A callback, a thread target, a `side_effect`, the `main` handed
    to `trio.run`, a handler under `@app.route(...)` are in use: they count;
  - under a `try` with a handler for `AssertionError`, `Exception`, `BaseException` or everything
    whose body does nothing: `pass`, `...`, a string, or `continue` when no assertion follows the
    `try`. A handler that sleeps and retries, records, reports, raises, or is followed by a final
    assertion keeps counting;
  - under `with suppress(...)` of one of those classes;
  - in a helper that is a generator or a coroutine, when its call is a statement by itself:
    nothing can iterate or await a value that was thrown away. Any other use counts;
  - in a test that yields (pytest does not run it), unless it is a fixture whose name starts
    with `test` (`test_client`).

A drop in the second number is reported as "asserts dropped from A to B"; a drop in the first
alone (removing assertions that already could not fail) as the same line with ", counting those
that cannot fail". The total of the directory is the written one.

**The base** (S5). `--strict` no longer passes when there was something to compare and it was not
compared: a base found by default that is `HEAD` itself exits 2 when the working tree holds no
problem of its own (uncommitted work is still compared and reported). It exits 0, with a note,
where there is nothing to compare by design: `RUMBO_BASE_REF` all zeros (a new branch or tag),
and a `workflow_dispatch` or `schedule` run whose base is `HEAD`. A base named by `--base`,
`RUMBO_BASE_REF` or `GITHUB_BASE_REF` is somebody's choice, `HEAD` included, and is compared
without more ado. `RUMBO_DEFAULT_BRANCH` is a candidate
(`origin/<value>`) after `GITHUB_BASE_REF`, for repositories whose default branch is neither
`main` nor `master`; with no base at all, an all-zero `RUMBO_BASE_REF` exits 0 with a warning.
A base that is not in the clone (a force push, a shallow clone) stays exit 2 and says what to do.
The upstream of the branch is not a default base. An unreachable candidate earns a warning.

**The approval** (S9, S12). The log is read with NUL separators, so a commit message cannot hold
the record of another commit. The pull-request label counts only in a `pull_request*` run of
GitHub Actions (`GITHUB_ACTIONS=true`).

**What is read.** A file that this Python cannot parse on either side is a note while it is
unchanged and a problem once it changes (run the sensor with the project's Python). A submodule
under the tests directory is skipped on both sides and a symbolic link is one entry, as in git.

## Consequences (+/−)
Measured on 2026-11-21 with the second audit's drivers (throw-away repositories, v0.4.0's sensor
against this one):
+ Weakenings: 80 scenarios; none that v0.4.0 reported is missed (24 were by the first version);
  2 more are reported (`self.skipTest`, `parametrize(argvalues=...)`). The reproductions of the
  first audit are still reported, except the two accepted below (a false run-time condition,
  the push after the push).
+ Honest changes: 157 scenarios; 3 are reported by the new rules alone (38 by the first
  version): a decorator written in the tests directory added to an existing test, a module-level
  `importorskip` added to an existing file, an existing test moved under a version `if`. Each is
  a rule stated above and costs one approval. 29 others were already reported by v0.4.0
  (renames, moves between files, asserts replaced by one helper of a library) and 4 that v0.4.0
  reported no longer are (a byte-order mark, an unchanged file that does not parse).
+ This repository: the 73 commits of its history that touch `tests/` get the same verdict from
  v0.4.0 and from this sensor (70 pass, 3 are reported by both); the first version reported one
  more, 17f4abf8, for a fixture of new tests that skips without git.
+ Time: a generated suite of 2,000 files and 20,000 tests takes 63.3 s with v0.4.0 and 64.9 s
  with this sensor on the same machine (one `git show` per file is most of both).
− In CI, measured on 65 situations: 11 end differently from v0.4.0. A force push to the default
  branch and a shallow clone exit 2 (they passed without comparing); a manual run in a
  repository whose default branch is neither `main` nor `master` exits 2 until the workflow
  sets `RUMBO_DEFAULT_BRANCH`; a tag pushed in such a repository passes (it was 2); `--strict`
  on a clean default branch with no base exits 2 (three local cases); and four approvals are
  refused because the base's `agent_authors` holds (ADR-0026).
− A handler that does anything before swallowing the failure (`except AssertionError:
  log(...)`), a nested function mentioned once and never run, and a generator helper whose result
  is kept and never iterated keep their count. The rules are for the plain cases; the rest is
  S13.
− Counts are the written ones again: a nested function counts once, however many times the test
  calls it.
− A decorator from the package under test that replaces the test is not reported (the first
  version reported every decorator it did not know, `parameterized.expand` and `moto.mock_aws`
  among them).
− A project that changes pytest's prefixes (`python_functions`) has the tests under other names
  protected by the total only.
− On a default branch after a pull, `--strict` without `--base` exits 2 when nothing is
  uncommitted: whoever runs it (gate G4) passes the base.
− **Not closed inside the sensor: the push after the push.** On a `push` event the base is the
  commit before the push; if a weakening reached the default branch with a red sensor, the next
  push compares with the weakened commit and is green. The fix belongs to the workflow (compare
  pushes to the default branch with the last green commit) or to the repository (protected
  default branch, changes through pull requests).
− **The push run after an approved pull request can be red.** The label lives on the pull
  request, not on the push that merges it; and a squash commit that carries an agent's
  `Co-authored-by:` trailer next to the mark is an agent's commit. The pull request was green;
  the run on the default branch reports the weakening again. Unchanged from v0.4.0.
− **Out of scope, inherent to reading syntax (S13)**: an empty helper named `assert_ok`,
  assertions under a condition that is false at run time (`if SLOW:`), mock assertions on a mock
  nobody uses, a widened `pytest.raises(Exception)`, an early `return`, dynamic tricks
  (`globals()`, `setattr`, `delattr`). They stay accepted risks in the threat model; mutation
  testing is the control that sees them.

## Fitness function
In `tests/test_sensor.py`:
`test_a_test_moved_where_pytest_does_not_collect_it_is_reported_as_deleted`,
`test_a_test_under_a_condition_is_inventoried_and_compared_like_any_other`,
`test_a_class_whose_base_comes_from_outside_the_tests_is_inventoried`,
`test_unbinding_a_test_after_its_definition_is_reported`,
`test_dunder_test_and_del_are_reported_only_where_the_base_had_tests`,
`test_a_decorator_added_to_an_existing_test_is_reported_for_review`,
`test_a_known_decorator_added_to_an_existing_test_is_accepted`,
`test_the_prefix_of_a_test_is_test_as_in_pytest_with_or_without_the_underscore`,
`test_tests_inherited_from_a_base_class_are_inventoried_for_each_collected_class`,
`test_every_way_of_skipping_from_inside_a_test_is_counted`,
`test_a_skip_outside_the_tests_is_counted_for_its_file`,
`test_a_skip_that_no_existing_test_meets_without_changing_is_not_reported`,
`test_a_skip_added_to_an_existing_test_class_is_reported_for_the_class`,
`test_a_fixture_that_skips_is_counted_for_the_tests_that_use_it`,
`test_a_mark_kept_in_a_name_of_the_tests_counts_as_the_mark_it_stands_for`,
`test_a_hook_added_to_a_conftest_is_reported_for_review`,
`test_a_hook_in_a_plugin_module_of_the_tests_is_reported_like_one_in_a_conftest`,
`test_an_assert_that_cannot_fail_counts_zero`,
`test_an_assert_that_can_still_fail_keeps_counting`,
`test_a_helper_whose_body_never_runs_counts_zero`,
`test_asserts_in_a_function_the_test_hands_over_keep_counting`,
`test_strict_refuses_a_default_base_that_is_head_itself`,
`test_a_new_ref_with_no_base_at_all_passes_with_a_warning`,
`test_a_run_nobody_pushed_for_has_nothing_to_compare_and_passes`,
`test_a_commit_message_cannot_forge_the_commit_of_a_human`.

## Reversibility: high
