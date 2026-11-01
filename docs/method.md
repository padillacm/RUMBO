# The RUMBO method

RUMBO is a five-phase cycle for building software with AI coding agents end to end:
**R**ecognize the problem, **U**nify the intent into specs and evals, **M**odel the architecture
and risks, **B**uild with ANCLA, and **O**perate/Observe/Optimize. A **human compass** runs
across all of it and protects the technical judgment of whoever leads the project.

The thesis is unglamorous: **AI amplifies the engineering discipline that already exists, but
it does not replace it**. That is why RUMBO invests in executable verifiers, machine-readable
architecture, small batches, observability and deliberate learning, and not in sophisticated
prompts or "virtual teams" of agents.

The operational instructions for the agent live in [`RUMBO.md`](../RUMBO.md). This document
explains the **why**. The Spanish source documents this method was distilled from are
summarized here; the evidence is dated to October 2026.

---

## 1. Findings that ground it (evidence as of October 2026)

### 1.1 AI amplifies discipline; it does not substitute for it
- **DORA 2025** (≈5,000 professionals): AI adoption is associated with more delivery
  *throughput* **and** more instability. "AI's primary role in software development is that of
  an amplifier." Without robust controls (automated tests, mature version control, fast
  feedback) more change volume produces instability. *Correlational, not causal.*
- **METR (RCT, July 2025):** experienced developers took 19 % longer with AI while believing
  they had been 20 % faster. **Lesson: perceived productivity is not a metric.**
- **Code quality (GitClear, vendor data):** "moved" code (refactoring) falls, duplication
  rises. *A signal, not a proof.*
- **Security (Veracode, vendor data):** 45 % of generated code samples failed security tests,
  with no improvement by model size.

### 1.2 Cognitive debt is real and manageable
- **Anthropic (RCT, January 2026, n=52):** developers who learned a library with AI scored
  17 % lower on comprehension without being significantly faster. The largest gap was in
  *debugging*. Asking conceptual questions and requesting explanations protected learning;
  delegating everything was the most harmful pattern. *Small RCT, one task, a new library — it
  did not measure the erosion of consolidated skills.*
- **MIT Media Lab (preprint, essay writing, educational context):** writing first without help
  and then using the LLM ("Brain-to-LLM") preserved recall better. The authors note it may
  not generalize.
- **Thoughtworks Radar vol. 34 (April 2026):** *codebase cognitive debt* = the gap between the
  implementation and the team's shared understanding.

### 1.3 The "harness" is the unit of design when programming with agents
- **OpenAI, "Harness engineering" (February 2026):** give the agent "a map, not a 1,000-page
  instruction manual", make the repository the source of truth, enforce architecture
  invariants mechanically and do continuous garbage collection of "AI slop".
- **Birgitta Böckeler (April 2026):** the harness is made of **guides** (feed-forward:
  AGENTS.md, documentation) and **sensors** (feedback: linters, tests, structural tests,
  reviewing agents), each either computational (deterministic) or inferential (an LLM). She
  also acknowledges that **verifying behavior remains the weak point** — a sensor catches
  failures but does not prove the agent understood the task.
- **Kent Beck, "Augmented Coding":** strict TDD, separate structural from behavioral changes
  (Tidy First), and watch three alarms: loops, unrequested functionality and "any indication
  that the genie was cheating, for example by disabling or deleting tests".

### 1.4 LLM applications: simple first, evals that come from errors, security from the architecture
- **Anthropic, "Building effective agents":** distinguish *workflows* (predefined code paths)
  from *agents* (the LLM directs its own process); start with the simplest thing and invest in
  the agent–computer interface (ACI).
- **Husain and Shankar (Evals FAQ):** "Error analysis is the most important activity in
  evals." Open coding (≥30 traces by hand) → axial coding (a failure taxonomy with counts) →
  only then evaluators. "Start with error analysis, not infrastructure."
