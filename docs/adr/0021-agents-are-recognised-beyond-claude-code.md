# ADR-0021: Agents are recognised beyond Claude Code, and only the human declares the human
Status: accepted    Date: 2026-11-09

Completes ADR-0008 (the guard as a git hook, the actor of a commit). ADR-0008 stands.

## Context
Two mechanisms decide whether an agent is acting. The git hooks read the environment
(`[tool.rumbo].agent_env`, four default markers) and `git config rumbo.human`. The anti-cheat
sensor reads the history: an approval mark `[tests-change-approved]` does not count when the
commit that carries it was authored by an agent. An audit of v0.4.0 found them uneven:

1. **The sensor only knows Claude.** Its markers are `noreply@anthropic.com`, `[bot]` and the
   trailer `Co-Authored-By: Claude`. An approval written by any other agent counts as the
   human's. The sensor in CI is the layer ADR-0008 calls binding.
2. **`rumbo init` declares the human by itself.** It runs `git config rumbo.human true` in the
   clone. The guard forbids the agent that very command, and then `init` runs it on the agent's
   behalf: an agent with no known marker that mounts the harness leaves a clone where every
   later commit of its own is the human's.

3. **Gemini CLI never reaches the git hook** (found while verifying the markers, not by the
   audit). It starts every shell command with `core.hooksPath` set to empty through
   `GIT_CONFIG_*` (its `shellExecutionService.ts`). Measured: with that environment a failing
   `pre-commit` hook does not run. ADR-0008 lists Gemini CLI among the agents bound at commit.

An agent with no known marker, in a clone a human declared, is still treated as the human.
That stays an accepted risk (threat model): it cannot be closed on the developer's machine.

## Drivers (non-functional requirements)
No fact about another tool is written from memory: every default marker is one confirmed in
the tool's documentation or source. A project must be able to add its own without waiting for
a release. Standard library only; the sensor and the guard run without `rumbo` installed.

## Options considered
1. **Invert the rule: an approval counts only when signed** (GPG/SSH) by a code owner.
   Rejected for now: it demands commit signing from every team before the harness works.
2. **Keep the lists short and fixed.** Rejected: it is the finding.
3. **Verified defaults plus a project-level list, in both mechanisms (chosen).**

## Decision and rationale
**The sensor** (`check_tests_not_weakened.py`). An approval is ignored when the commit's
author, committer or any trailer matches an agent marker. The defaults are the identities
confirmed against primary sources on the date of this ADR (they live in the code, each with
its source in a comment). A project extends them with `[tool.rumbo].agent_authors`: a list of
case-insensitive substrings matched against `Name <email>` of the author and the committer and
against every `Co-authored-by:` trailer.

**The hooks** (`guard.py`). `DEFAULT_AGENT_ENV` gains the environment markers confirmed the
same way; `[tool.rumbo].agent_env` still replaces the list.

Verified on 2026-11-09 and added: `CODEX_THREAD_ID` and `CODEX_CI` (Codex sets
`CODEX_SANDBOX_NETWORK_DISABLED` only when the network is sandboxed), `ROO_ACTIVE` (Roo Code),
`CLINE_ACTIVE` (Cline); for the sensor, `noreply@openai.com` (Codex), `aider@aider.chat` and
the `(aider)` name suffix (Aider), `amp@ampcode.com` and the `Amp-Thread-ID:` trailer (Amp),
the `Agent-Logs-Url:` trailer (GitHub Copilot coding agent). Not added, because no primary
source confirmed them: Windsurf, OpenCode, Devin, Jules, Kiro, Zed, and the exact trailer of
Cursor and of Copilot CLI.

**Gemini CLI.** A git hook cannot bind a shell that disables git hooks. The documentation
stops promising it: for Gemini CLI the binding layers are the sensor in CI and CODEOWNERS on
the protected files. A live hook through Gemini CLI's own hook system, like the Claude Code
one, is a candidate for a later ADR.

**`rumbo init`.** It sets `rumbo.human` only when a human is demonstrably there: no agent
marker in the environment **and** standard input is a terminal. Otherwise it does not touch the
setting and prints, in its next steps, the one command the human runs once per clone
(`git config rumbo.human true`); `rumbo doctor` already warns while it is missing.

## Consequences (+/−)
+ An approval mark written by the other common agents no longer passes in CI.
+ "Only the human declares `rumbo.human`" is true of the tool's own commands.
− When an agent mounts the harness, the human has one command to run before their first
  commit (the hook's message says which).
− The lists need maintenance; `agent_authors` and `agent_env` are the escape hatch.
− An agent with no marker, running in a terminal emulation, is still taken for the human.
− Aider and Amp have no confirmed environment marker: the git hook does not recognise them in
  a clone the human declared; only their commit attribution does, in CI, and it can be turned off.
− Gemini CLI is not bound at commit at all.

## Fitness function
`tests/unit/test_check_tests_not_weakened.py::test_an_approval_by_another_agent_is_ignored`,
`::test_agent_authors_come_from_pyproject`;
`tests/test_scaffold.py::test_init_declares_rumbo_human_only_for_an_interactive_human`;
`tests/test_guard.py::test_default_agent_markers_have_a_source`.

## Reversibility: high
