# AgentFoundry v0 - Build Specification

> Original idea as authored, 2026-08-27. Preserved as the baseline artifact.
> Revisions live in SPEC-v1.md; the research motivating them lives in
> docs/research/.

## 1. Project thesis

Build **a platform that experimentally discovers, evaluates, and explains better agent architectures** - not a new coding harness. Existing agent harnesses are the subjects under experiment.

AgentFoundry takes:

```text
Task + Agent architecture + Model + Tools + Memory strategy
+ Verification strategy + Budget + Environment
```

runs it repeatedly, collects the full trajectory and outcome, diagnoses failure, generates candidate architectural changes, runs controlled experiments, and determines whether the change genuinely improved the system.

Central loop:

```text
Benchmark -> Run Agents -> Observe trajectories -> Diagnose failures
 -> Propose experiments -> Run experiments -> Statistical comparison
 -> new baseline
```

The self-improving part must be **empirical**, not an agent rewriting its own prompt because it thinks it improved.

## 2. The fundamental abstraction

Six objects: Task, Architecture, Trial, Trajectory, Outcome, Experiment.

### Task

```yaml
id: swe_123
type: coding
repository: django/django
commit: abc123
instruction: "Fix ..."
environment: python-3.12
graders: [tests, patch, static-analysis]
```

### Architecture

```yaml
name: planner-coder-reviewer
agents:
  planner:  { role: planning,            model: model-A }
  coder:    { role: implementation,      model: model-A }
  reviewer: { role: adversarial-review,  model: model-B }
flow: [planner, coder, reviewer, repair_if_needed]
```

This is the most important abstraction in the project. **Architecture must be data, not hard-coded Python/TypeScript.** That is what allows search over architectures.

## 3. Architecture DSL

```yaml
architecture:
  name: repo-aware-coder
  agents:
    - { id: researcher, role: repository_analysis, model: strong }
    - { id: coder,      role: implementation,      model: strong }
    - { id: reviewer,   role: review,              model: cheap }
  graph:
    researcher -> coder
    coder -> reviewer
    reviewer -> coder:
      when: findings.nonempty
```

Searchable parameters: agents, roles, models, prompts, tool availability, memory, communication, ordering, parallelism, verification, retry, budget allocation, context strategy.

## 4. Example architecture search space

```text
Number of agents: 1-5
Roles:      researcher, planner, coder, tester, reviewer
Topology:   single, sequential, planner->coder, parallel-research->coder,
            coder->reviewer, planner->coder->reviewer
Models:     cheap, medium, strong
Memory:     none, task-local, shared-summary
Verification: none, tests, tests+review
Repair:     none, one retry, review-driven repair
```

Do not initially allow arbitrary graphs, or the search space explodes.

## 5. Three layers

- Layer A - Execution: actually execute an architecture.
- Layer B - Analysis: understand what happened.
- Layer C - Optimization: experiment with architectural alternatives.

## 6. Execution layer

```text
AgentBackend
├── PrimeAgentBackend
├── DeepSeekHarnessBackend
├── ClaudeCodeBackend
├── OpenAICodexBackend
└── LocalAgentBackend
```

Execution contract: `create_run, start_agent, send, tool_event, child_agent, checkpoint, pause, resume, cancel, collect_artifacts`.

Do not replicate these harnesses. DeepSeek Harness already treats capabilities as plugins and records complete run trajectories in append-only logs with resume/fork/replay, so AgentFoundry operates above that layer.

## 7. Environment layer

```text
Trial -> Environment
  ├── repository snapshot
  ├── filesystem
  ├── dependencies
  ├── network policy
  ├── credentials
  └── resource limits
```

Critical rule: every trial starts from the exact same initial environment. Otherwise experiments are invalid.

## 8. Trial execution

A trial is Task + Architecture + Model versions + Environment version + AgentFoundry version, with a unique ID.

```text
CREATED -> PREPARING -> RUNNING -> VERIFYING -> SCORING -> COMPLETED
RUNNING -> FAILED | CANCELLED | TIMEOUT
```

