# ADR-0025: An agent cannot create a numbered ADR or spec without the template mark
Status: accepted    Date: 2026-11-16

Completes ADR-0017 (a signed ADR or spec is read-only for the agent). ADR-0017 stands; its
sentence "new documents stay editable" is narrowed here, and spec F-0001 (R7, R14, R15) with it.

## Context
ADR-0017 defines the human's signature as an absence: a numbered ADR or spec (`specs/NNNN-*.md`,
`docs/adr/NNNN-*.md`) committed **without** the `rumbo:template` mark is signed. The guard
refused to remove a mark that was in HEAD, and let any new document through, marked or not.

The audit before v0.4.1 (finding G14) joined the two: an agent writes `docs/adr/0031-x.md`
without the first line and commits it. Nothing was removed, so nothing is refused. From that
commit on the document is, by the definition above, signed by the human: the gates read a
document without the mark as complete, and the guard itself now protects it from correction.

A second gap of the same family (finding G13): the edit rule looked for the whole mark in
`old_string`. An Edit that replaced only a part of it (`<!-- rumbo:temp`) removed the mark
unseen; the later layers caught it, the first one did not.

## Drivers (non-functional requirements)
A signature must be something only the human can produce. Drafting stays free: no approval step
to start or to edit a document under the mark. No subprocess for an ordinary edit (F-0001.R12).

## Options considered
1. **A positive signature** (a `Signed-off-by` line, a signed commit). Rejected for now: it
   changes what every existing project and gate reads; a candidate for a later ADR.
2. **Count as signed only what a human committed** (`rumbo.human`, the commit author).
   Rejected: the actor of a past commit is not recorded anywhere the guard can trust.
3. **Refuse the creation without the mark (chosen).** The mark is already how `rumbo new`
   starts a document; the rule makes it the only way an agent can.

## Decision and rationale
- **Edit tools.** A `Write` to a numbered ADR or spec whose content lacks the mark is blocked,
  whether the file exists or not. The reason names `rumbo new adr|spec`.
- **Commit.** For an agent, `pre-commit` refuses a numbered ADR or spec that HEAD does not have
  and whose staged blob lacks the mark. The same holds on a branch without commits in a
  repository that has them (`git checkout --orphan`): only the first commit of a repository
  with no commit at all is exempt.
- **After the fact.** When HEAD moved during a shell command (ADR-0024), a numbered document
  added between the two commits without the mark is reported.
- **The human** creates, signs and commits a document in one step if they wish: the hook does
  not bind a declared human, and edits by hand do not pass through the edit rule.
- **The mark is not removed by parts.** Edit and MultiEdit are simulated on the file's current
  content, in order and honouring `replace_all`; the call is blocked when the file had the mark
  and the result does not. An edit that names the whole mark and drops it is blocked even when
  the file cannot be read.
- The `0000-` templates and every other document (`docs/brief.md`, notes) are outside the rule.

## Consequences (+/−)
+ "Committed without the mark" again means "the human removed it".
+ An agent that forgets the first line learns it at the Write, not at a gate weeks later.
− A document the agent created by a shell command (`cat > specs/0031-x.md`) is caught at the
  commit, not when it is written.
− The simulation follows the edit tools' documented behaviour (first occurrence, or all with
  `replace_all`); a tool that matched differently would be judged by the later layers only.
− In a clone where a human declared `rumbo.human`, an agent without a known marker is still
  taken for the human (the accepted risk of ADR-0021), here as everywhere.

## Fitness function
`tests/test_guard.py::test_an_agent_cannot_create_a_numbered_document_without_the_mark`,
`::test_an_edit_is_simulated_to_see_whether_the_mark_survives`.

## Reversibility: high
