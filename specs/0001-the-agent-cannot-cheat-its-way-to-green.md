# F-0001: The agent cannot cheat its way to green
Story: As a developer working with a coding agent I want the hard rules of AGENTS.md enforced by
hooks, not by prose, so that the agent cannot weaken the harness, sign for me or finish on red,
whichever editor or CLI it runs in.

Scope: `scripts/rumbo_guard.py` (canonical source `src/rumbo/sensors/guard.py`) as Claude Code
hooks (`PreToolUse`, `PostToolUse`, `Stop`) and as git hooks (`pre-commit`, `commit-msg`).
Decisions: ADR-0005, ADR-0006 (consumers), ADR-0008, ADR-0017.

## Requirements (EARS)
<!-- Ubiquitous: The system shall… · Event: When…, the system shall… · State: While…, the system shall…
     Unwanted: If…, then the system shall… · Optional: Where…, the system shall… -->
- R1 (unwanted): If the agent calls an edit tool on a protected file (Makefile, `pyproject.toml`, CI, `infra/`, `.claude/`, the pre-commit config, the sensors, `.env*`), then the guard shall block the call with exit code 2 and a reason that tells the agent to ask the human.
- R2 (unwanted): If an edit would remove the template mark from a document, then the guard shall block it and name the file.
- R3 (unwanted): If a shell command would bypass the hooks (`--no-verify`, `SKIP=`, `core.hooksPath`, `pre-commit uninstall`), destroy work (`git reset --hard`, a forced push), declare the agent human (`rumbo.human`), write the approval mark or redirect into a protected file, then the guard shall block the command and give the reason.
- R4 (event): When a shell command has finished and a protected file differs from the last commit, the guard shall report the files and ask for them to be reverted, except `pyproject.toml` and `uv.lock`, which `uv add` writes legitimately.
- R5 (state): While code files have changed since the last commit and the verification runner is red, the guard shall refuse to let the agent finish and shall return the failing tail of the runner.
- R6 (unwanted): If the verification environment is broken (the runner exits 3, or neither the runner nor `make` exists), then the guard shall not hand the agent a repair job: it shall tell the agent to report to the human and stop, and tell the human in one line.
- R7 (event): When an agent commits, the git `pre-commit` hook shall refuse a staged protected file and a staged document whose template mark was in `HEAD` and is gone, except in the root commit of a repository.
- R8 (unwanted): If the message of an agent's commit contains `[tests-change-approved]`, then the git `commit-msg` hook shall refuse the commit.
- R9 (ubiquitous): The guard shall decide the actor of a commit as an agent when an environment marker of `[tool.rumbo].agent_env` is set, as the human when `git config rumbo.human` is `true`, and as an agent otherwise.
- R10 (ubiquitous): The guard shall never block the human: ordinary files and ordinary commands pass, a commit by the declared human passes, and a run with nothing staged passes silently.
- R11 (ubiquitous): The guard shall not crash when git is missing or the repository has no commit; it shall let the action through.
- R12 (ubiquitous): The guard shall decide a `PreToolUse` event in under 100 ms (p95), without network or test runs, and without a subprocess except one `git show` when the target is a numbered ADR or spec.
- R13 (event): When a shell command has finished and a document that carried the template mark in the last commit no longer carries it, the guard shall report the file and ask for the first line to be restored.
- R14 (unwanted): If the agent edits, rewrites or deletes a numbered ADR or spec that is committed without the template mark (signed by the human), through an edit tool, a shell command or a commit, then the guard shall block or report it and tell the agent to propose the change or write a document that supersedes it.
- R15 (ubiquitous): The guard shall let the agent create new ADRs and specs and edit those that still carry the template mark, and shall never treat the `0000-` templates as signed.

## Acceptance criteria
<!-- pytest tests / Hypothesis properties that verify them (path::test). A test covers a requirement
     by mentioning its full id, e.g. `F-000N.R1`, in its docstring; `rumbo trace` builds the table. -->
All in `tests/test_guard.py`; each test carries its requirement id in a comment.

