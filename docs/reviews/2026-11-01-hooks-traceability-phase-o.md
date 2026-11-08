# Guided review: `f6f8410..HEAD`

10 commit(s), 2351 changed lines (lockfiles excluded) → about **8 sitting(s)** of ≤300 lines. Review in order; stop when the budget runs out and come back fresh.

How to use this: read the risk hotspots first, then the files in the given order, run the verification commands, and only then answer the three questions **without looking at the hints**. If you cannot answer, do not approve yet: that is the explanation test of RUMBO's human compass.

## 1. feat(templates): Claude Code hooks turn the AGENTS.md hard rules into sensors

`20e01dc633` · Claude · kind: **behavioral** · 427 lines, 5 files

> scripts/rumbo_guard.py (canonical copy in src/rumbo/sensors/guard.py, refreshed by
> rumbo update) is wired by .claude/settings.json as a PreToolUse hook on
> Edit/Write/MultiEdit/NotebookEdit/Bash and as a Stop hook:
>
> - blocks agent edits to the protected files (Makefile, pyproject.toml, CI, infra/,
>   pre-commit config, the anti-cheat sensor, the guard, the evals runner, .env*);
> - blocks removing the <!-- rumbo:template mark, writing [tests-change-approved],
>   --no-verify, force pushes, git reset --hard, disabling hooks, rm -rf on the root

**Risk hotspots** (read these first):
- none detected by path rules (still read the tests)

**Reading order** (tests first, then what the subject names, then the largest):
1. `tests/test_guard.py` (test, new, +156/−0)
2. `src/rumbo/sensors/guard.py` (code, new, +204/−0)
3. `src/rumbo/templates/dot_claude/settings.json` (code, new, +60/−0)
4. `scripts/e2e.sh` (code, +4/−0)
5. `src/rumbo/scaffold.py` (code, +3/−0)

> Over budget (427 > 300): ask for a split, or review only the tests and sample the rest.

**Verify:**
```bash
git show --stat 20e01dc633
uv run pytest -q tests/test_guard.py
make check
git revert --no-commit 20e01dc633 && make test; git revert --abort
```

**Explanation test** (answer before approving):
- Which test or sensor proves that «feat(templates): Claude Code hooks turn the AGENTS.md hard rules into sensors» works? Point to the exact test.
  <details><summary>hint</summary>

  Tests touched here: tests/test_guard.py.

  </details>
- If the change to `src/rumbo/sensors/guard.py` were reverted, what observable behavior would change, and how would you notice?
  <details><summary>hint</summary>

  Run the revert command from the verification list. Author's stated intent: scripts/rumbo_guard.py (canonical copy in src/rumbo/sensors/guard.py, refreshed by

  </details>
- Name one alternative to this approach and why it was not chosen. Is that reason written down (commit body, ADR, decisions log)?
  <details><summary>hint</summary>

  If you cannot find the reason, the change carries intent debt: ask for an ADR or a line in docs/decisions.md before approving.

  </details>

## 2. feat(trace): requirement → test traceability (rumbo trace, gate G4)

`1cef3008fe` · Claude · kind: **behavioral** · 173 lines, 7 files

> A requirement is '- Rk (...): ...' under '## Requirements' of specs/NNNN-*.md and has the
> full id F-NNNN.Rk. A test or an eval case covers it by mentioning that id (docstring,
> comment, case id). rumbo trace prints the table (--strict exits 1 on a missing row);
> gate G4 adds the row 'every spec requirement is referenced by a test or eval case',
> a recommendation below production and required in production, measured even with
> --no-run. The spec template, AGENTS.md (tests name their requirement before the
> human approves them) and /rumbo-review (start from the generated table) follow.
>

**Risk hotspots** (read these first):
- none detected by path rules (still read the tests)

**Reading order** (tests first, then what the subject names, then the largest):
1. `tests/test_cli.py` (test, +40/−0)
2. `src/rumbo/trace.py` (code, new, +89/−0)
3. `src/rumbo/cli.py` (code, +21/−0)
4. `src/rumbo/gates.py` (code, +12/−0)
5. `src/rumbo/templates/AGENTS.md.tmpl` (code, +4/−2)
6. `src/rumbo/templates/specs/0000-template.md` (doc, +2/−1)
7. `src/rumbo/templates/dot_claude/commands/rumbo-review.md` (doc, +1/−1)

