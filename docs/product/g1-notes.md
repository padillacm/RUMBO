# G1 notes: critique of the brief and the interview script

Phase R of RUMBO on RUMBO itself (2026-11-08). The brief lives in `brief.md`; this file holds what
the gate needs that is not the brief: the critique that shaped it and the script of the
interviews that will validate it.

## Critique (what changed in the brief and why)

- The problem now names one pain, cheating its way to green, and keeps cognitive debt as a
  consequence rather than a second problem.
- The alternatives name the common case (reading the diff by eye) and the direct competitors
  (tdd-guard, probity) with the reason RUMBO exists next to them: the whole lifecycle, any
  agent, zero dependencies.
- The evidence says plainly that the indirect sources describe failure modes, not users, and that
  the direct evidence is n=1.
- The primary metric has a threshold (two of three pilots at four weeks); "cheats caught" is an
  indicator, since it depends on the agent trying.
- Viability has an abandonment number (eight hours a month).

## Interview script

Five Python developers who use a coding agent. Open questions, RUMBO not mentioned until the end.

1. Tell me about the last time an agent did something you had to undo. What was it, and how did
   you find out?
2. How do you know today that an agent's change broke nothing? What do you review, and how long
   does it take?
3. Has an agent ever touched tests, CI or lint configuration to make something pass? What did
   you do next?
4. What rules do you give the agent in writing? Does it follow them? When does it stop?
5. What tools did you try to keep it in check, and why did you drop them?
6. If you could mechanically block one action of the agent, which one?
7. How long would you spend setting up such a tool before giving up? What would make you delete
   it after a week?
8. Which OS and which agent do you work with?

Answers to 1, 3 and 6 are the direct evidence the brief lacks. Record them as
`docs/product/interviews/<date>-<n>.md` (one file per interview, no names).

## Feasibility test

RUMBO is not an LLM product: the 20–30 question test does not apply. Technical feasibility is
shown by RUMBO running on itself with the hooks live; feasibility in other hands is what the
pilots measure.

## Prototypes

None. The experiment is adoption, not a spike; `spikes/` stays empty.

## Gate G1 checklist (state on 2026-11-08)

- [ ] Brief written first by the human: drafted by the agent from the human's inputs; the human
  edits it, owns the problem statement and removes the template mark.
- [ ] Problem validated with ≥ 5 users or real data: pending the interviews.
- [x] Success metric and abandonment criterion written down.
- [x] Prototypes only in `spikes/`.

Decision: iterate. Sign with `rumbo decision "G1 signed" --why "5 interviews, 3 pilots planned"`
once the interviews are done.
