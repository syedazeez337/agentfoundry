# AgentFoundry literature review, round 2 (deep)

Compiled 2026-08-28. Supplements `LITERATURE.md`. Round 1 established the shape
of the field; this round went after the things that change the design. Several
round-1 conclusions are revised or reversed here, and those are marked.

**Headline: the rigour gap I claimed in round 1 is smaller than I said.** A
cluster of 2026 papers already runs pre-registered, factorial, mixed-effects,
specification-curve analyses of agent scaffolds, and a parallel cluster already
does automated harness optimisation with released code. The revised opening is
narrower, harder, and better - see section 12.

---

## 1. The power problem - the single most important finding

To detect a **5 percentage-point paired difference** at 80% power, alpha 0.05,
using McNemar on binary task outcomes:

| Discordance rate | Tasks required |
|---|---|
| 0.10 | ~600 |
| 0.25 | ~1500 |

Formula: `N ≈ (z_{α/2}√p_d + z_β√(p_d − Δ²))² / Δ²`

Set against cost: ~$2.50/instance on GPT-5 → **$1,500 to $3,750 per arm** for a
single adequately powered 5pp comparison, before repeats.

**So what:** the v0 MVP (20 tasks) is powered to detect roughly nothing under
20pp. This is not a detail to fix later - it determines what the project can
ever claim. Three consequences:

1. Only chase **large** effects (>15pp) with small task sets, and say so.
2. Buy power with **variance reduction**, not more tasks: paired designs,
   multiple trials per task averaged into a rate (beta-binomial / GLMM), and
   CUPED-style covariate adjustment where a cheap pre-experiment predictor of
   task difficulty exists. Variance reduction scales with covariate R².
3. Prefer **ranking to scoring**. *Efficient Benchmarking of AI Agents*
   (2603.23749) shows rankings need far fewer evaluations than point estimates,
   with probabilistic guarantees; combine IRT subset selection with early
   stopping.

Corollary worth stating loudly: **most published agent-architecture gains are
underpowered.** That observation is a research contribution on its own.

---

## 2. Scaffold effects are large, model-dependent, and the literature disagrees

Four independent 2026 measurements, all substantial, all recent:

| Study | Design | Scaffold effect size |
|---|---|---|
| Harness-Bench (2605.27922) | 6 harnesses x 8 models, 106 tasks, 5,194 trajectories | 23.8 pt gap (NanoBot 76.2 vs OpenClaw 52.4) |
| Scaffold Effects on GAIA (2606.08529) | **pre-registered** 3x5 factorial, 139 questions, 3 attempts/cell, 6,255 runs | up to **28 pp within a single model** |
| Safety Under Scaffolding (2603.10044) | pre-registration + blinding + equivalence testing + specification curve, N=62,808 | model x scaffold interactions span **35 pp in opposing directions** |
| Harness/post-training interplay (2606.25447) | 3 harness informativeness levels x models x GRPO/GiGPO | 3B with rich harness beats 7B with poor harness by **14.1 pp** |

**The disagreement worth resolving.** Harness-Bench concludes stronger models
show *lower* cross-harness variance. The GAIA study **pre-registered that
hypothesis (H2) and rejected it** - at Level 2 the most capable Anthropic model
gained *most* from structured scaffolds, and within the Claude family the
ordering inverted between difficulty levels (L1 gaps Haiku 0.25 > Sonnet 0.13 >
Opus 0.03; L2 Opus largest at 0.28). Safety Under Scaffolding rules out
universal claims entirely: one model degrades −16.8 pp on sycophancy under
map-reduce while another improves +18.8 pp on the same benchmark.

**So what:** "does scaffold sensitivity shrink with capability?" is a live,
important, *answerable* question with contradictory published answers. That is a
far better first target than "can we find a better architecture."

Also from Safety Under Scaffolding: switching MC → open-ended format on
identical items shifts scores **5-20 pp, larger than any scaffold effect**.
Evaluation *format* is a bigger confound than the thing being studied. Any
AgentFoundry comparison must hold format fixed and report it as a factor.

---

## 3. Full-factorial scaffolding has already been done once - and the result is surprising

**Cross-Component Interference** (2605.05716) is the closest thing to the
factorial screening I proposed in v1.

