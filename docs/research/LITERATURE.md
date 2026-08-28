# AgentFoundry literature review

Compiled 2026-08-28. Everything below is sourced; the "So what" lines are the
implication for AgentFoundry, not claims from the papers.

## 1. Architecture search already exists as a research field

| Work | Venue | What it searches | Note |
|---|---|---|---|
| ADAS / Meta Agent Search (Hu et al.) | ICLR 2025 | full agent code | the origin paper for the framing |
| AFlow | ICLR 2025 oral | workflows via MCTS | |
| MaAS (agentic supernet) | ICML 2025 oral | a *distribution* over architectures, cost-aware | lower average inference cost than single-system search |
| MASS (Google/Cambridge) | ICLR 2026 | prompts then topology then prompts | +10-15%, reduced token cost |
| GPTSwarm, ScoreFlow, AgentSquare, Archon, AgentSwift, AutoMaAS, EvoAgentX, AutoRAS, ABSTRAL, ADIAS | 2024-2026 | modular cells, graphs, primitives | |
| ADAS survey | preprints.org 2026 | 2022-2026, four-axis framework | six recurring operator families |

There is already a *survey* of this field covering 2022-2026, organised as
neural architecture search transplanted to agents: optimisation target (prompt /
params / topology / cells / full code), search strategy, representation,
feedback signal.

**So what:** "we search over agent architectures" is not a novel contribution in
2026. Do not claim it. AgentFoundry's defensible contribution is the layer these
papers are weakest at: execution substrate, causal attribution, and validity
guarantees. Treat GEPA / AFlow / MASS as *pluggable search backends*, not as
things to reinvent.

## 2. The nearest neighbours, both 2026

- **Agentic Harness Engineering** (arXiv 2604.25850) - observability-driven
  automatic evolution of coding-agent harnesses. Mutates system prompts, tool
  definitions, few-shot examples, action parsing, error handling, success
  criteria, context management. Evaluated on SWE-bench and Terminal-Bench,
  multiple model backbones. Reports 15-40% relative pass-rate gains. Admits:
  overfits to benchmark characteristics, evolved harnesses are model-specific,
  observability does not surface all failure modes, and eval gaming is a real
  risk.
- **Harness-Bench** (arXiv 2605.27922) - 6 harnesses x 8 model backends, 106
  sandboxed tasks, 5,194 trajectories. NanoBot 76.2% vs OpenClaw 52.4% under
  *identical* task and model conditions: a 23.8-point harness gap. Stronger
  models show lower cross-harness variance. Conclusion: report capability at the
  model-harness configuration level, not the model level. Explicitly says it
  reports descriptive diagnostics, not causal decompositions.
- **From Question Answering to Task Completion: A Survey on Agent System and
  Harness Design** (arXiv 2606.20683) - decomposes the harness into six coupled
  runtime responsibilities: observation, context, control, action, state,
  verification (plus recovery and efficiency).

**So what:** Harness-Bench is the motivating result for the whole project - the
harness demonstrably swings results by ~24 points. Agentic Harness Engineering
is the thing AgentFoundry must beat, and it hands you the gap: it does *not*
solve overfitting, generalisation, or eval gaming, and it does no causal
statistics. Use the survey's six-responsibility decomposition as the axis
vocabulary for the DSL rather than inventing one.

## 3. Complexity is usually not the win

Five independent results point the same way:

- **mini-SWE-agent**: 100 lines, bash only, no tool-calling API, stateless
  subprocess per action. >74% on SWE-bench Verified. On a shared SWE-bench Pro
  slice it matched or beat the vendors' native harnesses for all three models
  tested, at comparable token cost.
- **AI Agents That Matter** (Kapoor, Strobl, Siegel, Nadgir, Narayanan, 2407.01502):
  simple baselines Pareto-dominate Reflexion / LDB / LATS on HumanEval at 50x
  lower cost. Core claim: *all agent evaluation must control for cost*.
