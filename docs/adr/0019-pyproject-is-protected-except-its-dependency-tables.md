# ADR-0019: `pyproject.toml` is protected except its dependency tables
Status: accepted    Date: 2026-11-09

Narrows the exception of ADR-0005 and ADR-0008 ("`uv add`/`uv lock` are the exception:
pyproject.toml and uv.lock may change through Bash"). The rest of both ADRs stands.

## Context
The guard lets `pyproject.toml` and `uv.lock` change through a shell command and through an
agent's commit, because `uv add` writes them. The exemption is on the whole file. An audit of
v0.4.0 pointed out what that allows: an agent whose only binding layer is the git hook (Cursor,
Codex, Gemini CLI; ADR-0008) can edit `[tool.rumbo.checks]`, `[tool.ruff]`, `[tool.pyright]` or
the coverage threshold and commit it; inside Claude Code the same edit passes through any shell
command that is not a redirect (`python -c …`, `sed -i`). The verification contract is exactly
those tables. The threat model already claims "the table is a protected file, so only a human's
commit changes it"; the code did not back the claim.

## Drivers (non-functional requirements)
`uv add`, `uv remove` and `uv lock` keep working for the agent with no approval step (a new
dependency is approved in review, with `rumbo verify-dep`). Standard library only. Fail closed:
when the guard cannot tell, the change is refused. Within the `PostToolUse` budget (p95 < 500
ms): no extra work unless `pyproject.toml` actually changed.

## Options considered
1. **Protect the whole file.** Rejected: every `uv add` would need the human's commit.
2. **A deny list of tables** (`tool.rumbo`, `tool.ruff`, `tool.pyright`…). Rejected: it misses
   whatever is not listed today (`tool.pytest`, `tool.coverage`, `tool.bandit`, `build-system`).
3. **An allow list of tables, compared semantically (chosen).**

## Decision and rationale
`uv.lock` stays free. For `pyproject.toml`, the guard parses two versions with `tomllib` — the
one in `HEAD` and the current one (the working tree in `PostToolUse`, the staged blob in
`pre-commit`) — and allows the change only when the two are equal after removing these keys:

- `project.dependencies`
- `project.optional-dependencies`
- `dependency-groups`
- `tool.uv`

Anything else that differs is a protected change. The reason names the tables that changed
(dotted paths, at most two levels: `tool.rumbo`, `tool.ruff`, `project.version`) and tells the
agent to restore them and ask the human.

Fail closed: a version that does not parse, or a `pyproject.toml` that does not exist in `HEAD`
(a new file in a later commit), is a protected change. The root commit remains exempt (ADR-0008).

Where: `check_post_bash` and `check_pre_commit` in `src/rumbo/sensors/guard.py`, through one
function, `pyproject_change(root, staged: bool) -> list[str]` (the dotted paths that changed
outside the allow list; empty when the change is allowed). `PreToolUse` is unchanged: an edit
tool on `pyproject.toml` is always blocked, and a shell command cannot be judged before it runs.

Consequences for the method: `rumbo update` and `rumbo promote` write `[tool.rumbo]`, so an
agent that runs them is reported; they are the human's commands (AGENTS.md already says so).
`uv version` writes `project.version`: the human bumps the version.

## Consequences (+/−)
+ The checks table, the linter and type-checker configuration, the coverage threshold and the
  build configuration can only change in a human's commit, for every agent.
+ The threat model's claim about `tool.rumbo.checks` becomes true at the commit layer.
− `[tool.uv]` is open: an agent can point a dependency at another source there. It is a
  supply-chain decision visible in the diff, reviewed with the dependency itself.
− Reformatting or reordering allowed tables is fine; a comment changed elsewhere in the file
  is invisible to a semantic comparison and is allowed. Comments configure nothing.

## Fitness function
`tests/test_guard.py::test_pyproject_may_change_only_in_its_dependency_tables`,
`::test_pre_commit_refuses_pyproject_changes_outside_the_dependency_tables`,
`::test_an_unparseable_or_new_pyproject_is_a_protected_change`.

## Reversibility: high