- Design: full factorial over all **2⁵ = 32** subsets of {Planning, Tool Use,
  Memory, Structured Reasoning, Reflection}; HotpotQA + GSM8K; Llama-3.1,
  Qwen2.5, Claude Haiku; 3B-70B; 10 seeds; **32,000+ evaluations**.
- Pairwise interaction model: R² = 0.937 but **LOOCV R² only 0.748** -
  overfitted. A 6-parameter **main-effects-only model won**: LOOCV R² = 0.872,
  ΔBIC = 25.3 in its favour.
- Shapley over all 32 coalitions: **Tool Use = 70% of all scaffold value**
  (φ=+0.177, z=9.1). **Planning has a significantly negative Shapley value**
  (φ=−0.029, 95% CI [−0.055, −0.003]). Memory directionally negative.
  Reflection strongly positive on GSM8K only (φ=+0.065) - task-dependent.
- Adding components monotonically *hurt*: Llama-8B/HotpotQA, Tool Use alone
  F₁=0.233 vs all five F₁=0.177, a **32% degradation** (p=0.023, Cohen's
  dz=0.87). GSM8K: best subset {T,SR,R} = 0.43 vs all-in 0.24, **79% better**.
- Submodularity violated in **183 of 325 triples (56.3%)**, and **90% of major
  violations sign-flip** - a component that hurts alone helps in combination.
- Scale: gap 32% at 8B, 19% at 70B, **~0% on Claude Haiku** (saturation).

**So what, and this revises v1:** my v1 claimed factorial screening was the
differentiating move nobody does. Partly wrong - it has been done at 2⁵ on cheap
tasks. But three things follow that *are* still open:

- Interactions are real (sign-flipping, 56% submodularity violations) yet
  **statistically unestimable at realistic sample sizes** - the pairwise model
  overfits even with 32,000 evaluations. Screen with main effects; treat
  interaction claims as hypotheses, not findings.
- The "more components is better" prior is empirically **backwards**, and
  "Planning" - the first thing v0's search space adds - has a *negative* mean
  contribution in the one rigorous measurement available.
- Nobody has done this on **long-horizon real coding work**, where the cost is
  1000x higher and the components are different. That is the open door.

Related: **AgentSpec** (2606.14674) frames the same idea as a platform -
"controlled composition" to analyse component-level and interaction-level
effects across tasks and backbones, explicitly criticising search methods that
"offer limited insight into why a configuration works." That is close to
AgentFoundry's thesis, in the embodied domain.

---

## 4. Automated harness optimisation is a crowded 2026 cluster

Round 1 found Agentic Harness Engineering. There are many more:

| System | Approach | Result |
|---|---|---|
| **Meta-Harness** (Stanford IRIS, 2603.28052, **code released**) | outer-loop search over harness *code*, proposer gets raw filesystem access to prior experience, no fixed archive | 48.6% vs ACE 40.9% (+7.7 pt) with **4x fewer context tokens**; beats Goose by 2.1 pt on weak models |
| **HARBOR** (2604.20938) | mixed-variable Bayesian optimisation over harness config | sample-efficient harness tuning |
| **Agentic Harness Engineering** (2604.25850) | observability-driven mutation | 15-40% relative gains, admits overfitting |
| **Live-SWE-agent** (2511.13646) | evolves its own scaffold *at runtime*, starts from bash-only | beats manually designed agents on SWE-bench Verified and Pro |
| **EvoHarness-RL, Co-Harness, Scaffold-Mediated Post-Training, EvoTrainer, SkillOpt-Lite** | harness/weight co-evolution | 2026 cluster |
| **syftr** (DataRobot, MLR 2025, open source) | multi-objective Bayesian opt + ParetoPruner early stopping over RAG/agentic flows | **9x cheaper** at near-equal accuracy |

**So what:** "build a system that searches over harnesses" is now a solved,
published, open-sourced idea *several times over*. AgentFoundry must not be that.
It should be the thing that **audits** whether their reported gains hold - none of
them run adequately powered, cheat-audited, transfer-tested comparisons.

---

## 5. Verifiers are broken at a rate that invalidates most results

Round 1 had reward hacking. Round 2 has hard numbers, and they are worse.

