# AgentFoundry: enforcement-honesty plan

Synthesis of three research inputs (2026-08-29):
- **Internal code review** of `af/` — which declared invariants are currently false.
- **PRAXIST** (sapientinc/PRAXIST, Fair Source — *ideas only, no code copying*) — how to
  keep invariants true as the surface grows.
- **Symphony** (openai/symphony, Apache-2.0 — literal borrowing permitted) — how to
  supervise runs without trusting in-memory state.

## Diagnosis

The three worst review findings are one defect class:

> A declared invariant with no enforcement mechanism and no test that would fail
> if it were violated.

- `af/env/__init__.py:7` claims "network deny-by-default"; proxy-env deny is bypassed
  by any raw socket.
- `af/experiment/__init__.py:174` claims "infrastructure failures retry once"; the word
  `retry` appears nowhere else in `af/`.
- `af/exec/adapters/scripted.py:67` computes `git_ops`; `af/exec/__init__.py:259` reads a
  different dict, so `integrity["git_ops"]` is always `[]`.

AgentFoundry already knows the fix. The task admissibility contract requires that a null
patch fails and a cheat patch fails — a grader is not trusted until something that should
break it has been tried against it. That is an adversarial test tier. The project applies
that standard to its graders and to nothing else.

**This plan applies AgentFoundry's own admissibility standard to AgentFoundry.**

## Organizing principle: record enforcement, not intent

Commit `44bb976` already invented the pattern in `integrity.json`:
`network_enforcement: kernel | proxy-env (best effort)`, `is_security_boundary`.
Generalize it. Every assertion becomes `{claim, enforcement, verified_by}`.

Hashing decision:
- **Intent** stays in `EnvironmentSpec` → hashed → part of preregistration.
- **Realized enforcement** goes in `integrity.json` → not hashed → observed fact.
- **A credibility rule** downgrades trials whose realized enforcement is below the
  experiment's declared requirement.

This preserves cross-sandbox cache identity while making the gap visible. Payoff: an
experiment that required isolation and got best-effort returns `UNINTERPRETABLE`.

## Two findings from direct reading (not in the review)

1. **Errored trials are permanently memoized.** `af/exec/__init__.py:167-169` returns any
   bundle with a `SEALED` file; `:273-278` seals with `status="error"`. A provider outage
   is cached as evidence forever and never re-invoked. Retry-once *cannot* be built until
   the cache check consults seal status.
2. **The budget cap under-counts the trials that break it.** `af/experiment/__init__.py:239`
   does `or 0.0`; the error path writes a `usage.json` with no `cost_usd`. A trial that
   burned tokens then failed contributes $0.00 to the cap.

## Phases (execution order) - ALL COMPLETE 2026-08-29

### Phase 4 — CI  *(first: it is what makes the rest enforceable)*
`.github/workflows/ci.yml` running the unittest suite + ruff. Ruff config in pyproject.

### Phase 0 — Make existing claims true
1. **Sandbox honesty.** `Sandbox.enforcement()` beside `kind`. Path containment on
   `put`/`get` (Symphony `path_safety.ex`: normalize absolute, require workspace root as
   strict prefix). `--require-enforcement` so an experiment refuses a weaker sandbox
   rather than silently producing a weaker result. `local` stays the demo default.
2. **Reconnect the integrity channel.** Merge `outcome.raw` at `af/exec/__init__.py:259`.
   Write the missing `tests/test_graders.py`.
3. **Errored trials.** Seal-status-aware cache; `errored` trial state; excluded from
   estimates, still counted in the report (mirroring `TEST_TAMPERING`).
4. **`usage_unknown`.** Never coerce missing cost to zero (PRAXIST `BudgetLedger`).

### Phase 1 — Test tiers  *(PRAXIST's highest-value idea)*
- `tests/conformance/` — upgrade `conformance_check()` from "methods exist" to behavioral:
  every backend must demonstrate a computed signal arrives in `integrity.json`.
- `tests/adversarial/` — raw-socket escape; binary file writes (`snapshot_tree` skips
  non-UTF-8, so binaries are invisible to `files_changed`); multi-line heredoc truncation;
  `-`-prefixed `repo`/`commit`; `../` in sandbox `put`.
