# AgentFoundry v1 - revised specification

Supersedes SPEC-v0-original.md. Every change here traces to a finding in
docs/research/LITERATURE.md. This is a delta document: v0 sections not mentioned
stand as written.

## 0. Retitle

v0's subtitle was "Build Specification for a Codex-like Coding Agent," which
contradicts its own section 1. The project is not a coding agent.

**AgentFoundry: an experiment platform that makes claims about agent
architectures falsifiable.**

## 1. Revised thesis

v0: "a platform that experimentally discovers, evaluates, and explains better
agent architectures."

The discovery half is a crowded field - ADAS, AFlow, MaAS, MASS, GPTSwarm,
AgentSquare, ScoreFlow, AutoMaAS, EvoAgentX and a 2026 survey of the whole area
already exist. Claiming discovery as the contribution invites the response "this
is ADAS with a UI."

Revised thesis, keeping the same machine but moving the claim to the defensible
part:

> Architecture claims in this field are mostly unfalsifiable: uncontrolled for
> cost, uncorrected for clustering, uncorrected for multiple comparisons,
> unaudited for verifier exploits, and untested for transfer. AgentFoundry is
> the substrate that makes them testable, and it uses existing optimisers to
> generate the candidates.

Three things nobody has built, which is what AgentFoundry actually builds:

1. **A causal experiment layer** - preregistration, cost-matched arms, clustered
   and FDR-corrected inference, anytime-valid stopping, mandatory transfer gate.
2. **An anti-cheat layer** - because a system that optimises against graders will
   find verifier exploits faster than any human evaluator.
3. **A calibrated autopsy** - failure attribution that reports its own measured
   accuracy instead of an invented confidence number.

Everything else is adopted, not built.

## 2. Adopt, do not build

v0 sections 46-49 specify an API, scheduler, worker pool, NATS bus, Postgres and
object store. That is roughly 80% of the engineering and 0% of the thesis, and it
is why the v0 plan is not achievable solo.

| v0 section | v0 plan | v1 plan |
|---|---|---|
| 6 Execution layer | custom AgentBackend contract | wrap **hal-harness** (Princeton, ICLR 2026) or **Inspect AI** (UK AISI); both already run heterogeneous agents |
| 7 Environment layer | custom sandbox | **Inspect k8s_sandbox** (one pod per sample) or **SWE-ReX** for parallel exec |
| 9 Trajectory recorder | bespoke JSON event schema | **OpenTelemetry GenAI semantic conventions** - Claude Code, Codex and Copilot already emit it, so backends come nearly free |
| 16 Failure clustering | custom clustering | **Docent** (Transluce, open source) - rubric-driven behaviour search; HAL used it over 2.5B tokens |
| 22-23 Benchmark format | custom task.yaml | **Prime Intellect `verifiers`** env spec - gets Environments Hub tasks for free and makes outputs reusable for RL |
| 46-49 Control plane | API + scheduler + NATS + Postgres + S3 | Postgres + local queue. Nothing else until it actually hurts. |

Adopting the verifiers spec is also strategically aligned with the Prime Agent
work already studied, and gives a second consumer (prime-rl) for the same
artifacts.

## 3. The search space is ordered backwards

v0 section 4 leads with agent count and topology. The evidence says those are the
weakest axes:

- MASS: prompts dominate; influential topologies are a tiny fraction of the space.
- mini-SWE-agent: 100 lines, bash only, >74% SWE-bench Verified, matching or
  beating vendor harnesses on SWE-bench Pro slices.
- AI Agents That Matter: simple baselines Pareto-dominate Reflexion/LDB/LATS at
  50x lower cost.
- HAL: more reasoning effort *lowered* accuracy in 21 of 36 settings.
- Cognition: multi-agent orchestration is fragile; context engineering does the work.

Reordered axes, using the six harness responsibilities from the harness-design
survey (observation, context, control, action, state, verification):