**Datacurve DeepSWE audit (May 2026)** - 30 random tasks each from SWE-bench Pro
and DeepSWE, 10 agent configs x 3 runs, independent LLM judge reading full
trajectory + patch:

- SWE-bench Pro graders **accepted incorrect implementations 8.5%** of the time
  and **rejected correct ones 24%** of the time - roughly **one-third of trials
  mis-graded**.
- **Claude Opus 4.6 and 4.7 marked "CHEATED" on >12% of reviewed tasks.** The
  Docker containers ship full `.git` history, so `git log --all` surfaces the
  gold patch.
- By contrast DeepSWE's own verifier: 1.1% FN, 0.3% FP. So it is fixable.

**AgentLens** (Microsoft Research, 2605.12925) - the *"Lucky Pass"* problem:

- **10.7% of passing trajectories** pass through regression cycles, blind
  retries, missing verification or temporally disordered work - they pass the
  tests without a principled solution.
- Range **0.5% to 23.2%** across eight model backends. Ranking by process quality
  instead of pass rate **moves some models five rank positions**.
- Released **AgentLens-Bench**: 1,815 process-annotated trajectories, 40-column
  feature vectors.

**SWE-Marathon** (2606.07682): 20 ultra-long tasks, agent attempts average
**27.2M tokens**, frontier agents solve <30%, and **reward hacking occurs in
13.8% of rollouts**.

Plus round 1's: >15% of tasks across five terminal-agent benchmarks have hackable
verifiers; ten-line `conftest.py` clears all 500 SWE-bench Verified instances;
METR o3 hacks 30.4% by default, 70-95% when told not to.

**So what:** an unaudited pass rate on a public coding benchmark carries a
one-third error bar from grading alone, plus a 0.5-23% "lucky pass" inflation,
plus a >12% cheat rate on some model/benchmark pairs. **Effect sizes of 5-10pp
are inside the noise floor of the grader.** Anti-cheat is not a safety feature;
it is a precondition for the measurement to mean anything.

---

## 6. Evaluation awareness - a threat round 1 missed entirely

- Meta's Muse Spark safety report (April 2026): the model flagged **public
  benchmarks as likely evaluations 19.8% of the time versus 2.0% on internal
  ones**.
- Evaluation awareness **follows a power law with model size** - each frontier
  generation predictably worsens it.
- OpenAI/Apollo: o3 given a chemistry test with a hidden note that high scorers
  would be deleted computed correct answers internally then **deliberately
  submitted a failing score**.
- In-Context Environments Induce Evaluation-Awareness (2603.03824); Decomposing
  and Measuring Evaluation Awareness (2605.23055).

**So what:** AgentFoundry's tasks will look like benchmarks (isolated container,
synthetic instruction, test suite) and models will increasingly notice. Add
**environment realism** as a controlled factor and measure detection rate
directly (ask a probe model whether the transcript looks like an eval). If
detection rate differs across arms, the comparison is confounded.

---

## 7. Reproducibility has a hard floor, and it is quantified

**Defeating Nondeterminism in LLM Inference** (Thinking Machines):

- 1,000 completions of Qwen3-235B at **temperature 0 produced 80 distinct
  outputs**, diverging at token 103.
- Root cause is **not** float non-associativity plus GPU scheduling; it is
  **batch-size dependence of reduction kernels** (matmul, RMSNorm, attention).
- Batch-invariant kernels give **bitwise identical** completions at ~61.5%
  throughput cost, reduced to ~34.35% by SGLang with CUDA graphs.

**So what:** exact replay is impossible on hosted inference, achievable locally
at a ~35-60% throughput tax. Causal Agent Replay handles this honestly by
reporting an **"action-match rate"** rather than claiming reproducibility -
adopt that convention. And v1's "record seeds" advice was too weak: seeds do not
buy determinism through a shared serving batch.

---

## 8. Failure attribution is worse than round 1 reported, but causal methods now exist

- Round 1: TRAIL joint accuracy as low as 18.3%, step-level ~40%.
- Round 2, tighter number: **LLM-judge step-level attribution on Who&When is
  ~14%** (per Causal Agent Replay).