- `tests/hardening/` — architectural invariants as tests, per PRAXIST
  `test_env_read_discipline.py` + frozen allowlist. First one: no `os.environ` outside the
  CLI boundary.

### Phase 2 — Config and credential discipline
`af/config.py:RunConfig` built once at the CLI boundary. `llm_bash` resolves through
`auth.resolve()` (closes the `af auth add` → doctor-READY → run-fails gap). Redaction
before persistence — **reimplemented, not copied** (PRAXIST is Fair Source). HTML-escape
event attrs in `af/api` (stored XSS): redaction + escaping serve one property — the
evidence plane is safe to persist and safe to look at.

### Phase 3 — Reconciliation control plane  *(Symphony)*
`Scheduler.drain()` reconciles against the evidence plane each tick (does a sealed bundle
exist for this `trial_key`?) rather than trusting DB rows. `af reindex` already proves the
project believes the control plane is disposable; Symphony shows how to run that as a
supervision loop. Stall detection from `events.jsonl` timestamps catches a hung agent loop
that `wall_s` only catches at the end.

## Explicitly out of scope
PRAXIST's four-tier plugin resolver, `replay.py`, evidence lanes, telemetry, panel
topology. Symphony's issue-tracker control plane. Respect the layering rule (*deleting
`af/search` must break nothing*): `RunConfig` at L0/CLI, enforcement at L1, ledger at L3,
tiers in `tests/`. Nothing inverts the downward dependency direction.

## Known limitations after completion
Docker remains the only real boundary and is not a hard requirement; even Docker is not a
boundary against a determined adversary (shared kernel) — the threat model is agent
misbehavior, not a targeted attacker. Redaction is regex-based and best-effort — label it
like proxy-env deny. Integrity graders remain a blocklist, not a proof. **None of this
improves statistical power, and correctly excluding errored trials reduces effective n and
widens intervals** — some currently-reported results will become `INCONCLUSIVE`; that is
the plan working. The failure labeler stays uncalibrated. Single machine, SQLite,
single-process drain: reconciliation makes restarts safe, not the system distributed.
Budget caps remain between-trial.


---

## Outcome (2026-08-29)

All five phases implemented. 158 tests pass, ruff clean, `af demo run` verified
end to end (108 trials -> BETTER, integrity flags live).

Delivered beyond the written plan, because the work surfaced them:

- **`af/evidence` <-> `af/exec` import cycle**, found by the new layering test and
  hidden until then by a deferred import inside `Bundle.events()`. `Event` and
  `read_events` moved to `af/evidence` (the bundle format owns its own record);
  `af/exec` re-exports them.
- **NUL-byte files were treated as text.** `b"\x00" * 8` is valid UTF-8, so the
  first binary-visibility fix still missed them. Now uses git's heuristic.
- **Ruff config plus 27 real lint fixes**, including the two silent-swallow sites
  the review flagged (`reindex`, `Race.__init__`), which now report what they
  skipped instead of discarding it.

Changed from the plan:

- **Fail-fast replaced the credibility rule.** The plan said a trial whose realised
  enforcement fell below its requirement would be graded down. `run_trial` refuses
  such a trial outright instead, before a bundle is opened or a cent is spent, so
  the weak trial never exists to be downgraded. `enforcement_satisfied` is still
  recorded on every bundle; with the default `require_enforcement="none"` it is
  always true, which is honest rather than decorative.

Deliberately not done:

- **No retry-once loop.** The docstring claiming one was removed rather than
  implemented. `run_trial` no longer memoises an errored bundle, so re-running an
  experiment re-runs exactly the infrastructure failures - which is the behaviour
  the claim was reaching for, without a retry counter nobody asked for.
- **`Store.put_archive` TOCTOU** (review #9) left as-is. It only bites under
  concurrent workers, which nothing currently starts; reconciliation makes it
  recoverable rather than corrupting. Worth a `self.tx()` wrapper when concurrency
  actually arrives.
- **`Bundle.trial_spec_hash`** (review #11) still returns the wrong thing. It has
  no callers; deleting or fixing it is a decision about intent, not a defect to
  patch blindly.
