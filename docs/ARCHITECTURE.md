# AgentFoundry - system architecture

The buildable design. Concrete interfaces, data model, module boundaries, and
build order. Research justification lives in `research/`; this document assumes
those conclusions and does not re-argue them.

---

## 1. Design principles

Six principles. Each one has a hard consequence for the code, and every later
section is downstream of these.

### P1. Evidence is immutable; interpretation is a pure function over it

A trial produces an **evidence bundle** that is written once and never modified.
Grading, scoring, autopsy, clustering, statistics and verdicts are all pure
functions from bundles to derived artifacts.

*Consequence:* re-analysis costs nothing. Change the grader, change the failure
taxonomy, change the statistical test - re-run over stored bundles without
re-invoking a single agent. This is the single most important property of the
system, because agent execution is the only expensive thing in it.

### P2. Configuration is data, and identity is its hash

An architecture, a task, an environment and an experiment are all declarative
documents. Their identity is the SHA-256 of their canonical serialization.

*Consequence:* "same configuration" is decidable. Caching, deduplication,
lineage, and reproducibility claims all reduce to hash equality. Preregistration
needs no separate mechanism - see P6.

### P3. The agent is a black box behind a thin adapter

AgentFoundry never re-implements a coding agent. It installs one into a sandbox,
invokes it, and collects what it emits. Adapters are small and dumb.

*Consequence:* supporting a new harness is a day of work, not a rewrite. It also
forces honesty: whatever the platform cannot observe from outside, it does not
claim to know.

### P4. Two planes, never mixed

The **control plane** (small, transactional, mutable: queues, leases, indexes,
verdicts) is separate from the **evidence plane** (large, append-only,
content-addressed: events, artifacts, diffs).

*Consequence:* the control plane fits in SQLite on a laptop and swaps to Postgres
without touching evidence. The evidence plane is a directory of files that can be
tarred, shared, or published as a dataset.

### P5. Measurement validity is a subsystem, not a checklist

Grader correctness, exploit detection, and process quality are computed per trial
and stored *alongside* the score, not applied ad hoc.

*Consequence:* every score carries its own credibility metadata. A comparison can
be marked uninterpretable by the system rather than by a human remembering to
check.

### P6. Analysis plans are written before execution, by construction

An `ExperimentSpec` contains its own analysis plan and stopping rule, and is
frozen and hashed before any trial is queued. Results reference that hash.

*Consequence:* preregistration is not a policy anyone has to follow. It is the
only way to start a run.

---

## 2. The two planes

```
┌──────────────────────────── CONTROL PLANE ────────────────────────────┐
│  registries        queue + leases       indexes       verdicts        │
│  (specs by hash)   (work to do)         (query)       (conclusions)   │
│                        SQLite → Postgres                              │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ references by hash
┌───────────────────────────────▼───────── EVIDENCE PLANE ──────────────┐
│  bundles/<trial_key>/                                                 │
│    manifest.json  events.jsonl  patch.diff  artifacts/  usage.json    │
│    integrity.json  raw/                                               │
│              local filesystem → object storage                        │
└───────────────────────────────────────────────────────────────────────┘
```

The control plane holds nothing that cannot be rebuilt by re-scanning the
evidence plane. That is a deliberate invariant: `af reindex` must be able to
reconstruct the entire database from bundles alone. It makes the database
disposable and makes corruption a non-event.

---

## 3. Layers

Seven layers. Each depends only on the ones above it in this list, never
sideways, never upward.

| Layer | Owns | Never does |
|---|---|---|
| **L0 Spec** | the DSL, registries, hashing, lineage, operators | anything I/O bound |
| **L1 Environment** | images, sandboxes, snapshots, network policy | know what an agent is |
| **L2 Execution** | backend adapters, trial supervision, event capture | grade, score, or interpret |
| **L3 Evidence** | bundle format, writing, reading, integrity | mutate anything |
| **L4 Grading** | graders, integrity/anti-cheat, scoring | run agents |
| **L5 Analysis** | indexing, autopsy, clustering, attribution | decide verdicts |
| **L6 Experiment** | designs, allocation, statistics, verdicts, budget | run agents directly |
| **L7 Search** | proposers, racing, archive | anything not expressible as experiments |

