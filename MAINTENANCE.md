# Maintenance protocol

This file exists because the project was built for several hours with no commits
and no tests, and an unrequested change to `TaskSpec.canonical()` silently
altered every task hash and every experiment verdict. Nothing caught it. These
rules are the response to that specific failure, not general advice.

## Invariants

Break any of these and stored evidence becomes unusable. They are not style
preferences.

1. **Identity is content.** A spec's hash is the SHA-256 of its `canonical()`
   form. Changing which fields participate is a schema migration.
2. **Evidence is write-once.** A sealed bundle is never modified. Re-analysis
   creates new derived artifacts beside it, never in place.
3. **The control plane is disposable.** `af reindex` must rebuild the database
   from bundles alone. Anything the database knows that bundles do not is a bug.
4. **Replicates share a spec hash and differ by nonce.** This is what gives the
   statistics their clustering structure. Break it and cluster-aware inference
   silently becomes wrong.
5. **Verdicts are immutable and versioned.** Re-running analysis produces a new
   verdict beside the old one. It never overwrites.

## Before changing code

- **There must be a commit to return to.** No exceptions.
- **Run the suite first** so you know the starting state is green:
  ```
  .venv/Scripts/python -m unittest discover -s tests -t .
  ```
- **One concern per change.** A dependency audit, a portability fix, a task
  model change and a grading rewrite are four changes, not one. Landing them
  together makes damage assessment impossible.

## After changing code

- Run the suite again. If `test_verdict_is_deterministic` or anything in
  `test_identity.py` fails, **stop**. Do not re-pin the expected value to make
  the test pass. Decide whether the change was intended, and if it was, follow
  the schema-change procedure below.
- Commit with the verification command and its result stated in the message.

## Changing identity on purpose

When an identity-affecting change is genuinely wanted:

1. Bump `SCHEMA_VERSION` in `tests/test_identity.py`.
2. Record the break in `CHANGELOG.md`: what changed, which stored evidence is
   orphaned, and whether a migration is possible.
3. Re-pin the expected hashes **by measurement**, not by guessing. Run the
   computation twice and confirm the values are identical before pinning them.
4. Commit the schema change on its own, with nothing else in it.

## Scope discipline

The single most expensive mistake in this project so far was reading a research
request as an implementation mandate.

- "Research", "tell me", "report", "how important is X" produce **findings and a
  recommendation**, and then stop.
- Implementation is a separate authorisation. When a request is ambiguous, ask
  rather than resolving it in the direction of more work.
- Files may be added freely (docs, new modules). **Modifying working code needs
  a clear go-ahead.**

## Test layout

| File | Speed | Covers |
|---|---|---|
| `tests/test_identity.py` | instant | canonical key sets, pinned hashes, order independence, replicate derivation |
| `tests/test_pipeline.py` | ~15s | full stack: plan, schedule, execute, seal, grade, analyze, verdict, reindex |

Both were verified to catch the original failure: reintroducing a field in
`TaskSpec.canonical()` fails `test_task_keys` and
`test_verdict_is_deterministic`, and both pass again once reverted.

Pinned values in `test_pipeline.py` were **measured twice on a known-good tree**,
not guessed. The first attempt at this file guessed them and was wrong, which is
the reason for this paragraph.

## Environment

`af doctor` reports three tiers:

- **core** - needed to run anything
- **simulator** - needed for `af demo run`; no keys, no containers
- **real** - needed to drive a live agent on a real repository

`af doctor --tier core,simulator` is the pre-change check. Readiness is only
reported for tiers that actually ran, so a filtered run cannot vacuously claim
the others are fine.

## Known parked work

- `af/tasks.py` is on disk and **not wired into the CLI**. It was written for a
  real-repo import path that has not been authorised. Either wire it up
  deliberately or delete it; do not let it rot in between.
- `af/exec/adapters/llm_bash.py` has **never been run against a live API**, and
  research indicates it will fail on Claude Opus 5: it sends
  `thinking: {type: "enabled", budget_tokens: N}`, which is removed and returns
  400. See `docs/REAL-RUN.md` for the full list of required corrections.
