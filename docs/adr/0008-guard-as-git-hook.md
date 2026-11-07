# ADR-0008: The guard also runs as a git hook, so any coding agent is bound by the same rules
Status: accepted    Date: 2026-11-07

## Context
ADR-0005 enforces the hard rules of AGENTS.md (protected files, the template mark, the approval
mark) through Claude Code hooks running `scripts/rumbo_guard.py`. Its own consequence line says
it: only Claude Code honours those hooks. A developer on Cursor, Codex, Gemini CLI, Aider or any
future agent gets prose and CI, nothing mechanical before the commit. The expert review and the
community study (Error 4 and its caution on rule-sync tools) ask for an agent-agnostic layer
without inventing a protocol per agent: git hooks via pre-commit are the one hook surface every
agent passes through when it commits.

## Drivers (non-functional requirements)
Same rules, same script, same messages as ADR-0005; the human is never blocked in their own
clone; no new dependency (pre-commit is already in the harness); no network; works on Windows;
honest about what it cannot do (an agent that unsets its own environment marker is a policy
violation, not a technical defeat).

## Options considered
1. Document that other agents rely on CI alone (status quo).
2. One native hook integration per agent (Cursor `hooks.json`, Codex, Gemini…): several
   protocols, most in beta, each a maintenance surface.
3. Two git hooks (`pre-commit`, `commit-msg`) implemented as events of the same
   `scripts/rumbo_guard.py`, wired by pre-commit, deciding who the actor is from the environment
   and from a per-clone declaration by the human.

## Decision and rationale
Option 3, with option 2 left open for Cursor once its protocol stabilises. The contract:

**Actor.** The guard decides `agent` or `human` the same way for both git events:
1. If any variable named in `[tool.rumbo].agent_env` is set in the environment, the actor is an
   agent. Default list: `CLAUDECODE` (Claude Code, documented), `GEMINI_CLI` (Gemini CLI,
   documented), `CURSOR_AGENT` (Cursor, community-reported, not always set),
   `CODEX_SANDBOX_NETWORK_DISABLED` (Codex, set when its sandbox has no network).
2. Else, if `git config --get rumbo.human` is `true` in the clone, the actor is the human.
3. Else the actor is unknown and treated as an agent; the message explains that a human
   declares themself once per clone with `git config rumbo.human true`.
`rumbo init` sets `rumbo.human` in the clone it runs in, since the human runs it; `rumbo doctor`
warns when it is unset. The Claude Code `PreToolUse` guard blocks the agent from running
`git config` on `rumbo.human` or `core.hooksPath`, and from `pre-commit uninstall`.

**`pre-commit` event** (`python scripts/rumbo_guard.py pre-commit`). For an agent actor, with
the staged files from `git diff --cached --name-only --diff-filter=ACMRD`:
- a protected file is staged (same `PROTECTED_FILES` and `PROTECTED_PREFIXES` as the Claude Code
  hook; `pyproject.toml` and `uv.lock` are allowed, as in `PostToolUse`, because `uv add` writes
  them legitimately) → exit 1 listing the files and the ADR-0005 message;
- the template mark `<!-- rumbo:template` is present in `HEAD:<path>` and absent in the staged
  `:<path>` → exit 1 naming the file.
For the human actor, or with nothing staged (which is what `pre-commit run --all-files` sees in
CI), exit 0 silently.

**`commit-msg` event** (`python scripts/rumbo_guard.py commit-msg <message-file>`). For an agent
actor, a message containing `[tests-change-approved]` → exit 1 with the ADR-0005 wording ("only
the human writes it"). The CI sensor keeps its own author check (ADR-0005) as the second layer.

**Wiring.** `.pre-commit-config.yaml` gains `default_install_hook_types: [pre-commit,
commit-msg]` and two `repo: local` hooks, `language: system`, `entry: uv run python
scripts/rumbo_guard.py pre-commit` (`always_run: true`, `pass_filenames: false`, `stages:
[pre-commit]`) and `entry: uv run python scripts/rumbo_guard.py commit-msg` (`stages:
[commit-msg]`, pre-commit passes the message file). `rumbo init` keeps calling `pre-commit
install`, which now installs both hook types. Exit code `1` follows the git-hook convention;
the Claude Code hook keeps exit `2` for its own protocol.

## Consequences (+/−)
+ Every agent that commits through git meets the same three rules with the same messages.
+ The human's own commits are untouched in the clone where `rumbo init` ran; one command
  declares them anywhere else.
+ No new file: one script, two more events, one config block.
− The block is late (at commit) for agents without a live hook; the edit already happened.
− An agent can unset its marker or set `rumbo.human`; the Claude Code guard blocks that, other
  agents only have the prose and the CI sensor. Documented as a limit, not hidden.
− `CURSOR_AGENT` is intermittent upstream; Cursor users are covered by rule 3 until they declare.

## Fitness function
`tests/test_guard.py`: actor from markers, from `rumbo.human`, unknown treated as agent; the
`pre-commit` event with a protected file, with a template mark removed, with `pyproject.toml`
and `uv.lock`, with nothing staged; the `commit-msg` event with the approval mark; the new
dangerous `git config` / `pre-commit uninstall` patterns. `tests/test_scaffold.py`: the generated
pre-commit config declares both hook types; `rumbo init` records `rumbo.human`. The e2e script:
`CLAUDECODE=1 git commit` of a protected file is refused by the hook, the same commit with
`rumbo.human` set passes.

## Reversibility: high
Remove the two hooks from the config; the script's events are inert without them.
