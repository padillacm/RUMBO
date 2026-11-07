# ADR-0009: A fix lane in ANCLA, recognised by commit types and held to its own gate rules
Status: accepted    Date: 2026-11-07

## Context
Every change goes through the full ANCLA loop: read the spec, tests that cite `F-NNNN.Rk`, two
approval pauses, gate G4. spec-kit, BMAD and GSD show what happens when a five-line bug costs the
same ritual as a subsystem: people skip the framework for daily work and the specs drift from the
code. RUMBO needs a short lane that keeps what makes it safe (a red test first, the runner, the
sensor, a small diff) and drops what a bug fix does not need (a new spec, two pauses).

## Drivers (non-functional requirements)
No new metadata to remember; the lane is recognised mechanically from what the repository
already records; it can never be used to smuggle a feature; the spec traceability check keeps
running; the human still approves once, at the PR.

## Options considered
1. Recognise the lane by branch prefix (`fix/`): not in the history, tools name branches.
2. Recognise it by a commit trailer (`Lane: fix`): explicit but one more thing to forget.
3. Recognise it by Conventional Commit types, already mandatory: a PR is in the fix lane when
   every commit in `base..HEAD` has a type other than `feat` and none carries the breaking
   marker `!`.

## Decision and rationale
Option 3. The contract:

**Detection** (in `rumbo gate G4`): the subjects of `git log --format=%s <merge-base>..HEAD`.
The lane is `fix` when there is at least one commit and every subject matches
`^(fix|test|refactor|chore|docs|ci|perf|style|build)(\([^)]*\))?:` (no `!` before the colon).
Any `feat` commit, any breaking marker or any non-conforming subject means the normal lane. The
G4 report names the lane in its heading.

**Fix-lane rules**, added to G4 on top of the usual ones (small diff, spec requirements covered,
sensor, runner):
1. `fix lane: regression test added`: `git diff --numstat <merge-base>` has at least one file
   under the tests directory (`[tool.rumbo].tests`, default `tests`) with added lines.
2. `fix lane: diff ≤ 150 lines` (lockfiles excluded), half the normal limit.
3. `fix lane: no new runtime dependency`: `[project].dependencies` in `pyproject.toml` at HEAD
   adds nothing to the list at the merge base (normalised by name).
The normal-lane check "every spec requirement is referenced by a test or eval case" is unchanged
in both lanes.

**The command** `/rumbo-fix <bug>` (`.claude/commands/rumbo-fix.md`), one pause instead of two:
1. Reproduce: write the failing regression test first; its docstring names the bug (issue, trace
   id or report) and the spec requirement `F-NNNN.Rk` when one exists. Run it and show it red.
2. Fix: the minimal change, then refactor. `uv run python scripts/rumbo_check.py --brief` green,
   then the `sensor` step.
3. Stay in lane: diff ≤ 150 lines, commits `fix:`/`test:`/`refactor:` only, no new dependency.
   If the fix needs a change to a data contract, a public signature or a requirement described
   in a spec, stop and switch to `/rumbo-ancla` with that spec: that is a feature.
4. Close: three-sentence summary, the runner's summary line, `rumbo gate G4`, and the guided
   review. The human approves at the PR.

**Prose.** RUMBO.md (Part C, phase B) gets a "Fix lane" paragraph; AGENTS.md of the generated
project gets one line in the per-task flow pointing at `/rumbo-fix`; the README mentions it.

## Consequences (+/−)
+ A bug costs a red test, a small diff and one approval, not a spec and two pauses.
+ The lane cannot carry a feature: one `feat:` commit or one breaking marker returns the PR to
  the normal rules, and the regression-test rule forbids an untested patch.
+ Zero new metadata: Conventional Commits already carry the information.
− Semantic drift is invisible to G4: a fix that changes behaviour a spec describes without
  updating the spec passes the mechanics. The command tells the agent to stop in that case; the
  human reviewer remains the sensor, and the review command's traceability table helps.
− A team that does not keep Conventional Commits clean never gets the lane (by design).

## Fitness function
`tests/test_cli.py`: lane detection (all-fix, mixed with `feat`, breaking marker, empty range,
non-conforming subject), the three fix-lane results green and red, the heading naming the lane.
`tests/test_scaffold.py`: `rumbo-fix.md` is generated and `rumbo doctor` accepts the project.
`tests/test_fitness.py` keeps requiring a fitness section in every ADR.

## Reversibility: high
Remove the lane detection and its three results; the command file is prose.
