<!-- rumbo:template — delete this line when the document is complete. Only the HUMAN removes it; `rumbo gate` checks it. -->
# F-0003: Tests cannot be weakened unnoticed
Story: As a developer who reviews the work of a coding agent I want every change that leaves the
tests weaker than the base to fail the check until I approve it myself, so that the agent cannot
reach green by deleting, skipping or hollowing tests.

Scope: `scripts/check_tests_not_weakened.py` (canonical source
`src/rumbo/sensors/check_tests_not_weakened.py`), the anti-cheat sensor. It runs as the `sensor`
step of the verification runner, in gate G4 and in CI with `--strict`, and as
`rumbo check-tests` where the CLI is installed.
Decisions: ADR-0007 (what counts as an assertion, helper resolution, the tests directory),
ADR-0008 and ADR-0021 (who counts as an agent when the approval mark is read).
Written after the code: it describes the sensor as it is on 2026-11-09, not a plan.

## Requirements (EARS)
<!-- Ubiquitous: The system shall… · Event: When…, the system shall… · State: While…, the system shall…
     Unwanted: If…, then the system shall… · Optional: Where…, the system shall… -->
- R1 (ubiquitous): The sensor shall compare the files under the tests directory at the base commit, read from git, with the files under it in the working tree, read from disk whether committed or not, parsing the Python files without importing them; the tests directory is `--tests-dir` when given, else `[tool.rumbo].tests` of `pyproject.toml`, else `tests`.
- R2 (ubiquitous): The sensor shall take as base the merge base of `HEAD` with the first of these that resolves to a commit: `--base`, `RUMBO_BASE_REF` (an all-zero SHA is skipped), `origin/` followed by `GITHUB_BASE_REF`, `origin/main`, `origin/master`, `origin/HEAD`, the upstream of the branch, `main`, `master`; it shall print a warning when `--base` does not resolve, and go on to the next ones, and when the base found through a ref with one of those six default names is `HEAD` itself.
- R3 (unwanted): If none of those resolves to a commit, then the sensor shall print a warning and exit 0, or exit 2 where `--strict` is given.
- R4 (unwanted): If a file that is under the tests directory at the base is missing from the working tree, a `test_*` function of the base, identified by its file path and qualified name, is missing from it, or a Python file of the working tree cannot be parsed, then the sensor shall report each as a problem; a file or a test that was added is not one.
- R5 (unwanted): If a test has fewer assertions than at the base, or the tests directory as a whole has fewer (the assertions of every test plus those outside the tests that no test reaches through a helper), then the sensor shall report it with both counts.
- R6 (unwanted): If a test has more always-true assertions than at the base, an `assert` on a truthy constant or on a non-empty tuple, then the sensor shall report it.
- R7 (unwanted): If a test has more skip marks than at the base, counting each decorator whose last name is `skip`, `skipif` or `xfail` and each call to `pytest.skip` or `pytest.xfail` in its body or in a helper it resolves (R10), then the sensor shall report it.
- R8 (unwanted): If the number of literal `parametrize` cases of a test, the product of the lengths of the list or tuple literals passed as second positional argument to its `parametrize` decorators, is lower than at the base, then the sensor shall report it with both counts.
- R9 (ubiquitous): The sensor shall count as one assertion each `assert` statement, each `pytest.raises`, `pytest.warns` or `pytest.deprecated_call` item of a `with`, each call to `self.fail`, to `pytest.fail` or to a method of `self` whose name starts with `assert`, and each call on any receiver to one of the fourteen `unittest.mock` assertion methods by exact name (`MOCK_ASSERT_NAMES`); any other statement or call that R10 does not resolve counts zero.
- R10 (ubiquitous): The sensor shall count a call to a helper as the assertions of the helper's body under the same rules when the helper is a function defined in the same file, in a `conftest.py` of the same directory or of an ancestor, or in a module imported from inside the tests directory, or a method called as `self.name()`, `cls.name()` or `Class.name()` that belongs to the test's class or to a base class defined or imported inside the tests directory; it shall follow at most three helper calls from the test, and a call back into a helper already being counted counts zero.
- R11 (ubiquitous): The sensor shall print every problem it found and exit 0 when there is none, 1 when there is at least one and no human approved the change, and 2 on a usage error.
- R12 (event): When problems are found and a human approved the change, by a commit in `base..HEAD` whose message contains `[tests-change-approved]` or by the label `tests-change-approved` on the pull request described in the file `GITHUB_EVENT_PATH` names, the sensor shall still list the problems, name the approval and exit 0.
- R13 (unwanted): If an agent took part in the commit that carries the mark, because its author, its committer or the value of a `Co-authored-by:` trailer contains, whatever the case, an identity of `DEFAULT_AGENT_AUTHORS` or of `[tool.rumbo].agent_authors`, or its message contains one of `DEFAULT_AGENT_TRAILERS`, then the sensor shall ignore that mark and say which commit it ignored.
- R14 (ubiquitous): The sensor shall import the standard library only, be copied unchanged into every generated project, and be reachable as `rumbo check-tests`, which passes its arguments on and returns the sensor's exit code.