**Verify:**
```bash
git show --stat 1cef3008fe
uv run pytest -q tests/test_cli.py
make check
git revert --no-commit 1cef3008fe && make test; git revert --abort
```

**Explanation test** (answer before approving):
- Which test or sensor proves that «feat(trace): requirement → test traceability (rumbo trace, gate G4)» works? Point to the exact test.
  <details><summary>hint</summary>

  Tests touched here: tests/test_cli.py.

  </details>
- If the change to `src/rumbo/trace.py` were reverted, what observable behavior would change, and how would you notice?
  <details><summary>hint</summary>

  Run the revert command from the verification list. Author's stated intent: A requirement is '- Rk (...): ...' under '## Requirements' of specs/NNNN-*.md and has the

  </details>
- Name one alternative to this approach and why it was not chosen. Is that reason written down (commit body, ADR, decisions log)?
  <details><summary>hint</summary>

  If you cannot find the reason, the change carries intent debt: ask for an ADR or a line in docs/decisions.md before approving.

  </details>

## 3. feat(templates): PR template with the ANCLA checklist, CODEOWNERS and SECURITY.md

`77c58a0cc5` · Claude · kind: **behavioral** · 92 lines, 6 files

> Generated projects get .github/pull_request_template.md (the G4 checklist where the
> human is already looking), .github/CODEOWNERS (placeholder owners for the sensors,
> specs and decisions; binding with branch protection) and SECURITY.md (reporting
> policy plus what the harness does by design). The framework repository gets its own
> three files.
>
> Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
> Claude-Session: https://claude.ai/code/session_01Bbtm72LBvaJ7x6iPXUrXMH

**Risk hotspots** (read these first):
- ⚠️ CI / workflows (can weaken every other sensor): .github/CODEOWNERS, .github/pull_request_template.md
- ⚠️ auth / security code: SECURITY.md, src/rumbo/templates/SECURITY.md.tmpl

**Reading order** (tests first, then what the subject names, then the largest):
1. `src/rumbo/templates/SECURITY.md.tmpl` (code, new, +23/−0)
2. `src/rumbo/templates/dot_github/pull_request_template.md` (doc, new, +22/−0)
3. `SECURITY.md` (doc, new, +17/−0)
4. `src/rumbo/templates/dot_github/CODEOWNERS` (code, new, +15/−0)
5. `.github/pull_request_template.md` (doc, new, +13/−0)
6. `.github/CODEOWNERS` (code, new, +2/−0)

**Verify:**
```bash
git show --stat 77c58a0cc5
make check
git revert --no-commit 77c58a0cc5 && make test; git revert --abort
```

**Explanation test** (answer before approving):
- Which test or sensor proves that «feat(templates): PR template with the ANCLA checklist, CODEOWNERS and SECURITY.md» works? Point to the exact test.
  <details><summary>hint</summary>

  Tests touched here: none — that is a finding in itself.

  </details>
- If the change to `src/rumbo/templates/SECURITY.md.tmpl` were reverted, what observable behavior would change, and how would you notice?
  <details><summary>hint</summary>

  Run the revert command from the verification list. Author's stated intent: Generated projects get .github/pull_request_template.md (the G4 checklist where the

  </details>
- Name one alternative to this approach and why it was not chosen. Is that reason written down (commit body, ADR, decisions log)?
  <details><summary>hint</summary>

  If you cannot find the reason, the change carries intent debt: ask for an ADR or a line in docs/decisions.md before approving.

  </details>

## 4. feat(templates): phase O skeleton — per-request budget, GenAI telemetry, ops runbook entries

`7a2bc3f27e` · Claude · kind: **behavioral** · 375 lines, 15 files

> LLM products get app/budget.py (Budget from RUMBO_BUDGET_* env vars, Meter that raises
> BudgetExceeded on tokens, cost or time) and app/telemetry.py (spans logged as JSON records
> with OpenTelemetry GenAI attribute names plus cost.usd; no dependency, an OTLP/Langfuse
> exporter is an adapter added later). entrypoint.answer runs every request inside a span and
> a meter and abstains with the reason when the budget is exceeded; tests cover both modules
> and the abstention. The runbook gains entries for budget spikes and provider outages, the
> SLO table gains cost, budget-abstention and online-accuracy rows, .env.example documents the
> budget variables and /rumbo-o explains the wiring. Gate G5 checks that both modules exist