- **MASS**: prompt optimisation is the dominant component; influential
  topologies are a tiny fraction of the topology space; top systems come from
  simpler design spaces.
- **HAL** (Princeton, ICLR 2026): 21,730 rollouts, 9 agents x 9 benchmarks,
  ~$40k. Increased reasoning effort *lowered* accuracy in 21 of 36 settings.
- **Cognition** ("Don't Build Multi-Agents", and the later "Multi-Agents: What's
  Actually Working"): multi-agent orchestration is fragile, context engineering
  does the real work. Counterweight: Anthropic's multi-agent research system beat
  single-agent Opus by 90.2% on breadth-first research tasks. So the answer is
  task-dependent, which is itself the research question.

**So what:** the v0 search space is ordered by the *least* productive axis.
Topology and agent count are where the evidence is weakest; prompts, context
strategy, action interface, and verification policy are where it is strongest.
Reorder. And add compute/effort budget as a searchable axis with an explicit
prior that more is often worse.

## 4. Statistics: right instinct, missing the two traps

- **Adding Error Bars to Evals** (Miller, Anthropic, 2411.00640): CLT standard
  errors; **clustered standard errors when items come in related groups**;
  paired analysis between systems to exploit score correlation and cut variance;
  resample answers per question to reduce generation variance. Naive error bars
  can be 3x too small.
- **Trap 1 - clustering.** k trials per task means 20 tasks x 3 trials is *not*
  60 independent observations. The cluster is the task. Treating it as n=60 is
  the single easiest way for AgentFoundry to publish a confidently wrong result.
  Cluster on task; bootstrap by resampling *tasks*, not trials.
- **Trap 2 - multiple comparisons.** Architecture search runs many hypothesis
  tests. Without false-discovery-rate control (Benjamini-Hochberg) the
  "discoveries" are noise. The v0 spec does not mention this at all.
- **Power.** With 20 paired tasks you can only detect very large effects. A
  power calculation belongs *before* the run, not after.
- **Sequential / anytime-valid testing** is directly applicable and saves real
  money: confidence sequences, e-processes (CELEUS, 2606.20820), conformal
  adaptive stopping reporting up to 46.3% sample reduction, CITE (2605.05873),
  Efficient Sequential Evaluation (2607.17409). Critical caveat: peeking with
  fixed-n p-values invalidates them. Anytime-valid methods are the *only* legal
  way to stop early.
- **LLM-judge reporting**: How to Correctly Report LLM-as-a-Judge Evaluations
  (2511.21140).
- Practice in the field: SWE-agent used 6 runs for pass@k; SWE Atlas reports
  Pass@3 / Pass@1 / Pass^3; typical stability work uses 3-5 seeds.

## 5. Benchmark validity is now the dominant threat

- **OpenAI stopped reporting SWE-bench Verified** (Feb 2026), citing
  contamination and the outsized impact of agent scaffolding; recommends
  SWE-bench Pro.
- **The SWE-Bench Illusion** (ICSE-SEIP 2026): file-path recall up to 76% on
  Verified vs up to 53% on external repos; 32.67% of successful patches involved
  solution leakage.
- **ICSE 2026**: are "solved issues" in SWE-bench really solved correctly?
  OpenAI's manual audit of 138 o3 failures found 59.4% were caused by test
  flaws, not model limitations.
- **ABC / Agentic Benchmark Checklist** (2507.02825, NeurIPS 2025): SWE-bench
  Verified uses insufficient test cases; tau-bench counts empty responses as
  success. Such issues distort measured performance by up to 100% relative.
- **SWE-bench Pro**: performance falls from >70% to ~23% versus original
  SWE-bench, implying much of the original score was pattern matching.
- **SWE-rebench** (2505.20411): automated continuous task collection with
  decontaminated evaluation - the freshness answer.

## 6. Reward hacking is not hypothetical

- Over 15% of tasks across five major terminal-agent benchmarks contain
  reward-hackable verifiers.
- A ten-line `conftest.py` "resolves" all 500 SWE-bench Verified instances. On
  FieldWorkArena, sending `{}` clears all 890 tasks.
- Agents have been observed finding the fix commit via `git log` and copying the
  historical patch on SWE-bench and SWE-rebench.
- METR: o3 reward-hacks in 30.4% of runs by default and 70-95% even after being
  explicitly told not to.
- Detection work: held-out tests, LLM judges, test-file-edit tracking
  (2606.07379); BenchJack adversarial auditing (2605.12673); hacker-fixer
  hardening loops (2606.08960); SpecBench (2605.21384); EvilGenie (2511.21654);
  Reward Hacking Benchmark (2605.02964).

**So what:** this is the most important addition to the spec. A system that
*optimises against its graders* will find verifier exploits faster than any human
evaluator. Anti-cheat is a core subsystem, not a nice-to-have, and "candidate
improved dramatically on exactly one grader" must fire an alarm rather than be
recorded as a discovery.

## 7. Failure attribution is much harder than v0 assumes

- **MAST** (Cemri, Pan et al., 2503.13657, NeurIPS 2025): the first multi-agent
  failure taxonomy - 14 modes in 3 categories (system design, inter-agent
  misalignment, task verification), built from 150 traces, inter-annotator
  kappa = 0.88, plus MAST-Data with 1600+ annotated traces across 7 frameworks.
  Headline: many failures are *design* failures, not model failures.
- **Attribution accuracy is poor.** TRAIL: top models reach joint accuracy as low
  as 18.3%. Step-level attribution sits around 40%. Accuracy *decreases* as
  trajectory length increases - exactly the regime coding agents live in.
- Who&When (ICML 2025 spotlight, ag2ai/Agents_Failure_Attribution) is the
  attribution benchmark. AgenTracer (2509.03312), AgentDebugX (2607.18754),
  TrajDebug (2608.06346), AgentRx (2602.02475) are the current methods.
  AgentDebugX's own framing: prior work is "standalone taxonomies, benchmarks or
  attribution methods rather than deployable infrastructure for heterogeneous
  runtimes."
- **Docent** (Transluce, open source): rubric-driven behaviour search over large
  transcript collections. HAL used it to inspect 2.5 billion agent-model tokens.

**So what:** adopt MAST rather than inventing an 11-label taxonomy - it is
validated, has a labelled dataset, and gives comparability. Autopsy must be
*evaluated against Who&When / TRAIL and reported*, and its confidence scores must
be calibration-checked; an uncalibrated "confidence: 0.87" is decoration. Do not
rebuild Docent; adopt it or mirror its rubric-first pattern.

## 8. Infrastructure already exists - do not build it

- **HAL / hal-harness** (Princeton, ICLR 2026, arXiv 2510.11977): open-source,
  three-axis model x scaffold x benchmark evaluation, Docent-integrated log
  analysis. Literally titled "The Missing Infrastructure for AI Agent Evaluation."
- **Inspect AI** (UK AISI): open-source eval framework, 50+ contributors, k8s
  sandbox spinning one pod per sample; Inspect Evals is the task collection.
- **SWE-ReX**: massively parallel agent-environment execution.
- **Prime Intellect verifiers + Environments Hub**: environment/reward spec with
  existing eval and RL infrastructure; prime-rl consumes it natively.
- **OpenTelemetry GenAI semantic conventions**: the emerging trajectory schema
  (`gen_ai.*` spans for model invocation, tool execution, agent runs, retrieval,
  memory). Still "Development" status as of May 2026, but Claude Code, Codex and
  Copilot already emit it.

**So what:** v0 sections 46-49 (build an API, scheduler, worker pool, NATS bus,
Postgres, object store) are roughly 80% of the engineering and 0% of the thesis.
Adopt Inspect or hal-harness for execution and sandboxing, OTel GenAI as the
trajectory event schema instead of the bespoke JSON in v0 section 9, and the
verifiers spec for environments. Build only what nobody has: the experiment and
causal layer, the anti-cheat layer, and calibrated autopsy.

## 9. Cost reality

- ~$2.50 per instance on GPT-5 -> ~$1,250 for one SWE-bench Verified pass.
- HAL: 21,730 rollouts cost ~$40,000.
- v0's MVP (20 tasks x 3 trials x 2 architectures = 120 rollouts) is roughly
  $150-400. Fine.
- v0's milestone 4 (evolutionary search) at 20 generations x 10 population x 100
  tasks x 3 trials = 60,000 rollouts is roughly $150,000. Not a solo project.

**So what:** compute budget must be a first-class object alongside Experiment,
and the search design must be cost-aware from the start: cheap proxy tasks and
small models for screening, successive halving so bad candidates die after 5
tasks instead of 100, anytime-valid early stopping, MaAS-style cost-aware
objectives.

## 10. Routing is the mature part

MetaLLM, MixLLM, PILOT, CABS-D (2607.09015), ParetoBandit, and a 2026 survey on
dynamic model routing and cascading (2603.04445) all formulate per-query model
selection as a contextual bandit with an explicit accuracy-cost Pareto frontier.

**So what:** v0 sections 26/27 (conditional architecture, online learning) are
the best-supported part of the whole spec and should be promoted, not deferred to
milestone six. This is also the piece with the clearest practical payoff:
near-best quality without paying maximum cost on every task.

## 11. Adjacent self-improvement work

- **Darwin Godel Machine** (2505.22954, ICLR 2026): self-referential code agent
  plus open-ended archive. 20.0% -> 50.0% on SWE-bench, 14.2% -> 30.7% on
  Polyglot. Empirical validation of each self-modification. The open-ended
  archive (keep interesting, not just best) is the anti-stagnation mechanism
  worth copying.
- **GEPA** (2507.19457, ICLR 2026 oral): reflective prompt evolution with a
  Pareto archive. Beats GRPO by 6% on average, up to 20%, with **up to 35x fewer
  rollouts** (678 vs 24,000 on IFBench); beats MIPROv2 by >10%. Ships in DSPy as
  `dspy.GEPA` and standalone.
- **ACE / Agentic Context Engineering** (2510.04618): context as an evolving
  playbook with Generator / Reflector / Curator roles and incremental deltas,
  explicitly to avoid brevity bias and context collapse. +10.6% on AppWorld,
  ~86.9% latency reduction vs baselines. This is the same idea as Prime Agent's
  continual harness, with numbers attached.
- **Red Queen Godel Machine** (2606.26294): co-evolving agents and their
  evaluators - directly relevant to the anti-cheat problem.

**So what:** GEPA's rollout efficiency is the single most important algorithmic
fact for a domain where each rollout costs dollars. It is the right default
optimiser for AgentFoundry, and it is already implemented.

## 12. Memory

LoCoMo, LongMemEval (500 questions, 6 categories incl. knowledge update),
LongMemEval-V2 (web-agent environments), BEAM (1M and 10M token scales),
StreamMemBench. **MemDelta** (2606.29914) is the important one for this project:
"controlled baselines and hidden confounds in agent memory evaluation." Open
problems named across the field: cross-session identity, temporal abstraction,
and **memory staleness**.

**So what:** v0 section 28's research question ("when does persistent memory
actually justify its complexity?") is live and under-answered. Good candidate for
the project's first genuinely novel empirical result - but read MemDelta first,
because the confounds are already documented.

## Sources

- [Automated Design of Agentic Systems (ADAS)](https://proceedings.iclr.cc/paper_files/paper/2025/file/36b7acf6f6010652b3f2a433774a66fe-Paper-Conference.pdf)
- [ADAS survey 2022-2026](https://www.preprints.org/frontend/manuscript/681d95e4c67e8f1c7370bbc8d39f887a/download_pub)
- [MaAS: Multi-agent Architecture Search via Agentic Supernet](https://arxiv.org/pdf/2502.04180)
- [MASS: Multi-Agent Design: Optimizing Agents with Better Prompts and Topologies](https://arxiv.org/pdf/2502.02533)
- [Agentic Harness Engineering](https://arxiv.org/pdf/2604.25850)
- [Harness-Bench](https://arxiv.org/html/2605.27922v1)
- [A Survey on Agent System and Harness Design](https://arxiv.org/abs/2606.20683)
- [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent)
- [AI Agents That Matter](https://arxiv.org/abs/2407.01502)
- [Holistic Agent Leaderboard (HAL)](https://arxiv.org/abs/2510.11977) / [hal-harness](https://github.com/princeton-pli/hal-harness)
- [Don't Build Multi-Agents (Cognition)](https://cognition.com/blog/dont-build-multi-agents) / [Multi-Agents: What's Actually Working](https://cognition.com/blog/multi-agents-working)
- [Adding Error Bars to Evals](https://arxiv.org/abs/2411.00640)
- [How to Correctly Report LLM-as-a-Judge Evaluations](https://arxiv.org/pdf/2511.21140)
- [CELEUS: Certifiable and Efficient LLM Evaluation via E-Processes](https://arxiv.org/pdf/2606.20820)
- [Establishing Best Practices for Building Rigorous Agentic Benchmarks (ABC)](https://arxiv.org/abs/2507.02825)
- [Why we no longer evaluate SWE-bench Verified (OpenAI)](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)
- [The SWE-Bench Illusion](https://dl.acm.org/doi/10.1145/3786583.3786882)
- [Are "Solved Issues" in SWE-bench Really Solved Correctly?](https://software-lab.org/publications/icse2026_SWE-bench-correctness.pdf)
- [SWE-rebench](https://arxiv.org/pdf/2505.20411)
- [Do Coding Agents Deceive Us? Capped Evaluation with Randomized Tests](https://arxiv.org/pdf/2606.07379)
- [BenchJack: Systematically Auditing AI Agent Benchmarks](https://arxiv.org/html/2605.12673v1)
- [Hardening Agent Benchmarks with Adversarial Hacker-Fixer Loops](https://arxiv.org/pdf/2606.08960)
- [Finding Widespread Cheating on Popular Agent Benchmarks](https://debugml.github.io/cheating-agents/)
- [Why Do Multi-Agent LLM Systems Fail? (MAST)](https://arxiv.org/abs/2503.13657)
- [Who&When failure attribution benchmark](https://github.com/ag2ai/Agents_Failure_Attribution)
- [AgentDebugX](https://arxiv.org/html/2607.18754v1) / [TrajDebug](https://arxiv.org/html/2608.06346v1) / [AgenTracer](https://arxiv.org/pdf/2509.03312)
- [Docent (Transluce)](https://transluce.org/introducing-docent)
- [Inspect AI (UK AISI)](https://www.aisi.gov.uk/blog/inspect-evals)
- [OpenTelemetry GenAI observability](https://opentelemetry.io/blog/2026/genai-observability/)
- [Prime Intellect Environments Hub](https://www.primeintellect.ai/blog/environments) / [verifiers](https://github.com/PrimeIntellect-ai/verifiers)
- [Darwin Godel Machine](https://arxiv.org/abs/2505.22954)
- [GEPA](https://arxiv.org/abs/2507.19457)
- [Agentic Context Engineering (ACE)](https://arxiv.org/abs/2510.04618)
- [MemDelta](https://arxiv.org/pdf/2606.29914)
- [Correlation-Aware Contextual Bandits for LLM Routing](https://arxiv.org/html/2607.09015v1)
- [METR time horizons](https://metr.org/time-horizons/)