## 9. Trajectory recorder

Record: task, agent creation, model request/response, tool request/response, file change, shell command, subprocess, context injection, memory read/write, agent-to-agent message, checkpoint, retry, approval, verification. Append-only event stream.

```json
{ "sequence": 182, "type": "tool.completed", "trial_id": "...",
  "agent_id": "coder", "tool": "shell", "timestamp": "...", "payload": {} }
```

## 10. Outcome recorder

Trajectory is what the agent did. Outcome is what actually happened.

Coding: patch produced, tests passed/failed, build succeeded, security scan.
Research: claims, sources, citation correctness, coverage.

## 11. Grader system

- Deterministic: pytest, compiler, lint, static analysis, exact match, db state.
- Model grader: quality, reasoning, style, completeness.
- Human grader: optional calibration/reference judgments.

A task might produce: functional 1.0, requirements 0.9, quality 0.82, security 1.0.

## 12. Experiment system

```yaml
experiment:
  baseline: single-agent
  candidates: [planner-coder, planner-coder-reviewer, researcher-planner-coder-reviewer]
  benchmark: coding-v1
  trials_per_task: 3
  metrics: [success, cost, latency, tool_errors]
```

Do not run each architecture once. Agent outputs are stochastic.

## 13. Statistical comparison

Compute absolute improvement, relative improvement, confidence interval, variance, cost delta, latency delta. Use paired comparisons on the same task set. Eventually add bootstrap CIs, McNemar test, paired bootstrap, effect size. The system must be able to say "difference is statistically inconclusive."

## 14. Failure taxonomy

```text
UNDERSTANDING_FAILURE, PLANNING_FAILURE, TOOL_SELECTION_FAILURE,
TOOL_EXECUTION_FAILURE, CONTEXT_FAILURE, MEMORY_FAILURE,
COORDINATION_FAILURE, VERIFICATION_FAILURE, RECOVERY_FAILURE,
ENVIRONMENT_FAILURE, MODEL_FAILURE
```

## 15. Agent Autopsy engine

```text
failed trial -> trajectory extraction -> important-event selection ->
failure hypothesis generation -> evidence collection ->
root-cause classification -> confidence
```

Autopsy cannot invent evidence. Every diagnosis must point to specific trajectory events.

## 16. Failure clustering

One failure is useful; ten thousand are extremely useful. Cluster by failure signature, then drill into subclusters (wrong entry point, insufficient exploration, misunderstood dependency).

## 17. Failure-to-experiment bridge

```text
Failure pattern -> Hypothesis -> Architectural intervention -> Experiment -> Evidence
```

## 18. Experiment proposal engine

Interventions are explicit operators, not free-form LLM invention:

```text
ADD_AGENT(role), REMOVE_AGENT(id), CHANGE_MODEL(agent, model),
CHANGE_TOPOLOGY(...), ADD_REVIEW(...), ADD_RESEARCH(...),
CHANGE_MEMORY(...), CHANGE_BUDGET(...), CHANGE_CONTEXT_STRATEGY(...),
CHANGE_RETRY(...)
```

The LLM proposes an intervention plus hypothesis plus expected effect; AgentFoundry runs it.

## 19. Architecture search

Random search, then mutation search, then evolutionary search, then Bayesian optimization / bandits. Do not start at the end.

## 20. Multi-objective optimization

Maximize success; minimize cost, latency, failures, human intervention. Produce a Pareto frontier rather than a single winner.

## 21. Avoid self-improvement bullshit

Never conclude "B is better because the LLM said so." LLMs are for failure diagnosis, hypothesis generation, architecture proposal, and qualitative interpretation. Final evaluation relies on actual graders.

## 22. Benchmark system

Use SWE-bench Verified (500 human-validated tasks) as one external benchmark. Also build AgentFoundry Coding-100 with categories: bug fix, refactor, dependency issue, test repair, performance, security, multi-file, ambiguous specification, large repository. The point is not to beat SWE-bench but to have a benchmark whose purpose is architecture experimentation.