**Risk hotspots** (read these first):
- none detected by path rules (still read the tests)

**Reading order** (tests first, then what the subject names, then the largest):
1. `src/rumbo/templates/tests/unit/test_budget.py.tmpl` (test, new, +43/−0)
2. `src/rumbo/templates/tests/unit/test_telemetry.py.tmpl` (test, new, +42/−0)
3. `src/rumbo/templates/tests/unit/test_entrypoint.py.tmpl` (test, +10/−1)
4. `tests/test_cli.py` (test, +16/−0)
5. `tests/test_scaffold.py` (test, +6/−0)
6. `src/rumbo/templates/src/__pkg__/app/telemetry.py.tmpl` (code, new, +86/−0)
7. `src/rumbo/templates/src/__pkg__/app/budget.py.tmpl` (code, new, +78/−0)
8. `src/rumbo/templates/docs/ops/runbook.md.tmpl` (code, new, +29/−0)
9. `src/rumbo/templates/src/__pkg__/app/entrypoint.py.tmpl` (code, +15/−1)
10. `src/rumbo/templates/docs/ops/runbook.md` (doc, deleted, +0/−14)
11. `src/rumbo/templates/dot_claude/commands/rumbo-o.md` (doc, +4/−3)
12. `src/rumbo/templates/docs/ops/{slos.md => slos.md.tmpl}` (code, +5/−0)
13. `src/rumbo/templates/dot_env.example.tmpl` (code, +5/−0)
14. `src/rumbo/gates.py` (code, +13/−0)
15. `src/rumbo/scaffold.py` (code, +4/−0)

> Over budget (375 > 300): ask for a split, or review only the tests and sample the rest.

**Verify:**
```bash
git show --stat 7a2bc3f27e
uv run pytest -q tests/test_cli.py tests/test_scaffold.py
make check
git revert --no-commit 7a2bc3f27e && make test; git revert --abort
```

**Explanation test** (answer before approving):
- Which test or sensor proves that «feat(templates): phase O skeleton — per-request budget, GenAI telemetry, ops runbook entries» works? Point to the exact test.
  <details><summary>hint</summary>

  Tests touched here: src/rumbo/templates/tests/unit/test_budget.py.tmpl, src/rumbo/templates/tests/unit/test_telemetry.py.tmpl, src/rumbo/templates/tests/unit/test_entrypoint.py.tmpl, tests/test_cli.py, tests/test_scaffold.py.

  </details>
- If the change to `src/rumbo/templates/src/__pkg__/app/telemetry.py.tmpl` were reverted, what observable behavior would change, and how would you notice?
  <details><summary>hint</summary>

  Run the revert command from the verification list. Author's stated intent: LLM products get app/budget.py (Budget from RUMBO_BUDGET_* env vars, Meter that raises

  </details>
- Name one alternative to this approach and why it was not chosen. Is that reason written down (commit body, ADR, decisions log)?
  <details><summary>hint</summary>

  If you cannot find the reason, the change carries intent debt: ask for an ADR or a line in docs/decisions.md before approving.

  </details>

## 5. feat(cli): eval add, metrics and update --diff

`5b657d6502` · Claude · kind: **behavioral** · 488 lines, 5 files

> - rumbo eval add SUITE --input/--expected/--grader [--id --tag --source --note]: turns a
>   production failure into a JSONL case (grader validated against evals/graders, ids unique,
>   tags deduplicated) and logs it in evals/error_analysis/log.md: the 'evals come from errors'
>   loop gets a tool.
> - rumbo metrics [--days --json]: offline process metrics from git, docs/decisions.md, specs/,
>   docs/postmortems/ and evals/: specs closed and sessions per spec, gates signed, commits by
>   conventional type, test-change approvals, deploy/incident decisions as DORA proxies,
>   requirements with a test and the latest eval run.

**Risk hotspots** (read these first):
- ⚠️ dependencies: pyproject.toml

**Reading order** (tests first, then what the subject names, then the largest):
1. `tests/test_cli.py` (test, +72/−0)
2. `src/rumbo/metrics.py` (code, new, +237/−0)
3. `src/rumbo/cli.py` (code, +123/−0)
4. `src/rumbo/scaffold.py` (code, +53/−2)
5. `pyproject.toml` (code, +1/−0)

