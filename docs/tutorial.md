# Tutorial: from `rumbo init` to the first spec closed (≈30 minutes)

You need Python ≥ 3.11, `uv`, `git`, `make` and Claude Code. The example is a small LLM product
with the `claude-code` provider (no API key); drop `--llm --provider claude-code` for a plain
Python project. Times are indicative.

## 1. Mount the harness (5 min)

```bash
mkdir price-agent && cd price-agent && git init
uvx --from git+https://github.com/padillacm/RUMBO rumbo init --yes \
  --name "Price Agent" --package price_agent --llm --provider claude-code --rigor mvp \
  --case "answers questions about daily spot prices"
make check                      # green, offline
git add -A && git commit -m "chore: initial RUMBO harness"
```

What you got: `AGENTS.md` (the agent's map), a `Makefile` whose `make check` is the whole
harness, CI with pinned actions, document templates with a `<!-- rumbo:template` mark, an
anti-cheat sensor, Claude Code hooks (`.claude/settings.json`) and, for LLM products, an eval
runner, deterministic graders and a budget/telemetry skeleton. `rumbo doctor` confirms it.

Two things only you can do now: replace `@your-handle` in `.github/CODEOWNERS` and the contact
in `SECURITY.md`.

## 2. Phase R: the brief, written by you first (5 min)

Open `docs/product/brief.md`, write a few lines (problem, who has it, what success looks like,
what would make you abandon it) and delete the first line (the template mark). Then open
Claude Code and type `/rumbo-r`: the agent critiques the brief and proposes interview
questions. It does not write the brief for you.

```bash
rumbo gate G1                   # automatic checks + the checklist you sign
rumbo decision "G1 signed" --why "problem confirmed with 3 users"
```

## 3. Phase U: the first spec (5 min)

```bash
rumbo new spec "Spot price for a date"      # specs/0001-spot-price-for-a-date.md
```

Fill the EARS requirements (keep the ids: `R1`, `R2`…), the acceptance criteria and the
"Data contracts and ownership" section: the types, the function signature and the files this
spec owns. Delete the template mark. `/rumbo-u` lists the ambiguities the agent sees.

```bash
rumbo gate G2                   # EARS present, contracts filled (recommendation at mvp)
```

## 4. Phase B, ANCLA: one spec, one fresh session (10 min)

Work on a branch so the sensors have a base to compare against: `git checkout -b f-0001`.
Start a **new** Claude Code session (`/clear`) and type `/rumbo-ancla F-0001`.

- **Anchor.** The agent writes failing tests first. Each test names its requirement in the
  docstring (`F-0001.R1`). Read them; approve or correct them. Nothing else happens until you
  do.
- **Navigate.** It proposes a short plan (files, risks). Approve it.
- **Close the loop.** It implements and runs `make check-brief` until green. If it tries to
  edit the Makefile or a sensor, the hook blocks it and it asks you.
- **Limit.** Small diff, structural and behavioral commits apart. Then:

```bash
rumbo trace                     # every requirement has a test?
rumbo gate G4                   # sensor, make check, diff size vs main, traceability
make review BASE=main           # docs/reviews/<date>-<sha>.md: your guided tour (BASE defaults to origin/main)
```

Read the tour, not the agent's summary: risk hotspots first, tests before code, three
questions you should be able to answer. Optionally open a second fresh session with
`/rumbo-review` for an independent review against the spec. Then the explanation test:
explain the change in three sentences, out loud or in the PR description.

```bash
rumbo decision "G4 signed" --why "explained the change; trace complete"
rumbo decision "F-0001 closed" --why "1 session"
```

## 5. Learn and measure (2 min)

```bash
rumbo metrics                   # specs closed, sessions per spec, gates signed, requirements traced
```

When a production answer is wrong, turn it into a case the same day:

```bash
rumbo eval add regressions --input '{"question": "spot price on 2025-02-30"}' \
  --expected '{"abstain": true}' --grader abstention --source "trace 9f3a" \
  --note "invented a figure for a date that does not exist"
```

## What to do next

- Phase M before the second spec: `rumbo new adr "Workflow vs agent"` (you write your version
  first; `/rumbo-m` completes the options) and the threat model.
- Keep one spec per session. If a spec takes more than two sessions, split it: `rumbo metrics`
  shows the median.
- When the framework ships a new version, `rumbo doctor` warns you; `rumbo update --diff`
  shows what would change before `rumbo update` applies it.