## 23. Benchmark dataset format

task.yaml, repository snapshot, instruction, environment definition, reference solution, grader definitions, difficulty, capabilities tested. The reference solution validates the grader and the task itself.

## 24. Experimental control

Store benchmark version, task versions, agent version, architecture version, model, prompt version, environment image, tool versions, AgentFoundry commit. Only one thing changes between baseline and candidate.

## 25. Research matrix

```text
                Success   Cost   Latency
single            61%     $0.7     9m
planner+coder     69%     $1.1    13m
+reviewer         74%     $1.8    21m
+researcher       81%     $1.9    25m
```

Then slice by task category. The discovery may be "researcher is beneficial only on large repositories," which is more valuable than blindly adding agents.

## 26. Conditional architecture

Learn a task classifier that routes to an architecture instead of using one architecture for everything. Optimizing architecture *selection*, not just architecture.

## 27. Online learning eventually

Predict expected success/cost/latency from task features plus history, choose architecture per task, but keep exploration or the router converges prematurely. Contextual bandits eventually become relevant.

## 28. Agent memory experiments

Compare none / task summary / repository memory / retrieved trajectory memory / persistent project memory. Measure success, context tokens, retrieval errors, stale-memory failures, cost. Research question: when does persistent memory actually justify its complexity?

## 29. Multi-agent communication experiments

Compare single, planner->coder, planner->coder->reviewer, parallel researchers->coder, shared blackboard, direct messaging, hierarchical delegation. Measure success, communication tokens, latency, contradictions, coordination failures. More agents may be worse - a valuable result.

## 30. Context experiments

full history / summaries / retrieval / periodic compression / structured state. Measure context size, cost, failure rate, information loss.

## 31. Model experiments

Hold architecture constant, swap models, look for architecture-model interaction effects. The best model may depend on the architecture.

## 32. Agent Autopsy UI

Root cause, confidence, timestamped evidence, likely intervention, estimated effect, and a RUN EXPERIMENT button.

## 33. Experiment UI

Baseline vs candidate, live progress, final success delta, cost delta, 95% CI, and a plain-language conclusion.

## 34. Replay

Per-trial replay of the agent tree, and side-by-side trajectory comparison of baseline vs candidate.

## 35. Production runtime

scheduler, worker leases, heartbeats, retry, checkpoint, artifact store, resource quotas, sandbox. An implementation detail supporting experiments, not the product thesis.

## 36. Fault injection

Inject worker crash, model timeout, API 429, tool failure, network failure, context corruption, sandbox termination. Compare architectures under failure.

## 37. Reliability score

quality, reliability, cost, efficiency scores. Composite `utility = quality - lambda(cost) - mu(latency) - nu(human_intervention)`. Always display raw metrics alongside composite scores.

## 38. Architecture registry

```text
architectures/
├── single-agent.yaml
├── planner-coder.yaml
├── researcher-coder.yaml
├── reviewer-loop.yaml
└── discovered/
```

Versioned with parent/mutation lineage, forming an architecture genealogy.

## 39. Agent architecture genome

`A = {topology, roles, models, tools, memory, verification, retry, communication}`. Mutation operates on the genome, making evolutionary search natural.

## 40. Experiment provenance

Every discovery answers: where did this architecture come from, what motivated it, what changed, which experiments support it, which tasks improved, which got worse.

## 41. Anti-overfitting mechanisms

Split train / validation / test. Discovery on train, selection on validation, final reporting on held-out test. Never repeatedly optimize against the final test set.

## 42. Cross-validation for tasks

bootstrap, task resampling, multiple random seeds, multiple trials. Report variance.

## 43. Golden regression suite

Every discovered improvement becomes a regression test, so future runtime or model changes cannot silently destroy previous discoveries.

## 44. Production-vs-research separation