> Over budget (488 > 300): ask for a split, or review only the tests and sample the rest.

**Verify:**
```bash
git show --stat 5b657d6502
uv run pytest -q tests/test_cli.py
make check
git revert --no-commit 5b657d6502 && make test; git revert --abort
```

**Explanation test** (answer before approving):
- Which test or sensor proves that «feat(cli): eval add, metrics and update --diff» works? Point to the exact test.
  <details><summary>hint</summary>

  Tests touched here: tests/test_cli.py.

  </details>
- If the change to `src/rumbo/metrics.py` were reverted, what observable behavior would change, and how would you notice?
  <details><summary>hint</summary>

  Run the revert command from the verification list. Author's stated intent: - rumbo eval add SUITE --input/--expected/--grader [--id --tag --source --note]: turns a

  </details>
- Name one alternative to this approach and why it was not chosen. Is that reason written down (commit body, ADR, decisions log)?
  <details><summary>hint</summary>

  If you cannot find the reason, the change carries intent debt: ask for an ADR or a line in docs/decisions.md before approving.

  </details>

## 6. feat(evals): LLM-as-judge grader admitted only after calibration against human labels

`4bc200a09f` · Claude · kind: **behavioral** · 174 lines, 6 files

> evals/graders/judge.py: Judge(complete, rubric) grades open-ended answers with an injected
> model call (the prompt treats question and answer as data); calibrate() computes TPR, TNR
> and Cohen's kappa over human-labeled rows (evals/calibration/*.jsonl) and acceptable()
> requires ≥30 cases with TPR and TNR ≥ 0.8 before the judge is registered in GRADERS.
> Deterministic unit tests with a fake model keep make check offline; the eval plan asks for
> the calibration numbers and date.
>
> Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>

**Risk hotspots** (read these first):
- none detected by path rules (still read the tests)

**Reading order** (tests first, then what the subject names, then the largest):
1. `src/rumbo/templates/tests/unit/test_judge.py` (test, new, +58/−0)
2. `src/rumbo/templates/evals/graders/judge.py` (code, new, +105/−0)
3. `src/rumbo/templates/evals/graders/__init__.py` (code, +3/−2)
4. `src/rumbo/templates/docs/evals/plan.md` (doc, +3/−0)
5. `src/rumbo/templates/evals/calibration/example.jsonl` (code, new, +2/−0)
6. `src/rumbo/scaffold.py` (code, +1/−0)

**Verify:**
```bash
git show --stat 4bc200a09f
uv run pytest -q src/rumbo/templates/tests/unit/test_judge.py
make check
git revert --no-commit 4bc200a09f && make test; git revert --abort
```

**Explanation test** (answer before approving):
- Which test or sensor proves that «feat(evals): LLM-as-judge grader admitted only after calibration against human labels» works? Point to the exact test.
  <details><summary>hint</summary>

  Tests touched here: src/rumbo/templates/tests/unit/test_judge.py.

  </details>
- If the change to `src/rumbo/templates/evals/graders/judge.py` were reverted, what observable behavior would change, and how would you notice?
  <details><summary>hint</summary>

  Run the revert command from the verification list. Author's stated intent: evals/graders/judge.py: Judge(complete, rubric) grades open-ended answers with an injected

  </details>
- Name one alternative to this approach and why it was not chosen. Is that reason written down (commit body, ADR, decisions log)?
  <details><summary>hint</summary>

  If you cannot find the reason, the change carries intent debt: ask for an ADR or a line in docs/decisions.md before approving.

  </details>

## 7. fix(scaffold): write generated files with LF on every platform

`f4da3a2728` · Claude · kind: **behavioral** · 6 lines, 2 files

> Rendered templates were written in text mode, which produces CRLF on Windows and makes
> the generated repository differ by platform. The executable-bit assertion of the sensor
> test is skipped on Windows, where st_mode carries no exec bits.
>
> Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
> Claude-Session: https://claude.ai/code/session_01Bbtm72LBvaJ7x6iPXUrXMH

**Risk hotspots** (read these first):
- ⚠️ removed asserts or added skip/xfail in: tests/test_scaffold.py