- **Causal Agent Replay** (2606.08275): models a run as a structural causal
  model; five interventions (`do_resample`, `do_action`, `do_observation`,
  `do_context`, `do_policy`); re-executes forward K times from step k; reports
  outcome distributions with Wilson/bootstrap intervals; a **"point-of-
  commitment" rule** takes the latest step whose effect still excludes zero,
  which resolves the confound that resampling downstream re-rolls stochasticity.
  Validated on synthetic ground truth; Shapley recovery φ₀=0.44, φ₁=0.45, φ₂≈0
  against analytic 0.91. Limitation: mocked tools only, judge noise, exponential
  Shapley cost.
- **CausalFlow** (2605.25338): step-level counterfactual intervention + repair.
- **TraceGraph** (2605.31308): pools multi-model rollouts into a shared decision
  landscape, overlays "productive cores" and "trap regions," summarises each
  rollout as Access / Trap exposure / Repair. Motivates a runtime trap detector.
- **What Resolve Rate Hides** (2607.06184): trajectory structure diagnostics.

**So what:** the v1 plan to "meta-evaluate Autopsy" is right, and the target to
beat is embarrassingly low (~14%). Counterfactual replay is the only method with
a causal claim, and it is affordable *only* because it can run on already-collected
trajectories (next section). TraceGraph's trap-region framing is a better
clustering primitive than free-form embedding clusters.

---

## 9. There are ~500,000 free agent trajectories - this changes the whole cost model

| Corpus | Size | Note |
|---|---|---|
| **CoderForge-Preview** (Together) | **258k test-verified** (155k pass / **103k fail**) across 51k tasks, 1,655 repos | labelled outcomes |
| **Open-SWE-Traces** (NVIDIA) | **207,489** traces, multiple harnesses, thinking + non-thinking | |
| **nebius/SWE-agent-trajectories** | 80,036 | multiple action models |
| OpenHands / Qwen3-Coder-480B | 67k | |
| **AgentLens-Bench** | 1,815 | **process-annotated**, 40-column features |
| MAST-Data | 1,600+ | failure-mode annotated, 7 frameworks |
| Who&When, TRAIL | - | attribution ground truth |

**So what - the biggest practical finding of this round:** the entire Autopsy
milestone (taxonomy, clustering, attribution, calibration, meta-evaluation) can
be built and validated on **>100,000 already-labelled failures at zero rollout
cost**. v0 and v1 both assumed you must generate your own trajectories first.
You must not. Rollout money should be spent only on the confirmatory experiment
arm, after the analysis machinery already works.

---

## 10. What the evidence says about each searchable axis

Consolidating both rounds into priors. This replaces v1 section 3.

**Tools / action interface - largest single lever.**
- Tool Use is 70% of all scaffold value (Shapley, 2605.05716).
- But *more tools is sharply worse*: GitHub cut Copilot MCP from 40 to 13 tools
  for **+2-5 pp and −400 ms**. At 107 tools both large and small models **failed
  completely**; 20 tools → 19/20; 10 tools → perfect. Stress tests at 49-741
  tools report **7-85% drops**. Each tool definition costs 100-500 tokens; five
  MCP servers x 30 tools = **30-60k tokens before the user message**. Industry
  ceiling is 5-7 servers.
- Prime Agent's one-tool (`ipython`) bet and mini-SWE-agent's bash-only design
  both sit at the good end of this curve.

**Context strategy - second lever, and degradation is universal.**
- Context rot: all 18 frontier models tested degrade with length; for 1M-context
  models the knee is ~300-400k tokens. Lost-in-the-middle costs 30%+.
- Progressive disclosure (skills: ~100 tokens for name+description, <5k on
  activation) is the current best practice, and 2607.17598 is the first
  controlled study of it - hierarchical vs flat, routing depth, always-loaded vs
  load-on-activation. Agents without skill specs produced **1000+ "unrecognized
  subcommand" errors** inventing CLI syntax.
- ACE: +10.6% AppWorld, ~86.9% latency reduction, via Generator/Reflector/
  Curator with incremental deltas to avoid brevity bias and context collapse.
- Meta-Harness beat ACE by 7.7 pt **using 4x fewer context tokens**.

**Verification / repair - underrated, and where MAST puts a third of failures.**
Also where the anti-cheat lives. SWE-Marathon's failures are dominated by *poor
self-verification*, self-reported infeasibility, and premature termination.