Lab mode: many experiments, parallel trials, controlled environments, reproducibility, metrics. Production mode: real user task, durability, security, human approvals, GitHub, cost controls. Same execution substrate, different policies.

## 45. Security model

Treat agent code and repository code as untrusted. Sandbox each trial. Give only task-specific filesystem, credentials, and network. Never let an agent access the AgentFoundry control-plane database directly.

## 46. Control-plane architecture

```text
API -> Experiment Service -> {Task DB, Scheduler, Registry}
    -> Worker Pool -> Trials -> Sandboxes
```

## 47. Worker architecture

Worker = trial supervisor + agent adapter + sandbox client + event emitter + checkpoint manager + resource monitor + artifact uploader. The worker is disposable.

## 48. Event pipeline

```text
Worker -> Event Bus -> {Event Store, Trace Store, Metrics Engine} -> Experiment DB
```

The UI never queries live worker memory.

## 49. Storage

PostgreSQL for tasks, experiments, architectures, trials, scores, metadata. Object storage for logs, large trajectories, patches, test reports, sandbox snapshots. NATS or equivalent for the event bus. No exotic infrastructure at first.

## 50. Repository structure

```text
agentfoundry/
├── apps/            api, scheduler, worker, web, cli
├── core/            domain, state-machine, architecture, experiment, scoring
├── runtime/         adapters, sandbox, checkpoint, protocol
├── analysis/        trajectory, autopsy, clustering, hypothesis
├── search/          mutation, evolutionary, selection
├── evals/           coding, reliability, security, datasets
├── infrastructure/  docker, k8s
└── docs/
```

## 51. CLI

```bash
af run --task swe_123 --architecture planner-coder
af experiment run exp_42
af autopsy trial_123
af search architecture --benchmark coding-v1
af replay trial_123
af compare architecture:12 architecture:29
```

## 52. First actual MVP

coding task -> two architectures -> same sandbox -> multiple trials -> tests -> trajectories -> comparison. 20 tasks x 3 trials x 2 architectures. Architecture A single agent, architecture B researcher -> coder. Show A 60.0% vs B 73.3% and inspect failures. That is already a real prototype of the thesis.

## 53. Second milestone

Autopsy: failure -> trajectory analysis -> failure cluster -> suggested intervention.

## 54. Third milestone

Automated experiment proposal, closing the loop: autopsy -> hypothesis -> architecture mutation -> run benchmark -> accept/reject.

## 55. Fourth milestone

Architecture search with a genealogy of discovered architectures.

## 56. Fifth milestone

Model x architecture matrix - studying agent-model interactions.

## 57. Sixth milestone

Conditional architecture selection, measured against always-use-strongest. Practical benefit: near-best quality without paying maximum cost on every task.

## 58. Seventh milestone

Continuous operation with gated promotion: candidate -> validation -> held-out evaluation -> approval -> production. No automatic production deployment merely because one experiment improved.

## 59. Final architecture

EXECUTION / AUTOPSY / EXPERIMENTS sitting over agent harnesses, a failure graph, and architecture search, feeding EVALUATION (benchmark + graders) -> statistics -> promotion -> new baseline.

## 60. The portfolio story

> Built an experimental platform that automatically evaluates and discovers agent architectures for autonomous software engineering. The system executes heterogeneous agents in isolated environments, records complete trajectories, diagnoses recurrent failure modes, generates architectural hypotheses, evaluates them across controlled benchmarks, and promotes improvements using statistically validated results.

Concrete demo: baseline single coding agent -> autopsy finds repository-understanding failures -> system generates researcher->coder -> 300 controlled trials -> +8.1pp at +14% cost -> held-out +7.4pp -> architecture promoted.

This does not compete with DeepSeek Harness or Prime Agent. They become experimental subjects and backends.

Open question deliberately left undecided: the exact architecture-search algorithm. First nail down the Agent Architecture DSL and which variables are legitimately searchable.

## Source links cited in the original draft

- https://deepseek.com/harness/en/
- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- https://www.swebench.com/verified.html