**Reading order** (tests first, then what the subject names, then the largest):
1. `tests/test_scaffold.py` (test, +2/−1)
2. `src/rumbo/scaffold.py` (code, +2/−1)

**Verify:**
```bash
git show --stat f4da3a2728
uv run pytest -q tests/test_scaffold.py
make check
git revert --no-commit f4da3a2728 && make test; git revert --abort
```

**Explanation test** (answer before approving):
- Which test or sensor proves that «fix(scaffold): write generated files with LF on every platform» works? Point to the exact test.
  <details><summary>hint</summary>

  Tests touched here: tests/test_scaffold.py.

  </details>
- If the change to `src/rumbo/scaffold.py` were reverted, what observable behavior would change, and how would you notice?
  <details><summary>hint</summary>

  Run the revert command from the verification list. Author's stated intent: Rendered templates were written in text mode, which produces CRLF on Windows and makes

  </details>
- Name one alternative to this approach and why it was not chosen. Is that reason written down (commit body, ADR, decisions log)?
  <details><summary>hint</summary>

  If you cannot find the reason, the change carries intent debt: ask for an ADR or a line in docs/decisions.md before approving.

  </details>

## 8. ci: Python 3.11–3.13 and OS matrix, anti-cheat sensor job and release workflow

`8bc771ffa7` · Claude · kind: **mechanical** · 103 lines, 2 files

> ci.yml runs make check on Ubuntu for every supported Python and on macOS, the three
> commands behind it on Windows (no make there), the anti-cheat sensor on the framework's
> own tests and the existing secret scan and e2e matrix. release.yml fires on tags vX.Y.Z:
> verifies the tag against pyproject.toml and CHANGELOG.md, builds with uv, creates the GitHub
> Release with the CHANGELOG section as notes (gh, no third-party action) and, only when the
> repository variable PYPI_PUBLISH is 'true', publishes to PyPI with trusted publishing
> (OIDC, no token) from the protected pypi environment. The Windows job is unverified from
> this environment: it is there to tell us.

**Risk hotspots** (read these first):
- ⚠️ CI / workflows (can weaken every other sensor): .github/workflows/ci.yml, .github/workflows/release.yml

**Reading order** (tests first, then what the subject names, then the largest):
1. `.github/workflows/release.yml` (code, new, +64/−0)
2. `.github/workflows/ci.yml` (code, +38/−1)

**Verify:**
```bash
git show --stat 8bc771ffa7
make check
```

**Explanation test** (answer before approving):
- Which test or sensor proves that «ci: Python 3.11–3.13 and OS matrix, anti-cheat sensor job and release workflow» works? Point to the exact test.
  <details><summary>hint</summary>

  Tests touched here: none — that is a finding in itself.

  </details>
- If the change to `.github/workflows/release.yml` were reverted, what observable behavior would change, and how would you notice?
  <details><summary>hint</summary>

  Run the revert command from the verification list. Author's stated intent: ci.yml runs make check on Ubuntu for every supported Python and on macOS, the three

  </details>
- Name one alternative to this approach and why it was not chosen. Is that reason written down (commit body, ADR, decisions log)?
  <details><summary>hint</summary>

  If you cannot find the reason, the change carries intent debt: ask for an ADR or a line in docs/decisions.md before approving.

  </details>

## 9. docs(adr): record the framework's own decisions with fitness functions

`60d4b78e1e` · Claude · kind: **docs** · 210 lines, 6 files

> ADR-0001 stdlib-only CLI, ADR-0002 Mustache-subset template engine, ADR-0003 offline make
> check with the credential only in the nightly job, ADR-0004 claude-code provider through the
> CLI, ADR-0005 hard rules enforced by Claude Code hooks. tests/test_fitness.py asserts the
> empty dependency list and that no module under src/rumbo imports a third-party package, and
> that every ADR carries a fitness-function section (dogfooding gate G3).
>
> Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
> Claude-Session: https://claude.ai/code/session_01Bbtm72LBvaJ7x6iPXUrXMH

**Risk hotspots** (read these first):
- none detected by path rules (still read the tests)

