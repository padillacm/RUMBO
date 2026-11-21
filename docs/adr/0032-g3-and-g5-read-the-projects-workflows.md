# ADR-0032: G3 and G5 read the project's workflows, and one supported Python is enough when it is the newest
Status: accepted    Date: 2026-11-16

Completes ADR-0010 (what each kind has in operation), ADR-0013 (adoption) and ADR-0014 (the
minimum Python). They stand.

## Context
Three automatic checks look at continuous integration: G3 "CI runs rumbo check" (ADR-0006),
and in G5 the Python matrix of a `library` or `cli` (ADR-0010) and the SBOM step in
`production`. The audit of 2026-11-16 found two defects in how they look (findings T7, T8):

1. **They read one file name.** All three opened `.github/workflows/ci.yml`. `rumbo init
   --adopt` never overwrites a workflow the project already has (ADR-0013), so an adopted
   project whose workflow is `tests.yaml` or `build.yml` failed G3 and G5 for ever, whatever the
   workflow did. The human could only pass the gate by renaming a file.
2. **G5 asked for two Pythons that cannot exist.** `SUPPORTED_PYTHONS` ends at the newest
   version RUMBO knows. A package whose `[tool.rumbo].python` is that version is generated with
   a matrix of one entry, and G5 then failed it for not running "on at least two".

Reading every workflow opens a hole of its own. The generated `nightly.yml` of an adopted
project calls the runner (`scripts/rumbo_check.py --full`), and the one of a flat package calls
`scripts/rumbo_check.py audit`. If any file counted, "CI runs rumbo check" would be true of a
project in which no push and no pull request is ever verified.

## Drivers (non-functional requirements)
Standard library only: there is no YAML parser, and the checks stay cheap text sensors that
never replace the human signature. A check must not be passable by a file that does not do the
thing, nor fail a project that does the thing under another name. Nothing changes for a
generated project.

## Options considered
1. **A setting, `[tool.rumbo].ci = "tests.yaml"`.** Rejected: one more key to keep true by hand,
   and `pyproject.toml` is a protected file the agent cannot edit (ADR-0019).
2. **Any file under `.github/workflows/` satisfies any check.** Rejected for the runner call: it
   is the hole described above.
3. **Every workflow is read; the runner call counts only in a workflow that a push or a pull
   request triggers (chosen).**

For the matrix: (a) keep asking for two versions, which a project on the newest Python cannot
satisfy; (b) drop the check; (c) ask for what the project promises (chosen).

## Decision and rationale
**Which files.** Every `*.yml` and `*.yaml` directly under `.github/workflows/`, in name order.
A check passes when one workflow satisfies it and its result names that file. A workflow that
is not UTF-8 satisfies nothing and is named in the detail of the check that failed.

**"CI runs rumbo check" (G3).** A workflow satisfies it when it contains
`scripts/rumbo_check.py` **and** its `on:` key names `push`, `pull_request`,
`pull_request_target` or `merge_group`, in the inline form (`on: [push, pull_request]`) or as a
block. A workflow that only has `schedule` or `workflow_dispatch` verifies no change. The
runner call may name steps (`rumbo_check.py lint`): a project may split the runner across jobs.

**The Python matrix (G5, `library` and `cli`).** The entries of the `python` or
`python-version` list of a `matrix:` are counted per workflow and the largest count is the
project's. At least two are required, as before. When `[tool.rumbo].python` is the last entry
of `SUPPORTED_PYTHONS` there is one version to run on and every workflow job runs on one: the
check is then "CI runs on Python X, the newest supported", and it passes when the project has a
workflow at all. It does not ask for a matrix of one entry, which would be ceremony.

**The SBOM step (G5, `production`).** Any workflow that mentions `sbom`, as before, whatever
its trigger: a scheduled or a release workflow is a fair place for it.

Not changed: the release workflow stays `.github/workflows/release.yml`, because the Trusted
Publisher registered on PyPI names that file; the nightly stays `nightly.yml`, a file RUMBO
generates and owns.

## Consequences (+/−)
+ An adopted project passes with the workflow names it had.
+ A project on the newest Python is not asked for a version that does not exist; when a newer
  Python joins `SUPPORTED_PYTHONS`, the same project is asked for two again, with no change here.
+ A generated project sees no difference: its `ci.yml` satisfied the three checks and still does.
− The trigger is read with a regular expression, not a YAML parser: a workflow whose `on:` key
  is written in a form the expression does not know (a YAML anchor, a flow mapping over several
  lines) fails the check and the human has to rewrite the key. It errs on the red side.
− "Contains the runner call" is still text: a commented-out step passes. The human checklist of
  G3 and the pull request are the layers that see it.
− On the newest Python the matrix check only proves that a workflow exists.

## Fitness function
`tests/test_gates.py::test_g3_and_g5_read_every_workflow_and_one_python_is_enough_when_it_is_the_newest`
(a workflow under another name and extension, a schedule-only workflow that calls the runner,
the SBOM in a third file, one matrix entry on the oldest and on the newest Python, a project
with no workflow, the forms of the `on:` key). The generated workflows are covered by
`tests/test_cli.py::test_gate_g3_requires_ci_to_run_the_runner` and
`::test_gate_g5_library_and_cli_pass_with_the_generated_release_files`.

## Reversibility: high