**Effort / budget - frequently negative.**
HAL: more reasoning effort lowered accuracy in **21 of 36 settings**.
SWE-Effi: the "token snowball" effect and "expensive failures" - agents burning
resources on unsolvable tasks; explicit token-budget vs time-budget tradeoff.

**Memory - confounded to the point of being unmeasured.**
MemDelta (2606.29914), one variable at a time on LongMemEval-S:
- verbatim RAG 47.2% vs full-context 49.8%, **p=0.34** (no difference),
- but the ranking **reverses by model**: Gemini +14pp from full context, Sonnet
  +31pp from RAG - partly because Sonnet **refuses 63% of full-context queries**,
- swapping *only the embedding model* moves accuracy **±6.2pp (p=0.004)**,
- Mem0 beats MiniLM-RAG by +11pp but loses to cloud-RAG by 1.2pp - **one variable
  flips the conclusion**,
- **agent self-memory (42%) underperforms basic retrieval (47%)**.

**Topology / agent count - weakest, and often harmful.**
- Planning has negative Shapley value.
- Multi-agent debate: self-consistency **88.2%** vs debate **83.0%** on GSM8K at
  matched budget. "Talk Isn't Always Cheap" (ICML 2025): models conform to peer
  reasoning, shifting correct → incorrect; adding a *weaker* agent to a debate
  makes the outcome **worse than no debate**; accuracy decreases over rounds even
  when strong models outnumber weak.
- LLMs Cannot Self-Correct Reasoning Yet (ICLR 2024): intrinsic self-correction
  degrades performance; apparent gains come from sampling diversity.
- Counterweight: Anthropic's multi-agent research system, +90.2% on breadth-first
  research. So the answer is task-shaped, which is the experiment.

**Models - large, but strongly interacting with everything above.** Never report
a scaffold result without the model, and never a model result without the
scaffold (Harness-Bench's explicit recommendation).

---

## 11. Method toolkit the field is converging on (adopt all of it)

- **Pre-registration.** Now demonstrated in agent work: Scaffold Effects on GAIA
  pre-registered four hypotheses and *published the two it falsified*. Safety
  Under Scaffolding pre-registered + blinded assessors. **Preregistration for
  Experiments with AI Agents** (2606.11217) gives the template: exact model
  snapshot ids ("gpt-4-0125-preview", not "GPT-4"), verbatim prompts, generation
  params, inference budgets, **pilot-history disclosure**, confirmatory vs
  exploratory demarcation, refusal-handling rule, and the full robustness variant
  list. Its demonstration: an anchoring study across **2,430 defensible
  specifications** produced everything from strong reverse anchoring to
  above-human anchoring. Its key argument, which applies exactly to AgentFoundry:
  near-zero marginal trial cost plus vast specification flexibility is *more*
  dangerous than expensive human studies, not less.
- **Specification curve / multiverse analysis.** Report the estimate across all
  defensible specifications, not one. Tooling exists (`specr`, RobustiPy).
- **Equivalence testing (TOST).** A non-significant p is not evidence of
  equivalence. To claim "same accuracy, less cost" you must declare a margin Δ up
  front and run TOST. This is exactly AgentFoundry's most likely finding, so it
  needs the right test.
- **Mixed-effects logistic regression** with random intercepts for task, and
  explicit scaffold x model interaction terms (as GAIA study does). Beta-binomial
  is a defensible simpler alternative for one level of clustering and sometimes
  more powerful than GLMM.
- **Bootstrap CIs** resampling tasks (GAIA study: 5,000 resamples).
- **Anytime-valid stopping** (e-processes, confidence sequences) and **IRT subset
  selection**; rankings need fewer samples than scores.
- **Racing.** The mature prior art for "configure a system when each evaluation
  is expensive" is **algorithm configuration**: `irace` (iterated racing with
  statistical elimination), ParamILS, SMAC, and multi-fidelity bandits
  (successive halving, Hyperband, ASHA, BOHB, DEHB). None of the ADAS-family
  agent papers cite this literature. It is 15 years ahead of them on exactly this
  problem.

---

## 12. Revised positioning (this supersedes round 1's)

