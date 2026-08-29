# AgentFoundry

An experiment platform that makes agent-architecture claims falsifiable.

Coding agents are the subjects under experiment, not the product. AgentFoundry
runs them in isolated environments, records immutable evidence, grades outcomes
*and* the trustworthiness of those outcomes, and decides whether an architectural
change actually did anything - with cost matching, task-level clustering, FDR
control, equivalence testing, and a stated minimum detectable effect.

It is constitutionally capable of answering "we cannot say."

## Quick start

```bash
uv venv --python 3.12
uv pip install -e .

af init
af demo run --tasks 12
```

`af demo run` scaffolds a validated task suite, plans an experiment, runs 108
trials, grades them, and prints a verdict. No API keys, no Docker, ~90 seconds.

```
  INCONCLUSIVE   delta=-0.083 CI[-0.278,+0.111] p=0.354; design MDE=36.6%

  arm             resolve   95% CI          cost      flags
  baseline        0.833     [+0.64,+1.00]   $0.050    ORACLE_ACCESS:1,LUCKY_PASS:1
  kitchen_sink    0.750     [+0.58,+0.89]   $4.045    LUCKY_PASS:4,ORACLE_ACCESS:1
  no_verification 0.125     [+0.00,+0.26]   $0.030    LOW_PROCESS_QUALITY:36,...

  credibility {"clean": 65, "suspect": 32, "invalid": 11}
```

## Why it is built this way

**Evidence is immutable; interpretation is a pure function over it.** A trial
writes a sealed bundle and never touches it again. Grading, autopsy, clustering
and statistics all read bundles. Change the grader and re-run `af score
--regrade` - no agent is invoked, nothing is re-paid for.

**Identity is a hash.** Architectures, tasks, trials and experiments are
content-addressed. That gives caching, deduplication, lineage and reproducibility
claims for free, and it is why preregistration needs no separate mechanism: the
frozen `ExperimentSpec` hash *is* the preregistration.

**The control plane is disposable.** `af reindex` rebuilds the entire database
from bundles. Corruption is a non-event.

**Measurement validity is a subsystem.** Every score carries integrity flags and
a credibility grade. An experiment whose graders are not trustworthy returns
`UNINTERPRETABLE` rather than a plausible-looking number.

## The architecture DSL

Slots bound to components, not a free-form graph. Bounded, typed, always valid,
and a mutation is one field change.

```yaml
runtime:      { backend: scripted }
models:       { default: { provider: anthropic, id: claude-opus-5-20260101, effort: medium } }
action:       { component: bash-only, params: { stateful: false } }
context:      { component: progressive-disclosure }
memory:       { component: none }
control:      { component: staged, params: { stages: [research, implement, review] } }
verification: { component: project-tests }
budget:       { max_tokens: 400000, max_cost_usd: 4.0 }
```

Topology lives inside `control` as one component among several. Architectures are
also stored as `base + [operators]`, so the diff between two of them is
machine-readable and doubles as lineage.

```bash
af arch diff minimal-bash kitchen-sink
af arch apply minimal-bash '[{"op":"AddStage","stage":"research","before":"implement"}]'
```

Removal operators (`RemoveStage`, `RemoveTool`, `RemoveMemory`, `ScaleBudget`
down) are first class. A proposer that can only grow architectures will only ever
discover growth.

## Commands

```bash
af init                              # create .agentfoundry/
af demo scaffold --tasks 12          # tasks + architectures + experiments
af demo run                          # the entire pipeline in one command

af task validate --all               # four-check admissibility contract
af task list

af components                        # every slot implementation
af backends                          # adapters + conformance status
af arch list | show | diff | apply

af run --task demo-clamp --arch minimal-bash --score
af exp plan experiments/review-stage.yaml     # cost + MDE + warnings, no spend
af exp start experiments/review-stage.yaml    # freeze, queue, run
af score [--regrade]                          # L4 over stored bundles
af exp verdict <hash>

af analyze cluster                   # failure distribution (MAST taxonomy)
af analyze autopsy <trial>           # evidence-linked diagnosis
af analyze landscape                 # productive cores and trap regions
af replay <trial> --step 42 --intervention do_action

af search race --base minimal-bash   # racing with statistical elimination
af search archive                    # behaviour-keyed archive, not a leaderboard

af reindex                           # rebuild the DB from evidence
af reconcile                         # adopt sealed bundles, release stalled leases
af serve                             # read-mostly UI on :8787
```

## Layers

| Layer | Package | Owns |
|---|---|---|
| L0 Spec | `af/spec` | DSL, registry, hashing, operators, lineage |
| L1 Environment | `af/env` | sandboxes, limits, network policy |
| L2 Execution | `af/exec` | backend adapters, supervision, events |
| L3 Evidence | `af/evidence` | bundle format, sealing |
| L4 Grading | `af/grade` | outcome + integrity graders, task validation |
| L5 Analysis | `af/analyze` | indexing, autopsy, landscape, findings |
| L6 Experiment | `af/experiment` | plan, schedule, statistics, verdicts |
| L7 Search | `af/search` | proposers, racing, archive - removable |

