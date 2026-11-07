# Contributing

RUMBO is built with RUMBO. Read [AGENTS.md](AGENTS.md) first: it is the map of this repository.

## Setup

```bash
uv sync
make check   # lint, types and the test suite of the CLI
make e2e     # mounts real projects in several configurations and requires their `make check`
```

Python ≥ 3.11, `uv`, `git` and `make`. The CLI itself has **no runtime dependencies**: keep it
that way (standard library only).

## Rules

- Language: English everywhere (code, templates, documentation, commit messages).
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:`. Separate structural commits
  from behavioral ones (Tidy First).
- Never delete or weaken tests. If a test looks wrong, open an issue or ask in the PR.
- Every Python file under `src/rumbo/templates/` must pass ruff, ruff format and pyright **in the
  generated project**; `make e2e` checks it.
- Do not copy version numbers from memory. Pinned action SHAs are verified against the
  upstream tags; pre-commit revs are refreshed by `pre-commit autoupdate` at install time.
- Changes to `RUMBO.md` or the templates must keep `RUMBO.md`, `README.md` and
  `docs/method.md` consistent.

## Pull requests

Small diffs (~300 lines). Run `make review` (or `rumbo tour origin/main`) and attach the guide
to the PR description: it gives the reviewer the risk hotspots, a reading order and the
questions they should be able to answer. Update `CHANGELOG.md` under *Unreleased*.

## Releasing

1. Move *Unreleased* to a `## [X.Y.Z] - YYYY-MM-DD` heading in `CHANGELOG.md` and bump the
   version in `src/rumbo/__init__.py` and `pyproject.toml` (`uv lock` afterwards).
2. Tag: `git tag -a vX.Y.Z -m "vX.Y.Z"` and push the tag. `.github/workflows/release.yml`
   verifies tag == version == CHANGELOG section, runs `make check`, builds with `uv build` and
   creates the GitHub Release with that CHANGELOG section as notes.
3. Optional PyPI: create the `pypi` environment, register the workflow as a trusted publisher
   on pypi.org and set the repository variable `PYPI_PUBLISH=true`; the `pypi` job then
   publishes with `uv publish --trusted-publishing always` (no token stored anywhere).
4. Users pin the tag: `uvx --from git+https://github.com/padillacm/RUMBO@vX.Y.Z rumbo`.

## Architecture decisions

The framework's own decisions are ADRs in `docs/adr/` (stdlib-only CLI, the template engine,
the offline `make check`, the `claude-code` provider, hooks as sensors). Each has a fitness
function in `tests/`; propose a new ADR before changing one of those decisions.
