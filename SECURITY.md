# Security policy

## Reporting a vulnerability
Open a private security advisory on GitHub (Security → Advisories → "Report a vulnerability")
or write to the maintainer listed in `.github/CODEOWNERS`. Do not open a public issue. We
acknowledge within 72 hours and share a fix or mitigation within 14 days.

## Supported versions
The latest tagged release and `main`.

## Scope
`rumbo` is a stdlib-only CLI that writes files into your repository and runs `uv`, `git` and
`pre-commit` on your behalf. Its generated harness is where the security value lives: CI with
actions pinned by SHA, a full-history secret scan, `pip-audit` and `bandit`, dependency review
before adding a package (`rumbo verify-dep`), an offline `make check` that never holds a model
credential, and Claude Code hooks that keep agents away from the sensors. Issues in those
templates are security issues for every project that uses them: report them here.