L7 is optional and must remain removable. If deleting the search package breaks
anything else, the boundary is wrong.

---

## 4. Core data model

Seven objects. Identity rules matter more than field lists.

| Object | Identity | Mutable? |
|---|---|---|
| `TaskSpec` | hash of spec + fixture digest | no |
| `EnvironmentSpec` | image digest | no |
| `ArchitectureSpec` | hash of canonical manifest | no |
| `TrialSpec` | hash of (task, arch, env, models, seed, budget, foundry version) | no |
| `EvidenceBundle` | `trial_key` = hash of TrialSpec + run nonce | write-once |
| `ExperimentSpec` | hash of design + arms + analysis plan | no, frozen |
| `Verdict` | hash of (ExperimentSpec, bundle set, analysis version) | no |

Everything is content-addressed and append-only. There are no `UPDATE`
statements on any of these. Revising an architecture creates a new hash and a
lineage edge; it does not edit the old one.

**Run nonce.** Because inference is nondeterministic, a `TrialSpec` maps to many
runs. `trial_key = hash(trial_spec_hash, nonce)` where nonce is the replicate
index. Two trials sharing a `trial_spec_hash` are replicates by construction, and
the analysis layer gets its clustering structure for free - it never has to guess
which observations belong together.

---

## 5. The Architecture DSL

The abstraction the whole project rests on. Design goals: fully determines a run,
diffable, hashable, mutable by machine, and mappable onto real harnesses.

**Not a free-form graph.** An architecture is a set of **slots**, each bound to a
**component** with parameters. Slots come from the harness responsibilities:
observation, context, control, action, state, verification. Topology is one slot
among several, not the organising principle.

```yaml
apiVersion: agentfoundry/v1
kind: Architecture
name: minimal-bash
description: single agent, bash only, no memory, tests as verification

runtime:
  backend: mini-swe-agent          # which adapter drives this
  version: "1.4.2"

models:
  default:
    provider: anthropic
    id: claude-opus-5-20260101     # snapshot pin, never a family name
    effort: medium

action:                            # the tool / action interface
  component: bash-only
  params:
    stateful: false
    timeout_s: 120

context:
  component: full-history
  params:
    on_overflow: fail              # fail | truncate-head | compact

memory:
  component: none

control:                           # topology lives here
  component: single
  params:
    max_turns: 60

verification:
  component: project-tests
  params:
    command: "python -m pytest"
    run_before_finish: true

budget:
  max_tokens: 400000
  max_wall_s: 1800
  max_cost_usd: 4.00
```

A three-role variant differs only in the `control` and `models` slots:

```yaml
control:
  component: staged
  params:
    stages: [research, implement, review]
    on_review_findings: return_to: implement
    max_returns: 1

models:
  default:   { provider: anthropic, id: claude-opus-5-20260101, effort: medium }
  review:    { provider: openai,    id: gpt-5.4-2026-02-11,     effort: low }
```

**Component registry.** Each slot has a registry of implementations. A component
declares its parameter schema and which backends can honour it:

```python
@component(slot="context", name="compact-on-threshold")
class CompactOnThreshold(ContextComponent):
    schema = Schema({"threshold_tokens": int, "keep_last": int})
    supports = {"mini-swe-agent", "prime-agent", "openhands"}

    def apply(self, cfg: BackendConfig, params: dict) -> BackendConfig: ...
```

**Resolution.** `resolve(ArchitectureSpec, Backend) -> BackendConfig` walks the
slots, checks every component supports that backend, and folds them into
whatever concrete configuration the adapter needs. Unsupported combinations fail
loudly at resolve time, before anything is spent - never silently degrade.

**Why slots and not a graph.** A free graph makes the space unbounded, makes
diffs meaningless, makes mutation operators ill-defined, and makes most sampled
points invalid. Slots give a bounded, typed, always-valid space where a mutation
is a single field change, and where the diff between two architectures is
readable by a human and by the analysis layer. When you eventually need real
graphs, they arrive as a richer `control` component, not as a rewrite.

