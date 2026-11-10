<!-- rumbo:template — delete this line when the document is complete. Only the HUMAN removes it; `rumbo gate` checks it. -->
# F-0005: Gates and traceability
Story: As the human who signs each phase of a project built with a coding agent I want every
gate to tell me what was verified, what failed and what could not be measured, and to show me
which test stands behind each requirement, so that I sign on evidence and the tool never signs
for me.

Scope: `rumbo gate G1…G5`, `rumbo trace`, `rumbo doctor`, `rumbo metrics` and `rumbo decision`
(`src/rumbo/gates.py`, `src/rumbo/trace.py`, `src/rumbo/metrics.py`, the reading side of
`src/rumbo/project.py`, and their subcommands in `src/rumbo/cli.py`).
Decisions: ADR-0009, ADR-0010, ADR-0017, ADR-0018, ADR-0020; `docs/method.md`, sections 2 and 6.
This spec is retroactive: it states what the code does on the day it was written.

## Requirements (EARS)
<!-- Ubiquitous: The system shall… · Event: When…, the system shall… · State: While…, the system shall…
     Unwanted: If…, then the system shall… · Optional: Where…, the system shall… -->
- R1 (ubiquitous): `rumbo gate Gk` shall run the automatic checks of the gate and report each one in one of three states, passed (✅), failed (❌) or unmeasured (⚠️: it could not be measured, it was skipped, or it is a recommendation at the project's rigor), printing the detail of a check only when it did not pass.
- R2 (ubiquitous): The gate report shall be red (header 🛑, the number of failed checks, exit code 1) when at least one check failed and green (header ✅, exit code 0) when none failed, saying in the green case how many checks could not be measured or are recommendations; when the project state cannot be loaded from `pyproject.toml` the command shall exit 2 without a report.
- R3 (ubiquitous): The tool shall never sign a gate: every report shall print the gate's human checklist as unticked items under a heading that says the human signs, say `GATE NOT SIGNED yet` when no check failed, and end with the `rumbo decision "Gk signed"` command; `rumbo decision` shall append a dated entry (title, the reason when given, who decided, `human` by default) to `docs/decisions.md` and exit 2, creating nothing, when that file does not exist.
- R4 (optional): Where the project's rigor is below the level a check is required from, the gate shall report a miss of that check as unmeasured instead of failed: below `mvp` the PRD, the eval plan and the G5 operation checks (dependency updates, the Python matrix of a package, the runbook and the datasets of a `data` project); below `production` the data contracts of a spec and the fitness function of an ADR; and it shall ask for at least 20 real eval cases (not tagged `example`) at `prototype` and 50 at `mvp`.
- R5 (ubiquitous): The gate shall ask a project only for what its `llm`, `kind` and `layout` can have: eval cases and the eval plan in G2 for an LLM product or a `data` project; in G3 an ADR whose title names both "workflow" and "agent", and in G5 the budget and telemetry wired into the entry point, for an LLM product; in G3 two import-linter contracts for a layout with layers and the architecture test for a `flat` one; in G5 a changelog with an Unreleased section, a release workflow with Trusted Publishing and no PyPI token secret, and CI on at least two Python versions for `library` and `cli`, a runbook, SLOs and a Dockerfile for `service`, a runbook and datasets for `data`, whose Dockerfile is a recommendation.
- R6 (ubiquitous): The human checklist of a gate shall list only the items that apply to the project's rigor, `llm`, `kind` and `layout`, under the same conditions as the automatic checks, so that the human is not asked to sign what the checks do not expect.
- R7 (unwanted): If a document a gate reads still carries the template mark, then the gate shall report it as not complete (failed where the document is required at the project's rigor, unmeasured where it is recommended), shall not count the sections of a threat model or the diagrams of a C4 document under the mark, and shall not count a placeholder line of the spec template as an EARS requirement.
- R8 (event): When `rumbo gate G4` runs, it shall name the lane in the report heading, `fix` when there is at least one commit since the merge base of `HEAD` with the base branch and every subject has a Conventional Commit type other than `feat` with no `!` before the colon, `normal` otherwise, and shall fail the diff check when the lines added plus removed since the merge base, lockfiles excluded, exceed 300; without a merge base (no repository, or `HEAD` on the base branch itself) the lane shall be `normal` and the diff size shall be reported as unmeasured.
- R9 (state): While the lane is `fix`, G4 shall add three checks: a file under the project's tests directory gained lines since the merge base; the diff is at most 150 lines, lockfiles excluded; and `[project].dependencies` of `pyproject.toml` names no distribution, compared by normalised name, that the merge base did not have.
- R10 (unwanted): If the run was requested and the runner exits 3, or the runner or the sensor could not be started, then G4 shall fail that check with a detail saying that it is the environment and that the human repairs it; a runner that exits 0 passes and one that exits 1 fails with the tail of its output; the runner is `scripts/rumbo_check.py`, or `make check` when that script does not exist; with `--no-run` neither is started and G4 reports one unmeasured result, `skipped (--no-run)`.
- R11 (ubiquitous): `rumbo trace` shall read as a requirement each line `- Rk (pattern): text` or `- Rk: text` under the `Requirements` section of every `specs/NNNN-*.md` except the `0000-` template, give it the id `F-NNNN.Rk`, ignore nested items such as `R1.1`, and print a table with, for each requirement, the `file:line` places in the `.py` files of the project's tests directory and in `evals/datasets/*.jsonl` that mention the full id as a whole token (`F-0001.R10` does not cover `F-0001.R1`), or `**missing**`, followed by the count of requirements referenced.
- R12 (unwanted): If `rumbo trace --strict` finds a requirement that nothing mentions, or no requirement at all, then it shall exit 1, and 0 otherwise; without `--strict` it shall exit 0; gate G4 shall report requirements that nothing mentions as unmeasured below `production` and as failed in `production`, and shall add no coverage check when `specs/` has no requirement.
- R13 (ubiquitous): The system shall read the tests directory from `[tool.rumbo].tests` (`tests` when the key is absent) for the architecture test G3 looks for, for `rumbo trace`, for the coverage check of G4 and for the regression-test rule of the fix lane, and the source root from `[tool.rumbo].src` (`src` when absent, `.` for a package at the project root) for the spikes-import scan of G1.
- R14 (event): When `rumbo doctor` runs, it shall print one ❌ line per problem and exit 1 when a file of the harness plan for this project is missing, when `AGENTS.md` has more than 100 lines or when `uv.lock` is missing, and otherwise exit 0 with one line naming the package, kind, layout, llm, provider and rigor; it shall print as warnings, which do not change the exit code, the `@your-handle` placeholder in `.github/CODEOWNERS`, the security-contact placeholder in `SECURITY.md`, a harness version in `[tool.rumbo]` that differs from the CLI's, and a git clone in which `rumbo.human` is not `true`.
- R15 (ubiquitous): `rumbo metrics` shall compute offline, and print as text or with `--json` as JSON, the specs (total, closed), the sessions per closed spec and their median, the gates signed, the deploys and incidents logged inside the `--days` window, the postmortems, the commits of the window by Conventional Commit type, the requirements with and without a test, the real eval cases and a summary of the latest eval report; it shall read decision headings written by hand (hyphen, en dash or em dash, CRLF) and shall count as nothing, without failing, git that is not installed, a directory that is not a repository, a decision heading with an impossible date, a dataset line that is not a JSON object and an eval report that is not JSON; without a `pyproject.toml` it shall exit 2.

## Acceptance criteria
<!-- pytest tests / Hypothesis properties that verify them (path::test). A test covers a requirement
     by mentioning its full id, e.g. `F-000N.R1`, in its docstring; `rumbo trace` builds the table. -->
Each test carries its requirement ids in a comment directly above it.

| Requirement | `tests/test_gates.py` | `tests/test_cli.py` |
|---|---|---|
| R1 | `test_report_shows_three_states_and_only_a_failure_turns_the_gate_red` | `test_gate_g4_reports_unmeasured_instead_of_green` |
| R2 | `test_report_shows_three_states_and_only_a_failure_turns_the_gate_red`, `test_gate_exits_1_only_when_a_check_failed`, `test_g4_fails_when_the_check_could_not_be_measured` | `test_gate_header_is_green_when_no_check_failed`, `test_gate_g4_reports_unmeasured_instead_of_green` |
| R3 | `test_report_shows_three_states_and_only_a_failure_turns_the_gate_red`, `test_decision_logs_the_signature_the_report_asks_for` | `test_decision`, `test_gate_header_is_green_when_no_check_failed` |
| R4 | — | `test_gate_g2_scales_with_rigor`, `test_gate_g2_data_contracts_recommended_then_required`, `test_gate_g3_tolerates_empty_or_comment_only_adrs`, `test_gate_g5_prototype_does_not_require_dependabot`, `test_gate_g2_counts_eval_cases_for_a_data_project_without_llm`, `test_gate_g5_library_red_cases`, `test_gate_g5_data_requires_runbook_and_datasets_but_only_recommends_a_dockerfile` |
| R5 | `test_checklists_follow_kind_and_layout`, `test_checks_and_checklists_follow_rigor_and_llm` | `test_gate_g3_workflow_adr_by_title`, `test_gate_g5_requires_budget_and_telemetry_for_llm_products`, `test_gate_g2_counts_eval_cases_for_a_data_project_without_llm`, `test_gate_g3_flat_layout_asks_for_the_architecture_test_not_the_contracts`, `test_gate_g3_hexagonal_layout_requires_the_two_contracts`, `test_gate_g5_service_keeps_the_ops_trio`, `test_gate_g5_library_and_cli_pass_with_the_generated_release_files`, `test_gate_g5_library_red_cases`, `test_gate_g5_data_requires_runbook_and_datasets_but_only_recommends_a_dockerfile` |
| R6 | `test_checklists_follow_kind_and_layout`, `test_checks_and_checklists_follow_rigor_and_llm` | — |
| R7 | `test_a_document_under_the_template_mark_is_not_complete` | `test_gate_g1`, `test_gate_g2_scales_with_rigor`, `test_gate_g2_ears_ignores_template_placeholders`, `test_gate_g3_ignores_sections_of_untouched_templates` |
| R8 | `test_g4_measures_the_diff_without_lockfiles_and_reads_the_tests_directory` | `test_lane_is_fix_only_when_every_commit_is_a_fix_type`, `test_gate_g4_fix_lane_is_green_for_a_tested_small_fix`, `test_gate_g4_without_a_merge_base_is_the_normal_lane`, `test_diff_size_is_unmeasured_when_head_is_the_base` |
| R9 | `test_g4_measures_the_diff_without_lockfiles_and_reads_the_tests_directory` | `test_gate_g4_fix_lane_is_green_for_a_tested_small_fix`, `test_gate_g4_fix_lane_requires_a_regression_test`, `test_gate_g4_fix_lane_limits_the_diff_to_150_lines`, `test_gate_g4_fix_lane_forbids_a_new_runtime_dependency` |
| R10 | `test_g4_fails_when_the_check_could_not_be_measured` | `test_gate_g4_runs_the_runner_and_tells_red_from_broken`, `test_gate_g4_without_make_is_unmeasured_not_a_traceback`, `test_gate_g4_reports_unmeasured_instead_of_green` |
| R11 | `test_g3_and_trace_follow_the_tests_directory`, `test_trace_strict_fails_without_requirements_and_passes_when_all_are_covered` | `test_trace_and_gate_g4_requirements`, `test_trace_ignores_nested_items_and_lookalikes` |
| R12 | `test_trace_strict_fails_without_requirements_and_passes_when_all_are_covered`, `test_g3_and_trace_follow_the_tests_directory` | `test_trace_and_gate_g4_requirements` |
| R13 | `test_g3_and_trace_follow_the_tests_directory`, `test_g4_measures_the_diff_without_lockfiles_and_reads_the_tests_directory`, `test_g1_scans_the_source_root_of_the_project` | — |
| R14 | `test_doctor_tells_problems_from_warnings` | `test_doctor`, `test_doctor_warns_about_placeholders` |
| R15 | `test_metrics_tolerate_missing_git_and_malformed_files`, `test_decision_logs_the_signature_the_report_asks_for` | `test_metrics_reads_decisions_specs_and_evals`, `test_decisions_parsing_tolerates_hand_written_headings`, `test_gate_g2_skips_malformed_dataset_lines` |

End to end: `tests/test_self_adoption.py::test_rumbo_doctor_passes_on_the_repository_itself`
runs `rumbo doctor` on this repository.

## Data contracts and ownership
<!-- What two sessions (or two agents) must agree on BEFORE writing code. Omitting explicit data
     structures is what breaks parallel work (docs/method.md, section 3.1). -->
- Types / schemas: `Result(description: str, ok: bool | None, detail: str = "")`, where `ok` is `True` passed, `False` failed, `None` unmeasured; `Item(text, min_rigor="prototype", llm_only=False, max_rigor="production", arch_only=False, kinds=())`, where an item with `max_rigor="mvp"` is listed only below `mvp`; `Gate(code, phase, manual: tuple[Item, ...], checks: tuple[Check, ...])` with `Check = Callable[[Project, bool], list[Result]]`; `Requirement(spec_id, rid, text, covered_by: list[str])` with `full_id == f"{spec_id}.{rid}"`; `Decision(date, title, why, who)`; `Metrics` (`days`, `specs_total`, `specs_complete`, `specs_closed`, `sessions_per_spec`, `gates_signed`, `deploys`, `incidents`, `postmortems`, `commits_by_type`, `commits_total`, `test_change_approvals`, `requirements_total`, `requirements_uncovered`, `eval_cases`, `latest_eval`), whose `as_dict()` adds `median_sessions_per_spec`. A decision entry in `docs/decisions.md` is `## YYYY-MM-DD — title`, `- Decision: title`, `- Why: reason` (optional), `- Decided by: who`.
- Signatures: `evaluate(code, project, run=True) -> tuple[Gate, list[Result]]`; `report(gate, results, project) -> tuple[str, bool]` (the text and "no check failed"); `merge_base(root) -> tuple[str | None, str]` (sha and base name, or `None` and the reason); `diff_size(root) -> tuple[int | None, str]`; `lane(root, base) -> "fix" | "normal"`; `trace(root, tests=None) -> list[Requirement]`; `requirements(spec) -> list[Requirement]`; `uncovered(reqs) -> list[Requirement]`; `collect(root, days=30, today=None) -> Metrics`; `parse_decisions(text) -> list[Decision]`; `eval_cases(root) -> int`; `Project.load(root) -> Project`, raising `FileNotFoundError` or `ValueError`. Commands: `rumbo gate {G1..G5} [--dir D] [--no-run]`; `rumbo trace [--dir D] [--strict]`; `rumbo doctor [--dir D]`; `rumbo metrics [--dir D] [--days N] [--json]`; `rumbo decision TITLE [--why W] [--who WHO] [--dir D]`.
- Shared invariants: exit codes are 0 (no failed check; nothing missing), 1 (a failed check, a doctor problem, `trace --strict` with a gap) and 2 (no project, or no decisions log); the gate code is accepted in either case. The limits are 300 lines for a diff and 150 in the fix lane; the lockfiles left out of the count are `uv.lock`, `poetry.lock`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`; the base branches tried, in order, are `origin/main`, `origin/master`, `origin/HEAD`, `@{upstream}`, `main`, `master`; the fix-lane types are `fix`, `test`, `refactor`, `chore`, `docs`, `ci`, `perf`, `style`, `build`; the diff is the working tree against the merge base. The minimum of real eval cases is 20, 50 and 100 for `prototype`, `mvp` and `production`. The runner's exit codes are 0 green, 1 red, 3 environment (ADR-0006, ADR-0018). A numbered document is `NNNN-*.md` and never `0000-*`; a document is "under the template mark" when the mark string appears anywhere in its text, or when the file is missing. A requirement id is `F-NNNN.Rk`, `NNNN` being the number of the spec file; a mention counts wherever it is in a file (docstring, comment, string, case id). `rumbo metrics` reads decision titles `F-NNNN closed` (with `k sessions` in the reason), `Gk signed`, `deploy…` and `incident…`; closed specs and signed gates count whatever their date, deploys and incidents only inside the window. The keys of `[tool.rumbo]` read here are `package`, `rigor`, `kind`, `layout`, `layers`, `llm`, `provider`, `tests`, `src` and `version`.
- Files owned by this spec (one writer): `src/rumbo/gates.py`, `src/rumbo/trace.py`, `src/rumbo/metrics.py`, `tests/test_gates.py`, and `cmd_gate`, `cmd_trace`, `cmd_doctor`, `cmd_metrics`, `cmd_decision` with their parsers in `src/rumbo/cli.py`.
- Interfaces touched that other specs depend on: `Project`, `is_template`, `section` and `numbered_documents` in `src/rumbo/project.py`, shared with the scaffold (F-0004); the template mark, which the guard reads with its own copy of the constant (F-0001); the exit codes of the runner (F-0002) and of the sensor under `--strict` (F-0003); `plan_files` and `Config.from_project` of the scaffold, which give `rumbo doctor` the list of harness files (F-0004); `[tool.rumbo].tests`, also read by the sensor (F-0003).

## Evals (LLM products)
<!-- case IDs, threshold -->
Not applicable: these commands call no model.

## NFR
0 runtime dependencies (PRD) · 0 network calls and cost 0 USD: the commands start only `git`,
the project's Python and `make`, and import no SDK (by construction, not measured by a test) ·
generated `AGENTS.md` ≤ 100 lines is the PRD's target that `rumbo doctor` measures (R14) · no
latency target is set or measured for these commands.

## Out of scope
A check that every feature of the PRD has a spec: the human decided against it (2026-11-09);
the features already built got their specs instead. The catalogue of checks of each gate beyond the rules above (`GATES`, `_g1`…`_g5` in
`src/rumbo/gates.py` are the list). Who runs `rumbo decision`: the command does not check its
caller, and no gate reads the log to know whether it was signed. What the sensor and the runner
decide (F-0003, F-0002). Whether a test that mentions a requirement verifies it: `rumbo trace`
finds mentions, the reviewer judges them. A fix that changes behaviour a spec describes without
updating the spec (ADR-0009).

Read in the code and not stated above, because no test verifies it: the data contracts of a spec
and the fitness function of an ADR failing in `production`; the 100-case minimum in `production`;
the threat model and the C4 document as recommendations below `mvp`; at most three `file:line`
places per file in the trace table and the `(duplicate id!)` flag; the fix lane's dependency
check reported as unmeasured when `pyproject.toml` cannot be read; `rumbo doctor` exiting 2
without a project; `specs_complete` and `test_change_approvals` in the metrics.

## Questions open while interrogating the spec
- `rumbo trace` and the coverage check of G4 read the requirements of a spec that still carries
  the template mark, while G2 reports that spec as not complete. In `production` a draft with
  an untested requirement turns G4 red. Should a draft's requirements count?
- The mark is looked for anywhere in a document, not only on its first line, although the gate's
  message says "on its first line": a signed document that quotes the mark reads as a template.
- A red report still ends with the line that tells how to record the signature.
- With the run requested, an unmeasured diff size (no merge base) leaves G4 green: ADR-0018
  makes only the runner and the sensor fail when they cannot be measured.
- A missing sensor script, or a sensor that exits 2 because it found no base, fails its check
  with the sensor's own output and without the hand-off to the human that ADR-0018 describes;
  only an interpreter that cannot be started gets that text.
- ADR-0009 compares the dependencies "at HEAD"; the code compares the working tree.
- A requirement id inside a fixture string of a test counts as a mention of the real
  requirement (the trace tests do this to F-0001 today).
