# Parallelizing software development with multiple LLM coding agents (2024–2026), for 1–3-person teams

Research date: 2026-11-01. Every finding is dated and tagged **[vendor]** (claim by the tool's maker), **[independent]** (peer-reviewed or third-party empirical work), or **[practitioner]** (individual/blog report). Several primary vendor pages (cognition.ai, openai.com, cursor.com, docs.devin.ai, jules.google, dora.dev, faros.ai, dev.to, thoughtworks.com, addyosmani.com) could not be fetched from this session's network; where a claim rests on search-engine excerpts of those pages it is marked **[secondary]**.

---

## Key question 1 — Subagents with fresh context: what do vendors report about when parallelism helps and when it hurts?

### Takeaway
Every vendor converges on the same rule: parallel fresh-context agents pay off for *breadth-first, independent, read-heavy* work (research, review, exploration, competing hypotheses) and for tasks that exceed one context window; they hurt for *tightly coupled, same-file, sequential* work, and they cost 1.4×–15× more tokens. Anthropic's own 2025 writing explicitly says coding has fewer parallelizable parts than research, and the 2026 Claude Code docs now say "running out of context is rarely the problem anymore" and position subagents as a parallelism/independent-review tool, not a context-rescue tool.

### Cited Findings

**Anthropic (orchestrator-workers, research system) [vendor]**
- 2024-12-19: Anthropic's "Building effective agents" defines *parallelization* (sectioning: "Breaking a task into independent subtasks run in parallel"; voting: "Running the same task multiple times") and *orchestrator-workers* ("a central LLM dynamically breaks down tasks, delegates them to worker LLMs, and synthesizes their results"), suited to "complex tasks where you can't predict the subtasks needed", with the coding example of products that "make complex changes to multiple files each time"; it recommends "finding the simplest solution possible, and only increasing complexity when needed" — [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- 2025-06-13: Anthropic's multi-agent research system (Opus 4 lead + Sonnet 4 subagents) beat single-agent Opus 4 "by 90.2% on our internal research eval"; multi-agent systems use "about 15× more tokens than chats" (agents ≈4× chat); token usage alone explained 80% of performance variance on BrowseComp; the lead spawns 3–5 subagents in parallel and research time dropped "by up to 90%" for complex queries — [Anthropic, How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- Same post: multi-agent "hurts" for tasks needing shared context or many inter-agent dependencies; the authors state most coding tasks have fewer parallelizable parts than research; only high-value tasks justify the token cost; LLM agents are "not yet great at coordinating and delegating" in real time — [same source](https://www.anthropic.com/engineering/multi-agent-research-system)
- Same post, observed coordination failures: early versions spawned ~50 subagents for simple queries; vague delegation produced duplicated work (two subagents researched the same 2025 supply-chain topic); agents distracted each other with excessive updates; synchronous execution meant the lead could not steer subagents and stalled on the slowest one — [same source](https://www.anthropic.com/engineering/multi-agent-research-system)
- 2025-09-29: Anthropic "Effective context engineering" names *context rot* ("the model's ability to accurately recall information from that context decreases" as it grows) and describes sub-agent architectures where each sub-agent "may use tens of thousands of tokens" but returns "a condensed, distilled summary of its work (often 1,000–2,000 tokens)"; best "where parallel exploration pays dividends"; alternatives for long tasks are compaction and structured note-taking — [Anthropic, Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

**Claude Code subagents / agent teams (docs as of 2026-11) [vendor]**
- Subagents: "Each subagent runs in its own context window with a custom system prompt, specific tool access, and independent permissions"; a non-fork subagent "doesn't see your conversation history, the skills you've already invoked, or the files Claude has already read"; results come back summarized; default cap of 20 concurrent subagents (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`); nesting up to three layers; `isolation: worktree` frontmatter runs a subagent in a temporary git worktree — [Claude Code docs, Subagents](https://code.claude.com/docs/en/sub-agents)
- When *not* to use subagents (docs): "The task needs frequent back-and-forth or iterative refinement", "Multiple phases share significant context, such as planning, implementation, and testing", "Latency matters. A subagent that isn't a fork starts fresh and may need time to gather context". Parallel subagents "work best when the research paths don't depend on each other" — [same](https://code.claude.com/docs/en/sub-agents)
- Best-practices page (2026): "a fresh subagent first has to gather context your conversation already had, so it can end up *slower* than doing the task where you are"; "You get parallelism or a fresh pair of eyes out of it, but not a smarter answer"; and the explicit reversal of older advice: "Use subagents to protect your context window → Running out of context is rarely the problem anymore. Subagents are still great for parallel work, keeping noisy output out of your way, and independent review, but each of those costs extra tokens" (most current models have a 1M-token window with auto-compaction) — [Claude Code docs, Best practices](https://code.claude.com/docs/en/best-practices)
- Agent teams (experimental, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`): teammates are full sessions with a shared task list and mailbox; "Agent teams add coordination overhead and use significantly more tokens than a single session... For sequential tasks, same-file edits, or work with many dependencies, a single session or subagents are more effective"; "Start with 3-5 teammates"; "Three focused teammates often outperform five scattered ones"; "Two teammates editing the same file leads to overwrites. Break the work so each teammate owns a different set of files"; "Start with research and review" before parallel implementation; known limitations include "Task status can lag" and no nested teams — [Claude Code docs, Agent teams](https://code.claude.com/docs/en/agent-teams)
- Strongest use cases listed by Anthropic for teams: research/review, "New modules or features: teammates can each own a separate piece", "Debugging with competing hypotheses", "Cross-layer coordination" — [same](https://code.claude.com/docs/en/agent-teams)

**OpenAI Codex [vendor, secondary]**
- 2025-05 (research preview, Product Hunt listing 2025-05-17): Codex described as a cloud agent that "can work on many tasks in parallel", each task in "an isolated environment in the cloud, preloaded with the user's repository", guided by `AGENTS.md`; powered by codex-1 (o3 variant) — [Gigazine coverage](https://gigazine.net/gsc_news/en/20250519-openai-ai-agent-codex); [Product Hunt listing](https://www.hunted.space/dashboard/openai-o1/launches/codex-by-chatgpt)
- 2026-10 [independent]: Codex's native "multiagent v2" mode is one of the three dynamic-concurrency mechanisms benchmarked by Li et al. (see KQ4); under concurrency Codex's token use per solved task rose from 7.69M to 24.12M — [Li et al., When Sub-Agents Work in Parallel, arXiv 2610.10263](https://arxiv.org/abs/2610.10263)
- OpenAI Agents SDK "handoffs" docs could not be fetched (openai.github.io blocked); no primary statement obtained.

**Cursor [vendor, secondary]**
- 2025-10-29: Cursor 2.0 can run "up to eight agents at once", using "Git worktrees and remote trees" so they don't interfere; Composer model claims ~4× speed — [SD Times](https://sdtimes.com/ai/cursor-2-0-enables-eight-agents-to-work-in-parallel-without-interfering-with-each-other); [The Decoder](https://the-decoder.com/cursor-2-0-shifts-to-in-house-ai-with-composer-model-and-parallel-agents/)
- Limits reported by third parties: subagents cannot communicate during execution; background agents only work with GitHub repos and are billed separately at API pricing — [Medium guide](https://medium.com/towards-data-engineering/parallel-ai-agents-in-cursor-2-0-a-practical-guide-e808f89cffb9); [morphllm comparison](https://www.morphllm.com/comparisons/cursor-agent)
- 2026-04-02: Cursor 3 rebuilt the UI around an "Agents Window" ("you manage agents, not files") — [neura.market review](https://neura.market/directories/cursor/blog/devto-3487552)

**Devin / Cognition [vendor, secondary]**
- 2025-04 (Devin 2.0): "Spin up more than one Devin at a time, each with its own interactive, cloud-based IDE"; "Managed Devins" run in parallel, each "a full Devin, with its own VM, terminal, browser, and testing infrastructure" — [fast.io Devin 2.0 guide](https://www.fast.io/resources/devin-2-0-guide.md); [agentwiki](https://agentwiki.org/devin)
- 2026-01-22: "Devin Review" launched for inspecting agent-produced PRs — [agentwiki](https://agentwiki.org/devin)
- No primary Cognition page on "PR stacks" was reachable; the phrase did not surface in search results.

**Google Jules [vendor, secondary]**
- 2025-08-06 GA: Jules "securely clones the codebase into a dedicated Google Cloud virtual machine" and "can run several tasks at the same time"; free tier "fifteen tasks per day, with a maximum of three simultaneous"; shows plan before changes; beta produced "over 140,000 publicly shared code improvements" — [SiliconANGLE](https://siliconangle.com/2025/08/06/google-makes-jules-ai-coding-agent-available-everyone-free-paid-plans); [IT Brief](https://itbrief.co.uk/story/google-launches-jules-globally-an-ai-coding-agent-for-github)

**Independent cross-vendor evidence on when native concurrency helps [independent]**
- 2026-10-07, Li et al. (Nanjing U. / UIUC), 2,124 executions, $20,780 of API spend, Codex (GPT-5.4) / Claude Code (Opus 5) / Kimi Code: "dynamic concurrency benefits most on long-horizon tasks, while bounded tasks often see little or negative improvement"; tokens 1.41–3.31× sequential; on SWE-bench Verified Claude Code's task pass rate *fell 24.0 points* under concurrency but *rose 14.3 points* on the longest-horizon benchmark (LoopsBench); overall declines for Claude Code (p=5×10⁻⁷) and Kimi (p=0.013) statistically significant; mean runtime *increased* in 14 of 15 agent-benchmark combinations (Claude Code 27.8 min vs 17.5 min per SWE-bench task); only 12.6% of tasks solved in both modes were ≥20% faster concurrently — [arXiv 2610.10263](https://arxiv.org/abs/2610.10263)
- Same paper, where concurrency *did* help: "complementary work", "alternative attempts and selection" (e.g., 292/391 tests vs 0/391 sequential on one task), and "independent validation" (a sub-agent probing an assumption and correcting the main agent) — [same](https://arxiv.org/abs/2610.10263)

### Inferences
- The vendors' own guidance has converged with the independent data: parallel fresh-context agents are a *throughput* and *independence-of-judgment* tool, not a cure for context limits; with 1M-token windows and auto-compaction, the "context limit" motivation for subagents is weaker in late 2026 than in 2025.
- For a 1–3-person team, the cheapest high-value uses are the ones every vendor lists first: parallel *read-only* work (exploration, review from a fresh context, competing-hypothesis debugging) and *verification by an agent that did not write the code*. Parallel *implementation* is where cost and failure modes concentrate.
- Expect 1.5–3× token cost for in-session concurrency and roughly 15× for a full orchestrator-workers research run; latency does not reliably drop.

### Gaps
- No primary OpenAI, Cursor, Cognition or Google page could be fetched; their numbers above are from press/secondary coverage.
- No vendor publishes success-rate or conflict-rate numbers for parallel *implementation* on real repos; only Anthropic's research-eval number (90.2%, non-coding) and the independent Li et al. benchmark exist.
- OpenAI Agents SDK handoffs vs "agents as tools" (parallel) distinction: not verified from the primary docs.

---

## Key question 2 — Git worktrees as the isolation primitive; merge-conflict rates and how teams avoid them

### Takeaway
"One worktree (or sandbox) per agent" is now the default isolation primitive in Claude Code, Cursor, Codex and the orchestration tools built on them, but it only isolates the *filesystem*; conflicts move to merge time. The one large dataset (AgenticFlict, 107K agent PRs) shows 27.7% of unmerged agent PRs would textually conflict, strongly rising with PR size (≈10% at 2 changed lines, ≈30% at 25, 32–33% at 46–185); controlled experiments show semantic ("passes alone, fails together") conflicts in up to 97% of constructed interface-change pairs. Mitigation that has evidence: small PRs, disjoint file/scope ownership decided *before* work starts, and telling each agent about the concurrent change.

### Cited Findings

**Worktrees as a product feature [vendor]**
- Claude Code: `claude --worktree <name>` (or `-w`) creates `.claude/worktrees/<name>/` on branch `worktree-<name>`, branching from the remote default branch by default (`worktree.baseRef: "fresh"`), or from local HEAD with `"head"`; `.worktreeinclude` copies gitignored files such as `.env`; unnamed clean worktrees are removed on exit; Claude Code *blocks* edits/commands that target the main checkout while isolated ("Claude Code blocks an Edit, Write, or NotebookEdit that targets a path in the main checkout"); subagents can get `isolation: worktree`; `/batch` "splits the work into 5 to 30 units ... runs each unit in its own worktree and opens a PR for it" — [Claude Code docs, Worktrees](https://code.claude.com/docs/en/worktrees); [Best practices](https://code.claude.com/docs/en/best-practices)
- Caveats stated by Anthropic: a worktree "is a fresh checkout, which means gitignored files like `.env` aren't there and dependencies aren't installed"; `.worktreeinclude` "copies rather than links, so `node_modules` in there would mean a full copy per worktree" — [Best practices](https://code.claude.com/docs/en/best-practices)
- Cursor 2.0 (2025-10-29) isolates up to 8 agents via "Git worktrees and remote trees" — [SD Times](https://sdtimes.com/ai/cursor-2-0-enables-eight-agents-to-work-in-parallel-without-interfering-with-each-other). Codex cloud gives each task its own sandbox/worktree-like copy — [Gigazine](https://gigazine.net/gsc_news/en/20250519-openai-ai-agent-codex)

**Worktree managers (open-source / small tools)**
- Vibe Kanban (BloopAI): Apache-2.0, 28.6k stars; "each workspace gives an agent a branch, a terminal, and a dev server"; supports Claude Code, Codex, Gemini CLI, Copilot, Amp, Cursor, OpenCode and others; a `DISABLE_WORKTREE_CLEANUP` env var confirms worktree-based isolation — [GitHub BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban). The company (bloop) shut down 2026-04-10; the project continues as community-maintained open source and moved to a fully local architecture — [vibekanban.com/shutdown](https://vibekanban.com/shutdown); [AI IDE List](https://aiidelist.com/ide/vibe-kanban)
- Crystal (stravu): "Run multiple Codex and Claude Code AI sessions in parallel git worktrees", MIT, 3.1k stars; deprecated 2026-02 in favour of "Nimbalyst" — [GitHub stravu/crystal](https://github.com/stravu/crystal)
- Conductor: described as a standalone macOS orchestration layer coordinating multiple coding-agent runtimes in isolated workspaces with PR workflows — [AI IDE List comparison](https://aiidelist.com/zh/ide/vibe-kanban) (conductor.build itself not reachable)
- Gas Town (Steve Yegge, released ~2026-01) + Beads (git-backed issue tracker): coordinates "20 to 30 Claude Code instances" (another account says 50+) with roles (Mayor, Polecats, Witness, Refinery); early adopters report "roughly $100 per hour in API spend" and "bugs get fixed multiple times by different agents"; described as "a research prototype that got released into the wild" — [ascii.co.uk](https://ascii.co.uk/news/article/news-20260102-190a5f9f/steve-yegge-releases-gas-town-multi-agent-orchestrator-for-c); [Cloud Native Now](https://cloudnativenow.com/features/gas-town-what-kubernetes-for-ai-coding-agents-actually-looks-like/) **[practitioner, secondary]**
- Graphite (stacked PRs; acquired by Cursor 2025-12): stacks let dependent work proceed before earlier PRs merge and "be reviewed in parallel"; merge queue batches and tests PRs in parallel — [Contrary Research](https://research.contrary.com/company/graphite); [morphllm](https://www.morphllm.com/comparisons/graphite-vs-coderabbit) **[vendor/secondary]**

**Merge-conflict rates [independent]**
- 2026-04-04 (v2 2026-05-12), Ogenrwot & Businge (UNLV), *AgenticFlict*, AIware 2026: from AIDev, 142,652 open-or-closed-unmerged agent PRs; 107,026 merge-simulated; **29,609 (27.67%) textually conflict**; conflicting PRs average 4.36 files (median 2), 11.36 conflict regions and 540 conflicting lines — [arXiv 2604.03551](https://arxiv.org/abs/2604.03551)
- Same paper, per agent: Copilot 15.24%, Cursor 19.75%, Devin 22.85%, Claude Code 25.93%, OpenAI Codex 31.85% — [same](https://arxiv.org/abs/2604.03551)
- Same paper, by PR size (churn): ≈9.9% conflict rate at median churn of 2 lines, ≈30% at 25 lines, 32–33% at 46–185 lines; authors recommend "smaller, more focused AI-generated pull requests" — [same](https://arxiv.org/abs/2604.03551)
- Caveat: the sample is *unmerged* PRs only (merged PRs excluded by design), so 27.67% is not the conflict rate of all agent PRs; and no human baseline in the same repos yet — [same](https://arxiv.org/abs/2604.03551)
- 2025-07, Li, Zhang & Hassan (Queen's U.), AIDev dataset (456K+ agent PRs to 2025-06-22): earlier work by Watanabe et al. found merge conflicts contribute to >1.1% of agentic-PR rejections — cited in [AgenticFlict](https://arxiv.org/abs/2604.03551)

**Semantic conflicts (git-clean but broken) [independent]**
- 2026-09-21, Xia, Wu & Park (UIUC/Columbia), *Passes Alone, Fails Together* (benchmark "stale"): on 417 mined Django PR pairs (834 runs) "only one showed interference"; but on constructed tasks with 12 real Django helpers "interference occurred in 97% of runs"; "A message describing the completed concurrent change recovered 82% of runs"; authors warn constructed rates "do not estimate how often these problems occur in practice" — [arXiv 2609.25396](https://arxiv.org/abs/2609.25396)
- 2026-10-05, Muku, *NP-Bench* (single author, tool vendor Nerveplane): proactive scope partitioning + merge ordering raised clean integration from 1/9 to 9/9 scenarios and merge conflicts from 13 to 0 in simulation; on a live breaking contract change, clean integration was 0 for no-coordination and reactive detection, 1.0 (frontier model) / 0.6 (small model) with the planner; gap "grows with the number of agents" — [arXiv 2610.07261](https://arxiv.org/abs/2610.07261) **[vendor-affiliated]**
- 2026-10-07, Li et al. taxonomy of concurrency failures (804 instances in 650 trajectories): "Shared State and Merge Problems" is the largest category at 33.2%; "Concurrent Writes" alone 26.5% of all instances (62.9% of Codex's); "Deliverable Overwrite" 14.9%; "Same-File Collision" 5.2%; "Shared Environment Contamination" 6.7% (e.g., two sub-agents both recreating `/tmp/t1`; a sub-agent replacing the system git executable) — [arXiv 2610.10263](https://arxiv.org/abs/2610.10263)

**Practitioner reports [practitioner, secondary]**
- One developer running five parallel branches reported fewer than 3 conflicts over several weeks, attributing it to choosing truly independent tasks — summarized in search results for [dev.to worktrees post](https://dev.to/mashrulhaque/git-worktrees-for-ai-coding-run-multiple-agents-in-parallel-3pgb) (not fetchable)
- "Git worktrees aren't enough": worktrees isolate files but agents in separate worktrees can write code that reads clean per branch and clashes only when both land on main ("agentic drift"); non-merge friction includes port collisions, shared databases, dependency installs and disk — [dev.to/rollnuts](https://dev.to/rollnuts/git-worktrees-arent-enough-for-parallel-ai-agents-38b7); [Upsun](https://developer.upsun.com/posts/ai/git-worktrees-for-parallel-ai-coding-agents) (neither fetchable; from search excerpts)
- Boris Cherny (Claude Code lead), 2026-02 tip thread: "Spin up 3–5 git worktrees at once, each running its own Claude session in parallel"; secondary write-ups put his personal setup at ~5 terminal + 5–10 web sessions — [Medium summary](https://medium.com/@rub1cc/how-claude-codes-creator-uses-it-10-best-practices-from-the-team-e43be312836f); [skzl-ai playbook](https://skzl-ai.github.io/boris-cherny-claude-code-playbook/) (primary thread not fetchable)
- Scoping each background agent to one feature directory so it cannot touch shared barrel/index files — [dev.to/davekurian](https://dev.to/davekurian/cursor-3s-parallel-agents-change-what-a-template-ships-with-joc) (excerpt)

### Inferences
- Worktrees solve the *workspace* race (which the Li et al. taxonomy shows is real: 26.5% concurrent-write instances when agents share a tree) but not the *integration* race; the independent data say the levers that reduce integration conflicts are (1) PR size, (2) disjoint, pre-declared scopes, and (3) explicitly informing agents of concurrent interface changes.
- For 1–3 people, the practical envelope that appears in both vendor guidance and practitioner reports is 3–5 concurrent worktrees, each owning disjoint files, with small PRs merged serially onto main (or a stack).
- The tooling layer is unstable: two of the three best-known worktree managers (Vibe Kanban, Crystal) were sunset/deprecated within 2026; relying on the built-in `--worktree`/`/batch` features or plain `git worktree` is the lower-risk choice for a small team.

### Gaps
- No study compares conflict rates of agent PRs against human PRs in the same repositories (AgenticFlict lists this as future work).
- No measured conflict rate for *worktree-based parallel sessions* specifically (AgenticFlict measures PRs regardless of how they were produced).
- Conductor and Graphite primary pages not reachable; no independent data on stacked-PR workflows with agents.

---

## Key question 3 — The "one writer" rule and orchestration topologies: orchestrator-workers vs peer swarms; evidence from MAST and the Cognition/Anthropic debate

### Takeaway
The independent evidence (MAST, the Specification Gap paper, the dynamic-concurrency taxonomy) supports Cognition's core claim that *parallel writers make conflicting implicit decisions* and that this penalty persists even with good specs, while supporting Anthropic's claim that parallel *readers/researchers/verifiers* reporting to one lead are a net win. The practical synthesis in late-2026 tooling is a hybrid: one lead (and ideally one writer per file set) plus parallel fresh-context workers for exploration and verification.

### Cited Findings

**Cognition, "Don't Build Multi-Agents" (Walden Yan, 2025-06-12) [vendor, secondary]**
- Principle 1: "share full agent traces, not just individual messages"; Principle 2: "Actions carry implicit decisions" that other agents need to see; the Flappy Bird example (one subagent builds a Super-Mario-style background, another a bird that doesn't fit; the final agent must reconcile); the essay argues these principles "are rarely worth violating, so by default you should rule out agent architectures that break them" and favours a single-threaded agent with context compression — [agentic-ai readthedocs summary](https://agentic-ai.readthedocs.io/en/latest/ContextEngineering/devin/); [Biweekly Engineering ep. 42](https://biweekly-engineering.beehiiv.com/p/the-case-against-multi-agents-biweekly-engineering-episode-42); [TMCnet summary](https://blog.tmcnet.com/blog/rich-tehrani/ai/why-cognition-says-multi-agent-ai-is-still-too-fragile.html) (original at cognition.ai not fetchable)
- Anthropic's counterpoint (2025-06-13, one day later): parallel subagents with separate context windows "compress information and reduce path dependency"; token spend is the main performance driver so parallel capacity works — but Anthropic itself concedes coding has fewer parallelizable parts and agents are poor at real-time coordination — [Anthropic multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

**MAST — "Why Do Multi-Agent LLM Systems Fail?" (Cemri et al., UC Berkeley; v1 2025-03-17, v3 2025-10-26) [independent]**
- 1,642 annotated traces from 7 frameworks (ChatDev, MetaGPT, AG2, HyperAgent, AppWorld, OpenManus, Magentic-One) on coding, math and agent tasks; taxonomy built from 150 traces by 6 experts, κ=0.88; LLM annotator 94% accuracy — [arXiv 2503.13657](https://arxiv.org/abs/2503.13657)
- Failure rates of the studied systems ranged from 41% to 86.7% — [same](https://arxiv.org/abs/2503.13657)
- Three categories: **System design issues 44.2%** (disobey task spec 11.8%, step repetition 15.7%, unaware of termination 12.4%); **Inter-agent misalignment 32.3%** (reasoning-action mismatch 13.2%, task derailment 7.4%, fail to ask for clarification 6.8%, ignored other agent's input 1.9%, information withholding 0.8%); **Task verification 23.5%** (incorrect verification 9.1%, no/incomplete verification 8.2%, premature termination 6.2%) — [same](https://arxiv.org/abs/2503.13657)
- Design comparison: MetaGPT (SOP-driven) had 60–68% fewer design/misalignment failures than ChatDev but 1.56× *more* verification failures; ChatDev's explicit test/review phases mitigate verification failures better — [same](https://arxiv.org/abs/2503.13657)
- Interventions (better prompts, refined topology) yielded only +9.4 to +15.6 points for ChatDev; the authors conclude "superficial fixes are insufficient and more fundamental MAS redesigns are necessary"; gains of MAS over single agents or best-of-N "often remain minimal" — [same](https://arxiv.org/abs/2503.13657)

**Coordination tax with parallel writers [independent]**
- 2026-03-25, Chacón Sartori (ICN2), *The Specification Gap*: two agents implementing disjoint methods of the same class (Claude Sonnet 4.2; replicated with Haiku 4.5) show a persistent "coordination tax" of 29.5–34.8 points vs a single agent *at every specification level* (p<0.001); split-agent success fell from 58.2% (full spec) to 24.6% (bare signatures); giving the *merger* the full spec restored 88.9% (≈ single-agent 88.3%), whereas AST conflict reports alone added 0.0 points and even hurt when combined (−6.6); coordination cost (+15.7 to +19.8 pp) exceeded information-asymmetry cost (+11.2 to +15.3 pp) — [arXiv 2603.24284](https://arxiv.org/abs/2603.24284)
- 2026-07-05, Donato et al. (IEEE Software), role-based multi-agent generation on 12 real Java repos with GPT-5: sequential role pipeline *without* a reflexive loop did worse than a single LLM on scenario coverage (55% vs 61%) and compilation (22% vs 31%); only the reflexive variant beat single-LLM (78% scenarios, 40% compile), at ~$0.56–$1.05 per project vs $0.20–$0.28; generated code covered only 1–25% of the real repos — [arXiv 2607.04212](https://arxiv.org/abs/2607.04212)
- 2026-10-07, Li et al.: in native concurrency the *lead* is the failure point as often as the workers — "Task Orchestration Problems" 28.5% (no implementation/assembly owner, "pseudo-concurrency" 5.8%), "Global Context Management" problems (sub-agents retrying without inheriting progress) — [arXiv 2610.10263](https://arxiv.org/abs/2610.10263)

**Topology as shipped [vendor]**
- Claude Code: subagents = hub-and-spoke ("Subagents report results back to the main agent"); agent teams = peer messaging + shared task list with file-locked claiming; Anthropic's own stated rule: "Use subagents when you need quick, focused workers that report back. Use agent teams when teammates need to share findings, challenge each other, and coordinate on their own"; "Avoid file conflicts... each teammate owns a different set of files" — [Agent teams docs](https://code.claude.com/docs/en/agent-teams)
- Claude Code's `/code-review` runs "in a fresh subagent that never saw your conversation. It isn't swayed by the reasoning that produced the code" — the writer/verifier split as a product feature — [Best practices](https://code.claude.com/docs/en/best-practices)

### Inferences
- The two camps are less opposed than the headlines: both say *parallel implementation with shared implicit decisions fails*; they differ on whether parallel *exploration* is worth 15× tokens. For code, the evidence favours: one owner per file set (the "one writer" rule), a single lead holding the plan, and parallel workers limited to reading, testing, reviewing or producing alternatives for the lead to pick from.
- MAST's largest bucket (44% system-design: step repetition, ignoring termination, disobeying the spec) and the Li et al. "pseudo-concurrency"/missing-owner patterns mean that a small team's orchestration prompt (who owns what, when to stop, what "done" means) matters more than the number of agents.
- Spec quality fixes the *merge*, not the *split*: the Specification Gap result (full spec to the merger recovers single-agent quality) suggests the cheap pattern for a small team is "parallel drafts, one integrator with the full spec" rather than trying to make the parallel writers agree.

### Gaps
- Cognition's essay could not be read in the original; exact wording comes from secondary summaries.
- No head-to-head study of *peer-swarm* (agent teams, Gas Town) vs *hub-and-spoke* on the same coding tasks; Li et al. test native mechanisms per vendor, not topologies.
- MAST analyses academic frameworks (ChatDev/MetaGPT era); its percentages are not directly transferable to Claude Code / Codex in 2026.

---

## Key question 4 — Task decomposition methods that make parallel work safe (spec-first, interface-first, vertical slices, DAGs, test-first contracts); does decomposition improve SWE-bench-style success?

### Takeaway
On *bounded* tasks (SWE-bench-style) the evidence says decomposition into agents does not help and can hurt: Agentless (2024) matched or beat agent frameworks with a fixed localize→repair→validate pipeline, and Li et al. (2026) found native concurrency lowered Claude Code's SWE-bench Verified pass rate by 24 points. Decomposition starts to pay on *long-horizon* work (whole-repo generation, multi-unit evolution) and only when each unit has an owner, a declared scope, an explicit interface/data-structure contract, and an executable check. Spec-first and test-first are the two decomposition inputs with measured effects.

### Cited Findings

**Decomposition on bounded tasks [independent]**
- 2024-07 (Xia, Deng, Dunn, Zhang, UIUC), *Agentless*: a three-phase non-agent pipeline (hierarchical localization, 40 sampled patches, LLM-generated reproduction tests + regression tests + majority vote) solved 32.0% of SWE-bench Lite at $0.70/problem, the best open-source result at the time, and 38.8% on SWE-bench Verified; OpenAI adopted it as the reference harness for GPT-4o/o1; validation steps raised fixes from 77 (voting only) → 81 (+regression) → 96 (+reproduction tests) — [arXiv 2407.01489](https://arxiv.org/abs/2407.01489)
- 2026-10-07, Li et al.: on SWE-bench Verified "dynamic concurrency provides no improvement"; Claude Code −24.0 pp, Kimi also significantly down, Codex unchanged — [arXiv 2610.10263](https://arxiv.org/abs/2610.10263)
- Same paper, long-horizon: concurrency gains "up to 14.3 percentage points" on LoopsBench (sustained multi-unit development) and the average test-pass advantage grows with implementation size: −8.29 pp (RepoZero) → −0.04 (NL2Repo) → +1.24 (ProgramBench) → +4.31 (LoopsBench) — [same](https://arxiv.org/abs/2610.10263)
- 2026-07-05, Donato et al.: a planning→coding→assessment role pipeline helped only with an iterative (reflexive) loop; without compiler/test feedback in the loop, compile rates stayed ≤43% — [arXiv 2607.04212](https://arxiv.org/abs/2607.04212)

**Spec-first / interface-first [independent]**
- Specification Gap (2026-03-25): the critical drop occurred when *explicit data-structure references* were removed from the spec (L1→L2); integration success 58.2% → 24.6% from full spec to bare signatures; conclusion: "specification-first approach ... investing in richer specifications that explicitly include data-structure constraints is shown to be more effective ... than relying solely on post-hoc conflict detection"; AST conflict detection is useful only as a *monitoring* signal (precision 96.7% at bare-spec level) — [arXiv 2603.24284](https://arxiv.org/abs/2603.24284)
- Passes Alone, Fails Together (2026-09-21): simply sending each agent "a message describing the completed concurrent change recovered 82% of runs" — [arXiv 2609.25396](https://arxiv.org/abs/2609.25396)
- NP-Bench (2026-10-05): declaring each work item's scope up front, partitioning into disjoint scopes and ordering merges along the producer→consumer graph (a dependency DAG) took simulated clean integration from 1/9 to 9/9; "the benefit comes from how work is allocated rather than from model reasoning" — [arXiv 2610.07261](https://arxiv.org/abs/2610.07261) **[vendor-affiliated]**

**Test-first / executable targets [vendor + independent]**
- Anthropic best practices (2026): the core recipe is to "give it a check to run": tests, typecheck, `/verify` (build and run the app), and review by a fresh subagent against a spec ("use a subagent to review the rate limiter diff against SPEC.md, check every requirement is implemented and the listed edge cases have tests") — [Best practices](https://code.claude.com/docs/en/best-practices)
- Anthropic internal usage (2025-07-24): Security Engineering "guides Claude through test-driven development"; Product Design sets "autonomous loops where Claude Code writes the code for the new feature, runs tests, and iterates continuously" — [claude.com blog](https://claude.com/blog/how-anthropic-teams-use-claude-code) **[vendor]**
- MAST: verification failures are 23.5% of all failures and persist regardless of model; ChatDev's explicit test/review phases cut them; "sole reliance on final-stage, low-level checks is inadequate. Multi-level verification, integrating external knowledge and testing throughout generation, is needed" — [arXiv 2503.13657](https://arxiv.org/abs/2503.13657)
- Li et al. benefit mechanisms: sub-agents used for "independent validation" produced the clearest wins (e.g., a crypto sub-agent discovering the P-256 curve-order requirement, making the concurrent run pass where sequential failed) — [arXiv 2610.10263](https://arxiv.org/abs/2610.10263)

**Task sizing guidance [vendor]**
- Claude Code agent teams: tasks should be "self-contained units that produce a clear deliverable, such as a function, a test file, or a review"; "Having 5-6 tasks per teammate keeps everyone productive and lets the lead reassign work"; too-small tasks mean "coordination overhead exceeds the benefit", too-large mean "teammates work too long without check-ins" — [Agent teams docs](https://code.claude.com/docs/en/agent-teams)
- Claude Code `/batch`: one big mechanical change is split "into 5 to 30 units", each in its own worktree with its own PR — [Best practices](https://code.claude.com/docs/en/best-practices)

### Inferences
- Decomposition is a *long-horizon* tool. For issue-sized tasks (the bulk of a small team's backlog) a single agent with a good spec and tests is the evidence-backed default; parallelism should be across *independent issues*, not within one issue.
- The decomposition inputs with measured effect sizes are: (a) explicit interface/data-structure contracts in the spec (+34 pp recovery), (b) declared disjoint scopes and merge order (1/9 → 9/9), (c) notifying agents of concurrent interface changes (82% recovery), (d) executable verification at the unit level (Agentless +19 fixes from validation; MAST 23.5% of failures are verification). "Vertical slices" and "tracer bullets" per se have no measured evidence in the sources found; they are consistent with (b) but unproven.
- Conflict detection after the fact (AST diffs, reactive warnings) is a weak lever; the Specification Gap and NP-Bench both found it adds ~0 unless the integrator also has the full spec/scope plan.

### Gaps
- No study measures "vertical slice" vs "horizontal layer" decomposition for parallel agents.
- MapCoder, CodeR, AutoCodeRover and SWE-agent were not re-fetched; their 2024 results pre-date the 2025–26 parallel-agent tooling and were not needed given the more recent direct evidence. No source compares multi-agent *planning* decomposition (MapCoder-style) on SWE-bench against single-agent with the same model in 2026.
- NP-Bench is single-author and tied to a product (Nerveplane); treat as promising, not confirmed.

---

## Key question 5 — Human cost: how a 1–3-person team reviews the output of N parallel agents; DORA 2025; throughput vs review load

### Takeaway
Every empirical source says the bottleneck moved from writing to reviewing/integrating: DORA 2025 finds AI now raises throughput but still raises instability; Faros' 10K-developer data show ~98% more PRs merged with ~91% longer review time; AIDev shows agent PRs are accepted far less often than human PRs (35–64% vs 76.8%) and that a single Codex user produced 164 PRs in three days; METR's RCT found experienced developers were 19% *slower* with AI while believing they were 20% faster, spending ~9% of time reviewing AI output. For 1–3 people the review bottleneck is the binding constraint on how many parallel agents are useful; the mitigations with evidence are small PRs, agent-run checks before human review, and an independent (fresh-context) agent reviewer as a first pass.

### Cited Findings

**DORA 2025 (Google Cloud, 2025-09-23; ~5,000 respondents, 100+ hours of interviews) [independent survey]**
- "we observe a positive relationship between AI adoption on both software delivery throughput and product performance" (a reversal of 2024), but "AI adoption does continue to have a negative relationship with software delivery stability" — [Google Cloud blog](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report)
- Mechanism: "Without robust control systems, like strong automated testing, mature version control practices, and fast feedback loops, an increase in change volume leads to instability"; "Teams working in loosely coupled architectures with fast feedback loops see gains, while those constrained by tightly coupled systems and slow processes see little or no benefit" — [same](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report)
- "AI doesn't fix a team; it amplifies what's already there"; 90% use AI at work, >80% say it increased productivity, 30% report little or no trust in AI-generated code — [same](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report)
- Caveat: the widely quoted "1.5% throughput / 7.2% stability drop per 25% adoption" figures are from the *2024* report, not 2025; some secondary sources misattribute them — flagged in [search summary](https://devops.com/dora-2025-faster-but-are-we-any-better/) (dora.dev not fetchable)

**Throughput vs review load [independent / vendor-data]**
- Faros AI (2025, >10,000 developers, 1,255 teams): AI use correlated with 21% more tasks completed and 98% more PRs merged, but PR review time rose 91% and there was no significant correlation with company-level improvement — [Augment Code summary](https://www.augmentcode.com/guides/ai-productivity-paradox-engineering-delivery); [Faros FAQ](https://www.faros.ai/comparison/github-copilot-vs-codeium) **[vendor data, secondary]**. Faros' 2026 "Acceleration Whiplash" report reportedly adds +51% PR size, +28% bugs per PR, 5× median review time — [Faros research page](https://faros.ai/research/ai-acceleration-whiplash) (not fetchable; numbers vary across secondary write-ups, treat as unverified)
- AIDev (Li, Zhang, Hassan, 2025-07, 456K agent PRs): acceptance rates — Codex 64%, Devin 49%, Copilot 35% vs humans 76.8%; agents beat humans only on documentation PRs (Codex 88.6%, Claude Code 85.7% vs 76.5%); Codex PRs reviewed 10× faster when accepted (median 0.3 h vs 3.9 h) "raising concerns about the thoroughness of the review"; one developer "submitting 164 agent-assisted PRs in three days, nearly matching three years of human output", yet only 9.1% of those PRs changed cyclomatic complexity vs 23.3% for human PRs; the authors describe "a growing gap between agent PR submission rates and review capacity" and list "reducing the human cost of reviewing agentic code changes" as a research direction — [arXiv 2507.15003](https://arxiv.org/abs/2507.15003)
- LinearB 2026 benchmarks (secondary): agentic-AI PRs wait 5.3× longer for first review than unassisted PRs — [HN item](https://hackernews-dynamic.seasondane.workers.dev/news/48553126); CodeRabbit 2025 (470 OSS PRs): 10.83 issues per AI PR vs 6.45 human (~1.7×) — [literature note](https://pumacp.github.io/puma-vault/20---Literature/20.7-Blog-Web/LN-CodeRabbit-2025-AIvsHumanCode) **[vendor data, secondary]**

**Individual developer cost [independent RCT]**
- METR (2025-02 to 2025-06; 16 experienced OSS developers, 246 tasks, ~5 years' repo experience, Cursor Pro + Claude 3.5/3.7 Sonnet): with AI allowed, tasks took **19% longer**; developers predicted −24% beforehand and still believed −20% afterwards; ~9% of time went to reviewing and cleaning AI output — [arXiv 2507.09089](https://web3.arxiv.org/pdf/2507.09089); [DX summary](https://getdx.com/blog/metr-study-on-how-ai-affects-developer-productivity/)

**Vendor practices to cut review load [vendor]**
- Anthropic: review by a fresh subagent (`/code-review`) that "isn't swayed by the reasoning that produced the code"; `/verify` to build and run the app; hooks as hard gates ("a check that has to pass before anything counts as done"); agent-team hooks `TeammateIdle`/`TaskCompleted` can reject completion (exit code 2) — [Best practices](https://code.claude.com/docs/en/best-practices); [Agent teams](https://code.claude.com/docs/en/agent-teams)
- Anthropic: "Monitor and steer... Letting a team run unattended for too long increases the risk of wasted effort"; teammates "may stop after encountering errors instead of recovering"; "The lead can stop early too, deciding the team is finished before all tasks are actually complete" — [Agent teams](https://code.claude.com/docs/en/agent-teams)
- Devin Review (2026-01-22) and Graphite's AI reviewer (<3% comments rated unhelpful, vendor claim) are vendor answers to the same bottleneck — [agentwiki](https://agentwiki.org/devin); [morphllm](https://www.morphllm.com/comparisons/graphite-vs-coderabbit) **[secondary]**
- Anthropic internal (2025-07-24): teams "give Claude abstract problems, let it work autonomously, then review solutions before final refinements"; Security "checks in periodically" — [claude.com blog](https://claude.com/blog/how-anthropic-teams-use-claude-code)

**Practitioner numbers [practitioner, secondary]**
- Boris Cherny reportedly ships 50–100 PRs/week with ~3–5 worktrees recommended and ~10–15 total sessions personally — [dev.to summary](https://dev.to/lassiecoder/how-claude-codes-creator-ships-50-100-prs-per-week-4oeo); [Medium](https://medium.com/@rub1cc/how-claude-codes-creator-uses-it-10-best-practices-from-the-team-e43be312836f) (unverified against primary thread)
- Gas Town early adopters: ~$100/hour API spend, duplicated bug fixes across agents — [ascii.co.uk](https://ascii.co.uk/news/article/news-20260125-f25263de/steve-yegges-gas-town-reveals-agent-orchestration-design-pat)

### Inferences
- With 1–3 reviewers, the sustainable number of parallel *implementing* agents is bounded by review throughput, not by tokens: AIDev's 164-PRs-in-3-days case and Faros' +98% PRs / +91% review time show generation outruns review by roughly 2× even in larger teams. The vendor-recommended 3–5 worktrees is consistent with one human being able to review 3–5 small PRs per cycle.
- DORA's "amplifier" mechanism points to the same prerequisites the decomposition evidence does: small batches, automated tests, fast feedback, loosely coupled modules. Teams lacking those should expect instability to rise before throughput does.
- The cheapest review-load reducer with evidence is to make the agent *earn* human review: pass tests/typecheck/hooks, then a fresh-context agent review against the spec, and only then a human reads a small PR. METR's 9% review-time figure is for a single assistant; with N parallel agents it scales roughly linearly unless automated gates filter first.

### Gaps
- No study quantifies review throughput for teams of 1–3 specifically; all datasets are OSS repos or enterprise telemetry.
- Faros, LinearB and CodeRabbit numbers are vendor telemetry behind registration walls; methodology (period-to-period within org, sample selection) could not be checked.
- The DORA 2025 "seven AI capabilities" list was only in an image on the fetched page; names not captured.
- No independent measurement of how much an AI first-pass reviewer actually reduces human review time (the "30–50%" claim in blogs cites unverified GitHub internal data).