---

## 6. Operators and lineage

An architecture is stored two ways at once: as a full manifest (for execution)
and as `base_hash + [operators]` (for lineage and analysis).

```python
SetModel(slot="review", model="gpt-5.4-2026-02-11")
SetComponent(slot="context", component="compact-on-threshold", params={...})
SetParam(path="control.params.max_turns", value=80)
AddStage(stage="research", before="implement")
RemoveStage(stage="review")
RemoveTool(name="web_search")
ScaleBudget(factor=1.5)
```

Operators are total functions `ArchitectureSpec -> ArchitectureSpec`. They must
be:

- **closed** - output is always a valid architecture,
- **invertible where possible** - so A/B and B/A are the same experiment,
- **loggable** - the operator list *is* the changelog.

*Removal operators are first class and listed early on purpose.* The evidence
says subtraction wins as often as addition, and a system whose operator set only
grows architectures will only ever discover growth.

This gives three things for free: a machine-readable diff for every comparison,
a genealogy DAG for the registry, and a clean interface for L7 search - a
proposer emits operator lists, nothing else.

---

## 7. Backend adapters (L2)

The whole external surface of the agent world, in five methods.

```python
class Backend(Protocol):
    name: str
    version: str

    def capabilities(self) -> Capabilities:
        """Which slots/components this backend can honour."""

    def provision(self, cfg: BackendConfig, sandbox: Sandbox) -> None:
        """Install the agent and write its config inside the sandbox."""

    def invoke(self, task: TaskSpec, sandbox: Sandbox, sink: EventSink) -> RunOutcome:
        """Start the agent, stream events to sink, return when it stops."""

    def collect(self, sandbox: Sandbox) -> RawArtifacts:
        """Native logs, transcripts, whatever the harness wrote."""

    def normalize(self, raw: RawArtifacts) -> Iterator[Event]:
        """Backend-native logs -> canonical event stream."""
```

`invoke` streams what it can live; `normalize` is the authoritative pass and runs
after the fact over collected logs. Live streaming is for the UI; normalized
events are for analysis. Never let the analysis layer depend on the live stream.

**Event vocabulary.** OpenTelemetry GenAI attribute names, because the major
harnesses already emit them and adopting the names costs nothing:

```
run.start / run.end
agent.start / agent.end
model.request / model.response         gen_ai.usage.*, gen_ai.request.model
tool.call / tool.result                gen_ai.tool.name
file.write / file.delete
shell.exec
context.compact
memory.read / memory.write
message.send                           (agent-to-agent)
error
```

Every event carries `seq`, `ts`, `trial_key`, `agent_id`, `parent_agent_id`.
Monotonic `seq` per trial is what makes the stream replayable and what every
attribution method indexes on.

**Adapter conformance suite.** A shared test suite every adapter must pass:
produce a well-formed stream on a trivial task, honour budget caps, terminate on
cancel, and report usage. An adapter that fails conformance cannot be registered.
This is what stops adapter rot as harnesses change under you.

---

## 8. Environment layer (L1)

```python
class Sandbox(Protocol):
    def start(self, image: ImageRef, limits: Limits, net: NetPolicy) -> Handle
    def exec(self, handle, argv, timeout) -> ExecResult
    def put(self, handle, src, dst) -> None
    def get(self, handle, src) -> bytes
    def snapshot(self, handle) -> SnapshotRef
    def stop(self, handle) -> None
```

Three implementations, one interface: `LocalDocker` (default, laptop),
`RemotePool` (a hosted sandbox provider), `K8sPod` (parity with existing eval
harnesses). Nothing above L1 knows which is in use.

**Hard environment rules, enforced in code and asserted in `integrity.json`:**

- Fixture is a **pinned image digest**, never a tag.
- `.git` history is **truncated to the parent commit** of the task. The gold
  patch is not on disk. This is enforced at image build and re-verified at start.
- Network is **deny by default**; an allowlist is part of `EnvironmentSpec` and
  therefore part of the hash. A task that needs the network says so, and every
  request is logged.
- Held-out tests live **outside** the sandbox and are injected only after the
  agent has stopped and the workspace has been captured.