| Rank | Axis | Prior on effect size | Cost to search |
|---|---|---|---|
| 1 | **Prompt / instruction content** (per role) | large (MASS, GEPA) | low |
| 2 | **Context strategy** (full / summary / retrieval / compaction / structured state) | large (ACE, Cognition) | low |
| 3 | **Action interface** (bash-only vs typed tools vs REPL; stateful vs stateless) | large (mini-SWE-agent, Prime Agent's one-tool bet) | medium |
| 4 | **Verification and repair policy** (tests, held-out tests, review, retry count) | large (MAST: verification is one of three failure categories) | medium |
| 5 | **Effort / budget** (reasoning level, turn cap, token cap) | large *and often negative* (HAL) | low |
| 6 | **Memory** (none / task-local / project / retrieved trajectory) | unknown, confounded (MemDelta) | medium |
| 7 | **Model per role** | large but well-studied | low |
| 8 | **Topology / agent count** | smallest of the set | highest |

Axis 8 stays in the DSL but goes last in the search order. This inversion is
itself a finding worth reporting.

## 4. New: compute budget is a first-class object

v0 never budgets its own compute. Reality:

- ~$2.50/instance on GPT-5 -> ~$1,250 per SWE-bench Verified pass.
- HAL: 21,730 rollouts ~ $40,000.
- v0 MVP (120 rollouts) ~ $150-400. Fine.
- v0 milestone 4 (20 generations x 10 population x 100 tasks x 3 trials =
  60,000 rollouts) ~ $150,000. Not a solo project.

Add `Budget` alongside Task / Architecture / Trial / Trajectory / Outcome /
Experiment, with hard caps enforced by the scheduler, and design every search to
respect it:

- **Screening on proxies** - small models and cheap tasks first, confirm the
  survivors on the expensive benchmark.
- **Successive halving / Hyperband** - kill bad candidates after 5 tasks, not 100.
- **Anytime-valid early stopping** (below).
- **Cost-aware objective** - MaAS-style, optimise the frontier not the point.

## 5. Statistics: fix two things v0 gets wrong or omits

### 5.1 Cluster on the task (v0 gets this wrong by omission)

20 tasks x 3 trials is **not** n=60. The cluster is the task. Use clustered
standard errors; bootstrap by resampling *tasks*, not trials. Miller (2411.00640)
shows naive error bars can be 3x too small. This is the single easiest way for
this project to publish a confidently wrong result.

### 5.2 Control the false discovery rate (v0 omits entirely)

Architecture search runs many tests. Without Benjamini-Hochberg across the search
campaign, the "discoveries" are noise by construction. Every experiment record
carries its family, and the promotion gate uses the corrected q-value.

### 5.3 Power before, not after

`af experiment plan` runs a power calculation and refuses or loudly warns when
the design cannot detect the effect it is looking for. With ~20 paired tasks only
very large effects are detectable, which is worth knowing before spending money.

### 5.4 Anytime-valid sequential stopping

Peeking at fixed-n p-values invalidates them. Use confidence sequences /
e-processes (CELEUS, CITE) so the run can legally stop as soon as the interval
excludes zero. Reported sample reductions of ~46% translate directly into money
saved on a benchmark that costs $1,250 a pass.

### 5.5 Tests

Paired binary outcomes: McNemar. Paired continuous or clustered: paired bootstrap
over tasks. Always report effect size and interval, never a bare delta. Keep
v0's rule that "inconclusive" is a valid, first-class conclusion.

## 6. New core subsystem: anti-cheat

This is the largest addition to v0 and it is not optional.

Evidence: over 15% of tasks across five terminal-agent benchmarks have
reward-hackable verifiers; a ten-line `conftest.py` "resolves" all 500 SWE-bench
Verified instances; agents have been caught finding the fix commit via `git log`;
METR found o3 reward-hacks in 30.4% of runs by default and 70-95% even when told
not to. AgentFoundry *searches for whatever maximises the grader*, so it will find
these faster than a human.

Required per trial:

- **Held-out tests** the agent never sees, run after the visible ones.
- **Environment scrubbing** - no future git history, no network to the upstream
  repo, no access to the reference solution.
- **Test-file edit detection** - any diff touching test files or conftest is
  flagged and the trial is quarantined, not scored.
- **Trajectory exploit audit** - an LLM judge over the trajectory looking for
  known patterns (searching for the fix commit, monkey-patching the runner,
  special-casing the assertion).
- **Divergence alarm** - a candidate that improves dramatically on exactly one
  grader while flat on the others is treated as a *suspected exploit*, not a
  discovery, and is routed to manual review.

Task authoring gains a matching requirement. v0 section 23 requires a reference
solution; v1 also requires a **known-cheat test**: a deliberately hacky patch
that the grader must reject. A task whose cheat test passes is a broken task.

Related work to mine: BenchJack, hacker-fixer hardening loops, capped evaluation
with randomized tests, SpecBench, EvilGenie, and the Red Queen Godel Machine
(co-evolving agents and evaluators).

## 7. Failure taxonomy: adopt MAST, do not invent

Replace v0 section 14's 11 invented labels with **MAST** (Cemri/Pan et al.,
NeurIPS 2025): 14 failure modes in 3 categories - system design, inter-agent
misalignment, task verification - built from 150 traces at inter-annotator
kappa = 0.88, with MAST-Data (1600+ annotated traces across 7 frameworks) as
labelled training data.

