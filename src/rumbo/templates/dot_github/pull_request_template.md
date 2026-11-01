<!-- RUMBO pull request. The agent may draft the first section; the HUMAN ticks the checklist. -->
## What and why
Spec: `specs/F-NNNN-….md` · ADRs touched: none

Three sentences written by the human after reading the diff, not the agent's summary
(explanation test).

## ANCLA checklist (gate G4)
- [ ] Tests were written first, name their requirement (`F-NNNN.Rk`) and were approved by the human before the code
- [ ] `make check` green locally; CI is the sensor, not the proof
- [ ] No test deleted or weakened (`make sensor`); if one had to change, a human-authored commit says `[tests-change-approved]` and why
- [ ] Diff ≤ ~300 lines (lockfiles excluded); structural and behavioral commits separated
- [ ] Only the files owned by the spec were touched; interfaces other specs depend on were updated in their "Data contracts" section
- [ ] New dependencies verified with `rumbo verify-dep` and listed below
- [ ] `make rumbo ARGS="trace"` shows no missing requirement
- [ ] Reviewed with `make review` (guided tour); second review by a fresh session (`/rumbo-review`) for mvp and above

## New dependencies
none

## Risks and rollback
How this is undone if it misbehaves in production (feature flag, revert, data migration back).
