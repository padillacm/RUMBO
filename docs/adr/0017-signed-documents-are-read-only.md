# ADR-0017: A signed ADR or spec is read-only for the agent, and a removed template mark is caught when it happens
Status: accepted    Date: 2026-11-09

## Context
Interrogating spec F-0001 (the guard) found two holes. First, the template mark is the human's
signature on a document, and the guard stops an edit tool from removing it, but a shell command
(`sed -i '1d' docs/product/prd.md`) removes it unnoticed until the git hook refuses the commit.
Second, `specs/` and `docs/adr/` are not protected: an agent can rewrite an accepted ADR or a
signed spec, although the ADR template itself says accepted ADRs are immutable and are changed
by a new ADR that supersedes them. Protecting the two folders outright would break the flow:
the agent drafts new ADRs and specs all the time.

## Drivers (non-functional requirements)
The signature already exists (the mark and its removal by the human): no new marker, no new
file; drafting stays free; the rule holds at the three moments the guard already watches (the
edit, after a shell command, the commit); the human is never blocked; the `PreToolUse` budget
(100 ms) is kept for ordinary edits.

## Options considered
1. Leave both to review (status quo).
2. Protect `specs/` and `docs/adr/` like the harness files (the agent could not draft).
3. Treat "committed without the template mark" as signed: a numbered ADR or spec in that state
   is read-only for the agent; new documents and documents that still carry the mark stay
   editable. And compare marks against `HEAD` after every shell command, not only at commit.

## Decision and rationale
Option 3. The contract, in `scripts/rumbo_guard.py`:

**Signed.** A file matching `specs/NNNN-*.md` or `docs/adr/NNNN-*.md` (not the `0000-`
templates) is signed when its version in `HEAD` exists and does not contain the template mark.
A file that is new (absent from `HEAD`) or whose `HEAD` version carries the mark is not.

**At the edit** (`PreToolUse`, edit tools): an edit of a signed document is blocked with the
reason "signed by the human: propose the change, or write a new document that supersedes it".
This costs one `git show` and only for paths that match the pattern; every other edit is decided
without a subprocess.

**After a shell command** (`PostToolUse`, and `Stop`, which already runs the same check): for
every `.md` file that differs from `HEAD`, the guard reports (a) a document whose mark was in
`HEAD` and is gone from the working tree, and (b) a signed document that was modified or
deleted, next to the protected files it already reports. The message says to revert them, or to
ask the human to commit the change if the human made it.

**At the commit** (git `pre-commit`, agent actor): a staged modification or deletion of a signed
document is refused, like a staged protected file. The root commit stays exempt (ADR-0008).

**Unchanged.** The human signs by removing the mark and committing; the human edits a signed
document by hand and commits it (the git hooks let the declared human through). An unknown
actor is still treated as an agent (ADR-0008): the README says so next to the clone
instructions, because the first hand-made commit on a fresh clone meets that rule.

## Consequences (+/−)
+ A signature means something mechanical: after it, the agent proposes and the human writes.
+ Mark removal through the shell is seen at the next hook, not at the commit.
+ Drafting is untouched: new ADRs and specs, and those still under the mark, are editable.
− Fixing a typo in an accepted ADR is now the human's job, or a new ADR's.
− While the human has uncommitted edits to signed documents, the agent's `PostToolUse` keeps
  reporting them, as it already does for protected files; committing them ends it.
− One `git show` per edit of a numbered ADR or spec.

## Fitness function
`tests/test_guard.py`: an edit of a signed spec and of a signed ADR is blocked, a new one and a
marked one are not, the `0000-` template is not; after a shell command a removed mark and a
modified or deleted signed document are reported and a new document is not; the git hook
refuses a staged change to a signed document from an agent and lets the human's through.
`rumbo trace` links them to F-0001.R13–R15.

## Reversibility: high
One pattern and three checks; removing them returns to ADR-0008's behaviour.
