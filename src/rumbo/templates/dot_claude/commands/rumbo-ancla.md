---
description: Phase B — one task with the ANCLA loop (Anchor, Navigate, Close, Limit, Learn)
argument-hint: <task>
---
Implement this task with **ANCLA** (`RUMBO.md`, Part C "The RUMBO cycle", phase B "Build with ANCLA"): $ARGUMENTS
If no task was given, ask for one before doing anything.

1. **Anchor:** read the spec and the relevant ADRs. Write the acceptance tests first (Hypothesis for domain invariants). Run them and show that they **fail**. 🛑 Stop: the human approves the tests.
2. **Navigate:** propose a short plan (files, tests, risks, new dependencies). 🛑 Wait for approval.
3. **Close the loop:** minimal code to pass, then refactor. Iterate until `uv run python scripts/rumbo_check.py --brief` is green.
4. **Limit:** diff ≤ ~300 lines; structural commits separated from behavioral ones. Verify new dependencies with `rumbo verify-dep <pkg>`. Run `uv run python scripts/rumbo_check.py sensor`.
5. **Learn:** if a mistake repeated, propose a sensor or a line in AGENTS.md.

Close with: a 3-sentence summary, new dependencies, the summarized check output, the checklist of `rumbo gate G4`, and `rumbo tour origin/main --out docs/reviews/` so the human gets a guided review. Remind the human to run `/rumbo-review` in a clean session or another model and to do the explanation test.