## Acceptance criteria
<!-- pytest tests / Hypothesis properties that verify them (path::test). A test covers a requirement
     by mentioning its full id, e.g. `F-000N.R1`, in its docstring; `rumbo trace` builds the table. -->
All in `tests/test_sensor.py`; each test carries its requirement id in a comment.

| Requirement | Tests |
|---|---|
| R1 | `test_an_uncommitted_weakening_is_reported_and_the_exit_code_tells`, `test_tests_directory_defaults_then_pyproject_then_flag`, `test_configured_tests_directory_is_honoured_end_to_end` |
| R2 | `test_the_base_is_the_merge_base_with_the_first_candidate_that_resolves` |
| R3 | `test_without_a_base_the_sensor_warns_and_only_strict_fails` |
| R4 | `test_a_deleted_file_and_a_deleted_or_renamed_test_are_reported`, `test_a_test_file_that_no_longer_parses_is_reported` |
| R5 | `test_each_counted_form_is_seen_when_removed`, `test_deleting_a_pytest_raises_block_is_reported`, `test_unreached_helpers_and_module_level_asserts_still_count_in_the_total`, `test_a_drop_in_the_total_alone_is_reported` |
| R6 | `test_constant_asserts_are_still_flagged`, `test_truthy_constants_and_tuples_are_always_true_asserts` |
| R7 | `test_a_skip_or_xfail_marker_on_a_test_is_reported`, `test_a_skip_or_xfail_call_in_a_test_is_reported`, `test_skip_inside_a_resolved_helper_counts_as_a_skip` |
| R8 | `test_a_shorter_parametrize_list_is_reported` |
| R9 | `test_each_counted_form_is_seen_when_removed`, `test_self_assert_methods_and_fail_are_counted`, `test_pytest_contexts_count_only_with_the_pytest_prefix`, `test_a_self_assert_method_still_counts_one_and_is_not_resolved`, `test_unresolvable_calls_count_zero` |
| R10 | `test_extracting_asserts_into_a_helper_is_accepted`, `test_hollowing_a_helper_is_reported_for_its_callers`, `test_helper_with_pytest_raises_counts_under_the_same_rules`, `test_helper_cycle_terminates_and_counts_zero`, `test_resolution_depth_is_bounded_at_three`, `test_unresolvable_calls_count_zero`, `test_helper_methods_are_resolved_on_self_cls_and_the_class_name`, `test_a_self_call_resolves_only_inside_a_class_and_only_to_its_methods`, `test_helper_methods_keep_the_cycle_guard_and_the_depth_limit`, `test_inherited_helper_methods_follow_the_bases_inside_the_tests_directory`, `test_moving_asserts_between_a_test_method_and_its_helper_method_is_accepted` |
| R11 | `test_an_uncommitted_weakening_is_reported_and_the_exit_code_tells`, `test_without_a_base_the_sensor_warns_and_only_strict_fails`, `test_the_mark_approves_in_a_commit_of_the_human_and_not_in_one_an_agent_co_authored` |
| R12 | `test_the_mark_approves_in_a_commit_of_the_human_and_not_in_one_an_agent_co_authored`, `test_the_mark_counts_only_in_a_commit_between_the_base_and_head`, `test_the_pull_request_label_approves_the_change` |
| R13 | `test_the_mark_approves_in_a_commit_of_the_human_and_not_in_one_an_agent_co_authored`, `test_an_approval_an_agent_took_part_in_is_ignored`, `test_the_project_adds_agent_identities_in_pyproject` |
| R14 | `test_the_sensor_imports_only_the_standard_library`, `test_the_generated_project_gets_the_sensor_verbatim`, `test_rumbo_check_tests_passes_its_arguments_to_the_sensor`, `test_sensor_tests_pass_in_generated_project` |