- **Security:** OWASP Top 10 for LLM Applications 2025 and for Agentic Applications 2026
  (*least agency*). Simon Willison's **lethal trifecta**: private data + untrusted content +
  external communication; "the only way to stay safe is to avoid that lethal trifecta
  combination entirely". There is no proven defense against prompt injection ("The Attacker
  Moves Second", 2025: 12 recent defenses evaded with >90 % success, 100 % in human red
  teaming with 500 participants): defenses are mostly architectural. Meta's "Agents Rule of
  Two" (October 2025) asks that an agent satisfy no more than two of: processing untrusted
  input, accessing sensitive systems or private data, and changing state or communicating
  externally.
- **Observability:** OpenTelemetry's GenAI conventions are still in "Development" status: use
  them, but pin versions and isolate the instrumentation.

### 1.5 Prototyping is cheap; the risk is mistaking the prototype for the product
- **Cagan/SVPG:** separate *build to learn* from *build to earn*. RUMBO adopts it with a
  twist: **the prototype is the spec, but never the production code**.

---

## 2. The five phases

| Phase | Question | Artifacts | Gate |
|---|---|---|---|
| **R: Recognize** | Is it worth it? For whom? | Brief, opportunity tree, assumptions log, throwaway prototypes | **G1:** problem validated with ≥5 users or real data; success metric; prototype outside `src/` |
| **U: Unify** | What must it do and how will we know? | Light PRD, EARS specs with acceptance criteria, quantified NFRs, eval plan, seed dataset | **G2:** every requirement has a verifier or a planned eval; NFRs have numbers |
| **M: Model** | How do we structure it to change it safely? | ADRs (MADR), C4 1–2, import-linter contracts, fitness functions, light threat model | **G3:** contracts in CI; threat model reviewed; workflow vs. agent decision documented |
| **B: Build with ANCLA** | Is every change correct, small and understood? | Plan per task, tests, small diffs, AGENTS.md | **G4 (per PR):** CI green with smoke evals; human review + second model; the human can explain the change |
| **O: Operate, Observe, Optimize** | Does it work in production, what does it cost, how does it evolve? | CI/CD, SLOs, LLM traces, runbook, postmortems, debt backlog | **G5:** SLOs and alerts active; rollback rehearsed; cost budget with alerts |
| **Human compass** (transversal) | Do we still understand and decide? | Decisions log, no-AI sessions, diff explanation | If the human cannot explain a module, it is not merged |

ANCLA (Anchor, Navigate, Close, Limit, Learn-and-measure) **is phase B**, but its principles
apply recursively to the rest: in R, "anchor" means fixing metrics; in M, fixing contracts.

### 2.1 R — Recognize
1. **One-page brief**, written first by the human: problem, user, evidence, outcome metric
   and riskiest assumptions.
2. **Opportunity tree** (Teresa Torres): outcome → opportunities → solutions → experiments.
   AI can synthesize interviews, but **the human runs the interviews**.
3. **Prototypes to learn** in `spikes/`, throwaway and validated with users or real data.
4. **Rule against the "zombie prototype":** nothing in `spikes/` is merged into `src/`. What
   moves on is *what was learned*, reimplemented with ANCLA.
5. **LLM products:** a feasibility test with 20–30 real questions, run "by hand".

### 2.2 U — Unify
1. **Light PRD** (1–2 pages): goals, non-goals, users, flows, NFRs with numbers, risks.
2. **EARS specs**, one pattern per requirement: ubiquitous ("The system shall…"), event
   ("When…"), state ("While…"), unwanted ("If…, then…") or optional ("Where…"). Each
   requirement carries executable criteria in pytest/Hypothesis.
3. **Quantified NFRs:** p95 latency, maximum cost per request, SLO, privacy, tools allowed to
   the agent, maintainability.
4. **Eval plan:** quality dimensions, seed dataset and provisional thresholds. The definitive
   evaluators *come out of* the error analysis.
5. **AI without delegating judgment:** the agent *interrogates* the spec; the human decides
   and signs.

### 2.3 M — Model
1. **Modular monolith** with a light hexagonal architecture: `domain/` (pure), `app/` (use
   cases), `adapters/` (APIs, LLM, DB) and `api/`. The agent touches one module per task.
2. **MADR ADRs with an executable fitness function**: the ADR stops being documentation and
   becomes a sensor.
