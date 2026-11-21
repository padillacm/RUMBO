# F-0001: The agent cannot cheat its way to green
Story: As a developer working with a coding agent I want the hard rules of AGENTS.md enforced by
hooks, not by prose, so that the agent cannot weaken the harness, sign for me or finish on red,
whichever editor or CLI it runs in.

Scope: `scripts/rumbo_guard.py` (canonical source `src/rumbo/sensors/guard.py`) as Claude Code
hooks (`PreToolUse`, `PostToolUse`, `Stop`) and as git hooks (`pre-commit`, `commit-msg`).
Decisions: ADR-0005, ADR-0006 and ADR-0018 (consumers), ADR-0008, ADR-0017, ADR-0019, ADR-0021, ADR-0023, ADR-0024, ADR-0025, ADR-0026.

## Requirements (EARS)
<!-- Ubiquitous: The system shall… · Event: When…, the system shall… · State: While…, the system shall…
     Unwanted: If…, then the system shall… · Optional: Where…, the system shall… -->
- R1 (unwanted): If the agent calls an edit tool (`EDIT_TOOLS`) on a protected file (at the project root, `PROTECTED_FILES`: the Makefile, `pyproject.toml`, the pre-commit config, `.claude/settings.json`, the sensors; `.env` and `.env.<name>`; under `PROTECTED_PREFIXES`: `.github/`, `infra/`, `.claude/`, `.git/`, the git directory also above the project root; in any directory, the tool-configuration names of `SHADOW_CONFIGS`, ADR-0023), then the guard shall block the call with exit code 2 and a reason that tells the agent to ask the human. Names are compared without case and without the spellings Windows opens as the same file (`Makefile.`, `Makefile::$DATA`; on Windows also `\\?\C:\x`); a path that cannot be placed inside or outside the project is blocked when its name is a protected one.
- R2 (unwanted): If an `Edit`, `MultiEdit` or `Write` would leave a file of the project that carries the template mark without it, then the guard shall block it and name the file. `Edit` and `MultiEdit` are simulated on the file's content (in order, `replace_all` honoured), so replacing a part of the mark counts; an edit that names the whole mark and drops it is blocked even when the file cannot be read (ADR-0025).
- R3 (unwanted): If a command sent through a shell tool (`SHELL_TOOLS`: `Bash`, `PowerShell`) matches one of `BASH_RULES`, then the guard shall block it with exit code 2 and give the reason. The rules refuse: a bypass of the hooks (`--no-verify` and the abbreviations git accepts, `-n` on `git commit`, `SKIP=` for a git command or exported, `pre-commit uninstall`; `core.hooksPath`, `include.*`, `alias.*` or `rumbo.human` through `git config`, `-c` or `--config-env`; an assignment to `GIT_CONFIG_COUNT`, `_KEY_<n>`, `_VALUE_<n>`, `_GLOBAL`, `_SYSTEM` or `_PARAMETERS`); destroyed work (`git reset --hard`, a forced push, a recursive `rm` on the tree root, home or a glob); the approval mark; a redirection or a writing command (`sed -i`, `tee`, `rm`, `Set-Content`, `Remove-Item` among others) on a protected file; a file command (`rm`, `mv`, `cp`, `chmod` among others) that names the git directory; `rumbo verify-dep --index`, or an index variable set for it; `git diff --no-index` and `git diff` on a `.env` file. A flag counts bare or quoted, up to a separator. The command is judged as written and as the shell reads it (line continuations joined; line breaks and `;`, `|`, `&` inside quotes, `$(...)` or backticks flattened). A command whose rule work exceeds `MAX_RULE_WORK` (about 20 000 characters in one simple command), or that is not judged within `RULES_BUDGET` (3 s), is refused.
- R4 (event): When a shell command has finished, the guard shall report with exit code 2: (a) the protected files that differ from the last commit or are new; `uv.lock` is free, `pyproject.toml` may differ only in the tables `uv add` writes (`project.dependencies`, `project.optional-dependencies`, `dependency-groups`, `tool.uv`), and a version that cannot be parsed, is new or was deleted counts as a protected change (ADR-0019); (b) when HEAD is not the one recorded before the command, the changes between the two commits that R7 and R14 name (a pull or a merge included), with both commits and `git reset --soft` (ADR-0024); (c) what `git status` cannot see and appeared or changed during the command: a tracked protected file or numbered ADR or spec under `--skip-worktree` or `--assume-unchanged` (judged by (a), R13 and R14), and an untracked `PROTECTED_FILES` or `SHADOW_CONFIGS` name in a directory that holds tracked files; (d) `.claude/settings.local.json` changed outside `permissions` during the command. A rename counts as its source and its target, names with spaces or outside ASCII are read as they are, and paths are relative to the project root, also in a sub-directory of the repository. (b), (c) and (d) are reported once, and only against a state recorded before the command. Each report says what to do if the change is the agent's and, if it is the human's, to ask them to commit it or that there is nothing to undo.
- R5 (state): While code files (`.py`, `.toml`, `.jsonl`) differ from the last commit, or were committed since the last HEAD a Stop recorded as green (ADR-0024), or the guard's state exists and cannot be read or decoded, and the verification runner is red (`scripts/rumbo_check.py --brief` exits 1; without that script, `make check-brief` fails), the guard shall refuse to let the agent finish (exit code 2) and shall return the failing tail of the runner. Stop first refuses for (a) of R4, R13 and R14, does not judge the files of (c), passes at once when `stop_hook_active` is true and skips the runner when `RUMBO_GUARD_SKIP_CHECK` is set; it records HEAD as green when the runner is green or there was nothing to verify.
- R6 (unwanted): If the verification environment is broken (the runner exits with a code other than 0 or 1, such as its environment code 3; or neither the runner nor `make` exists), then the guard shall not hand the agent a repair job: it shall tell the agent to report to the human and stop, and tell the human in one line.
- R7 (event): When an agent commits, the git `pre-commit` hook shall refuse with exit code 1 a staged protected file, a staged `pyproject.toml` that differs from `HEAD` outside those dependency tables (or cannot be parsed, is new or was deleted), a staged file whose template mark was in `HEAD` and is gone, and a numbered ADR or spec that `HEAD` does not have and whose staged blob lacks the mark (ADR-0025). A rename counts as its source and its target, and a type change counts. Exempt are the first commit of a repository with no commit on any ref (no other: an orphan branch is judged) and, while a merge is concluded, a path staged exactly as it is in `MERGE_HEAD`.
- R8 (unwanted): If the message of an agent's commit contains `[tests-change-approved]`, then the git `commit-msg` hook shall refuse the commit.
- R9 (ubiquitous): The guard shall decide the actor of a commit as an agent when an environment marker of `[tool.rumbo].agent_env` is set, as the human when `git config rumbo.human` is `true`, and as an agent otherwise. The list is read from the `pyproject.toml` of `HEAD` (ADR-0026), from the working tree only in a repository with no commit on any ref; it replaces `DEFAULT_AGENT_ENV`, which applies when `HEAD` gives no list of strings.
- R10 (ubiquitous): The guard shall never block the human: ordinary files, files outside the project and ordinary commands pass, also a command that only names a forbidden word (a commit message, a `grep` pattern, `git config --get`); `conftest.py`, `.gitignore` and `.envrc` are not protected; a commit by the declared human passes, and a run with nothing staged passes silently; in a repository with no commit on any ref, (a) and (c) of R4, R13 and R14 report nothing; a hidden file that was there before a command is never reported.
- R11 (ubiquitous): The guard shall not crash: without git, or outside a repository, the checks that need git are skipped, not failed; a payload that is not a JSON object is judged as an empty one; a state that cannot be read or decoded refuses nothing by itself; an internal error never reads as "allow": `pre-tool` and `post-tool` exit 2 with the error, `stop` takes the route of R6, the git hooks exit 1 with one readable line.
- R12 (ubiquitous): The guard shall decide a `PreToolUse` call (`check_edit`, `check_bash`) in under 100 ms (p95; R3 bounds a command that takes longer), without network or test runs, and without a subprocess except one `git show` when the target of an edit is a numbered ADR or spec. For a shell command it allows, it then records the state of R4 with one `git rev-parse`, one `git ls-files` and, only when a tracked file of (c) carries an index flag, one `git hash-object`.
- R13 (event): When a shell command has finished, or at Stop, and a `.md` file that carried the template mark in the last commit no longer carries it, the guard shall report the file and say to restore the first line if the agent removed it and, if the human did, to ask them to commit the signature. A deleted document is not a removed mark; the committed versions are read in one `git cat-file --batch`.
- R14 (unwanted): If the agent edits, rewrites or deletes a numbered ADR or spec (`specs/NNNN-*.md`, `docs/adr/NNNN-*.md`, whatever the case of the path) that is committed without the template mark (signed by the human), through an edit tool, a shell command or a commit, then the guard shall block or report it and tell the agent to propose the change or write a document that supersedes it. While a merge is concluded, a signed document staged exactly as it is in `MERGE_HEAD` passes (R7).
- R15 (ubiquitous): The guard shall let the agent create new ADRs and specs that carry the template mark and edit those that still carry it, and shall never treat the `0000-` templates as signed. A `Write` of a numbered ADR or spec whose content lacks the mark is blocked with exit code 2, whether the file exists or not (ADR-0025); R7 and (b) of R4 judge the commit.

