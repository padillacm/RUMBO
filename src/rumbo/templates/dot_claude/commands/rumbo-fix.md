---
description: Phase B — a bug fix in the ANCLA fix lane (red regression test, minimal fix, one approval)
argument-hint: <bug>
---
Fix this bug in the **fix lane** of ANCLA (`RUMBO.md`, Part C "The RUMBO cycle", phase B "Build with ANCLA", paragraph "Fix lane"): $ARGUMENTS
If no bug was given, ask for one before doing anything. The lane is for a defect in behaviour that already exists; a new behaviour is a feature and goes through `/rumbo-ancla`.

1. **Reproduce:** write the failing regression test first, under the tests directory. Its docstring names the bug (issue, trace id or user report) and the spec requirement `F-NNNN.Rk` it belongs to when one exists. Run it and show that it **fails**.
2. **Fix:** the minimal change that makes it pass, then refactor. Iterate until `uv run python scripts/rumbo_check.py --brief` is green, then run `uv run python scripts/rumbo_check.py sensor`.
3. **Stay in lane:** diff ≤ 150 lines (lockfiles excluded); commits typed `fix:`, `test:` or `refactor:` only (one `feat:` commit or one `!` breaking marker returns the PR to the normal lane and its rules); no new dependency. If the fix needs a change to a data contract, a public signature or a requirement described in a spec, 🛑 stop and switch to `/rumbo-ancla` with that spec: that is a feature, not a fix.
4. **Close:** a 3-sentence summary (the bug, its cause, the fix), the runner's summary line, the checklist of `rumbo gate G4` (its heading says `lane: fix` and it adds the three fix-lane checks), and `rumbo tour origin/main --out docs/reviews/` so the human gets a guided review.

One pause instead of two: the human approves at the PR.