- Every trial starts from the same snapshot. No reuse of a dirty container, ever.

---

## 9. Evidence bundle (L3)

```
bundles/<trial_key>/
  manifest.json      resolved TrialSpec + backend/adapter versions + image digest
  events.jsonl       canonical event stream, seq-ordered
  patch.diff         workspace diff vs the pinned start state
  usage.json         tokens, cost, wall time, per model and per agent
  integrity.json     network calls, files touched, git ops, env assertions
  artifacts/         test output, build logs, screenshots
  raw/               untouched backend-native logs
```

`manifest.json` is written first, `events.jsonl` appended during the run, the
rest on completion, then the directory is marked sealed. A bundle without a seal
marker is treated as a crashed trial and is never scored.

Bundles are the product. If the rest of AgentFoundry burned down, a directory of
sealed bundles would still be a valuable dataset - which is a good check that the
boundary is in the right place.

---

## 10. Grading and integrity (L4)

Runs entirely post-hoc, over sealed bundles, in a fresh sandbox from the same
pinned image. It never observes a live agent.

```python
class Grader(Protocol):
    name: str
    kind: Literal["deterministic", "model", "human"]
    def grade(self, bundle: Bundle, task: TaskSpec, sandbox: Sandbox) -> GradeResult
```

Two families, both required, run in order:

**Outcome graders** - visible tests, held-out tests, build, lint, static
analysis, a model rubric where a rubric is genuinely needed.

**Integrity graders** - the subsystem P5 demands. Each emits a flag plus
evidence, never a silent adjustment:

| Grader | Looks for |
|---|---|
| `test-tampering` | diffs touching test files, conftest, CI config, the runner |
| `oracle-access` | git history reads, network fetches of the upstream repo, reference-solution paths |
| `null-patch` | does an empty patch pass? if yes the *task* is broken, not the agent |
| `process-quality` | passes reached through blind retries, regression cycles, or with no verification step |
| `eval-awareness` | trajectory signals that the agent believes it is being tested |
| `grader-validity` | independent judge over patch + trajectory on a sample, producing an FP/FN estimate for the outcome grader itself |

Output is a `Score` record: outcome scores, integrity flags, and a
`credibility` field derived from them. The experiment layer refuses to compute a
verdict from scores whose credibility is degraded, and says so explicitly rather
than quietly dropping trials.

**Task authoring contract.** A task is not admissible without: pinned fixture,
visible tests, held-out tests, a reference solution that passes both, and a
**known-cheat patch that must fail**. `af task validate` runs all four. Tasks
that fail validation never enter a suite.

---

## 11. Analysis (L5)

Pure functions over sealed bundles. Nothing in this layer may start an agent.

```
bundles ─► indexer ─► trajectory store (features, phases, spans)
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   failure labeler    landscape        attribution
   (MAST taxonomy)    (shared graph,   (counterfactual
                       trap regions)     replay)
                          │
                          ▼
                    finding registry
```

- **Indexer** - normalizes events into queryable features: phase segmentation
  (explore / implement / verify / orchestrate), tool histograms, context growth
  curve, retry and revisit counts, time-to-first-edit.
- **Failure labeler** - assigns taxonomy labels to failed trials. Ships as a
  rule pass plus a model pass; both write evidence pointers (`seq` ranges), never
  bare labels.
- **Landscape** - pools rollouts of the same task across architectures into a
  shared state graph, marking productive regions and traps. This is the
  clustering primitive; it is far more useful than embedding clusters because its
  nodes are actual observable states.
- **Attribution** - counterfactual replay. Reconstruct state at step *k*,
  intervene (resample / force action / replace observation / edit context / swap
  model), re-execute forward *n* times, compare outcome distributions. This is the
  one part of L5 that *does* spend money, so it is explicitly budgeted and only
  runs on trials selected by the cheaper passes.
- **Finding registry** - a labeled, evidence-linked claim about a failure mode.
  Findings are what feed L6 and L7. A finding with no `seq` pointers is rejected
  at write time.

