# Empirical evidence (2023–2026) on LLM performance degradation as the context window fills — focus on coding/agent tasks and the "~200k tokens" claim

Research date: 2026-11-01. Numbers below come from primary sources (arXiv full text, vendor blogs/docs) unless explicitly flagged. Several primary hosts could not be reached from this session (research.trychroma.com / trychroma.com, openai.com, fiction.live, huggingface.co, metr.org, epoch.ai); where that happened the figure is marked UNVERIFIED and listed under Gaps.

Cross-cutting summary (details and citations in each section):
- Every controlled study found degradation that starts far below the advertised window — at 1k–8k tokens (NoLiMa, Levy et al.), ~3k tokens (Levy et al.), 32k (RULER), 7.5k (Du et al.) — and that it is a gradient, not a cliff.
- "Effective context" (where a model keeps ≥85% of short-context performance) is typically a small fraction of the advertised window: 2k–8k for 128k–2M models on NoLiMa (Feb 2025); 16k–64k for most 128k–1M models on RULER (Apr 2024), with Gemini 1.5 Pro the exception (>128k).
- No primary source documents a measured cliff at 200k tokens. "200k" is (a) the standard Claude context window size through Sonnet 4.5, (b) the pricing boundary for 1M-context requests, and (c) the point where Claude Code historically compacted. Anthropic's own docs describe long-context degradation as "a performance gradient rather than a hard cliff."
- For coding specifically: LongSWE-Bench (May 2025) shows Claude 3.5 Sonnet falling from 29% solved at 32k to 3% at 256k, and Gemini 2.5 Pro from 23–25% (32k–256k) to 7% at 1M; Du et al. (Oct 2025) show HumanEval falling 20–48 points for Llama-3.1-8B with just 7.5k–30k tokens of irrelevant text; Laban et al. (May 2025) show a 39% average drop in multi-turn vs single-turn settings across 15 models.

## Key question 1 — Chroma Research "Context Rot" (July 2025): curve, models, lengths, Claude/GPT/Gemini numbers

### Takeaway
The Chroma report (Hong, Troynikov, Huber, July 2025) is the most-cited source for "models degrade well before the window is full"; Anthropic's own documentation now cites it and adopts the term "context rot". The primary page could not be fetched from this session, so only the README-level claims and Anthropic's paraphrase are verified here; the specific per-model numbers circulating online are listed as unverified.