The sensor also ships its own tests into every project
(`tests/unit/test_check_tests_not_weakened.py`, the copy of
`src/rumbo/templates/tests/unit/test_check_tests_not_weakened.py`). They carry no requirement
id, because the file is the same in every project; `test_sensor_tests_pass_in_generated_project`
runs them in a generated project.

## Data contracts and ownership
<!-- What two sessions (or two agents) must agree on BEFORE writing code. Omitting explicit data
     structures is what breaks parallel work (docs/method.md, section 3.1). -->
- Types / schemas: `TestInfo(asserts: int, constant_asserts: int, skips: int, param_cases: int | None)`, where `param_cases` is `None` when no `parametrize` decorator has a literal list; `Inventory(files: set[str], tests: dict[str, TestInfo], asserts: int, errors: list[str])`, whose `tests` keys are `path::Qualified.name` with POSIX paths relative to the repository root; `[tool.rumbo].tests: str` and `[tool.rumbo].agent_authors: list[str]` in `pyproject.toml` (a value of another type is ignored, as are blank entries and entries that are not strings); the GitHub event file, a JSON object read only at `pull_request.labels[].name`.
- Signatures: `main(argv: list[str] | None) -> int` with the options `--base REF`, `--root DIR` (default `.`), `--strict`, `--tests-dir DIR`; `analyze(files: Mapping[str, str | None]) -> Inventory` (path to content, `None` for a file that is not Python); `compare(base: Inventory, current: Inventory) -> list[str]`; `tests_directory(root, override=None) -> str`; `resolve_base(explicit: str | None, root) -> tuple[str | None, list[str]]` (base commit or `None`, warnings); `agent_authors(root) -> tuple[str, ...]`; `human_approval(base: str, root) -> str | None` (the reason, or `None`).
- Shared invariants: exit 0 means not weakened, approved by a human, or no base without `--strict`; exit 1 means weakened and not approved; exit 2 means a usage error or no base with `--strict`. Everything is printed on standard output. The problem lines start with `test file deleted:`, `test deleted or renamed:`, `parse error:`, `total asserts in the tests directory dropped`, or with the test's key followed by `asserts dropped`, `gained always-true asserts`, `gained skip/skipif/xfail markers` or `parametrize cases dropped`. `APPROVAL_MARK` is `[tests-change-approved]`, the same string the guard refuses from an agent; `APPROVAL_LABEL` is `tests-change-approved`. The label is looked at before the commits, and one approval covers every problem of the run. `[tool.rumbo].agent_authors` adds to `DEFAULT_AGENT_AUTHORS`, it never replaces it. A test is any function, plain or `async`, whose name starts with `test_`, at module level, in a class or inside a function that is not itself a test. Both sides of the comparison are analysed with the same rules, each on its own.
- Files owned by this spec (one writer): `src/rumbo/sensors/check_tests_not_weakened.py`, `tests/test_sensor.py`, `src/rumbo/templates/tests/unit/test_check_tests_not_weakened.py`, and the dispatch of `check-tests` in `src/rumbo/cli.py`. `scripts/check_tests_not_weakened.py` is a copy that `rumbo update` refreshes; `tests/unit/test_check_tests_not_weakened.py` is a copy of the template that `rumbo update` does not refresh (it is not in `FRAMEWORK_OWNED`).
- Interfaces touched that other specs depend on: the guard protects the script and refuses the mark from an agent (F-0001); the `sensor` step of `[tool.rumbo.checks]` calls the script with `--base {base}` (F-0002); `init`, `--adopt` and `update` copy the script, and the generated CI workflow runs it with `--strict` and `RUMBO_BASE_REF` (F-0004); gate G4 runs it with `--strict`, and `rumbo metrics` counts the commits that carry the mark (F-0005).