**Calibration is a first-class output.** Every labeler and attribution method
carries a measured accuracy on held-out labeled data, stored with it and rendered
next to every diagnosis it produces. An uncalibrated component may run but its
output is marked provisional and cannot feed L7.

---

## 12. Experiment layer (L6)

The heart of the product.

### ExperimentSpec

```yaml
apiVersion: agentfoundry/v1
kind: Experiment
name: does-a-review-stage-help

design:
  type: paired            # paired | factorial | screening
  arms:
    - id: baseline
      architecture: minimal-bash@a1b2c3
    - id: candidate
      architecture: minimal-bash+review@d4e5f6
    - id: cost_matched     # baseline given the candidate's budget
      architecture: minimal-bash@a1b2c3
      overrides: { budget: { max_tokens: 600000 } }
  suite: coding-fresh-v3@9f8e7d
  replicates: 3

analysis:
  primary: resolved                # the outcome that decides the verdict
  secondary: [cost_usd, wall_s, integrity_flags]
  cluster_on: task
  test: mixed_effects_logistic
  interval: bootstrap_tasks
  equivalence_margin: 0.03         # for "no difference" conclusions
  correction: benjamini_hochberg

stopping:
  rule: anytime_valid
  max_trials: 900

budget:
  max_cost_usd: 900
  max_wall_h: 48
```

Freezing this document and hashing it *is* the preregistration. The verdict
record references the hash. There is no path to "adjust the analysis after seeing
the data" because the analysis plan is an input to execution, not a later step.

### Planner

`plan(ExperimentSpec) -> TrialSpec[]` expands arms x suite x replicates into the
concrete trial list, resolves architectures against backends (failing early on
unsupported components), estimates cost, and checks feasibility against the
budget. It reports what the design can and cannot detect, and refuses to queue an
experiment whose design cannot answer its own question.

### Scheduler

Deliberately boring: a table of queued `TrialSpec`s, workers that lease with a
timeout, heartbeats, bounded retries for infrastructure failures only (never for
agent failures - a failed agent run is data), and a budget guard that halts the
queue when spend crosses the cap.

Workers are disposable. A worker crash loses at most one trial, and that trial's
unsealed bundle is discarded rather than repaired.

### Analyzer and verdict

```python
def analyze(spec: ExperimentSpec, scores: list[Score]) -> Verdict
```

Consumes only sealed, credible scores. Emits a `Verdict`: effect, interval,
per-arm cost and latency, integrity summary, and one of

`BETTER` · `WORSE` · `EQUIVALENT` · `INCONCLUSIVE` · `UNINTERPRETABLE`

`EQUIVALENT` and `INCONCLUSIVE` are different conclusions and both are success
states. `UNINTERPRETABLE` fires when integrity flags or grader-validity estimates
undermine the comparison - the system says so instead of reporting a number.

Verdicts are immutable and reference the spec hash, the bundle set hash, and the
analysis code version. Re-running analysis with newer code produces a *new*
verdict, side by side with the old one. Conclusions have version history.

---

## 13. Search (L7, optional and last)

```python
class Proposer(Protocol):
    def propose(self, ctx: SearchContext) -> list[Proposal]
    # Proposal = (base_arch_hash, operators, hypothesis, expected_effect)
```

Proposers: `Manual`, `Grid`, `Mutation`, `FindingDriven` (turns L5 findings into
operators), `Reflective` (an LLM proposer, constrained to emit operator lists,
never free-form code).

The controller is a **racing loop**, not a generational GA: run every candidate
on a small shared task subset, statistically eliminate the clearly worse ones,
promote survivors to larger subsets, repeat. This is the mature answer to
"configure a system when each evaluation is expensive and noisy," and it maps
directly onto the existing scheduler.

Survivors go into an **archive keyed by behaviour**, not just by score - cheap
vs expensive, exploratory vs direct, fast vs thorough - so the system illuminates
which architectures win in which regions of task space instead of collapsing to a
single winner. Promotion out of the archive requires a full L6 experiment with a
transfer gate. Search proposes; only experiments conclude.

---

## 14. Control-plane schema