### Cited Findings
- Title "Context Rot: How Increasing Input Tokens Impacts LLM Performance"; authors Kelly Hong, Anton Troynikov, Jeff Huber; institution Chroma; date July 2025 (citation block in the repository README) — [chroma-core/context-rot README](https://github.com/chroma-core/context-rot)
- The README's framing: LLMs are often presumed to handle context uniformly, so "the model should handle the 10,000th token just as reliably as the 100th"; the authors state: "We observe that model performance varies significantly as input length changes, even on simple tasks." — [chroma-core/context-rot README](https://github.com/chroma-core/context-rot)
- Three experiment families are published in the repo: (1) "NIAH Extension" — Needle-in-a-Haystack with needles that match the question semantically rather than lexically, plus variations of haystack content; (2) "LongMemEval" (conversational QA, linked to arXiv 2410.10813); (3) "Repeated Words" — replicating a sequence of repeated words. The README shows a chart "Latest Models on Repeated Words Task" but the data values are not in the text. — [chroma-core/context-rot README](https://github.com/chroma-core/context-rot)
- Anthropic's engineering blog (Sep 29, 2025) links to research.trychroma.com/context-rot for the concept, describing context rot as the phenomenon where "the model's ability to accurately recall information from that context decreases" as tokens grow, uncovered via "needle-in-a-haystack style benchmarking". — [Anthropic, Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic's platform docs now state as fact: "As token count grows, accuracy and recall degrade, a phenomenon known as *context rot*. This makes curating what's in context just as important as how much space is available." — [Claude Platform docs, Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)
- Secondary coverage (web search snippets, not verified against the report): 18 models tested including GPT-4.1, Claude 4, Gemini 2.5 and Qwen3; every model became less reliable as input grew; the LongMemEval "focused" condition held only the relevant history while the "full" condition padded with irrelevant conversation to roughly 113k–120k tokens; one commentary says Anthropic models showed the biggest focused-vs-full gap, often abstaining on full inputs. Sources disagree on the focused size (~100 vs ~300 tokens) and full size (~113k vs ~120k). — search snippets from [morphllm.com](https://www.morphllm.com/context-rot), [zenml.io](https://www.zenml.io/llmops-database/context-rot-evaluating-llm-performance-degradation-with-increasing-input-tokens) (neither page could be fetched; treat as unverified)

### Inferences
- The Chroma report's influence is now institutional: Anthropic uses its vocabulary in docs and marketing (see Opus 4.6 launch, Feb 2026, under Key question 5). This explains why "context rot" and "degradation" are so widely discussed in 2025–2026 agent-engineering writing, but the report itself does not define a 200k threshold (the experiments ran at ~113k tokens for LongMemEval, well below 200k).

### Gaps
- Could not fetch research.trychroma.com or trychroma.com (blocked in this session), so the exact degradation curves, the full list of 18 models, per-model LongMemEval focused-vs-full accuracies, the needle-question-similarity and distractor results, and the shuffled-vs-coherent haystack result are NOT verified here. Anyone citing specific Chroma percentages should open the report directly.
- The repository README did not list the models or input lengths; GitHub API access to the repo contents was refused in this session.

## Key question 2 — NoLiMa (Feb 2025): effective context per model (≥85% of short-context performance)

### Takeaway
NoLiMa (Modarressi et al., arXiv 2502.05167, v1 Feb 7 2025, v3 Jul 9 2025) measured the effective context length of 13 models advertising ≥128k windows: the best (GPT-4o) is 8k; Claude 3.5 Sonnet (200k advertised) is 4k; most are ≤2k. At 32k, 11 of 13 models fell below 50% of their short-context baseline.

### Cited Findings
- Abstract: 13 LLMs claiming ≥128K context; strong at <1K; "At 32K, for instance, 11 models drop below 50% of their strong short-length baselines." GPT-4o goes "from an almost-perfect baseline of 99.3% to 69.7%". "Even models enhanced with reasoning capabilities or CoT prompting struggle to maintain performance in long contexts." — [arXiv 2502.05167 abstract](https://arxiv.org/abs/2502.05167)
- Table 3 (claimed context / base score / 32K score / effective length, where effective length = largest length retaining ≥85% of base score):
  - GPT-4o — 128K / 99.3 / 69.7 / 8K
  - Llama 3.3 70B — 128K / 97.3 / 42.7 / 2K
  - Llama 3.1 405B — 128K / 94.7 / 38.0 / 2K
  - Llama 3.1 70B — 128K / 94.5 / 43.2 / 2K
  - Gemini 1.5 Pro — 2M / 92.6 / 48.2 / 2K
  - Jamba 1.5 Mini — 256K / 92.4 / 43.6 / <1K
  - Command R+ — 128K / 90.9 / 7.4 / <1K
  - Gemini 2.0 Flash — 1M / 89.4 / 41.0 / 4K
  - Mistral Large 2 — 128K / 87.9 / 18.8 / 2K
  - Claude 3.5 Sonnet — 200K / 87.5 / 29.8 / 4K
  - Gemini 1.5 Flash — 1M / 84.7 / 28.6 / <1K
  - GPT-4o mini — 128K / 84.8 / 13.7 / <1K
  - Llama 3.1 8B — 128K / 76.7 / 14.2 / 1K
  — [arXiv 2502.05167 HTML, Table 3](https://arxiv.org/html/2502.05167)
- NoLiMa-Hard (Table 5, reasoning models, base vs 32K): Llama 3.3 70B w/o CoT 98.3 → 8.9; w/ CoT 97.1 → 10.1; GPT-o1 99.9 → 31.1; GPT-o3 Mini 98.8 → 18.9; DeepSeek R1-Distill-Llama-70B 99.9 → 20.7. — [arXiv 2502.05167 HTML, Table 5](https://arxiv.org/html/2502.05167)

### Inferences
- NoLiMa's needles require associative (non-lexical) matching, so it measures reasoning-over-context rather than pure retrieval; its effective lengths are therefore much lower than RULER's for the same model class. The two benchmarks bracket "effective context": roughly 2k–8k (associative) to 32k–64k (retrieval-heavy) for 2024–early-2025 frontier models.
- Degradation in NoLiMa starts at the very first step up (1k–2k tokens) and is monotonic; there is no threshold behaviour.

### Gaps
- NoLiMa did not evaluate Claude Sonnet 4/4.5, Opus 4.x, GPT-4.1/5 or Gemini 2.5 (paper pre-dates them; v3 of July 2025 did not add them in the fetched tables).

## Key question 3 — RULER (NVIDIA, Apr–Aug 2024): effective vs claimed context per model

### Takeaway
RULER (Hsieh et al., arXiv 2404.06654, v1 Apr 9 2024, v3 Aug 6 2024, COLM 2024) evaluated 17 models on 13 synthetic tasks; using Llama2-7B's 4K score (85.6) as the threshold, effective lengths were typically 25–50% of the claimed length (e.g. GPT-4-1106 128K → 64K; Yi-34B 200K → 32K), with Gemini 1.5 Pro the only model holding to >128K.

### Cited Findings
- Abstract: 17 long-context LMs, 13 tasks; models near-perfect on vanilla NIAH but "almost all models exhibit large performance drops as the context length increases"; "While these models all claim context sizes of 32K tokens or greater... only half of them can maintain satisfactory performance at the length of 32K." — [arXiv 2404.06654 abstract](https://arxiv.org/abs/2404.06654)
- Threshold definition: "We use the performance of Llama2-7b model at the 4K context length as the threshold" (85.6%); "The effective context length is the maximum length passing this threshold." — [arXiv 2404.06654 HTML](https://arxiv.org/html/2404.06654v3)
- Table 3 (claimed / effective / scores at 4K, 8K, 16K, 32K, 64K, 128K):
  - Gemini-1.5-Pro — 1M / >128K / 96.7, 95.8, 96.0, 95.9, 95.9, 94.4
  - GPT-4 (gpt-4-1106-preview) — 128K / 64K / 96.6, 96.3, 95.2, 93.2, 87.0, 81.2
  - Llama 3.1 70B — 128K / 64K / 96.5, 95.8, 95.4, 94.8, 88.4, 66.6
  - Qwen2 72B — 128K / 32K / 96.9, 96.1, 94.9, 94.1, 79.8, 53.7
  - Command-R-plus 104B — 128K / 32K / 95.6, 95.2, 94.2, 92.0, 84.3, 63.1
  - GLM4 9B — 1M / 64K / 94.7, 92.8, 92.1, 89.9, 86.7, 83.1
  - Llama 3.1 8B — 128K / 32K / 95.5, 93.8, 91.6, 87.4, 84.7, 77.0
  - GradientAI/Llama3 70B — 1M / 16K / 95.1, 94.4, 90.8, 85.4, 80.9, 72.1
  - Mixtral-8x22B — 64K / 32K / 95.6, 94.9, 93.4, 90.9, 84.7, 31.7
  - Yi 34B — 200K / 32K / 93.3, 92.2, 91.3, 87.5, 83.2, 77.3
  - Phi3-medium 14B — 128K / 32K / 93.3, 93.2, 91.1, 86.8, 78.6, 46.1
  - Mistral-v0.2 7B — 32K / 16K / 93.6, 91.2, 87.2, 75.4, 49.0, 13.8
  - LWM 7B — 1M / <4K / 82.3, 78.4, 73.7, 69.1, 68.1, 65.0
  — [arXiv 2404.06654 HTML, Table 3](https://arxiv.org/html/2404.06654v3)

### Inferences
- Even for GPT-4-1106 the curve is a steady slope (96.6 → 81.2 across 4K→128K), with the steepest fall between 32K and 128K. The "effective = claimed" case (Gemini 1.5 Pro) shows that the gap is model-dependent, not architectural destiny.
- RULER tasks are mostly retrieval/aggregation; combined with NoLiMa, this shows effective context shrinks as the task requires more inference over the retrieved content.

### Gaps
- RULER's published table is from 2024 models; no Claude 3.x/4.x or GPT-4.1/5 results in the paper (Claude models were not in Table 3). The GitHub leaderboard may have newer entries but was not fetched.

## Key question 4 — Other benchmarks: LongBench v2, Lost in the Middle, Same Task More Tokens, LongCodeBench, vendor numbers (Claude 4.x, GPT-4.1, Gemini 2.5, MRCR, Fiction.LiveBench)

### Takeaway
Degradation is documented at every scale: ~3k tokens (Levy et al. 2024), 2k–4k tokens with position effects (Liu et al. 2023), 7.5k–30k tokens of pure padding (Du et al. 2025), 32k→256k on real bug fixing (LongCodeBench 2025), and 128k→1M on MRCR (Gemini 2.5 report; Anthropic Opus 4.6 launch). Vendor "1M context" announcements (Anthropic Aug 2025) came with no accuracy numbers; the only vendor-published 1M accuracy figures found are Gemini 2.5 Pro 16.4% and Opus 4.6 76% on MRCR-v2 8-needle.

### Cited Findings

Lost in the Middle (Liu et al., Jul 2023, TACL 2023):
- Models: MPT-30B-Instruct (8K), LongChat-13B (16K), GPT-3.5-Turbo (4K), GPT-3.5-Turbo-16K, Claude-1.3 (8K), Claude-1.3-100K; GPT-4 on a 500-example subset. Inputs: 10/20/30 documents ≈ 1,476 / 2,946 / 4,419 tokens. — [arXiv 2307.03172 HTML](https://arxiv.org/html/2307.03172v3)
- GPT-3.5-Turbo, 20 documents: 75.8% when the answer document is first, 53.8% when it is 10th (middle), 63.2% when last; closed-book baseline 56.1%, oracle 88.3%. "In the worst case, performance in the 20- and 30-document settings is lower than the closed-book result." — [arXiv 2307.03172 HTML](https://arxiv.org/html/2307.03172v3)
- Extended-context versions were no better when input fit: GPT-3.5-Turbo-16K 75.7/57.3/54.1/55.4/63.1 across positions; Claude-1.3-100K 59.8/55.9/57.0/57.4/60.0 vs Claude-1.3 59.9/55.9/56.8/57.2/60.1. "Extended-context models are not necessarily better at using input context." — [arXiv 2307.03172 HTML](https://arxiv.org/html/2307.03172v3)
- Key-value retrieval (75/140/300 pairs): Claude-1.3 near-perfect at all lengths; GPT-3.5-Turbo and MPT-30B worst in the middle; worst-case 45.6% without query-aware contextualization. — [arXiv 2307.03172 HTML](https://arxiv.org/html/2307.03172v3)

Same Task, More Tokens (Levy, Jacoby, Goldberg, Feb 2024, ACL 2024):
- FLenQA: three True/False reasoning tasks padded to ~250/500/1000/2000/3000 tokens; models GPT-4 (1106-preview), GPT-3.5-turbo-1106, Gemini Pro, Mistral Medium, Mixtral 8x7B. — [arXiv 2402.14848 HTML](https://arxiv.org/html/2402.14848v2)
- Average accuracy falls "from 0.92 to 0.68" from the shortest to 3000-token inputs; the drop appears even when padding is duplicated key paragraphs; it is larger with Books-corpus padding than with same-task padding; accuracy "drops significantly beyond 500 tokens regardless of placement"; CoT adds a roughly constant gain and does not close the gap (and hurts Gemini Pro at length). — [arXiv 2402.14848 HTML](https://arxiv.org/html/2402.14848v2)
- "LLMs quickly degrade in their reasoning capabilities, even on input length of 3000 tokens, which is much shorter than their technical maximum"; failures that rise with length: refusals, bias toward "False", answering before reasoning (odds ratio 3.643). — [arXiv 2402.14848 HTML](https://arxiv.org/html/2402.14848v2)

LongBench v2 (Bai et al., Dec 19 2024 / Jan 3 2025):
- 503 multiple-choice questions, 8k–2M words (median 54k, mean 104k words); buckets Short <32k words (180 q), Medium 32k–128k (215), Long >128k (108). Human experts 53.7% overall (47.2 / 59.1 / 53.7 by bucket, 15-minute limit). — [arXiv 2412.15204 HTML](https://arxiv.org/html/2412.15204v2)
- Zero-shot (overall / short / medium / long): o1-preview 57.7 / 62.6 / 53.5 / 58.1; GPT-4o-2024-08-06 50.1 / 53.3 / 52.4 / 40.2; GLM-4-Plus 44.3 / 50.0 / 46.5 / 30.6; Claude-3.5-Sonnet-20241022 41.0 / 46.1 / 38.6 / 37.0; Qwen2.5-72B 39.4 / 44.4 / 34.0 / 41.7; Llama-3.1-70B 31.6 / 41.1 / 27.4 / 24.1. — [arXiv 2412.15204 HTML, Table 2](https://arxiv.org/html/2412.15204v2)

LongCodeBench (Rando et al. incl. John Yang & Tatsunori Hashimoto, arXiv 2505.07897, May 12 2025) — the coding-specific long-context benchmark:
- Two tasks: LongCodeQA (443 multiple-choice questions from GitHub issues, 98 repos) and LongSWE-Bench (600 real bug-fixing instances, 100 per bin, 61 repos; scored by % issues solved via unit tests). Bins: 32K, 64K, 128K, 256K, 512K, 1M tokens. — [arXiv 2505.07897 HTML](https://arxiv.org/html/2505.07897v1)
- Abstract: performance drops "from 29% to 3% for Claude 3.5 Sonnet, or from 70.2% to 40% for Qwen2.5" (the first is LongSWE-Bench 32K→256K; the second LongCodeQA 512K→1M). — [arXiv 2505.07897](https://arxiv.org/html/2505.07897v1)
- LongSWE-Bench % solved at 32K / 64K / 128K / 256K / 512K / 1M: Claude 3.5 Sonnet 29 / 19 / 15 / 3 / – / –; Gemini 2.5 Pro 23 / 25 / 22 / 24 / 12 / 7; GPT-4o 11 / 6 / 5 / – / – / –; Gemini 2 Flash 10 / 6 / 7 / 3 / 2 / 2; GPT-4.1 1 / 1 / 1 / 1 / 1 / 2; Gemini 1.5 Pro 1 / 6 / 2 / 3 / 4 / 5; Qwen2.5 14B and Llama 4 Scout 0 everywhere. — [arXiv 2505.07897 HTML, Table 3](https://arxiv.org/html/2505.07897v1)
- LongCodeQA accuracy at 32K / 64K / 128K / 256K / 512K / 1M: GPT-4.1 72.6 / 73.7 / 78.3 / 72.3 / 78.7 / 80.0; Gemini 2.5 Pro 75.2 / 71.1 / 71.7 / 67.7 / 68.1 / 69.8; Claude 3.5 Sonnet 65.5 / 69.7 / 71.7 / 66.6 / – / –; Llama 4 Scout 66.4 / 73.7 / 70.7 / 63.1 / 78.7 / 76.0; Qwen2.5 14B 61.9 / 65.8 / 68.5 / 63.1 / 70.2 / 40.0. — [arXiv 2505.07897 HTML, Table 3](https://arxiv.org/html/2505.07897v1)

Context Length Alone Hurts LLM Performance Despite Perfect Retrieval (Du, Tian et al., UIUC/Amazon/Argonne/HUJI, arXiv 2510.05381, Oct 6 2025, EMNLP 2025 Findings) — includes a coding task:
- Five models (Llama-3.1-8B-Instruct, Mistral-v0.3-7B-Instruct, GPT-4o, Claude 3.5/3.7 Sonnet, Gemini 2.0); tasks GSM8K, MMLU, HumanEval, synthetic VarSum; distractor lengths 0 / 7,500 / 15,000 / 30,000 tokens. Abstract range of drops: "13.9%–85%". — [arXiv 2510.05381 HTML](https://arxiv.org/html/2510.05381v1)
- HumanEval with essay distractors (Table 6): Llama-3.1-8B baseline 57.3 → −20.1 (7.5K), −40.9 (15K), −47.6 (30K); Mistral-7B baseline 34.8 → −17.7 (7.5K), −23.8 (15K), −34.8 (30K). MMLU: Llama-3.1-8B −24.2 at 30K despite retrieving all evidence exactly on 970 of 1,000 problems. — [arXiv 2510.05381 HTML](https://arxiv.org/html/2510.05381v1)
- Degradation persists with whitespace padding and even when distractor tokens are attention-masked (Table 3: Llama-3.1-8B HumanEval −50.0 at 30K masked tokens; both models drop ≥7.9% at 30K masked). Closed models were more robust to whitespace: GPT-4o HumanEval 68.3 → 0.0 / 0.0 / −3.1; Gemini HumanEval 86.0 → −11.0 / −2.5 / −1.8. — [arXiv 2510.05381 HTML](https://arxiv.org/html/2510.05381v1)
- Mitigation "Retrieve-then-Solve" (recite evidence, then answer from the short prompt): up to +31.2 points for Mistral on synthetic GSM8K; up to ~4 points for GPT-4o on RULER QA2 at 32K. — [arXiv 2510.05381 HTML](https://arxiv.org/html/2510.05381v1)

Vendor-published long-context numbers:
- Anthropic, "Claude Sonnet 4 now supports 1M tokens of context" (Aug 12, 2025): window "up to 1 million tokens... a 5x increase"; "process entire codebases with over 75,000 lines of code"; pricing ≤200K: $3/$15 per MTok, >200K: $6/$22.50 per MTok; public beta for Tier 4 / custom rate limits. The page contains no benchmark results (no NIAH, MRCR or accuracy at 1M). — [claude.com/blog/1m-context](https://claude.com/blog/1m-context)
- Gemini 2.5 technical report (arXiv 2507.06261; v6 dated Dec 19 2025), Table 3, MRCR-V2 8-needle ≤128K vs 1M: Gemini 2.5 Pro 58.0% vs 16.4%; Gemini 2.5 Flash 54.3% vs 21.0%; Gemini 2.0 Flash 19.0% vs 5.3%; Gemini 1.5 Pro 26.2% vs 12.1%. LOFT (hard) ≤128K vs 1M: 2.5 Pro 87.0% vs 69.8%; 2.0 Flash 58.0% vs 7.6%. Table 4 (≤128K only): OpenAI o3 57.1% MRCR / 77.0% LOFT; Claude 4 Sonnet 39.1% / 81.6%; Claude 4 Opus 16.1% MRCR (footnoted: no thinking, API refusals); Grok 3 Beta 34.0% / 73.1%. — [arXiv 2507.06261 HTML](https://arxiv.org/html/2507.06261)
- Anthropic, Claude Opus 4.6 launch (Feb 5, 2026): "Opus 4.6 is our first Opus-class model with 1M token context" (beta); MRCR v2 8-needle 1M variant: "Opus 4.6 scores 76%, whereas Sonnet 4.5 scores just 18.5%"; "Premium pricing applies for prompts exceeding 200k tokens" ($10/$37.50 per MTok); the page says Opus 4.6 "holds and tracks information over hundreds of thousands of tokens with less drift". — [anthropic.com/news/claude-opus-4-6](https://www.anthropic.com/news/claude-opus-4-6)
- OpenAI GPT-4.1 (Apr 2025) long-context results, SECONDARY SOURCES ONLY (openai.com blocked): Graphwalks BFS <128k: GPT-4.1 61.7% vs GPT-4o 42%; >128k ≈ 19% (the-decoder). An aggregator lists GPT-4.1 at 46.3% mean match ratio on OpenAI-MRCR 2-needle 1M (benchmarklist.com; dating inconsistent — UNVERIFIED). — [the-decoder](https://the-decoder.com/openai-launches-gpt-4-1-new-model-family-to-improve-agents-long-contexts-and-coding/); [benchmarklist](https://benchmarklist.com/benchmarks/openai_mrcr_2_needle_1m/)
- Fiction.LiveBench: primary site blocked. Secondary snippets: Dan Cleary's post says o3-pro holds up to 120k but drops at 192k and that Grok 4 and Gemini were "the two standouts" above 192k (no exact numbers); DataLearner lists overall scores o3-pro / GPT-5 (medium) 97.20, Grok 4 94.40 (not per-length). UNVERIFIED. — [danjcleary.substack.com](https://danjcleary.substack.com/p/do-long-context-windows-actually); [datalearner](https://www.datalearner.com/en/benchmarks/fiction-livebench)

### Inferences
- The two coding-specific sources agree that real bug fixing degrades much faster than code comprehension: on LongCodeBench, LongCodeQA accuracy is nearly flat for GPT-4.1 and Gemini 2.5 Pro up to 1M, while LongSWE-Bench solve rates collapse (Claude 3.5 Sonnet 29 → 3 by 256K; Gemini 2.5 Pro 24 → 7 between 256K and 1M). Retrieval surviving at long context does not imply the ability to act on it survives — consistent with Du et al.'s "perfect retrieval, degraded answers" result.
- Vendor "1M context" launches separate window size from accuracy: the Sonnet 4 1M page gives no accuracy; Gemini's own report shows a 3.5× drop (58.0 → 16.4) from ≤128K to 1M on its hardest retrieval test. The Opus 4.6 76% at 1M (Feb 2026) is the first vendor figure found that is high at the full window, but it is a single retrieval benchmark, not a coding task.
- Anthropic's choice to price >200K prompts at a premium (Sonnet 4: 2× input; Opus 4.6: $10 vs $5 base) is a cost boundary, not a quality boundary.

### Gaps
- OpenAI GPT-4.1 / GPT-5 MRCR and Graphwalks tables: openai.com and huggingface.co (dataset cards) unreachable; only secondary numbers above.
- Fiction.LiveBench per-length table (0 → 192k) for Sonnet 4/4.5, Opus 4/4.1, GPT-5, o3, Gemini 2.5 Pro: not verifiable (fiction.live unreachable; aggregators give only overall scores).
- No Anthropic-published per-length accuracy for Sonnet 4/4.5 or Opus 4/4.1 at 100k–1M was found other than the Sonnet 4.5 18.5% figure quoted in the Opus 4.6 launch.
- The RULER and NoLiMa papers predate Claude 4.x/GPT-4.1/5/Gemini 2.5; no equivalent effective-context table for those models was found in a primary source.

## Key question 5 — Is there a primary source for a specific "200k" threshold?

### Takeaway
No. Every primary source found describes a gradient starting far below 200k; "200k" is the long-standing standard Claude window (through Sonnet 4.5), the pricing boundary for 1M-context requests, and the historical Claude Code compaction boundary. Anthropic's own words: degradation is "a performance gradient rather than a hard cliff."

### Cited Findings
- Anthropic docs: "Other Claude models, including Claude Sonnet 4.5 (deprecated), have a 200k-token context window"; newer models (Opus 4.6+, Sonnet 4.6+, Haiku 5.5, etc.) have 1M. Context awareness injects `<budget:token_budget>200000</budget:token_budget>` for Sonnet 4.5 / Haiku 4.5 (1M for Sonnet 4.6/5), plus `<system_warning>Token usage: 35000/200000; 165000 remaining</system_warning>` after each tool call. — [Claude Platform docs, Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)
- Anthropic docs: "more context isn't automatically better. As token count grows, accuracy and recall degrade, a phenomenon known as *context rot*." — [Claude Platform docs, Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)
- Anthropic engineering (Sep 29, 2025): "Context, therefore, must be treated as a finite resource with diminishing marginal returns"; models "may show reduced precision for information retrieval and long-range reasoning" as "a performance gradient rather than a hard cliff"; n tokens → "n² pairwise relationships" so attention "gets stretched thin"; LLMs "lose focus or experience confusion at a certain point". The page does not mention a 200k window. — [Anthropic, Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic marketing (Feb 5, 2026) defines the complaint, not a number: "A common complaint about AI models is 'context rot,' where performance degrades as conversations exceed a certain number of tokens." — [anthropic.com/news/claude-opus-4-6](https://www.anthropic.com/news/claude-opus-4-6)
- Pricing boundary at 200K: Sonnet 4 1M context $3/$15 ≤200K vs $6/$22.50 >200K (Aug 2025); Opus 4.6 "Premium pricing applies for prompts exceeding 200k tokens" (Feb 2026). — [claude.com/blog/1m-context](https://claude.com/blog/1m-context); [anthropic.com/news/claude-opus-4-6](https://www.anthropic.com/news/claude-opus-4-6)
- Claude Code auto-compaction boundary: "With auto-compaction on, sessions compact at the 200K boundary through auto-compaction" when `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`; the auto-compact window is configurable from 100K to 1M (`/autocompact 500k`, `autoCompactWindow`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000`) and "Claude Code caps it at the model's context window"; Haiku 5.5 "Sessions auto-compact at about 967K tokens by default". Docs recommend setting 200000 "if your gateway rejects requests above 200K tokens". — [Claude Code docs, Model configuration](https://code.claude.com/docs/en/model-config)
- Claude Code cost docs describe auto-compaction as a cost/limit mechanism ("summarizes conversation history when approaching context limits") and recommend "/clear to start fresh when switching to unrelated work. Stale context wastes tokens on every subsequent message." No degradation threshold is stated. — [Claude Code docs, Manage costs](https://code.claude.com/docs/en/costs)
- Web commentary that uses the "200K" framing (e.g. a blog titled "Claude Code Context Rot: Why Accuracy Drops at 200K Tokens") cites the Chroma study, which per the same search found "measurable degradation at 50K tokens" for 200K-window models and describes decline as "continuous, not a cliff". The blog could not be fetched; its claim that Anthropic used "context rot" in the Opus 4.6 launch is confirmed above, but no Anthropic statement of a 200k cliff exists. — search snippets, [getunblocked.com](https://getunblocked.com/blog/context-rot-claude-code/) (UNVERIFIED, unreachable)
- Benchmarks that would show a cliff show none: RULER curves are smooth from 4K to 128K; NoLiMa effective lengths are 1K–8K; Levy et al. see drops at 500–3000 tokens; LongSWE-Bench shows Claude 3.5 Sonnet already at 15% (from 29%) at 128K, before any 200K boundary. — [arXiv 2404.06654](https://arxiv.org/html/2404.06654v3); [arXiv 2502.05167](https://arxiv.org/html/2502.05167); [arXiv 2402.14848](https://arxiv.org/html/2402.14848v2); [arXiv 2505.07897](https://arxiv.org/html/2505.07897v1)

### Inferences
- The "degrades after ~200k" meme most plausibly conflates three true facts: (1) Claude's window was 200k until 2025–2026, so a session could not exceed it and Claude Code compacted there; (2) >200k pricing is higher; (3) measured degradation is real — but it begins at a few thousand tokens and worsens continuously. There is no evidence that 199k is safe and 201k is not.
- For practical agent engineering, the evidence supports a budget far below the window (tens of thousands of tokens of working context, with retrieval/compaction) rather than a "stay under 200k" rule.

### Gaps
- No paper or vendor document was found that identifies a measured inflection near 200k tokens for any model; absence of evidence here is after checking the sources listed, not an exhaustive literature search.

## Key question 6 — Agentic coding: does performance fall with more turns / accumulated context?

### Takeaway
Yes, in every study found: multi-turn settings cut performance by 39% on average across 15 models (Laban et al., May 2025); long-context bug fixing collapses with context size (LongCodeBench); Anthropic's own evaluations show that removing stale tool results improves agent performance by 29% and cuts tokens 84% on a 100-turn task (Sep 2025); and JetBrains' SWE-agent study shows simple observation masking halves cost without hurting solve rate (Aug–Oct 2025). Anthropic's long-running-agent guidance (Nov 2025) reports agents running out of context mid-feature and prematurely declaring completion.

### Cited Findings
- Laban, Hayashi, Zhou, Neville (Microsoft Research / Salesforce Research, arXiv 2505.06120, May 9 2025): 15 LLMs from 8 families (GPT-4o-mini, GPT-4o, o3, GPT-4.1, Claude 3 Haiku, Claude 3.7 Sonnet, Gemini 2.5 Flash, Gemini 2.5 Pro, Llama 3.1-8B, Llama 3.3-70B, Llama 4 Scout, OLMo-2-13B, Phi-4, DeepSeek-R1, Command-A); 200,000+ simulated conversations; six tasks (Code, Database, Actions, Math, Data-to-text, Summary); "an average drop of 39% across six generation tasks" in multi-turn vs single-turn; decomposed into aptitude loss of 16% and an unreliability increase of 112%; "when LLMs take a wrong turn in a conversation, they get lost and do not recover"; top models "get equally lost" with 30–40% degradation; "Claude 3.7 Sonnet and GPT-4.1 conserve performance well on Code". — [arXiv 2505.06120](https://arxiv.org/abs/2505.06120); [HTML](https://arxiv.org/html/2505.06120v1)
- Anthropic, "Managing context on the Claude Developer Platform" (Sep 29, 2025): context editing "clears stale tool calls and results when the agent approaches token limits"; on an internal agentic-search evaluation, memory tool + context editing "improved performance by 39% over baseline", context editing alone "delivered a 29% improvement"; on a 100-turn web-search evaluation context editing cut token consumption by "84%" and "enabled workflows that would otherwise fail from context exhaustion"; agents "often exhaust their effective context windows". Sonnet 4.5 has "built-in context awareness... tracking available tokens throughout conversations". — [claude.com/blog/context-management](https://claude.com/blog/context-management)
- Anthropic, Effective context engineering (Sep 29, 2025): recommends compaction ("one of the safest lightest touch forms of compaction" is tool-result clearing), structured note-taking (NOTES.md, to-do lists), sub-agents returning condensed summaries "often 1,000–2,000 tokens", and just-in-time retrieval via lightweight identifiers (file paths, queries); Claude Code implements compaction by summarizing message history. — [Anthropic, Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic, "Effective harnesses for long-running agents" (Nov 26, 2025): "each new session begins with no memory of what came before"; even Opus 4.5 looping across context windows fell short of building a production-quality app; failure modes: agents "try to do too much at once", "running out of context mid-implementation" leaving half-built undocumented features, and later instances that "declare the job done" too early; compaction alone "doesn't always pass perfectly clear instructions to the next agent". Fixes: initializer agent, JSON feature list (>200 features), progress file, git commits, one feature per fresh session, end-to-end test at session start. — [Anthropic, Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- Anthropic docs describe how Sonnet 4.5 / Haiku 4.5 / Sonnet 4.6 / Sonnet 5 receive budget tags so the model can "manage long-running tasks against the space that remains rather than guess how many tokens are left"; Opus 4.7+ and Fable/Mythos models don't receive them (task budgets beta instead). — [Claude Platform docs, Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)
- Lindenbauer et al. (JetBrains Research, "The Complexity Trap", arXiv 2508.21433, v1 Aug 29 2025, v3 Oct 27 2025): within SWE-agent on SWE-bench Verified across five model configurations, simple observation masking "halves cost relative to the raw agent" while "matching, and sometimes slightly exceeding, the solve rate of LLM summarization"; a hybrid reduces cost "by 7% and 11%" vs masking / summarization; initial evidence of generalization to OpenHands. Search snippet (v1): with Qwen3-Coder 480B masking raised solve rate 53.8% → 54.8%. — [arXiv 2508.21433](https://arxiv.org/abs/2508.21433)
- LongCodeBench LongSWE-Bench (May 2025): % issues solved falls with context — Claude 3.5 Sonnet 29 → 19 → 15 → 3 (32K→256K); Gemini 2.5 Pro 23 → 25 → 22 → 24 → 12 → 7 (32K→1M); GPT-4o 11 → 6 → 5 (32K→128K). — [arXiv 2505.07897 HTML](https://arxiv.org/html/2505.07897v1)
- METR, "Measuring AI Ability to Complete Long Software Tasks" (arXiv 2503.14499, v1 Mar 18 2025, v4 Jul 10 2026, NeurIPS 2025): defines the "50%-task-completion time horizon"; Claude 3.7 Sonnet ≈ "around 50 minutes"; horizon "doubling approximately every seven months since 2019"; attributes gains to "greater reliability and ability to adapt to mistakes". The arXiv abstract does not give the 80% horizon; a secondary review states the 80% horizon is 4–6× shorter than the 50% horizon (UNVERIFIED; metr.org unreachable). — [arXiv 2503.14499](https://arxiv.org/abs/2503.14499); secondary: [emptysqua.re review](https://emptysqua.re/blog/review-measuring-ai-ability-to-complete-long-software-tasks/)

### Inferences
- Multi-turn degradation (Laban) is primarily an unreliability problem (+112% variance) rather than loss of capability (−16%), which matches the agentic failure modes Anthropic describes (premature completion, wrong-turn lock-in). Context accumulation and turn count are confounded in these studies; none isolates "more tokens" from "more turns" for coding agents.
- The vendor-measured gains from removing stale tool results (29% better, 84% fewer tokens at 100 turns) are direct evidence that accumulated agent context actively hurts, not merely costs more.
- METR's horizon data measure task length in human time, not tokens; they show success falls with task length but do not attribute it to context fill, so they are supporting rather than direct evidence.

### Gaps
- No primary study found that varies only accumulated context (tokens) while holding turns/tasks fixed for an agentic coding benchmark (SWE-bench/Terminal-Bench style). LongCodeBench varies repository context given up front, not accumulated agent history.
- Per-task Code numbers from Laban et al. were not extractable from the HTML; METR's 80% horizon and task-failure analysis could not be read from metr.org.
- Anthropic's 29%/39%/84% figures come from unpublished internal evaluations described in a blog post; methodology is not available.