3. **C4 levels 1–2** in Mermaid inside the repo.
4. **Workflow vs. agent decision** in an ADR: a single call with retrieval → a workflow → an
   autonomous agent only if the evals prove it is needed.
5. **Tool design (ACI):** few tools, parameters typed with Pydantic, validated enums and
   ranges, bounded outputs.
6. **One-hour threat model:** data flow, STRIDE per trust boundary, the OWASP LLM/Agentic
   checklist and the trifecta test: which leg do we remove, and which sensor proves it?

### 2.4 B — Build with ANCLA

| Step | Guides (feed-forward) | Sensors (feedback) | Who |
|---|---|---|---|
| **A**nchor in verifiers | EARS spec with ids (`F-000N.Rk`); tests that name their requirement, reviewed by the human before the code | pytest in red; Hypothesis; strict pyright in `domain/`; `rumbo trace` (requirement → test) | Human approves; agent drafts the tests |
| **N**avigate with a plan | AGENTS.md ("a map, not a manual"); linked ADRs | Plan review before coding | Agent proposes; human approves |
| **C**lose the loop in a sandbox | Documented verification commands | ruff, pyright, pytest, import-linter, bandit, pip-audit, regression evals | Agent iterates until green |
| **L**imit and review | Diffs <~300 lines; structure separated from behavior; one writing agent; PR template with the checklist | Human review + second model; Beck's alarms; new-dependency verification (slopsquatting); guided review (`rumbo tour`); Claude Code hooks that block edits to the sensors, template-mark removal, approval marks, force pushes and a red `make check` at Stop | Human decides the merge |
| Learn **A**nd measure | Findings turned into rules | `rumbo metrics` (sessions per spec, gates signed, deploy/incident proxies, requirements traced, eval runs), mutmut on critical modules, reverted PRs, pass^k | Both |

**Golden rule of the harness:** if the agent makes the same mistake twice, add a sensor (test,
lint rule, contract) or a guide, besides fixing the code.

**Rules are prose until a hook enforces them.** AGENTS.md says "never touch the Makefile" and
"never remove the template mark"; nothing in a prompt guarantees it. RUMBO wires
`scripts/rumbo_guard.py` as Claude Code `PreToolUse` and `Stop` hooks (A6 in RUMBO.md): a
blocked call returns the reason to the agent, which has to ask the human. The hook never binds
the human, who edits by hand or overrides it locally. This is Böckeler's sensor/guide split
applied to the harness itself, and the cheapest place to start when a mistake repeats.

### 2.5 O — Operate, Observe, Optimize
1. **CI/CD:** trunk-based with short branches; automatic deploy to staging and manual
   approval to production until evals and SLOs earn trust.
2. **Deployment and rollback:** feature flags for new LLM functions, immutable images
   (tag = SHA) and a one-command rollback, **rehearsed** before launch.
3. **Classic observability:** OpenTelemetry with 2–3 SLIs/SLOs and an error budget. When the
   budget is exhausted, features freeze.
4. **LLM observability:** `gen_ai.*` spans with model, tokens, tool and cost. The skeleton
   emits them from day one as JSON log records (`app/telemetry.py`, no dependency) inside a
   per-request budget (`app/budget.py`: tokens, cost, time; the overrun becomes an abstention);
   shipping them to Langfuse, Phoenix or an OTLP collector is an adapter added later. Prompts
   are versioned in the repo and their hash lands in the trace. Production traces become eval
   cases (`rumbo eval add`); open-ended ones get an LLM judge only after calibration against
   ≥30 human labels (TPR/TNR ≥ 0.8).
5. **Incidents:** a runbook per failure mode and blameless postmortems that answer "which
   sensor was missing?".
6. **Maintenance:** Dependabot/Renovate weekly with a cooldown; biweekly cleanup with Tidy
   First refactors; a clone detector; documentation updated in the same PR.

### 2.6 Human compass
- **Conceptual mode while learning:** ask for explanations and use AI to check what you
  understand, not to delegate.
- **Me first, then the AI** in design: the human sketches the ADR, the data model or the
  prompt before asking the agent.