```sql
task(id, hash, suite, fixture_digest, validated_at, ...)
suite(id, hash, name, task_ids[])
architecture(hash, name, manifest_json, base_hash, operators_json, created_at)
environment(hash, image_digest, net_policy_json)
trial_spec(hash, task_id, arch_hash, env_hash, models_json, budget_json, foundry_version)
trial(trial_key, trial_spec_hash, nonce, state, worker, lease_until, bundle_path, sealed_at)
score(trial_key, grader, kind, value, flags_json, credibility, analysis_version)
experiment(hash, spec_json, state, frozen_at, budget_json)
experiment_trial(experiment_hash, trial_key, arm_id)
finding(id, kind, label, evidence_json, confidence, calibration_version)
verdict(hash, experiment_hash, bundle_set_hash, analysis_version, result, payload_json)
```

Every table is append-only except `trial` (state machine) and `experiment`
(state machine). `af reindex` rebuilds everything except those two from bundles.

Trial states: `queued → leased → running → collected → sealed → scored`, with
terminal `failed | cancelled | expired`. Scoring is a separate state on purpose -
it is the boundary between L2/L3 and L4, and it is where re-scoring re-enters.

---

## 15. Interfaces

**CLI is the primary interface.** Everything else is a view over it.

```bash
af task validate ./tasks/django-31234      # fixture, tests, reference, cheat-patch
af arch show minimal-bash@a1b2c3
af arch diff a1b2c3 d4e5f6                 # prints the operator list
af run --task django-31234 --arch a1b2c3   # one trial
af exp plan  ./experiments/review-stage.yaml   # cost, feasibility, what it can detect
af exp start ./experiments/review-stage.yaml   # freezes + hashes + queues
af exp watch <hash>
af exp verdict <hash>
af score --regrade <experiment>            # re-run L4 over stored bundles
af analyze autopsy <trial_key>
af analyze cluster --suite coding-fresh-v3
af replay <trial_key> --intervene step=42 --n 8
af search race ./search/context-axis.yaml
af reindex
```

**API** is a thin HTTP layer over the same functions - it exists so the UI is not
special-cased, not because a service is needed.

**UI** is read-mostly: experiment list and live progress, verdict page with
per-task breakdown, trial replay with the event stream and the diff, and a
landscape view showing where trajectories diverge. Its one write action is
"queue this experiment," which posts an `ExperimentSpec`.

---

## 16. Repository layout

```
agentfoundry/
├── af/
│   ├── spec/          L0  dsl, components, registry, hashing, operators, lineage
│   ├── env/           L1  sandbox protocol, docker, remote, k8s, images
│   ├── exec/          L2  backend protocol, adapters/, supervisor, events
│   │   └── adapters/      mini_swe.py  prime_agent.py  openhands.py  claude_code.py
│   ├── evidence/      L3  bundle read/write, seal, integrity assertions
│   ├── grade/         L4  graders/, integrity/, scoring, task validation
│   ├── analyze/       L5  indexer, labeler, landscape, attribution, findings
│   ├── experiment/    L6  spec, planner, scheduler, analyzer, verdict, stats
│   ├── search/        L7  proposers, racing, archive
│   ├── store/             control-plane db, migrations, reindex
│   ├── cli/               af entrypoint
│   └── api/               http layer
├── components/            slot implementations, one file per component
├── architectures/         versioned manifests + discovered/
├── tasks/                 fixtures, task.yaml, tests, reference, cheat-patch
├── experiments/           experiment specs (checked in - they are the record)
├── web/                   ui
└── docs/
```

`experiments/` living in git is deliberate. Specs are frozen documents; the repo
history becomes the audit trail of what was asked, when, and by whom.

---

## 17. Technology choices