Keep two v0 labels MAST underweights, as an explicit coding extension:
`ENVIRONMENT_FAILURE` and `MODEL_FAILURE`. Add `REWARD_HACK` from section 6.

Benefit: comparability with published work, and a labelled dataset to calibrate
against on day one instead of hand-labelling your own.

## 8. Autopsy must be evaluated, not trusted

v0 emits `"confidence": 0.87`. That number is decoration unless calibrated, and
the literature says the task is *hard*: TRAIL joint accuracy as low as 18.3%,
step-level attribution around 40%, and accuracy **decreases with trajectory
length** - exactly the regime coding agents live in.

Requirements added:

- **Meta-evaluation**: run Autopsy against Who&When and TRAIL and publish its own
  accuracy in the UI, next to every diagnosis.
- **Calibration**: measure ECE / reliability of the confidence score on labelled
  traces. An uncalibrated score is not displayed.
- **Gating**: Autopsy-generated hypotheses enter the experiment queue with a
  prior weighted by measured attribution accuracy in that failure category.
- v0's rule that Autopsy cannot invent evidence stays, and gets teeth: every
  diagnosis stores the event sequence numbers it cites, and the UI renders them.

## 9. Anti-overfitting: v0's split is necessary but not sufficient

Because the system optimises against its own benchmark, and because the field has
already shown what that does (SWE-bench Pro drops from >70% to ~23%; OpenAI
retired Verified over contamination and scaffolding effects; Agentic Harness
Engineering admits its evolved harnesses overfit and are model-specific), v0
section 41 needs four additions:

1. **FDR control across the campaign** (section 5.2).
2. **Mandatory transfer gate** - a discovered architecture must hold on a
   benchmark *family* it was never searched on. Search on Python repo tasks,
   confirm on Terminal-Bench or a different language. No transfer, no promotion.
3. **Freshness** - SWE-rebench-style continuously collected tasks post-dating
   model cutoffs, to keep contamination bounded over time.
4. **Enforced test-set quota** - a hard, logged budget of held-out evaluations
   per quarter, enforced by the scheduler. Discipline is not a mechanism.

## 10. New: preregistration

Before any experiment runs, the system writes an immutable record: hypothesis,
arms, primary metric, secondary metrics, analysis plan, stopping rule, expected
effect size, and cost ceiling. Analysis is then executed against that plan.

This kills the garden of forking paths, and it is a genuinely novel thing for an
*automated* system to do - the ADAS-family papers all analyse post hoc. It also
converts v0 section 21 ("avoid self-improvement bullshit") from a stated value
into an enforced mechanism.

Promotion requires all five, no exceptions: preregistered hypothesis met,
held-out confirmation, cheat audit clean, cost-matched, transfer gate passed.

## 11. Cost-matched arms

v0's headline demo is "+8.1pp at +14% cost." That is a confounded result:
researcher->coder both changes the architecture *and* spends more. Per AI Agents
That Matter, every comparison needs a **cost-matched control** - the baseline
given the same budget, e.g. best-of-n sampling or a higher effort level at equal
spend. If the baseline closes the gap when handed the same money, the
architecture did nothing and the finding is "more compute helps," which is not a
result.

Every experiment therefore has at least three arms: baseline, candidate, and
cost-matched baseline.

## 12. Search algorithm - answering the question v0 left open

v0 deliberately deferred this. The evidence supports a specific staged answer,
and it is *not* "build an evolutionary searcher."

1. **Factorial screening.** A designed experiment over 4-6 factors from section
   3's ranked axes, to estimate main effects *and interactions* cheaply. Classic
   DoE. Nobody in this literature does it, and it tells you which axes are worth
   money before you spend it. This is the most differentiating step in the plan.