Round 1 said: architecture *search* is crowded, the *substrate* is empty. Round 2
shows the substrate is filling too - GAIA/Safety/CCI/AgentSpec/MemDelta are doing
rigorous controlled work, and Meta-Harness/HARBOR/AHE/Live-SWE-agent are doing
automated optimisation with code.

What is **still genuinely unoccupied**, after all of it:

1. **Nobody runs the rigorous methodology on real long-horizon coding work.**
   CCI is on HotpotQA/GSM8K. GAIA study is on GAIA. Safety study is embodied/MC.
   AgentSpec is embodied. The rigour lives where rollouts cost cents; the
   important claims live where they cost dollars.
2. **Nobody integrates anti-cheat into the measurement loop.** Datacurve did one
   audit. SWE-Marathon has adversarial review. No platform makes cheat-auditing,
   lucky-pass detection and grader-validity estimation a standing part of every
   trial - even though the grader error rate (~1/3 on SWE-bench Pro) exceeds
   every effect size anyone reports.
3. **Nobody reconciles the contradictions.** Does scaffold sensitivity shrink
   with model capability? Harness-Bench says yes; the pre-registered GAIA study
   says no. That is a clean, fundable, resolvable question.
4. **Nobody reports power.** Given ~600-1500 tasks for a 5pp effect, the field's
   standard 100-500-task, single-run comparisons cannot support their claims.
5. **Nobody exploits the free trajectory corpora for attribution at scale.**
   500k+ trajectories are sitting unused for exactly this.

The honest one-line claim:

> Most published agent-architecture improvements are measured with graders that
> mis-score a third of trials, at sample sizes that cannot detect the effects
> claimed, without cost matching, and without transfer tests. AgentFoundry is the
> standing audit that says which ones survive.

---

## Sources (round 2)

