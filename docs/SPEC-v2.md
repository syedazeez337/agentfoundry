# AgentFoundry v2 - specification after deep literature review

Supersedes SPEC-v1.md. Delta document; v1 items not mentioned still stand.
Every change traces to `research/LITERATURE-v2.md`. Where v2 reverses v1, it
says so.

---

## 0. What changed since v1

v1 said: architecture *search* is crowded, the rigorous *substrate* is empty, so
build the substrate. Round 2 shows the substrate is filling too. Pre-registered,
factorial, mixed-effects, specification-curve studies of agent scaffolds now
exist (GAIA scaffold study; Safety Under Scaffolding; Cross-Component
Interference; AgentSpec; MemDelta), and automated harness optimisation is a
crowded 2026 cluster with released code (Meta-Harness, HARBOR, Agentic Harness
Engineering, Live-SWE-agent, syftr, EvoHarness-RL, Co-Harness).

Three v1 claims are downgraded:

- **"Preregistration is novel for an automated system."** Partly. It is already
  standard in the careful corner of the 2026 literature. Still not done
  *continuously as infrastructure*, which is the remaining claim.
- **"Factorial screening is the differentiating move nobody does."** Wrong. It
  has been done at 2⁵ with 32,000+ evaluations. Nobody has done it on
  long-horizon real coding work.
- **"Build the experiment layer, anti-cheat, calibrated autopsy."** Still right,
  but the *reason* has changed: not "nobody does rigour," but "rigour is only
  done on cheap benchmarks, one paper at a time, and never with anti-cheat."

---

## 1. The finding that reshapes the project: nothing is adequately powered

To detect a 5 pp paired difference at 80% power (McNemar, binary outcomes):
**~600 tasks at discordance 0.10, ~1500 at 0.25.** At ~$2.50/instance that is
**$1,500-$3,750 per arm** for one comparison.

Meanwhile the measurement floor is worse than the effects being claimed:

- SWE-bench Pro graders accept incorrect patches **8.5%** of the time and reject
  correct ones **24%** - roughly **one third of trials mis-graded** (Datacurve,
  May 2026).
- **0.5-23.2% of *passing* trajectories are "Lucky Passes"** - they pass without a
  principled solution (AgentLens, Microsoft Research). Re-ranking by process
  quality moves models up to five positions.
- Claude Opus 4.6/4.7 flagged as **cheating on >12%** of reviewed SWE-bench Pro
  tasks by reading the gold patch from shipped `.git` history.
- Reward hacking in **13.8% of SWE-Marathon rollouts**.

**Therefore: a 5-10 pp architecture claim on a public coding benchmark is inside
the noise floor of the grader.** This is the project's actual thesis. Not "we can
find better architectures" but "almost nobody can currently tell whether they
have."

Design consequences, binding on everything downstream:

1. **Declare a minimum detectable effect up front.** With a feasible 100-200 task
   set, AgentFoundry can only support claims of roughly **>15 pp**. Say so in the
   UI, on every experiment.
2. **Buy power with variance reduction, not tasks.** Paired designs; multiple
   trials per task modelled as rates (beta-binomial or GLMM with task random
   intercepts); CUPED-style covariate adjustment using a cheap difficulty
   predictor.
3. **Prefer ranking to scoring** where the question allows it - rankings need far
   fewer evaluations with probabilistic guarantees (*Efficient Benchmarking of AI
   Agents*).
4. **Equivalence testing is the default hypothesis, not the fallback.** The most
   likely true finding is "candidate is not better." Claiming that requires TOST
   against a pre-declared margin, not a non-significant p-value.

---

## 2. Revised thesis and one-line claim

> Most published agent-architecture improvements are measured with graders that
> mis-score a third of trials, at sample sizes that cannot detect the effects
> claimed, without cost matching, and without transfer tests. AgentFoundry is the
> standing audit that says which ones survive.

Five things still genuinely unoccupied after everything found in both rounds:

1. Rigorous method applied to **real long-horizon coding work** (all the rigorous
   studies are on HotpotQA, GSM8K, GAIA, embodied MC tasks).
2. **Anti-cheat inside the measurement loop**, not as a one-off audit.
3. **Reconciling the published contradictions** (section 4).
4. **Reporting power** at all.
5. **Exploiting the ~500k free trajectories** for attribution at scale (section 3).

---

## 3. New: bootstrap the entire analysis layer on free data

The biggest practical finding of round 2. Public, already-labelled corpora:

| Corpus | Size |
|---|---|
| CoderForge-Preview (Together) | **258k test-verified** - 155k pass / **103k fail**, 51k tasks, 1,655 repos |
| Open-SWE-Traces (NVIDIA) | **207,489** traces, multiple harnesses |
| nebius/SWE-agent-trajectories | 80,036 |
| OpenHands / Qwen3-Coder-480B | 67k |
| AgentLens-Bench | 1,815 **process-annotated** |
| MAST-Data | 1,600+ failure-mode annotated |
| Who&When, TRAIL | attribution ground truth |

Both v0 and v1 assumed AgentFoundry must generate trajectories before it can
analyse them. **It must not.** Build and validate taxonomy, clustering,
attribution, calibration and meta-evaluation on >100,000 labelled failures at
**zero rollout cost**. Spend rollout money only on the confirmatory arm, once the
analysis machinery already works and its accuracy is known.

This makes milestones 2 and 3 (Autopsy, failure clustering) nearly free, and
moves them **before** the MVP rather than after it.

---

## 4. New primary research question: settle a published contradiction

Better than "can we find +8 pp." Three papers disagree, in print, on whether
scaffold sensitivity shrinks as models get more capable:

- **Harness-Bench**: stronger models show *lower* cross-harness variance.
- **Scaffold Effects on GAIA**: pre-registered exactly this as H2 and
  **rejected it** - at Level 2 the most capable Anthropic model gained *most*
  from structured scaffolds; within the Claude family the ordering inverted
  between difficulty levels.
- **Safety Under Scaffolding**: model x scaffold interactions span **35 pp in
  opposing directions**, ruling out any universal claim.

This is clean, answerable, cheap relative to architecture search, and the answer
matters to everyone doing capability evaluation (the "elicitation gap"). Ship
this before ship anything about discovering architectures.

---

## 5. Revised axis priors (supersedes v1 section 3)

Now grounded in measured effects rather than inference.

| Rank | Axis | Evidence | Prior |
|---|---|---|---|
| 1 | **Action/tool interface** | Tool Use = **70% of all scaffold value** (exact Shapley over 32 subsets). But 40→13 tools gave GitHub **+2-5 pp, −400 ms**; at **107 tools models fail completely**; 49-741 tools → **7-85% drops**; each tool def is 100-500 tokens | large, non-monotonic, peaked at *few* tools |
| 2 | **Context strategy** | context rot in **all 18** frontier models tested, knee at ~300-400k for 1M models; lost-in-middle 30%+; ACE +10.6% AppWorld; Meta-Harness beat ACE by 7.7 pt with **4x fewer tokens** | large |
| 3 | **Verification / repair** | one of MAST's three failure categories; SWE-Marathon failures dominated by poor self-verification and premature termination; also where anti-cheat lives | large, under-studied |
| 4 | **Effort / budget** | HAL: more reasoning effort **lowered** accuracy in **21 of 36** settings; SWE-Effi "token snowball" and "expensive failures" | large and **often negative** |
| 5 | **Memory** | MemDelta: RAG 47.2% vs full-context 49.8% **p=0.34**; ranking **reverses by model** (Gemini +14 pp full-context, Sonnet +31 pp RAG, refuses 63% of full-context); **embedding swap alone ±6.2 pp**; agent self-memory 42% < basic retrieval 47% | unknown, heavily confounded |
| 6 | **Model per role** | interacts with everything above | large but well-studied |
| 7 | **Topology / agent count** | Planning has **significantly negative Shapley** (φ=−0.029, CI [−0.055,−0.003]); self-consistency 88.2% vs debate 83.0% at matched budget; adding a weaker agent makes debate **worse than no debate**; intrinsic self-correction degrades performance | smallest, frequently negative |

Two structural facts about this space, both from Cross-Component Interference:

- **Interactions are real but statistically unestimable.** Submodularity violated
  in **56.3%** of triples and **90% of major violations sign-flip**, yet the
  pairwise interaction model *overfits* (LOOCV R² 0.748) versus main-effects-only
  (0.872, ΔBIC 25.3) even at 32,000 evaluations. Screen with main effects; treat
  any interaction claim as a hypothesis needing its own confirmatory run.
- **More components is empirically backwards.** Best subset beat all-in by
  **32%** at 8B and **79%** on GSM8K. The effect shrinks with capability (19% at
  70B, ~0% on Haiku) but does not reverse.