**Reading order** (tests first, then what the subject names, then the largest):
1. `tests/test_fitness.py` (test, new, +40/−0)
2. `docs/adr/0001-standard-library-only-cli.md` (doc, new, +36/−0)
3. `docs/adr/0005-hard-rules-as-hooks.md` (doc, new, +36/−0)
4. `docs/adr/0003-offline-make-check.md` (doc, new, +34/−0)
5. `docs/adr/0002-mustache-subset-template-engine.md` (doc, new, +32/−0)
6. `docs/adr/0004-claude-code-provider.md` (doc, new, +32/−0)

**Verify:**
```bash
git show --stat 60d4b78e1e
uv run pytest -q tests/test_fitness.py
```

**Explanation test** (answer before approving):
- Which test or sensor proves that «docs(adr): record the framework's own decisions with fitness functions» works? Point to the exact test.
  <details><summary>hint</summary>

  Tests touched here: tests/test_fitness.py.

  </details>
- If the change to `tests/test_fitness.py` were reverted, what observable behavior would change, and how would you notice?
  <details><summary>hint</summary>

  Run the revert command from the verification list. Author's stated intent: ADR-0001 stdlib-only CLI, ADR-0002 Mustache-subset template engine, ADR-0003 offline make

  </details>
- Name one alternative to this approach and why it was not chosen. Is that reason written down (commit body, ADR, decisions log)?
  <details><summary>hint</summary>

  If you cannot find the reason, the change carries intent debt: ask for an ADR or a line in docs/decisions.md before approving.

  </details>

## 10. docs: hooks, traceability, phase O, eval/metrics tools, tutorial and release process

`8d93fb16e4` · Claude · kind: **docs** · 303 lines, 8 files

> RUMBO.md: hooks in the instructions and A6, the new files in A1, PR template and CODEOWNERS
> in A5, rumbo eval add and judge calibration in A7, requirement ids and rumbo metrics in
> phase B, budget/telemetry wiring in phase O, the spec template line, the per-rigor table.
> README: new commands, a 'Hard rules as hooks' section, what init generates, update --diff,
> the tutorial link and the CI/release summary. docs/method.md: ANCLA table rows, a paragraph on
> rules vs hooks, LLM observability as shipped. CONTRIBUTING: release workflow and ADR policy.
> docs/tutorial.md: 30 minutes from rumbo init to the first spec closed with G4. CHANGELOG under
> Unreleased. Generated README explains the hooks and the two placeholders to replace.

**Risk hotspots** (read these first):
- ⚠️ agent guides (AGENTS.md / CLAUDE.md / commands): AGENTS.md

**Reading order** (tests first, then what the subject names, then the largest):
1. `docs/tutorial.md` (doc, new, +101/−0)
2. `docs/method.md` (doc, +17/−6)
3. `RUMBO.md` (doc, +60/−17)
4. `README.md` (doc, +34/−8)
5. `CHANGELOG.md` (doc, +30/−0)
6. `CONTRIBUTING.md` (doc, +15/−4)
7. `src/rumbo/templates/README.md.tmpl` (code, +6/−0)
8. `AGENTS.md` (doc, +4/−1)

> Over budget (303 > 300): ask for a split, or review only the tests and sample the rest.

**Verify:**
```bash
git show --stat 8d93fb16e4
```

**Explanation test** (answer before approving):
- Which test or sensor proves that «docs: hooks, traceability, phase O, eval/metrics tools, tutorial and release process» works? Point to the exact test.
  <details><summary>hint</summary>

  Tests touched here: none — that is a finding in itself.

  </details>
- If the change to `src/rumbo/templates/README.md.tmpl` were reverted, what observable behavior would change, and how would you notice?
  <details><summary>hint</summary>

  Run the revert command from the verification list. Author's stated intent: RUMBO.md: hooks in the instructions and A6, the new files in A1, PR template and CODEOWNERS

  </details>
- Name one alternative to this approach and why it was not chosen. Is that reason written down (commit body, ADR, decisions log)?
  <details><summary>hint</summary>

  If you cannot find the reason, the change carries intent debt: ask for an ADR or a line in docs/decisions.md before approving.

  </details>

## Before you approve

- [ ] I read the risk hotspots and the tests of every commit.
- [ ] I ran the verification commands (or CI did, and I read its output).
- [ ] I answered the explanation questions without the hints.
- [ ] Structural and behavioral changes are in separate commits.
- [ ] I can explain this change in three sentences to someone who has not seen it.

Record the decision: `rumbo decision "Approved <range>" --why "..."`