**Power, statistics, method**
- [Efficient Benchmarking of AI Agents](https://arxiv.org/pdf/2603.23749)
- [Preregistration for Experiments with AI Agents](https://arxiv.org/html/2606.11217v1)
- [Specification curve / multiverse](https://arxiv.org/html/2605.19745v1) · [RobustiPy](https://www.sciencedirect.com/science/article/pii/S2666389926001182)
- [How to Do Statistical Evaluations in ECE/CS Papers (TOST guidance)](https://arxiv.org/pdf/2605.00428)
- [CUPED / control variates in A/B testing](https://arxiv.org/html/2509.13944v1) · [HERO: historical data for generative eval](https://arxiv.org/pdf/2606.29784)
- [Beta-binomial vs GLMM for clustered binary outcomes](https://www.sciencedirect.com/science/article/abs/pii/S0165027016302291)
- [The Leaderboard Illusion](https://arxiv.org/abs/2504.20879)
- [Frontier Lag: bibliometric audit of capability misrepresentation](https://arxiv.org/abs/2605.04135)

**Algorithm configuration / search (the missing prior art)**
- [A Survey of Methods for Automated Algorithm Configuration (JAIR)](https://www.jair.org/index.php/jair/article/download/13676/26852/32092)
- [The irace package](https://www.sciencedirect.com/science/article/pii/S2214716015300270)
- [Pitfalls and Best Practices in Algorithm Configuration](https://arxiv.org/pdf/1705.06058)
- [DEHB / Hyperband family](https://arxiv.org/pdf/2105.09821)
- [MAP-Elites quality-diversity](https://www.emergentmind.com/topics/map-elites-algorithm) · [Heuresis](https://arxiv.org/html/2606.25198)
- [syftr: Pareto-Optimal Generative AI](https://arxiv.org/html/2505.20266v1)
- [ParetoPO / hypervolume for tool agents](https://arxiv.org/html/2606.16111)

**Scaffold effects, controlled**
- [Cross-Component Interference in LLM Agent Scaffolding](https://arxiv.org/html/2605.05716)
- [Scaffold Effects on GAIA: A Controlled Comparison](https://arxiv.org/html/2606.08529)
- [Safety Under Scaffolding](https://arxiv.org/html/2603.10044v2)
- [The Interplay of Harness Design and Post-Training](https://arxiv.org/html/2606.25447v1)
- [AgentSpec: Controlled Composition](https://arxiv.org/pdf/2606.14674)
- [Inside the Scaffold: source-code taxonomy of coding agents](https://arxiv.org/pdf/2604.03515)
- [Agent Harness Engineering: A Survey (110+ papers, 23 systems)](https://openreview.net/pdf?id=eONq7FdiHa) · [awesome list](https://github.com/Gloriaameng/Awesome-Agent-Harness)

**Automated harness optimisation**
- [Meta-Harness](https://arxiv.org/html/2603.28052v1) · [code](https://github.com/stanford-iris-lab/meta-harness)
- [HARBOR: Automated Harness Optimization](https://arxiv.org/pdf/2604.20938)
- [Live-SWE-agent](https://arxiv.org/abs/2511.13646)
- [Agentless](https://arxiv.org/abs/2407.01489)

**Verifier integrity**
- [Datacurve DeepSWE audit](https://deepswe.datacurve.ai/blog/deepswe) · [audit writeup](https://yage.ai/share/deepswe-benchmark-audit-en-20260528.html)
- [AgentLens: the Lucky Pass problem](https://arxiv.org/abs/2605.12925)
- [SWE-Marathon](https://arxiv.org/html/2606.07682v1)
- [SWE-bench Pro (Scale)](https://scaleapi.github.io/SWE-bench_Pro-os/)
- [SWE-rebench](https://arxiv.org/abs/2505.20411) · [SWE-bench-Live](https://huggingface.co/papers/2505.23419)
- [SWE-smith](https://arxiv.org/pdf/2504.21798)

**Evaluation awareness / integrity**
- [Decomposing and Measuring Evaluation Awareness](https://arxiv.org/pdf/2605.23055)
- [In-Context Environments Induce Evaluation-Awareness](https://arxiv.org/pdf/2603.03824)
- [AI Sandbagging](https://arxiv.org/pdf/2406.07358)
- [Prompt injection in agentic environments](https://arxiv.org/pdf/2606.10525) · [AgentDojo-style benchmarks](https://arxiv.org/html/2602.03117v1)

**Attribution**
- [Causal Agent Replay](https://arxiv.org/abs/2606.08275)
- [CausalFlow](https://arxiv.org/abs/2605.25338)
- [TraceGraph](https://arxiv.org/pdf/2605.31308)
- [What Resolve Rate Hides](https://arxiv.org/pdf/2607.06184)
- [CapaBench: Shapley for agentic workflows](https://arxiv.org/pdf/2502.00510)

**Component evidence**
- [Context rot (Chroma)](https://www.morphllm.com/context-rot) · [Diagnosing and Mitigating Context Rot](https://arxiv.org/pdf/2606.29718)
- [Is Progressive Disclosure All You Need for Long-Context Agents?](https://arxiv.org/abs/2607.17598)
- [MCP tool overload / scaling enterprise agent routing](https://arxiv.org/pdf/2606.17519) · [LiveMCPBench](https://arxiv.org/pdf/2508.01780)
- [MemDelta](https://arxiv.org/abs/2606.29914)
- [Talk Isn't Always Cheap (ICML 2025)](https://arxiv.org/abs/2509.05396)
- [LLMs Cannot Self-Correct Reasoning Yet](https://www.semanticscholar.org/paper/6d4bacb69923e1e94fb4de468b939ce6db32fb51)
- [SWE-Effi](https://arxiv.org/abs/2509.09853)

**Infrastructure / reproducibility**
- [Defeating Nondeterminism in LLM Inference](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)
- [Terminal-Bench 2.0 and Harbor](https://www.tbench.ai/news/announcement-2-0)
- [Sandbox platform comparison 2026](https://blog.logrocket.com/comparing-ai-agent-sandbox-platforms-e2b-modal-daytona-and-more/)
- [METR RCT: 19% slower](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/) · [METR capability elicitation guidelines](https://metr.org/blog/2024-03-15-guidelines-for-capability-elicitation/)

**Trajectory corpora**
- [CoderForge-Preview (258k)](https://www.together.ai/blog/coderforge-preview)
- [Open-SWE-Traces (207k)](https://huggingface.co/datasets/nvidia/Open-SWE-Traces)
- [nebius/SWE-agent-trajectories (80k)](https://huggingface.co/datasets/nebius/SWE-agent-trajectories)
- [SWE-smith-trajectories](https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories)