**Practical implication for the DSL:** search *subtraction* as aggressively as
addition. `REMOVE_AGENT`, `REMOVE_TOOL`, `REDUCE_EFFORT` should be first-class
and probably first-tried operators. v0's operator list is addition-biased.

---

## 6. Anti-cheat, expanded (v1 section 6 was too small)

Now a measurement-validity subsystem, because the grader error rate exceeds the
effect sizes.

Per trial, in addition to v1's held-out tests / git scrubbing / test-edit
detection / exploit audit / divergence alarm:

- **Estimate grader validity itself.** Run an independent LLM judge over
  trajectory + patch on a random sample of trials and report the grader's FP/FN
  rate as a first-class experiment statistic, the way Datacurve did (8.5% / 24%).
  A comparison whose effect is smaller than its grader's error bar is reported as
  **uninterpretable**, not as a result.
- **Lucky-pass detection.** Adopt AgentLens' intent labelling (Exploration /
  Implementation / Verification / Orchestration) and flag passes with regression
  cycles, blind retries, missing verification or temporally disordered phases.
  Report a **process-quality-adjusted** pass rate alongside the raw one, and show
  when ranking changes between them.
- **Evaluation-awareness probe.** Models flag public benchmarks as evals **19.8%**
  of the time vs **2.0%** for internal ones, and the tendency scales with model
  size. Probe each trajectory for eval-awareness signals; if detection rate
  differs across arms, the comparison is confounded and must be flagged.
- **Prompt-injection hygiene.** Repository content is untrusted input. Task
  fixtures must be scanned for injected instructions before use (AgentDojo-style
  injection points are a real, benchmarked attack surface).

Task authoring gains, on top of v1's known-cheat test: a `.git`-history scrub
check, and a "does a null patch pass?" check (a `{}` submission cleared all 890
FieldWorkArena tasks; a 10-line `conftest.py` clears all 500 SWE-bench Verified
instances).

---

## 7. Reproducibility: state the hard floor, do not promise replay

v1 said "record seeds." Too weak. Temperature-0 inference is **not**
deterministic: 1,000 completions of Qwen3-235B at temp 0 gave **80 distinct
outputs**, diverging at token 103. The cause is **batch-size dependence of
reduction kernels**, not float non-associativity. Batch-invariant kernels give
bitwise-identical output at ~61.5% throughput cost (~34.35% with SGLang + CUDA
graphs).

So:

- Hosted inference: exact replay is impossible. Report an **action-match rate**
  (the convention Causal Agent Replay uses) instead of claiming reproducibility.
- Local models: offer a `--deterministic` mode using batch-invariant kernels,
  accept the throughput tax, and use it for anything that needs counterfactual
  replay.
- Pin model snapshot ids everywhere, never family names.

---

## 8. Autopsy: use causal replay, and target a very low bar

LLM-judge step-level attribution on Who&When is **~14%**. The bar is low enough
that a modest causal method wins.

Adopt **Causal Agent Replay**'s design: treat a run as a structural causal model;
support `do_resample`, `do_action`, `do_observation`, `do_context`, `do_policy`;
re-execute forward K times from the intervened step; report outcome distributions
with Wilson/bootstrap intervals; use the **point-of-commitment rule** (latest step
whose effect still excludes zero) to avoid the confound where downstream
resampling re-rolls its own stochasticity. Its stated limits - mocked tools,
judge noise, exponential Shapley - are exactly what a platform with real sandboxes
and Monte-Carlo Shapley can push on.

Adopt **TraceGraph**'s clustering primitive instead of free-form embeddings: pool
rollouts into a shared decision landscape, mark productive cores and **trap
regions**, and summarise each rollout as Access / Trap exposure / Repair. It maps
cleanly onto v0's failure clustering and yields a runtime trap detector as a
by-product.

Keep v1's rule: publish Autopsy's own measured accuracy and calibration next to
every diagnosis.

---

## 9. Search: the answer, revised

v1 said screening → halving → GEPA → bandits. Round 2 changes two things.

**Import the algorithm-configuration literature.** The mature prior art for
"configure a system when every evaluation is expensive and noisy" is not ADAS -
it is **`irace` (iterated racing with statistical elimination), ParamILS, SMAC**,
and the multi-fidelity bandit family (**successive halving, Hyperband, ASHA,
BOHB, DEHB**). Fifteen years of work on exactly this problem, and *not one
ADAS-family agent paper cites it*. Using `irace` as the outer loop is both
better-founded and a differentiating position.

Revised staging:

1. **Main-effects screening** over the ranked axes - deliberately *not* a full
   factorial, because interactions overfit even at 32,000 evaluations.
2. **Racing (`irace`) + successive halving** as the outer loop. Bad configs die
   after 5 tasks with a statistical elimination test, not a heuristic.
3. **GEPA** for anything reflective (35x fewer rollouts than GRPO; ships in DSPy).
   **syftr**'s ParetoPruner is the reference for multi-objective early stopping
   and reported **9x cost reduction** at near-equal accuracy.
4. **Contextual bandits for routing only**, not discovery.
5. Archive: **MAP-Elites** rather than a plain best-so-far list. Quality-diversity
   illumination fits "which architectures win in which regions of task space,"
   which is the actual question, and it pairs with v0's genealogy and the Darwin
   Godel Machine's anti-stagnation argument.

---

## 10. Method stack to adopt wholesale

| Concern | Adopt |
|---|---|
| Pre-registration | template from *Preregistration for Experiments with AI Agents* - exact model snapshot ids, verbatim prompts, generation params, budgets, **pilot-history disclosure**, confirmatory/exploratory split, refusal handling, full robustness variant list |
| Robustness | **specification curve / multiverse** reporting across all defensible specs (its demo: 2,430 specs produced everything from reverse to super-human anchoring) |
| "No difference" claims | **TOST equivalence** with pre-declared margin |
| Clustered inference | mixed-effects logistic regression, task random intercepts, explicit scaffold x model interaction terms; beta-binomial as the simpler alternative |
| Intervals | bootstrap resampling **tasks**, 5,000 resamples |
| Early stopping | e-processes / confidence sequences (anytime-valid only) |
| Sample efficiency | IRT subset selection; rankings over scores |
| Multiple comparisons | Benjamini-Hochberg across the campaign |
| Blinding | assessor blinding for any model-graded outcome |

---

## 11. Revised MVP

Reordered so the free work comes first.

**Phase 0 - free (weeks, ~$0 rollout cost).**
Ingest CoderForge (103k labelled failures) + Open-SWE-Traces + AgentLens-Bench +
MAST-Data. Build the MAST-based taxonomy, TraceGraph clustering, and the Autopsy
attribution model. Meta-evaluate against Who&When and TRAIL and publish the
accuracy. This alone is a publishable artifact and costs nothing.

**Phase 1 - the contradiction (bounded spend).**
Pre-registered scaffold x model factorial on real coding tasks: 3 scaffolds x 3-4
models, fresh decontaminated tasks (SWE-rebench / SWE-bench-Live), 3 trials per
cell, mixed-effects logistic regression with interaction terms, bootstrap CIs,
specification curve. Primary question: **does scaffold sensitivity shrink with
model capability?** Publish whichever way it falls, including "inconclusive."

**Phase 2 - the audit.**
Take 3-4 accepted architecture improvements (add reviewer, add planner, raise
effort, add memory) and test each under: cost-matched arms, grader-validity
estimation, lucky-pass adjustment, cheat audit, transfer gate, TOST for
equivalence, declared MDE. Given the evidence in section 5 - Planning has
negative Shapley, effort hurts in 21/36 settings, agent self-memory
underperforms plain retrieval, best subset beats all-in by 32-79% - **most of
them should fail.**

**Only then** consider search. Search is the least defensible part of the project
and the most expensive; it should be the last thing built, not the fourth
milestone.

---

## 12. Open questions after round 2

- Does the section 5 axis ordering, measured on HotpotQA/GSM8K/GAIA, hold on
  long-horizon coding? Testing that *is* Phase 1's secondary contribution.
- Is `irace` workable when a single "instance" costs $2.50 and outcomes are
  binary and noisy? Racing assumes cheap-ish instances; the elimination test may
  never fire at feasible budgets. Needs a simulation study before committing.
- Can grader-validity estimation be made cheap enough to run on every experiment
  rather than a sample? Datacurve's method needs a judge reading full
  trajectories, which is not free.
- Where does the decontaminated task supply come from long-term? Still the
  hardest unsolved piece. SWE-rebench and SWE-bench-Live are the current answers;
  SWE-smith-style synthesis (50k instances from 128 repos) is the fallback.
- Does any of this survive RL-trained agents? The harness/post-training interplay
  work suggests harness benefits must be applied **at training time** (training-
  time application beat post-hoc by 20.7-22.5 pp), which would mean inference-time
  harness search has a ceiling. Worth a dedicated review before committing years
  to this.