- **Explanation test:** before a relevant merge, the human explains the change in 3 sentences
  without looking at the agent's summary.
- **One weekly *debugging* session without AI**, because that was the largest gap in the
  Anthropic RCT.
- **When NOT to use agents:** when learning something that must be mastered; in irreversible
  decisions; in security code without prior tests; when the context is not in the repo; when
  specifying the task takes longer than doing it.

---

## 3. Reviewing agent-written changes without drowning the human

`rumbo tour` turns a git range into a guided review. Its design follows the evidence on how
reviewers actually read and learn:

- **Small, single-concern changes** (Google's engineering practices; Tidy First): the guide
  classifies each commit as behavioral, structural, tests, docs or mechanical and flags the
  ones that mix concerns or exceed a ~300-line sitting budget (SmartBear/Cisco, 2006).
- **Reading order is not alphabetical.** Showing the defective file last lowers the odds of
  catching the defect by 64 % (Fregnan et al., ESEC/FSE 2022); 66 % of developers want
  custom ordering and recommend tests first and dependency-aware grouping (Rahman et al.,
  ICSE 2026; Bouraffa et al., 2025). The guide orders tests first, then the files the subject
  names, then the largest diff.
- **Context-building before inspection** (Wurzel Gonçalves et al., ICPC 2025): intent, kind
  and risk hotspots come before any file.
- **Describe from the diff, never from the message.** 1.7 % of agentic PRs claim changes they
  do not implement, and those merge at 28 % vs 80 % (Gong et al., MSR 2026). The guide is
  built from `git show`, and `/rumbo-tour` asks the agent to write questions from the diff.
- **Comprehension gates and explaining back** preserve understanding, delegation erodes it
  (Anthropic RCT 2026; the open-source "Commit Comprehension Gate"). Each commit ends with
  three explanation-test questions whose hints are folded away; the human answers first.
- **Re-review with `git range-diff`** instead of re-reading the whole series.

### 3.1 Working within the agent's effective context

The claim that models "degrade past ~200k tokens" has no primary source: 200k is Claude's
historical window, a pricing boundary and Claude Code's old compaction point. What the
controlled studies show is a gradient that starts far earlier, and that matters more for
*acting* on code than for *reading* it (full report with sources:
`reports/Desarrollar con agentes bajo límite de contexto.md`):

- Effective context (≥85 % of short-context performance) is 2k–8k tokens on associative
  retrieval (NoLiMa 2025: Claude 3.5 Sonnet 4k of 200k advertised) and 16k–64k on direct
  retrieval (RULER 2024). Degradation is measurable from 500–3,000 tokens (Levy et al. 2024).
- In code, comprehension holds up to 1M, but bug fixing collapses: Claude 3.5 Sonnet solves
  29 % of LongSWE-Bench issues at 32k, 15 % at 128k, 3 % at 256k (LongCodeBench 2025).
  Multi-turn conversations lose 39 % on average, mostly through inconsistency (Laban 2025).
- The cheapest context management works best: masking old tool observations halves cost and
  matches or beats LLM summarization on SWE-bench Verified; summaries lengthen trajectories
  13–15 % (JetBrains, NeurIPS 2025 workshop). Vendors converge on root instructions of ~100
  (OpenAI) to <200 (Claude Code) lines.
- Parallelism helps reading, not writing: native concurrency cost Claude Code 24 points on
  SWE-bench Verified and helped only on long-horizon tasks (Li et al. 2026); two parallel
  writers pay a 30–35 point "coordination tax" that only an integrator holding a spec with
  explicit data structures recovers (88.9 % vs 24.6 %; Specification Gap 2026); merge conflicts
  go from ≈10 % at 2 changed lines to ≈30 % at 25 (AgenticFlict 2026). For 1–3 people the
  binding constraint is human review, not tokens.

RUMBO's answers are deliberately boring: one spec per fresh session; a "Data contracts and
ownership" section in every spec; `make check-brief` and `# Compact instructions` so noise
never reaches the context; decisions in files; one writer per set of files with read-only
parallel agents; a fresh-context reviewer (`/rumbo-review`) that traces every requirement
before the human opens `rumbo tour`; and a self-measurement nobody has published: how many
sessions each spec takes to close (`rumbo decision "F-000N closed" --why "1 session"`).

---

## 4. RACI: human vs. agent

A = approves/accountable, R = does the work, C = consulted.

| Activity | Human | Agent | The human verifies | The agent or a sensor verifies |
|---|---|---|---|---|
| Problem and success metric | **A/R** | C (challenges assumptions) | User evidence | — |
| Interviews | **A/R** | C (scripts, synthesis) | Fidelity of the synthesis | — |
| Discovery prototypes | A | **R** | What was learned | That it works for the demo |
| PRD and EARS specs | **A** | R (draft, ambiguities) | Every requirement and non-goal | Consistency, edge cases |
| NFRs and budgets | **A/R** | C | Realistic numbers | Token and cost estimates |
| Error analysis and evals | **A/R** (open coding by hand) | R (preliminary axial coding, synthetic data) | Final taxonomy, gold labels | TPR/TNR of the LLM judge |
| ADRs and architecture | **A** (sketches first) | R (options, fitness functions) | Trade-offs, reversibility | Contracts in CI |
| Threat model | **A/R** | C (enumerates threats) | Priorities, accepted risks | Adversarial tests, SAST |
| Acceptance tests | **A** | R | That they test the intent | That they fail before the change |
| Implementation | C | **R** | Diff and explanation test | Full CI |
| PR review | **A** | R (second model) | Design, security, tests intact | Lint, types, architecture, SAST |
| Deployment | **A** (go/no-go) | R (pipeline) | Rollback plan | Smoke tests, post-deploy evals |
| Incidents and postmortems | **A/R** | C/R (diagnosis, draft) | Root cause, new sensor | Trace correlation |
| Debt and maintenance | **A** (prioritizes) | R (refactors, upgrades) | That the structure improves | Tests, mutation score, duplication |

**Permanent split:** the human owns the *what* and the *why*: problem, specs, irreversible
decisions, accepted risks, merge, go/no-go and understanding of the system. The agent owns the
drafts, the alternatives, the verified implementation and the vigilance (review, trace
analysis, cleanup). No decision by the agent reaches production without passing through a
sensor or the human.

---

## 5. Recommended Python stack

| Layer | Recommended | Alternatives | Note |
|---|---|---|---|
| Environment | **uv** | Poetry | Reproducible lockfile; idempotent commands for the agent |
| Lint/format | **ruff** | black + flake8 | Fast in pre-commit and in the agent's loop |
| Types | **pyright** (strict in `domain/`) or mypy | basedpyright | Cheap sensor against hallucinated APIs |
| Tests | **pytest + Hypothesis** | — | Tests-first and PBT |
| Mutation | **mutmut** (nightly, critical modules) | cosmic-ray | Detects weak or cheating tests |
| Architecture | **import-linter** | pytestarch, tach | Fitness functions for the ADRs |
| Hooks/CI | **pre-commit + GitHub Actions** | lefthook, GitLab CI | The same sensors for human and agent |
| SAST | **bandit** (+ semgrep from week 3) | CodeQL | Per Veracode, prompting is not enough |
| Dependencies | **pip-audit + Dependabot/Renovate** | OSV-Scanner, Socket | Supply chain and slopsquatting |
| Advanced supply chain | SBOM CycloneDX (from `uv.lock`), OpenSSF Scorecard, SLSA attestations | — | MVP and above |
| Secrets | Platform secret manager + **gitleaks** (hook and CI) | trufflehog | The agent never sees production secrets |
| API | **FastAPI + Pydantic v2** | Litestar | Validation at the boundary |
| LLM | Official SDK, **PydanticAI**, or the Claude Code CLI (`claude -p`) | LangGraph only if a graph is needed | Start with direct APIs |
| Evals | pytest + versioned JSONL; **Inspect AI** or **promptfoo** | DeepEval | Evals in CI as tests |
| LLM observability | **Langfuse** or **Arize Phoenix** | LangSmith, Logfire | Ingest OTel GenAI; watch licenses |
| General observability | **OpenTelemetry** → Grafana Cloud | Honeycomb | Open standard |
| Deployment | **Fly.io, Railway, Render or Cloud Run** | VPS + Docker Compose, Modal | No Kubernetes for 1–3 people |
| Data | **Postgres** (+pgvector) or **DuckDB/Parquet** | SQLite | |
| Flags | Environment variables; **Unleash/GrowthBook** later | LaunchDarkly | Proportional to size |

`rumbo init` installs the core with `uv add` (ruff, pyright, pytest, Hypothesis, pytest-cov,
import-linter, pre-commit, pip-audit, bandit, mutmut) and configures gitleaks as a hook and as a
CI job. The rest is added in its phase, verified with `rumbo verify-dep` and approved by the
human.

---

## 6. Scaling the rigor to the risk

| Practice | Prototype/hackathon | MVP | Critical production |
|---|---|---|---|
| Product | 5-line brief | Brief + light PRD | PRD reviewed with stakeholders |
| Specs | Criteria list | EARS on core features | EARS everywhere, requirement→test traceability |
| ADRs | 0–1 | Irreversible decisions | All significant ones + fitness function |
| Tests | Smoke | Unit + integration + Hypothesis | + mutation, contract tests with external APIs |
| Evals | 20 questions by hand | Error analysis + 50 cases + smoke in CI | 100 cases, validated judge, pass^k, OWASP red teaming, online evals |
| Security | No secrets in the code | Threat model, bandit, pip-audit, gitleaks | + SBOM, Scorecard, SLSA, red team, privacy review |
| Observability | Logs | OTel + Langfuse/Phoenix + cost alerts | SLOs with error budget, complete runbooks |
| Deployment | Local | PaaS with rehearsed rollback | Canary/flags per cohort |
| Review | Informal | Human + second model | + two pairs of eyes in critical areas |
| Human compass | Optional | Explanation test | Mandatory + documented mental models |

**Decision rule:** what matters is the **worst plausible impact**, not the size of the code. An
agent that hands out energy figures used in commercial decisions is, at the very least, an
"MVP with production controls" as far as accuracy goes.

---

## 7. Anti-patterns across the cycle

| Phase | Anti-pattern | Antidote |
|---|---|---|
| R | **Zombie prototype:** the weekend spike ends up in production | Separate `spikes/`; reimplement with ANCLA |
| R | **Building because it is cheap** | Brief with a metric; G1 |
| U | **Spec theater:** long AI-generated specs nobody reads | Short EARS + verifiers; human signature |
| U | **Generic evals** ("helpfulness 4.2/5") | Error analysis first |
| M | **Agent by default** with 15 tools | Workflow vs. agent ADR |
| M | **Dead ADR** the agent violates | One fitness function per ADR in CI |
| M | **One-person microservices** | Modular monolith |
| B | **The cheating genie:** deleted tests or weakened asserts | Rule in AGENTS.md + CI sensor + mutmut |
| B | **Encyclopedic AGENTS.md** | "A map, not a manual" (≤100 lines) |
| B | **Several agents writing at once** | One writer; the rest only review |
| B | **Copying instead of reusing** | Clone detector; biweekly cleanup |
| B | **Reviewing alphabetically, all at once** | `rumbo tour`: risks first, tests first, 300-line sittings |
| O | **Rollback never tried** | Rehearse it before launch |
| O | **Invisible costs** | Budget per request, alerts, hard limits |
| O | **Traces nobody reads** | 30-minute weekly ritual |
| Human | **Cognitive debt:** "I don't know how it works, but CI passes" | Explanation test; no-AI session |
| Human | **Perceived productivity** | Measure (in METR the perception was off by ~40 points) |

---

## 8. Adoption plan

### Week 1: the harness and the compass

| Day | Human | Agent |
|---|---|---|
| 1 | Picks the pilot and writes the brief by hand | Critiques the brief and proposes interview questions |
| 2 | Schedules 3–5 conversations and gathers real questions | `rumbo init`: skeleton, uv, pre-commit and CI; the human reviews every file |
| 3 | Writes 3 EARS specs | Lists ambiguities and proposes acceptance tests |
| 4 | Writes ADR-0001 with their own opinion first | Completes options and proposes the import-linter contract |
| 5 | One-hour threat model | Enumerates threats and proposes adversarial tests |
| 6–7 | First complete ANCLA task and the explanation test | Implements with TDD in a sandbox; another session acts as second reviewer |

**Week-1 gate:** CI green (ruff, pyright, pytest, lint-imports, gitleaks, pip-audit);
AGENTS.md ≤100 lines; one ADR with a fitness function; ≥20 real questions in the dataset (LLM
products).

### Month 1
- **Week 2:** domain core and deterministic evals. First error analysis over ~50 traces.
- **Week 3:** API, minimal UI, observability (OTel + Langfuse/Phoenix), cost alerts, semgrep
  and a prompt-injection suite.
- **Week 4:** deployment with a rehearsed rollback, SLOs, runbook, 3–5 beta users behind a flag
  and a retro: which sensor was missing? what is superfluous in AGENTS.md?
- **Metrics from day 1:** the four DORA metrics (deployment frequency, lead time, change
  failure rate, time to restore); cost per request and online accuracy; percentage of PRs that
  passed the explanation test; weekly hours without AI.

---

## 9. Existing frameworks: what RUMBO takes from each

| Framework | Strengths | Weaknesses | What RUMBO takes |
|---|---|---|---|
| BMAD Method | Covers analysis to dev/QA with personas and scales the process | Ceremony, lots of text; spec-theater risk; no computational sensors | Scale the process with complexity; brief→PRD→architecture handoff |
| Spec Kit / Kiro / OpenSpec / Tessl | The spec as an artifact; EARS (Kiro) | Modest evidence; non-executable specs age | EARS linked to tests and evals |
| Product model / dual-track (Cagan, Torres) | Continuous discovery; prototype as spec | Does not cover engineering with agents | Phase R |
| Lean Startup / Shape Up | Experiments, abandonment criterion, bets with "appetite" | Risk of "MVP = bad code"; Shape Up is for teams | Assumptions log; 2–4-week cycles |
| XP | TDD, CI, small batches, refactoring | Human pair programming rarely applies | The core of ANCLA |
| "AI-native" SDLCs from consultancies | Organizational vision | Generic and often marketing | Measure outcomes, not activity |
| Harness engineering (OpenAI, Böckeler) | Guides + sensors, mechanical invariants, cleanup | Centered on phase B | Phase B and "repeated mistake → sensor" |
| DORA AI Capabilities Model | Statistical backing | Designed for organizations | Small batches, version control, user focus, the repo template as a "platform" |

---

## 10. Open questions and limits of the evidence

1. **There are no controlled studies of the full lifecycle with agents.** The evidence is
   fragmentary: RCTs on isolated tasks (METR, Anthropic), surveys (DORA), vendor data
   (GitClear, Veracode) and practitioner cases (OpenAI, Beck, Böckeler). RUMBO is a reasoned
   synthesis, not a validated method: treat it as a hypothesis and measure.
2. **Real productivity with 2026 agents is unknown.** METR called its own 2026 data unreliable.
3. **The Anthropic RCT measured learning a new library**, not the erosion of consolidated
   skills; the MIT study is a preprint about essay writing. The human-compass practices are
   prudent, but their efficacy is unproven.
4. **GitClear and Veracode figures come from vendors** that sell solutions to those very
   problems. The direction is consistent; the magnitudes do not generalize.
5. **Prompt injection has no known robust solution.** Defenses are mostly architectural;
   detectors are one more layer, not a guarantee.
6. **OTel GenAI is still in "Development":** isolate the instrumentation and pin versions.
7. **Scale:** the framework is calibrated for 1–3 people.
8. **Not verified in primary sources during the original research:** the details of MADR 4.0
   and the content of Husain and Shankar's book. Confirmed: Meta's "Agents Rule of Two" and
   the acquisition of Langfuse by ClickHouse (January 2026) — watch the license.
9. **The guided-review design (section 3) rests on reviewer studies of human-written code**;
   whether the same ordering and question effects hold for agent-written changes has not
   been measured. Cross-model review has anecdotal support only.
