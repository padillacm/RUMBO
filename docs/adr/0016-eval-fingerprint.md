# ADR-0016: The eval runner fingerprints prompts and the judge, and warns when fixtures or calibration may be stale
Status: accepted    Date: 2026-11-08

## Context
The smoke suite runs offline against recorded fixtures, and the LLM judge is calibrated once
against human labels. Both drift silently: a prompt in `src/<pkg>/prompts/` changes and the
fixture keeps answering as before, hiding a regression (fixture drift); the judge's rubric or
model changes and the calibration figures no longer describe it (judge drift). The community
study names both as the quiet failure mode of eval suites built on static fixtures. Nothing in
the harness notices today.

## Drivers (non-functional requirements)
Deterministic, dependency-free fingerprints; a warning the weekly trace-review ritual can act
on, never a red check that forces re-recording fixtures on every prompt edit; the data in every
report so drift is visible in history; nothing for projects without prompts or judge beyond a
stable entry fingerprint.

## Options considered
1. Document the risk and leave it to the ritual (status quo).
2. Fingerprint prompts and judge in every report; record them on live runs; warn offline when
   they differ from the last live run.
3. Like 2 but fail the `evals` step on a mismatch: friction on every prompt change, and the
   fixture re-recording becomes a reflex instead of a decision.

## Decision and rationale
Option 2. The contract, in `evals/run_evals.py`:

**Fingerprint.** `fingerprint(root) -> dict[str, str]` with three sha256 hex digests:
`prompts`, over the sorted relative paths and contents of every file under
`src/<pkg>/prompts/` (empty string hashed when the directory is absent); `judge`, over the
content of `evals/graders/judge.py` (empty when absent); `entry`, over the `--entry` reference.
Every report written to `evals/reports/` carries `"fingerprint": {...}`.

**Record on live runs.** A run with `--live` writes `evals/fingerprint.json`:
`{"prompts": ..., "judge": ..., "entry": ..., "recorded": "<ISO date>", "suite": "<suite>"}`.
The file is committed with the fixtures it describes.

**Warn offline.** A run without `--live` that finds `evals/fingerprint.json` compares the
`prompts` and `judge` digests. On a difference it prints, after the summary line and before the
exit code is decided, one warning per changed part: `WARNING: prompts changed since the last
live run (<recorded>): the recorded fixtures may be stale; re-record with --live` and
`WARNING: the judge changed since the last live run (<recorded>): re-calibrate before trusting
its verdicts (see evals/calibration/)`. The exit code is unchanged by the warnings. Without the
file nothing is printed.

**Data pipelines.** Without `llm` there are no prompts and no judge: the fingerprint carries
`entry` and the empty-input digests, the file is still written on `--live`, and the warnings
can only fire if someone adds a `prompts/` directory later.

## Consequences (+/−)
+ Drift becomes visible in every report and shouted at the first offline run after it.
+ The decision to re-record or re-calibrate stays with the human, who now has the date.
+ Zero dependencies; a few lines of `hashlib`.
− A warning can be ignored; the ritual is the sensor for that.
− Renaming a prompt file changes the digest even if its content did not: acceptable, a rename
  is a change worth a look.

## Fitness function
`tests/test_async_templates.py` (the file that already exercises the rendered runner): the
report carries the three digests; `--live` with a stub entry writes `evals/fingerprint.json`;
an offline run after changing a prompt file prints the prompts warning and keeps exit 0; after
changing `judge.py` prints the judge warning; without the file prints nothing; a `data`
project renders a runner that fingerprints with empty prompts and judge. The e2e script's LLM
flag sets run the smoke suite once with `--live` against the fixture-only entry (no credential
needed: the skeleton answers `ping` without a model), then touch a prompt and require the
warning in the offline output.

## Reversibility: high
One function and two blocks in the runner; the file is data.