2. **Successive halving** over a small hand-written candidate set.
3. **GEPA** for anything reflective. It beats GRPO by 6% on average (up to 20%)
   with **up to 35x fewer rollouts** (678 vs 24,000 on IFBench) and beats
   MIPROv2 by >10%. In a domain where a rollout costs dollars, rollout
   efficiency is the only metric that matters, and GEPA already ships in DSPy.
4. **Contextual bandits for routing only.** Bandits are the right tool for
   "which architecture for *this* task," not for discovery. Mature prior work:
   MetaLLM, MixLLM, PILOT, CABS-D, ParetoBandit.

Borrow one mechanism from the Darwin Godel Machine: an **open-ended archive** that
retains interesting-but-not-best architectures, which is its stated
anti-stagnation device and pairs naturally with v0's genealogy.

## 13. Promote conditional architecture out of milestone six

v0 defers per-task architecture routing (sections 26/27) to milestone six. It is
the best-supported idea in the document and has the clearest practical payoff:
near-best quality without paying maximum cost on every task. The bandit
literature is mature and the objective is naturally Pareto. Move it earlier -
right after the first search results exist, since it consumes the same trial data
that search already produces.

## 14. New: model drift is an experimental factor

Providers update models silently. v0's golden regression suite (section 43) will
fire spuriously and v0's reproducibility record (section 24) will be quietly
false. Pin model snapshot identifiers, record them per trial, treat drift as a
factor, and rerun a small canary set on a schedule to detect it.

Related: exact replay is not achievable. Provider nondeterminism, batching and
temperature mean trajectories are not reproducible even at fixed seed. Record
what is recordable, and state the limit plainly rather than implying determinism.

## 15. Revised MVP

v0's MVP compares single-agent vs researcher->coder on 20 tasks x 3 trials. Keep
the shape, fix the science, and pick a first result that is cheaper and more
credible than another +8pp claim.

**MVP scope:**

- 20-30 tasks from a fresh, decontaminated source (SWE-rebench-style), each with
  a reference solution *and* a known-cheat test.
- Execution via hal-harness or Inspect, sandboxed one container per trial.
- Trajectories as OTel GenAI events.
- Three arms: baseline, candidate, **cost-matched baseline**.
- 3-5 trials per task per arm.
- Preregistered hypothesis and analysis plan written before the run.
- Clustered paired bootstrap over tasks, effect size with interval, FDR-corrected
  if more than one candidate.
- Anti-cheat: held-out tests, test-edit detection, scrubbed git history.
- Verdict may be "inconclusive," and that counts as success for the MVP.

**The first result to aim for is a refutation, not a discovery.** Take two or
three widely believed architecture improvements - "add a reviewer stage," "add a
planner," "raise reasoning effort" - and test whether they survive a cost-matched,
cluster-corrected, cheat-audited comparison. Given HAL found higher reasoning
effort *hurt* in 21 of 36 settings, and given AI Agents That Matter found simple
baselines Pareto-dominating the published complex ones, at least one of them
probably will not survive.

"We tested three accepted architecture improvements and two do not replicate
under cost-matched, cluster-corrected conditions" is a stronger, cheaper and more
interesting result than another +8pp claim - and it is exactly the result the
field is currently missing.

## 16. Revised portfolio claim

> Built an experiment platform that makes agent-architecture claims falsifiable.
> It runs heterogeneous coding agents in isolated environments over
> decontaminated tasks, records trajectories in OpenTelemetry GenAI form,
> diagnoses failures against the MAST taxonomy with measured and calibrated
> attribution accuracy, audits every trial for verifier exploits, and evaluates
> candidate architectures under preregistered, cost-matched, cluster-corrected,
> FDR-controlled, transfer-gated comparisons. Applied to three widely accepted
> architectural improvements, it found that N of them do not replicate.

## 17. Open questions after this revision

- Which backend to standardise on first: hal-harness (built for exactly this
  three-axis analysis) or Inspect (better sandboxing, wider adoption). Needs a
  day of hands-on with both.
- Whether to adopt the `verifiers` env spec now, which buys Environments Hub
  tasks and RL reusability but couples the project to one ecosystem.
- How to get a decontaminated task supply cheaply enough to keep running - this
  is the real bottleneck, and probably the hardest unsolved piece of the plan.
- Whether the factorial screening (section 12.1) can be run cheaply enough on
  proxy tasks for the effects to transfer to real ones. If it cannot, the whole
  cost model has to change.
