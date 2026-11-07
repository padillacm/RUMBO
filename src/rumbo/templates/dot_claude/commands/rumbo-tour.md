---
description: Build a guided, pedagogical review of this branch for the human (rumbo tour + real questions)
argument-hint: [base ref, default origin/main]
---
Prepare a **guided review** of the current branch for the human reviewer. Base: $ARGUMENTS (default `origin/main`).

1. Run `make review` (it calls `rumbo tour`), which writes `docs/reviews/<date>-<sha>.md` with, per commit: intent, kind, risk hotspots, reading order, verification commands and three template questions.
2. Improve that file **from the diff, never from the commit messages**: for each commit replace the three template questions with real explanation-test questions (what breaks if X is removed; which test proves Y; why this structure and not Z). Keep the answers inside `<details>` so the human answers first.
3. Flag any commit that mixes structural and behavioral changes, exceeds the ~300-line budget, or touches CI, the Makefile, secrets, dependencies or tests.
4. Tell the human how long the review should take (≈ 300 lines per sitting) and where to start. Do not approve anything yourself.
