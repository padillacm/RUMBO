# ADR-0026: Harness settings are read from the base, not from the tree under test
Status: accepted    Date: 2026-11-16

Completes ADR-0007 (the tests directory), ADR-0021 (`agent_authors`, `agent_env`) and ADR-0019
(pyproject.toml is protected). All three stand.

## Context
The anti-cheat sensor compares the tests of a base commit with the working tree. Two of its
settings live in `pyproject.toml`: `[tool.rumbo].tests` (where the tests are) and
`[tool.rumbo].agent_authors` (who counts as an agent when an approval mark is read). The audit of
2026-11-16 (S2) found that both were read from the working tree, that is, from the change being
judged:

1. **Narrowing `tests`.** A change that sets `tests = "tests/unit"` and deletes
   `tests/test_calc.py` passed: the sensor looked only where the change told it to look.
2. **Emptying `agent_authors`.** An agent listed there wrote the approval mark, then removed its
   own entry in a later commit: the mark counted as the human's.

The guard has the same shape of problem (G7): `[tool.rumbo].agent_env`, which says how an agent
is recognised in the environment, was read from the unstaged working tree and replaced the
defaults.

The guard protects `pyproject.toml` outside its dependency tables (ADR-0019), but the guard binds
on the developer's machine and only the agents it recognises. The sensor in CI is the layer
ADR-0008 calls binding, so it cannot take its own configuration from the accused.

## Drivers (non-functional requirements)
A change must not be able to choose what it is judged against. Changing a setting must stay
possible for the human, without a release and without a second pull request. Adopting RUMBO in a
project whose tests are not in `tests/` must not need an approval mark. Standard library only.

## Options considered
1. **Keep reading the working tree and rely on the guard.** Rejected: it is the finding, and the
   guard does not run in CI.
2. **Read the base only.** Simple, but a legitimate change of `tests` would compare the wrong
   directory until the change is merged, and an identity the change adds would not count for the
   change itself.
3. **The base decides; the working tree can only add (chosen).**

## Decision and rationale
**`[tool.rumbo].tests` (sensor).** The tests directory is the one `pyproject.toml` has at the base
commit (`git show <base>:./pyproject.toml`; `tests` when the file, the table or the key is
missing). When the working tree names another directory, the sensor inventories **both
directories on both sides**, so nothing that was compared before stops being compared and the new
directory is compared from its first change. The change of the setting is itself a problem,
which needs the human's approval like any other, when at least one file of the base is under the
old directory and not under the new one: those files leave the sensor's sight for every later
change. When no file is left out (a wider directory, or a first setting in a project whose
`tests/` held nothing) the sensor prints a note and reports nothing. `--tests-dir` on the command
line still overrides everything: whoever types the command chooses.

**`[tool.rumbo].agent_authors` (sensor).** The agent identities are `DEFAULT_AGENT_AUTHORS`, plus
the list of the base, plus the list of the working tree. An entry can be added by the change and
counts at once; an entry of the base cannot be removed by the change.

**`[tool.rumbo].agent_env` (guard).** Stricter than the two settings above: the guard reads
it from `HEAD` only, not from the working tree, so an agent cannot unset its own marker by
editing the file it is about to be judged by. The committed list replaces the default
markers, as it always did; the working tree is read only in a repository with no commit on
any ref, where there is no `HEAD` yet. Tested by
`tests/test_guard.py::test_agent_env_is_read_from_head_not_from_the_working_tree`.

**Reading the base.** The sensor reads the base with paths relative to the project root
(`git show <base>:./<path>`, `git ls-tree -z`), so a project in a sub-directory of its repository
is compared too (S1), and a base test file that cannot be read stops the run with exit code 2: an
unreadable base is not a base without tests.

The `conftest.py` files of the directories above the tests directory, up to the project root, are
part of both inventories: pytest loads them, so a hook there reaches every test.

## Consequences (+/−)
+ A change can no longer narrow the directory the sensor looks at, nor take an agent off the list.
+ Adoption in a project with another layout (`tests = "spec"`) is compared from the first run and
  needs no approval.
+ A project that is not at the root of its repository is no longer always green.
− Moving the tests to another directory needs the approval mark once (every file of the base
  leaves the old directory). That is the intent: it is a change a human should see.
− Any file counts, also one that is not a test: a project whose tests live in `spec/` and that
  kept a `tests/README.md` needs the mark when it sets `tests = "spec"` (measured: the second
  audit's `adoption_tests_spec_but_a_tests_dir_exists`).
− A value that leaves the repository (`tests = "../elsewhere"`, an absolute path) stops the run
  with exit code 2 instead of comparing nothing.
− One more `git show` per run (two when an approval is looked for).
− Removing an identity from `agent_authors` takes effect only once the removal is in the base:
  a human whose own name the base lists as an agent cannot approve the change that removes it
  (the pull-request label still can).
− `--tests-dir` remains a way to look elsewhere; the CI workflow and the runner do not pass it,
  and both files are protected (ADR-0019, CODEOWNERS).

## Fitness function
`tests/test_sensor.py::test_the_tests_directory_is_the_one_the_base_configured`,
`::test_a_tests_setting_that_leaves_no_file_of_the_base_out_is_compared_not_reported`,
`::test_an_agent_identity_of_the_base_cannot_be_dropped_by_the_change`,
`::test_a_project_in_a_subdirectory_of_the_repository_is_compared`,
`::test_a_base_that_cannot_be_read_stops_the_run_instead_of_passing`,
`::test_a_conftest_above_the_tests_directory_is_inventoried`.
The guard's part (`agent_env` read from `HEAD`) is enforced by its own test in
`tests/test_guard.py`, named by the change that fixes G7.

## Reversibility: high
