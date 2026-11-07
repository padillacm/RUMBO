<!-- rumbo:template — delete this line when the document is complete. Only the HUMAN removes it; `rumbo gate` checks it. -->
# Eval plan

## Target behaviors

## Failure taxonomy (with counts from the error analysis)
| Failure | Count | Evaluator (code / LLM judge with TPR/TNR) |
|---|---|---|

LLM judges: rubric, calibration file (`evals/calibration/<name>.jsonl`, ≥30 human-labeled rows), TPR/TNR/kappa
from `graders.judge.calibrate` and the date of the last calibration. Below 0.8 the judge does not count.

## Dataset
Sources, how it grows (production traces become cases). Minimum real cases by rigor:
20 (prototype), 50 (mvp), 100 (production); cases tagged `example` do not count.

## CI thresholds
- smoke per PR (offline, recorded fixtures): pass^1 ≥ …
- full suite nightly against the real model: pass^k ≥ … (k = 3 for consistency)

## Online metrics

## Error-analysis cadence
Weekly ritual: the human reviews 20–30 traces (open coding); the agent proposes categories.