## Acceptance criteria
<!-- pytest tests / Hypothesis properties that verify them (path::test). A test covers a requirement
     by mentioning its full id, e.g. `F-000N.R1`, in its docstring; `rumbo trace` builds the table. -->
All in `tests/test_guard.py`; each test carries its requirement ids in a comment, except
`test_stop_hook_active_and_skip_env_short_circuit_the_runner`. A test that covers several
requirements is listed under the ones it chiefly verifies.

| Requirement | Tests |
|---|---|
| R1 | `test_protected_files_are_blocked`, `test_relative_paths_are_normalised`, `test_files_that_shadow_pyproject_are_protected`, `test_the_git_directory_is_protected_and_its_neighbours_are_not`, `test_only_real_env_files_are_protected` |
| R2 | `test_template_mark_cannot_be_removed_by_edit_or_write`, `test_an_edit_is_simulated_to_see_whether_the_mark_survives` |
| R3 | `test_dangerous_commands_are_blocked`, `test_every_shell_tool_is_judged_like_bash`, `test_bypass_spellings_are_blocked`, `test_a_writer_at_the_start_of_a_command_is_blocked` |
| R4 | `test_post_bash_detects_protected_files_changed_since_head`, `test_pyproject_may_change_only_in_its_dependency_tables`, `test_an_unparseable_or_new_pyproject_is_a_protected_change`, `test_a_new_shadow_file_is_caught_after_the_command_and_at_commit`, `test_a_commit_that_skipped_the_hook_is_judged_after_the_command`, `test_protected_files_hidden_from_git_status_are_found` |
| R5 | `test_stop_blocks_with_the_tail_when_the_runner_is_red`, `test_stop_passes_when_the_runner_is_green`, `test_stop_runs_make_check_only_when_code_changed`, `test_stop_verifies_code_committed_since_the_last_green_head`, `test_a_state_that_cannot_be_read_is_unknown_not_empty`, `test_parallel_shell_hooks_keep_the_green_head` |
| R6 | `test_stop_reports_a_broken_environment_to_the_human`, `test_stop_without_runner_or_make_asks_for_rumbo_update` |
| R7 | `test_pre_commit_refuses_a_staged_protected_file_from_an_agent`, `test_pre_commit_refuses_removing_the_template_mark`, `test_pre_commit_exempts_the_root_commit_and_guards_from_the_second`, `test_pre_commit_allows_uv_writes_and_lists_every_other_protected_file`, `test_pre_commit_refuses_pyproject_changes_outside_the_dependency_tables`, `test_an_agent_cannot_create_a_numbered_document_without_the_mark`, `test_an_orphan_branch_is_not_a_root_commit`, `test_a_staged_type_change_counts` |
| R8 | `test_commit_msg_refuses_the_approval_mark_from_an_agent` |
| R9 | `test_actor_is_an_agent_when_a_marker_is_set`, `test_actor_is_the_human_who_declared_rumbo_human`, `test_agent_markers_come_from_pyproject_when_declared`, `test_default_agent_markers_have_a_source`, `test_agent_env_is_read_from_head_not_from_the_working_tree` |
| R10 | `test_ordinary_files_and_files_outside_the_project_are_allowed`, `test_ordinary_commands_are_allowed`, `test_pre_commit_passes_silently_with_nothing_staged`, `test_commands_that_only_look_dangerous_are_allowed`, `test_a_writer_word_inside_an_argument_is_not_a_command`, `test_a_repository_with_no_commit_has_nothing_to_protect_yet` |
| R11 | `test_git_missing_never_crashes`, `test_git_hooks_without_git_never_crash`, `test_a_payload_of_another_shape_never_crashes`, `test_an_internal_error_never_reads_as_allow`, `test_a_hostile_state_file_blocks_nothing_and_makes_stop_verify` |
| R12 | `test_pre_tool_decides_within_the_latency_budget`, `test_a_command_too_long_to_judge_is_refused_not_timed_out` |
| R13 | `test_post_bash_reports_a_removed_mark_and_a_changed_signed_document`, `test_changed_documents_are_read_in_one_git_call` |
| R14 | `test_editing_a_signed_spec_or_adr_is_blocked_and_drafting_is_not`, `test_post_bash_reports_a_removed_mark_and_a_changed_signed_document`, `test_pre_commit_refuses_a_staged_change_to_a_signed_document`, `test_a_signed_document_is_signed_whatever_the_case_of_its_path` |
| R15 | `test_editing_a_signed_spec_or_adr_is_blocked_and_drafting_is_not`, `test_an_agent_cannot_create_a_numbered_document_without_the_mark` |

