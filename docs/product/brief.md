<!-- rumbo:template — delete this line when the document is complete. Only the HUMAN removes it; `rumbo gate` checks it. -->
# Brief

**Problem:** A developer or a team of one to three people building Python software with a coding
agent (Claude Code, Cursor, Codex, Gemini CLI) cannot stop the agent from cheating its way to
green (weakening or deleting tests, editing CI or lint configuration, declaring a task done while
the checks are red) because the rules live in prompts, and prompts are not sensors: under
pressure the agent ignores them and nothing mechanical blocks the edit. Today they do one of
three things: nothing beyond reading the diff by eye; long prompt files and manual review of
every change; or a TDD guard such as tdd-guard/probity, which blocks edits outside the
red-green cycle but covers one practice, one editor and nothing of the rest of the lifecycle
(specs, architecture, evals, operations).

**Evidence:** Indirect: an external expert audit of RUMBO v0.3.0; a community study of 19
spec-driven, scaffolding, linting and eval projects (what failed in Cookiecutter, spec-kit, BMAD,
FlakeHell, tdd-guard); the research report on agent context limits (NoLiMa, RULER, LongSWE-Bench,
MAST, The Complexity Trap). They describe the failure modes, not the users. Direct: n=1, the
author, on RUMBO itself (self-adopted as a flat cli at production rigor, ADR-0013; the guard
blocked the agent twice on the first day). No user interview and no external project yet: the
interview script in the G1 notes closes that gap.

**Outcome metric:** Primary: of three pilot projects, at least two still run the check four
weeks after `rumbo init`. Secondary: agent pull requests that reach human review with the check
green and no human intervention (target ≥ 80 % in the pilots), and time from `rumbo init` to the
first green check on a fresh machine (target < 1 hour). Indicator, not target: cheats caught
mechanically (a weakened test, a protected-file edit, a red Stop) before the merge. All read with
`rumbo metrics` and the pilots' decision logs.

**Risky assumptions** (value / usability / feasibility / viability):
- Value: a small team wants this discipline enough to accept hooks that block their own agent.
- Usability: four questions and one command are enough to start, on Windows, macOS and Linux,
  greenfield or brownfield (`--adopt`), without reading the method first.
- Feasibility: the hard rules can be enforced deterministically, offline, with the standard
  library only, for any agent that commits through git. Shown on RUMBO itself; not yet in
  other hands.
- Viability: one maintainer can sustain templates for four kinds, three layouts and two type
  checkers, and the harness on RUMBO itself, in under eight hours a month.

**Experiments and success/failure criterion:**
1. Five interviews with Python developers who use a coding agent (script in the G1 notes).
   Success: at least three describe an agent cheat they had to undo and name one action they
   would block. Failure: the pain is not there, or it is already solved by a TDD guard for them.
2. Three pilot projects within two months, at least one on native Windows and one brownfield
   with `--adopt`. Success: two of three still run the check after four weeks. Failure: nobody
   keeps it installed for a month.
3. RUMBO on itself: G1–G5 signed and the runner green on the three platforms by the end of the
   pilots. Failure: its own harness costs more than eight hours a month to keep green.

**Non-goals:** Frontend, mobile and non-Python stacks (the method applies, the CLI does not);
monorepos with uv workspaces; an agent SDK or orchestration of agents; replacing FastAPI, the
model SDKs or the team's CI provider; teams larger than three people per repository; a hosted
service or a GitHub App.

**G1 decision:** iterate — run the interviews and the pilots, build only what they need, and
return to G1 with ≥5 users or abandon.