## Evals (LLM products)
<!-- case IDs, threshold -->
Not applicable: the sensor calls no model.

## NFR
0 dependencies (R14) · 0 network calls: the only subprocesses are local git commands
(`rev-parse`, `merge-base`, `ls-tree`, `show`, `log`) · cost 0 USD. No latency target is set and
none is measured: the cost is one `git show` per Python file of the tests directory at the base,
plus one parse per file on each side.

## Out of scope
What the sensor does not see, by its own account: an early `return` before the asserts, mocks
that empty the behavior, helpers built dynamically or imported from outside the tests
directory, changes to `[tool.pytest.ini_options]`. Review these by hand; mutation testing
covers part of them.

Checked against the code and also not detected:
- a skip mark on a class or on a module (`pytestmark`), `unittest.skipIf`, `unittest.skipUnless`, `unittest.expectedFailure` and `pytest.importorskip`;
- a `parametrize` whose values are not a literal list or tuple, or are passed by keyword, and the removal of the decorator;
- an assertion that is always true without being a constant or a tuple (`assert x or True`), and an assertion whose expression was made weaker;
- weakening already committed on the base branch itself: there the base is `HEAD`, so only uncommitted changes are compared (the warning of R2);
- who added the label: the sensor checks the commits for agents, not the label;
- which problem an approval is for: a mark anywhere in `base..HEAD` approves the whole run.

Not stated as requirements because no test covers them: a base ref that shares no history with
`HEAD` (the code then compares against the ref itself), an event file whose
`pull_request.labels` is not a list (a `null` there raises `TypeError`, which the code does not
catch), a machine without git (the code does not catch the missing executable either), and
git output that the locale encoding cannot decode (first open question below).

The purity rules for `domain/` that ADR-0007 also decides belong to the architecture test of the
generated project, not to the sensor.

## Questions open before signing
- Defect, measured on Windows with Python 3.13 (locale encoding cp1252): `_git` decodes git's
  output with the locale encoding (`text=True` without `encoding`, line 125 of the sensor). A
  test file at the base that holds a byte cp1252 cannot decode (`Í`, `Á`, `❌`, the `⚠️` of
  `tests/test_gates.py`) is dropped from the base inventory, so emptying its tests exits 0:
  R4 and R5 do not hold for that file. A commit message with such a byte in `base..HEAD`
  makes the approval check raise `AttributeError` instead of exiting 1. Not covered by a test.
- ADR-0007 keeps "the per-file total" among the unchanged rules; the code keeps one total for the
  whole tests directory (R5). Which one is meant?
- Helper methods of the test class (R10) are in the code, the tests and the changelog, but the
  text of ADR-0007 only describes calls to a bare name or to `module.name`.
- The pull request label (R12) had no test before `test_the_pull_request_label_approves_the_change`.
- `test_configured_tests_directory_is_honoured_end_to_end` here, and `test_end_to_end_with_git` in
  the tests shipped to every project, expect exit 1 without clearing `GITHUB_EVENT_PATH`: on a
  pull request that carries the label they get 0 and fail.
- The PRD row of F-0003 still says "on next touch".