Dependencies run strictly downward. Deleting `af/search` must break nothing.

## Task admissibility contract

A task is inadmissible unless all four hold:

1. the fixture **fails** the visible tests (the bug is real),
2. the reference solution **passes** visible and held-out (it is solvable),
3. a null patch **fails** (the grader is not vacuous),
4. a known cheat patch **fails** (the grader is not exploitable).

Demo tasks also ship a *partial* solution that passes visible and fails held-out.
That is the lucky-pass shape, and it is what gives the integrity layer something
real to catch.

## Integrity graders

| Grader | Catches |
|---|---|
| `test-tampering` | diffs touching tests, conftest, CI config |
| `oracle-access` | git history reads, reference-solution paths |
| `process-quality` | passes reached with no verification, blind retries, edit-before-explore |
| `eval-awareness` | signals the agent believes it is being tested |
| composed `LUCKY_PASS` | visible passes, held-out fails |

`TEST_TAMPERING` or `ORACLE_ACCESS` marks a trial **invalid** and excludes it from
estimates while still counting it in the report.

## Statistics

- **Cluster on the task.** k replicates per task are not k independent
  observations. Every interval bootstraps over tasks.
- **MDE up front.** `af exp plan` prints the smallest effect the design can
  detect and warns when claims below it will not be supported.
- **Equivalence, not silence.** "No difference" requires TOST against a declared
  margin. A non-significant p-value is not evidence of equivalence.
- **FDR control.** Benjamini-Hochberg across the campaign, because searching many
  candidates means running many tests.
- Verdicts: `BETTER` `WORSE` `EQUIVALENT` `INCONCLUSIVE` `UNINTERPRETABLE`.

## Backends

- **`scripted`** - a deterministic agent simulator. Exercises every layer above
  L2 with no API keys and no dollars, producing structurally realistic signal
  (capability vs difficulty, lucky passes, oracle attempts, cost multipliers).
  It is a fixture, not a claim about the world.
- **`llm-bash`** - a real single-tool agent loop over Anthropic or OpenAI, stdlib
  only. Point an architecture's `runtime.backend` at it and supply a credential
  either way `af auth` accepts - an environment variable or `af auth add`; the
  backend resolves through `af/auth.py` rather than reading the environment
  itself, so `af doctor` and the runner cannot disagree about whether you are
  ready. Refuses loudly without a key rather than degrading silently.

Adding a backend is five methods plus passing `af backends` conformance.

## Enforcement is recorded, not assumed

A sandbox does not get to claim a control. It declares, per axis, how strongly
it can back one, and that declaration is what lands in `integrity.json`:

| Level | Meaning |
|---|---|
| `kernel` | the OS refuses the operation |
| `advisory` | only cooperating software honours it |
| `unenforced` | requested, nothing backs it |

`--sandbox local` reports `advisory` for both filesystem and network, because
proxy variables stop a cooperating HTTP client and do not stop a raw socket.
`--sandbox docker` reports `kernel` for both. An experiment states the strength
it *requires* in `EnvironmentSpec.require_enforcement` - part of the spec, so
part of the preregistration - and `--require-enforcement kernel` refuses to run
at all rather than quietly producing a weaker result:

```
error: environment requires enforcement='kernel' but sandbox 'local' provides
       {'filesystem': 'advisory', 'network': 'advisory', 'is_security_boundary': False}
```

Secrets are redacted on the way into the event log rather than on the way out,
and the bundle records which classes were matched. Like the network control,
this is a blocklist and is labelled as one.

## Tests

Organised by failure mode, not by module:

| Tier | Asks |
|---|---|
| `tests/` | does it work? |
| `tests/conformance/` | does *every* implementation satisfy the contract? |
| `tests/adversarial/` | does hostile input fail safely, and is the record honest? |
| `tests/hardening/` | do the architectural claims still hold? |

The last two are the admissibility contract turned on the platform itself. A
grader is not trusted here until a null patch and a cheat patch have been thrown
at it; the sandbox, the scheduler and the layering now face the same standard.
`tests/hardening` fails the build if a module outside the CLI boundary reads
`os.environ`, or if a layer imports one below it.

## Status

Every layer is implemented and exercised by `af demo run`. Sandboxing defaults to
process isolation (`--sandbox local`), which is a lifecycle boundary and not a
security one; `--sandbox docker` is the real boundary, and even that assumes a
misbehaving agent rather than an attacker with a kernel exploit. The failure
labeler is **uncalibrated** and says so on every output - calibrating it against
labelled trajectory corpora is the next piece of work.
