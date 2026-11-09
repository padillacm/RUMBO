---
description: Evaluate a RUMBO gate (G1..G5) and summarize what is missing
argument-hint: G1 | G2 | G3 | G4 | G5
---
Evaluate RUMBO gate **$ARGUMENTS**. If no gate was given, ask which one.

1. Run `rumbo gate $ARGUMENTS`. If the CLI is unavailable, use the checklist in `RUMBO.md`.
2. Summarize in ≤5 lines: automatic checks in red and what it takes to turn them green.
3. List the human checklist items. **Do not tick them**: the human signs the gate (`rumbo decision "$ARGUMENTS signed"`).
