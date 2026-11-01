# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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