End to end: `scripts/e2e.sh` exercises the three Claude Code hook paths and the two git hooks in
a generated project (`CLAUDECODE=1 git commit` of a protected file refused, the human's passes).

## Data contracts and ownership
<!-- What two sessions (or two agents) must agree on BEFORE writing code. Omitting explicit data
     structures is what breaks parallel work (docs/method.md, section 3.1). -->
- Types / schemas: the Claude Code hook payload on stdin, read as UTF-8 bytes, a JSON object with `tool_name: str`, `tool_input: {file_path | notebook_path | command | content | old_string, new_string, replace_all | edits}`, `cwd: str`, `stop_hook_active: bool`; the `Stop` environment answer on stdout, exactly one JSON object `{"decision": "block", "reason": str, "systemMessage": str}`; `[tool.rumbo].agent_env: list[str]` in the `pyproject.toml` of `HEAD`; the guard's state, one JSON object in the file `git rev-parse --git-path rumbo-guard-head` (inside the git directory, never in the working tree) with the keys `head` (HEAD before the last shell command), `green` (the last HEAD a Stop recorded as green), `local` (a digest of `.claude/settings.local.json` without `permissions`), `hidden: {path: digest}` and `unverified` (a state that could not be decoded, until a green Stop).
- Signatures: `main(argv: list[str] | None) -> int` with `argv[0]` in `pre-tool | post-tool | stop | pre-commit | commit-msg <file>`; `check_edit(tool_name, tool_input, root) -> str | None`; `check_bash(command) -> str | None`; `record_pre_shell(root) -> None`; `check_post_shell(root) -> str | None`, whose working-tree part, which Stop also runs, is `check_post_bash(root, hidden=()) -> str | None` and whose part (b) is `check_commits(root, old, new) -> str | None`; `check_stop(payload, root) -> tuple[int, str]`; `check_pre_commit(root) -> str | None`; `check_commit_msg(root, message_file) -> str | None`; `actor(root) -> "agent" | "human" | "unknown"`; `agent_env_names(root) -> tuple[str, ...]`; `is_protected(rel) -> bool`. No `Exception` escapes `main` (R11).
- Shared invariants: exit 0 lets the action through; exit 2 blocks a Claude Code event with the reason on stderr; exit 1 refuses a git hook; exit 0 plus JSON on stdout is the environment route of `Stop`; the runner's exit codes are 0 green, 1 red, 3 environment (ADR-0006; ADR-0018: only when a tool could not start); `is_protected` (`PROTECTED_FILES`, `PROTECTED_PREFIXES`, `SHADOW_CONFIGS`, the `.env` names) is the single test of a protected path inside the project at the edit, after a command, at Stop and at the commit, and `BASH_RULES` the single list for every tool of `SHELL_TOOLS`; a missing state file is an empty state, and one that cannot be read or decoded is unknown, never empty (nothing is reported from it, Stop verifies; an unreadable one is not saved over, an undecodable one is repaired by the next save); each hook changes only its own keys, and a read or a replace that fails is retried; the script is standard-library only and identical in every project.
- Files owned by this spec (one writer): `src/rumbo/sensors/guard.py`, `tests/test_guard.py`, the two `rumbo-guard` hooks in `src/rumbo/templates/dot_pre-commit-config.yaml.tmpl`, the `hooks` block of `src/rumbo/templates/dot_claude/settings.json` (its matchers name the tools of `EDIT_TOOLS` and `SHELL_TOOLS`).
- Interfaces touched that other specs depend on: the runner's exit codes and `--brief` output (F-0002); the approval mark and the agent-authorship check of the sensor (F-0003); `EDIT_TOOLS`, `SHELL_TOOLS` and `agent_env_names`, which `src/rumbo/scaffold.py` imports (F-0004).

## Evals (LLM products)
<!-- case IDs, threshold -->
Not applicable: the guard calls no model.

## NFR
`PreToolUse` decision p95 < 100 ms (R12); a command of 150 000 characters is refused in < 2 s
(the test of R12) · `PostToolUse` p95 < 500 ms (ADR-0024 measured on this repository about
25 ms before a command and 120 ms after it, plus the interpreter's start) · 0 network calls ·
0 dependencies · cost 0 USD.

## Out of scope
Live (pre-edit) hooks for agents other than Claude Code (Cursor's protocol is in beta, ADR-0008);
stopping an agent that unsets its own marker outside Claude Code (CI and CODEOWNERS are the
binding layer); protecting files the project adds to its own harness (the list is fixed).
Gemini CLI, which turns git hooks off in the shell it runs (an empty hooks path through
`GIT_CONFIG_*`), so the git hooks never see its commits (ADR-0021); agents with no confirmed
environment marker (Aider, Amp) in a clone the human declared.
What the rules of R3 do not read (they are heuristics: a script that writes a protected file
is seen after the command, R4); the state file, which a shell can rewrite like everything in a
clone (ADR-0024); a commit made by a background process after the command returned, or between
the two hooks of a parallel command (not judged; Stop still verifies its code); a hidden change
once reported (it does not block Stop); entries an agent adds to `permissions` in an ignored
`.claude/settings.local.json`; a new ignored `.env` file or file under `infra/` or `.github/`;
`conftest.py` (the anti-cheat sensor's, ADR-0023); a numbered document written without the
mark by a shell command (caught at the commit, ADR-0025); a project in a sub-directory of its
repository at the `pre-commit` hook, which reads paths from the top of the repository, so
`PROTECTED_FILES`, the tables of `pyproject.toml` and the numbered documents of that project
do not match there.

## Questions resolved while interrogating the spec
- A shell command that strips the template mark is now reported after the command, not only
  refused at the commit (R13, ADR-0017).
- An undeclared human on a fresh clone is treated as an agent, on purpose (R9, fail-closed):
  one `git config rumbo.human true` per clone; the README says so next to the clone steps.
- A signed ADR or spec is read-only for the agent; drafting stays free under the mark, and an
  agent's numbered document without it is refused (R7, R14, R15, ADR-0017, ADR-0025).
