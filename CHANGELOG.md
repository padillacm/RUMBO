# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-11-01

### Added
- Claude Code hooks: the generated `.claude/settings.json` pre-approves the harness commands
  and wires `scripts/rumbo_guard.py` (PreToolUse and Stop) so the agent cannot edit the
  Makefile, pyproject, CI, infra, the sensors or `.env`, remove a template mark, write the
  approval mark, force-push, bypass git hooks or finish with `make check-brief` red.
- Requirement → test traceability: requirement ids `F-NNNN.Rk`, `rumbo trace [--strict]`, a
  G4 row (recommended below production, required in production), `/rumbo-review` starting
  from the generated table.
- `.github/pull_request_template.md` (ANCLA checklist), `.github/CODEOWNERS` and `SECURITY.md`
  in generated projects and in this repository.
- Phase O skeleton for LLM products: `app/budget.py` (per-request token, cost and time limits
  from `RUMBO_BUDGET_*`, `BudgetExceeded` → abstention) and `app/telemetry.py` (spans with
  OpenTelemetry GenAI attribute names and cost, logged as JSON); runbook entries, SLO rows,
  `.env.example` variables and a G5 check that both are wired into the entry point.
- `rumbo eval add`: turns a failure into a JSONL case and logs it in
  `evals/error_analysis/log.md`.
- `rumbo metrics [--days --json]`: specs closed and sessions per spec, gates signed, commits
  by type, deploy/incident decisions as DORA proxies, requirements traced, latest eval run.
- `rumbo update --diff`: preview of what `update` would replace and how harness files drifted.
- `evals/graders/judge.py`: LLM-as-judge grader with `calibrate()` (TPR, TNR, kappa) against
  human-labeled rows in `evals/calibration/`; admitted only at TPR/TNR ≥ 0.8 on ≥30 cases.
- Framework CI on Python 3.11–3.13 (Linux and macOS), an anti-cheat sensor job and a
  release workflow (`vX.Y.Z` tag → GitHub Release from the CHANGELOG, optional PyPI trusted
  publishing); five ADRs with fitness functions in `docs/adr/`; `docs/tutorial.md`.
- Context-limit practices, grounded in the research report
  `reports/Desarrollar con agentes bajo límite de contexto.md` (`docs/method.md`, section 3.1 "Working within the agent's effective context"): a "Data
  contracts and ownership" section in the spec template (checked by gate G2), a one-writer
  rule and a `# Compact instructions` section in the generated AGENTS.md, `make check-brief`
  (only the failing tail reaches the agent's context), a requirement-by-requirement
  traceability table in `/rumbo-review`, and a session-count log when a spec closes.

### Changed
- Newcomer UX (from a from-zero usability run): `rumbo` alone prints help and the two commands
  to start with; `rumbo tour` and the generated Makefile fall back to `main` when `origin/main`
  does not exist; the Makefile uses a locally installed `rumbo` before fetching it with `uvx`;
  the gate header is ✅ when no check failed (still "NOT SIGNED" until the human ticks the
  checklist); interactive `rumbo init` asks before writing into a non-empty directory;
  `rumbo doctor` warns about the `@your-handle` and security-contact placeholders; gate G3 no
  longer accepts sections of untouched templates; `pre-commit autoupdate` rev bumps are not
  reported as drift; the README opens with the install block and a "just the sensors" path.
- `rumbo update` lists only harness files (Makefile, AGENTS.md, CI, hooks…) under "differ",
  never the human's documents.

### Fixed
- Generated files are written with LF on every platform (CRLF on Windows before), including
  merged `pyproject.toml` / `.gitignore`.
- Review fixes before release: the guard's Bash rules are backed by a git-based post-check
  (protected files vs HEAD) and `.claude/` is protected; the generated skeleton configures JSON
  logging at the process boundaries (spans were never emitted) and treats blank `RUMBO_BUDGET_*`
  as defaults; `rumbo metrics` tolerates hand-written decision headings, missing git and
  malformed eval files; `rumbo eval add` reads `GRADERS` with the AST and escapes the log table;
  `rumbo trace` ignores nested `R1.1` items and `R10a` look-alikes; release notes stop at the
  Keep-a-Changelog link references and PyPI publishes the released files.

## [0.2.0] - 2026-11-01

### Added
- `rumbo update`: refreshes the files the framework owns (RUMBO.md, `.claude/commands`,
  `scripts/`, the `0000` templates) without touching documents the human edited.
- `rumbo tour` and `make review`: a guided, pedagogical review of a git range (risk hotspots
  first, reading order with tests first, verification commands, explanation-test questions).
- Provider `claude-code`: evals call the model through the Claude Code CLI with the developer's
  own login; no API key on the machine.
- Gates scale with the rigor level; unmeasurable checks are reported instead of shown green.
- Anti-cheat sensor: per-test assert counts, always-true asserts, skip/xfail markers and
  parametrize cuts; approvals authored by an agent are ignored; PR label support.
- `rumbo verify-dep`: typosquatting check against popular packages and OSV vulnerability query.
- Full-history secret scan in CI (gitleaks action); SBOM from `uv.lock` (CycloneDX 1.5).
- `.env.example`, OWASP LLM/Agentic checklist in the threat model, `docs/reviews/`.

### Changed
- The whole framework is in English (CLI, templates, documentation).
- `make check` is offline: no model credential in CI on pushes or pull requests; the real
  model only runs in the nightly `evals-live` job (protected environment).
- `--force` keeps `.bak` copies and always merges `pyproject.toml` / `.gitignore`.
- The coverage threshold in production was replaced by nightly mutation testing.

### Fixed
- Dockerfile started `uvicorn` without installing it.
- Provider `other` generated invalid workflows; project names were not escaped in TOML/Python.
- Tracebacks in `rumbo new` (backslashes), `rumbo verify-dep` (incomplete JSON) and gates.

## [0.1.0] - 2026-11-01

### Added
- First version: `rumbo init`, `new`, `decision`, `gate`, `doctor`, `check-tests`,
  `verify-dep`; harness templates; RUMBO.md, method and the XM example.