| Choice | What | Why |
|---|---|---|
| Language | **Python 3.12+** | the entire ecosystem you must interoperate with - eval harnesses, task datasets, statsmodels, HF datasets - is Python. A faster language buys nothing when the bottleneck is a $2 model call. |
| Control plane | **SQLite → Postgres** | one file, zero ops, correct transactions, and a migration path. Postgres only when concurrent writers exceed one machine. |
| Evidence plane | **filesystem → S3-compatible** | bundles are directories. `rsync` is a valid distribution mechanism. |
| Queue | **table + leases in the control DB** | a real broker is unjustified until multi-machine. Leases with heartbeats handle worker death. |
| Sandbox | **Docker → remote pool** | one interface, three backends, chosen at config time. |
| Event schema | **OTel GenAI names** | adapters get cheaper because harnesses already emit these. |
| Stats | **statsmodels + custom bootstrap** | mixed-effects logistic, TOST, bootstrap over task clusters. |
| Config | **YAML in, canonical JSON for hashing** | humans write YAML; hashes need a canonical form. |
| UI | **server-rendered + small JS** | it is a read-mostly dashboard. |

Deliberately excluded from v1: Kubernetes, a message broker, a vector database, a
workflow engine, microservices, and any orchestration framework. Every one of
them is available later behind an interface that already exists.

---

## 18. Extension points

Six, and they are the only six:

1. **Component** - a new slot implementation (`components/`).
2. **Backend** - support a new harness (`exec/adapters/`), must pass conformance.
3. **Sandbox** - a new isolation mechanism (`env/`).
4. **Grader** - outcome or integrity (`grade/`).
5. **Labeler / Attributor** - a new analysis method (`analyze/`), must ship a
   calibration.
6. **Proposer** - a new search strategy (`search/`).

Each is a Protocol plus a registry entry. Adding one must never require editing
core code - if it does, the boundary is wrong and should be fixed rather than
worked around.

---

## 19. Build order

Vertical slices. Each one ends with something runnable, and nothing later is a
prerequisite for something earlier.

**Slice 1 - one trial end to end.**
`spec` + `env/docker` + `exec` with a single adapter + `evidence`. Deliverable:
`af run` produces a sealed bundle. No grading, no stats, no UI. This is the
skeleton and everything else hangs off it.

**Slice 2 - scoring and task validation.**
`grade` with visible tests, held-out tests, and `test-tampering`. `af task
validate` including the cheat patch. Deliverable: bundles get scores, tasks are
admissible or not.

**Slice 3 - the experiment.**
`experiment` spec, planner, scheduler, analyzer, verdict. Deliverable:
`af exp start` on a two-arm paired design produces a `Verdict`. **This is the
first point at which the product exists.**

**Slice 4 - analysis on free data.**
`analyze` indexer, labeler, landscape - built and calibrated against public
trajectory corpora rather than freshly generated runs. Deliverable: `af analyze`
over an imported corpus, with measured accuracy attached.

**Slice 5 - integrity, in full.**
Remaining integrity graders, credibility propagation into verdicts, the
`UNINTERPRETABLE` path. Deliverable: comparisons that refuse to conclude when
they should not.

**Slice 6 - second and third adapters.**
Prove P3 by adding two more backends. If either takes more than a few days, the
adapter interface leaked and needs fixing before anything else proceeds.

**Slice 7 - attribution.**
Counterfactual replay, budgeted, gated on the cheaper passes.

**Slice 8 - search.**
Racing loop and behavioural archive, only after 1-7 are solid.

The UI can start after slice 3 and grow alongside; it is never on the critical
path.

---

## 20. Failure and degradation

| Failure | Behaviour |
|---|---|
| Worker dies mid-trial | lease expires, bundle unsealed and discarded, trial requeued once |
| Sandbox unavailable | queue pauses, no trial silently runs unsandboxed |
| Backend upgrades under you | conformance suite fails at registration, adapter pinned to last good version |
| Grader changes | old scores retained, new scores written with a new `analysis_version`; verdicts are versioned, never overwritten |
| Control DB lost | `af reindex` rebuilds from bundles |
| Budget exceeded | queue halts, partial results are analyzable, verdict marked `INCONCLUSIVE` with the reason |
| Model deprecated mid-experiment | trials fail fast on the pinned snapshot; the experiment is marked incomplete rather than silently mixing model versions |
| Integrity flags on many trials | verdict is `UNINTERPRETABLE` with the flag breakdown |

The unifying rule: **degrade to "we cannot say," never to a plausible-looking
number.** A platform whose entire purpose is deciding whether a claim holds must
be constitutionally capable of refusing to answer.