| Requirement | Tests |
|---|---|
| R1 | `test_protected_files_are_blocked`, `test_relative_paths_are_normalised` |
| R2 | `test_template_mark_cannot_be_removed_by_edit_or_write` |
| R3 | `test_dangerous_commands_are_blocked` |
| R4 | `test_post_bash_detects_protected_files_changed_since_head` |
| R5 | `test_stop_blocks_with_the_tail_when_the_runner_is_red`, `test_stop_passes_when_the_runner_is_green`, `test_stop_runs_make_check_only_when_code_changed` |
| R6 | `test_stop_reports_a_broken_environment_to_the_human`, `test_stop_without_runner_or_make_asks_for_rumbo_update` |
| R7 | `test_pre_commit_refuses_a_staged_protected_file_from_an_agent`, `test_pre_commit_refuses_removing_the_template_mark`, `test_pre_commit_exempts_the_root_commit_and_guards_from_the_second`, `test_pre_commit_allows_uv_writes_and_lists_every_other_protected_file` |
| R8 | `test_commit_msg_refuses_the_approval_mark_from_an_agent` |
| R9 | `test_actor_is_an_agent_when_a_marker_is_set`, `test_actor_is_the_human_who_declared_rumbo_human`, `test_agent_markers_come_from_pyproject_when_declared` |
| R10 | `test_ordinary_files_and_files_outside_the_project_are_allowed`, `test_ordinary_commands_are_allowed`, `test_pre_commit_passes_silently_with_nothing_staged` |
| R11 | `test_git_missing_never_crashes`, `test_git_hooks_without_git_never_crash` |
| R12 | `test_pre_tool_decides_within_the_latency_budget` |
| R13 | `test_post_bash_reports_a_removed_mark_and_a_changed_signed_document` |
| R14 | `test_editing_a_signed_spec_or_adr_is_blocked_and_drafting_is_not`, `test_post_bash_reports_a_removed_mark_and_a_changed_signed_document`, `test_pre_commit_refuses_a_staged_change_to_a_signed_document` |
| R15 | `test_editing_a_signed_spec_or_adr_is_blocked_and_drafting_is_not` |

End to end: `scripts/e2e.sh` exercises the three Claude Code hook paths and the two git hooks in
a generated project (`CLAUDECODE=1 git commit` of a protected file refused, the human's passes).

## Data contracts and ownership
<!-- What two sessions (or two agents) must agree on BEFORE writing code. Omitting explicit data
     structures is what breaks parallel work (docs/method.md, section 3.1). -->
- Types / schemas: the Claude Code hook payload on stdin, a JSON object with `tool_name: str`, `tool_input: {file_path | command | edits…}`, `cwd: str`, `stop_hook_active: bool`; the `Stop` environment answer on stdout, exactly one JSON object `{"decision": "block", "reason": str, "systemMessage": str}`; `[tool.rumbo].agent_env: list[str]` in `pyproject.toml`.
- Signatures: `main(argv: list[str] | None) -> int` with `argv[0]` in `pre-tool | post-tool | stop | pre-commit | commit-msg <file>`; `check_edit(tool_name, tool_input, root) -> str | None`; `check_bash(command) -> str | None`; `check_stop(payload, root) -> tuple[int, str]`; `check_pre_commit(root) -> str | None`; `check_commit_msg(root, message_file) -> str | None`; `actor(root) -> "agent" | "human" | "unknown"`. No exception escapes `main`.
- Shared invariants: exit 0 lets the action through; exit 2 blocks a Claude Code event with the reason on stderr; exit 1 refuses a git hook; exit 0 plus JSON on stdout is the environment route of `Stop`; the runner's exit codes are 0 green, 1 red, 3 environment (ADR-0006); `PROTECTED_FILES` and `PROTECTED_PREFIXES` are the single list for every event; the script is standard-library only and identical in every project.
- Files owned by this spec (one writer): `src/rumbo/sensors/guard.py`, `tests/test_guard.py`, the two `rumbo-guard` hooks in `src/rumbo/templates/dot_pre-commit-config.yaml.tmpl`, the `hooks` block of `src/rumbo/templates/dot_claude/settings.json`.
- Interfaces touched that other specs depend on: the runner's exit codes and `--brief` output (F-0002); the approval mark and the agent-authorship check of the sensor (F-0003); `PROTECTED_FILES` mirrored by `scaffold.py` (F-0004).

## Evals (LLM products)
<!-- case IDs, threshold -->
Not applicable: the guard calls no model.

## NFR
`PreToolUse` decision p95 < 100 ms (R12) · `PostToolUse` p95 < 500 ms (one `git status`) ·
0 network calls · 0 dependencies · cost 0 USD.

## Out of scope
Live (pre-edit) hooks for agents other than Claude Code (Cursor's protocol is in beta, ADR-0008);
stopping an agent that unsets its own marker outside Claude Code (CI and CODEOWNERS are the
binding layer); protecting files the project adds to its own harness (the list is fixed).

## Questions resolved while interrogating the spec
- A shell command that strips the template mark is now reported after the command, not only
  refused at the commit (R13, ADR-0017).
- An undeclared human on a fresh clone is treated as an agent, on purpose (R9, fail-closed):
  one `git config rumbo.human true` per clone; the README says so next to the clone steps.
- A signed ADR or spec is read-only for the agent; drafting stays free (R14, R15, ADR-0017).
