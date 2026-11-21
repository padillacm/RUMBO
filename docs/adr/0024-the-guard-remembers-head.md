# ADR-0024: The guard remembers HEAD: a commit that skipped the hook is judged afterwards, and Stop verifies committed code
Status: accepted    Date: 2026-11-16

Completes ADR-0005 (the hard rules as hooks), ADR-0006 (the runner at Stop) and ADR-0008 (the
guard as a git hook). All three stand; spec F-0001 (R4, R5) needs the new behaviour.

## Context
After a shell command and at Stop the guard asks `git status` what changed. A commit leaves a
clean tree, so both layers are blind to what was just committed. The audit before v0.4.1 used it
twice.

1. **A commit made without `pre-commit`** (finding G5). `git stash` followed by `git cherry-pick
   -m 1 stash@{0}`, `git commit-tree` plus `git update-ref`, a hook made non-executable, an
   alias, `GIT_CONFIG_*`: each creates a commit the hook never saw. Measured: a protected
   Makefile and the approval mark reached HEAD, and the checks after the command and at Stop
   answered "nothing changed". The shell rules are regexes; they cannot list every spelling.
2. **Red code, committed** (finding G12). Stop ran the verification only when code files
   differed from the last commit. Commit the red code, then stop: nothing ran.

The same blindness covers what `git status` is told not to look at (finding G10): a tracked file
under `--skip-worktree` or `--assume-unchanged`, a new file hidden by an ignore rule, and
`.claude/settings.local.json`, which is ignored on purpose and can switch the hooks off.

## Drivers (non-functional requirements)
Deterministic: the layer must not depend on recognising the command. Cheap: the check after a
shell command stays well under 500 ms on a normal repository (measured on this one: about 25 ms
before the command and 120 ms after it, plus the interpreter's start). Fail-safe: a missing or
broken state, or a git failure, is "no information", never a crash and never a false refusal.
Nothing new in the working tree. Standard library only.

## Options considered
1. **More regexes.** Rejected: it is the finding. They stay as the friendly early message.
2. **Compare with the upstream branch or `origin/main`.** Rejected for the hook: every commit of
   a long branch would be judged again at each command, and a clone without that ref has no
   base. It is what the sensor does in CI, where the base is known.
3. **A `post-commit` or `reference-transaction` git hook.** Rejected: the bypass disables hooks.
4. **Remember HEAD in the guard itself (chosen).**

## Decision and rationale
- **State.** One small JSON file inside the git directory, at `git rev-parse --git-path
  rumbo-guard-head` (per worktree, never in the working tree, never committed). Keys: `head`
  (HEAD before the last shell command), `green` (the last HEAD a Stop saw green), `local`
  (a digest of `.claude/settings.local.json`), `hidden` (the protected files `git status`
  cannot see, each with a digest) and `unverified` (set when what was on disk could not
  be decoded: the next Stop runs the check whatever `green` says).
- **The file is shared by parallel hooks.** Each hook changes its own keys on a fresh copy; a
  read or a replace that fails is retried. A state that stays unreadable is unknown, not
  empty: nothing is reported from it, nothing is saved over it, and Stop verifies whatever
  changed. A state that cannot be decoded is treated the same and repaired by the next save.
- **Before a shell command** (`pre-tool`, every tool of `SHELL_TOOLS`): record HEAD, the
  digest and the hidden files. One `git rev-parse` and one `git ls-files`.
- **After it** (`post-tool`): if HEAD is not the recorded one, judge `git diff --no-renames
  --name-status -z <old> <new>` by the rules of a staged change: protected files (ADR-0023), the
  tables of `pyproject.toml` from the blobs of the two commits (ADR-0019), signed documents
  (ADR-0017), removed marks, and numbered documents created without the mark (ADR-0025). The
  report names both commits and how to undo (`git reset --soft <old>`); it is made once, then
  the new HEAD is recorded.
- **A HEAD moved by the human** (a pull, a merge, another branch checked out while the command
  ran) is judged the same way: the guard cannot tell who made a commit. The message says so,
  and that nothing needs undoing in that case. A report after the fact blocks nothing.
- **Stop.** Besides uncommitted code, the verification runs when code files differ between
  `green` and HEAD. `green` is written when the runner is green, and when there was nothing to
  verify. With no recorded value, or one git cannot compare, Stop records HEAD and behaves as
  before. A red run does not move `green`: the next Stop verifies again.
- **What `git status` cannot see** is looked up directly before and after each command: the
  tracked protected files and signed documents that carry an index flag are hashed against the
  index; the names that change the local verification by existing (the Makefile family,
  `pyproject.toml`, the sensors, the files of ADR-0023) are searched in every directory that
  holds tracked files. Only what appears or changes while the command runs is reported, once:
  a file that was there before (the human's own ignored `pyrightconfig.json`) never is, and
  with no baseline the first call records and stays silent. Stop does not judge these files,
  so one of them can never stand between Stop and the runner. `.env*` files are local by
  design and are not reported. `.claude/settings.local.json` is reported only when its content
  outside `permissions` changes while a command runs: the human's "always allow" writes that
  list at that moment.
- **No commit yet, and whose change it is.** A repository with no commit on any ref has
  nothing to protect: the check after a command and Stop report nothing there, like the git
  hook. Every report about the working tree offers both routes: revert the change if the agent
  made it; if the human made it (`rumbo update`, an edit by hand), ask them to commit it.

## Consequences (+/−)
+ A commit that skipped the hook is reported whatever command produced it, with a clean tree.
+ Committing red code no longer hides it from Stop.
+ The cost is two git calls before a command and three or four after it.
− A change hidden from `git status` is reported once, after the command that made it; it
  does not block Stop afterwards, as a visible change to a protected file does.
− A legitimate pull or checkout that brings the human's change to a protected file produces
  one report the agent has to read and set aside.
− The state can be rewritten from the shell, like everything in a clone: this layer raises the
  cost of a bypass, it does not bind. CODEOWNERS and the sensor in CI are the binding layers.
− A commit made by a background process after the command returned, or between the two hooks
  of another command running in parallel, is recorded as the starting point of the next
  command and not judged. Stop still verifies its code.
− An agent that adds its own entries to `permissions` in the local settings is not reported.
− A new ignored file under `infra/` or `.github/` is not reported: it verifies nothing locally
  and cannot be committed while ignored.

## Fitness function
`tests/test_guard.py::test_a_commit_that_skipped_the_hook_is_judged_after_the_command`,
`::test_stop_verifies_code_committed_since_the_last_green_head`,
`::test_protected_files_hidden_from_git_status_are_found`,
`::test_a_hidden_file_that_was_there_before_is_never_reported`,
`::test_local_settings_changed_during_a_command_are_reported`,
`::test_a_state_that_cannot_be_read_is_unknown_not_empty`,
`::test_a_repository_with_no_commit_has_nothing_to_protect_yet`.

## Reversibility: high
