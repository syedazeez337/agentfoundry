"""Experiment layer (L6): plan, schedule, analyze, conclude.

Freezing an ExperimentSpec and hashing it IS the preregistration. There is no
path to adjusting the analysis after seeing the data, because the analysis plan
is an input to execution rather than a later step.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from af import ANALYSIS_VERSION, FOUNDRY_VERSION
from af.evidence import Bundle
from af.exec import get_backend, run_trial
from af.grade import score_bundle
from af.spec.models import (
    ArchitectureSpec, EnvironmentSpec, ExperimentSpec, TaskSpec, TrialSpec,
)
from af.spec.registry import resolve
from af.store import Store
from af.util import hash_obj, now_iso

from af.experiment import stats


# ------------------------------------------------------------------ loading


def load_architecture(root: Path, ref: str) -> ArchitectureSpec:
    p = Path(ref)
    if p.exists():
        return ArchitectureSpec.load(p)
    for cand in (root / "architectures" / f"{ref}.yaml",
                 root / "architectures" / ref):
        if cand.exists():
            return ArchitectureSpec.load(cand)
    raise FileNotFoundError(f"architecture not found: {ref}")


def load_suite(root: Path, suite: str) -> list[TaskSpec]:
    tasks = []
    for p in sorted((root / "tasks").glob("**/task.yaml")):
        t = TaskSpec.load(p.parent)
        if suite in ("*", "all") or t.suite == suite:
            tasks.append(t)
    return tasks


def apply_overrides(arch: ArchitectureSpec, overrides: dict,
                    arm_id: str | None = None) -> ArchitectureSpec:
    """Overridden variants get a distinct name as well as a distinct hash.

    Without this, two different architectures share a name and name lookup
    becomes ambiguous - a real bug, not a cosmetic one.
    """
    from af.spec.operators import apply_operators

    ops = []
    if "budget" in overrides:
        for k, v in overrides["budget"].items():
            ops.append({"op": "SetParam", "path": f"budget.{k}", "value": v})
    if "effort" in overrides:
        ops.append({"op": "SetEffort", "effort": overrides["effort"]})
    for op in overrides.get("operators", []):
        ops.append(op)
    if not ops:
        return arch
    ops.append({"op": "Rename", "name": f"{arch.name}@{arm_id or 'override'}"})
    return apply_operators(arch, ops)


# ------------------------------------------------------------------- plan


@dataclass
class Plan:
    experiment_hash: str
    trials: list[tuple[TrialSpec, str]]      # (spec, arm_id)
    arms: dict[str, dict]
    n_tasks: int
    est_cost_usd: float
    mde: float
    warnings: list[str]
    architectures: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "experiment_hash": self.experiment_hash,
            "n_trials": len(self.trials),
            "n_tasks": self.n_tasks,
            "arms": self.arms,
            "est_cost_usd": round(self.est_cost_usd, 2),
            "minimum_detectable_effect": self.mde,
            "warnings": self.warnings,
        }


def plan_experiment(root: Path, spec: ExperimentSpec,
                    env: EnvironmentSpec | None = None) -> Plan:
    """Expand arms x suite x replicates, resolve early, and report what the
    design can and cannot detect."""
    env = env or EnvironmentSpec()
    tasks = load_suite(root, spec.suite)
    if not tasks:
        raise RuntimeError(f"suite {spec.suite!r} has no tasks")

    trials: list[tuple[TrialSpec, str]] = []
    arms_info: dict[str, dict] = {}
    architectures: list = []
    est_cost = 0.0
    warnings: list[str] = []

    for arm in spec.arms:
        arch = apply_overrides(load_architecture(root, arm.architecture),
                               arm.overrides, arm.id)
        backend = get_backend(arch.backend)
        resolved = resolve(arch, backend.capabilities())    # fails loudly here
        architectures.append(arch)
        arms_info[arm.id] = {
            "architecture": arch.name,
            "arch_hash": arch.hash,
            "backend": arch.backend,
            "capability": round(resolved.capability, 4),
            "cost_mult": round(resolved.cost_mult, 3),
            "stages": resolved.stages,
        }
        for task in tasks:
            for nonce in range(spec.replicates):
                ts = TrialSpec(
                    task_id=task.id,
                    task_hash=task.hash,
                    arch_hash=arch.hash,
                    env_hash=env.hash,
                    models=resolved.models,
                    budget=resolved.budget,
                    nonce=nonce,
                    foundry_version=FOUNDRY_VERSION,
                )
                trials.append((ts, arm.id))
        est_cost += len(tasks) * spec.replicates * 0.02 * resolved.cost_mult

    mde = stats.minimum_detectable_effect(len(tasks))
    if mde > 0.15:
        warnings.append(
            f"design can only detect effects >= {mde:.1%}; "
            f"claims below that are not supported by this task count"
        )
    if spec.replicates < 3:
        warnings.append("fewer than 3 replicates per task: within-task variance "
                        "will be poorly estimated")
    if len(spec.arms) > 1 and not any("cost" in a.id or "matched" in a.id
                                      for a in spec.arms):
        warnings.append("no cost-matched control arm: a positive result may just "
                        "mean the candidate spent more")
    budget_cap = spec.budget.get("max_cost_usd")
    if budget_cap and est_cost > budget_cap:
        warnings.append(f"estimated cost ${est_cost:.2f} exceeds cap ${budget_cap}")

    return Plan(spec.hash, trials, arms_info, len(tasks), est_cost, mde, warnings,
                architectures)


# -------------------------------------------------------------- scheduler


def _last_progress_ts(bundle_dir: Path) -> float | None:
    """When this trial last produced evidence, or None if it never has."""
    events = Path(bundle_dir) / "events.jsonl"
    for candidate in (events, Path(bundle_dir) / "manifest.json"):
        try:
            return candidate.stat().st_mtime
        except OSError:
            continue
    return None


def _stalled(bundle_dir: Path, stall_s: float) -> bool:
    last = _last_progress_ts(bundle_dir)
    if last is None:
        # No bundle at all: the trial never started, so there is nothing to
        # call stalled. Lease expiry is the right mechanism for that case.
        return False
    return (time.time() - last) > stall_s


class Scheduler:
    """Deliberately boring. Table + leases + heartbeat + budget guard.

    A failed agent run is data and is never retried. An infrastructure failure
    is not data: it is filed as "errored", excluded from the estimates, and
    counted in the report. Re-running the experiment re-runs those trials,
    because `run_trial` only treats a *successfully* sealed bundle as evidence
    it already owns.
    """

    def __init__(self, store: Store, root: Path, bundles: Path,
                 sandbox_kind: str = "local", require_enforcement: str = "none"):
        self.store = store
        self.root = Path(root)
        self.bundles = Path(bundles)
        self.sandbox_kind = sandbox_kind
        self.env = EnvironmentSpec(require_enforcement=require_enforcement)
        self.worker = f"{socket.gethostname()}:{os.getpid()}"

    # ------------------------------------------------------------ reconcile

    def reconcile(self, exp_hash: str | None = None,
                  stall_s: float = 900.0) -> dict:
        """Rebuild queue state from the evidence plane.

        The control plane is disposable - `af reindex` already rebuilds the
        whole database from bundles - but that was only ever a recovery
        command. Running the same idea as a supervision step means the
        scheduler trusts what is on disk rather than what it remembers, so a
        crash between sealing a bundle and updating the row costs nothing, and
        a re-run does not pay again for work already done.

        Two things are decided here:

        - a queued or leased trial whose bundle is already sealed is *adopted*,
          not re-run. Seal status decides whether that is `sealed` or
          `errored`, exactly as in `drain`.
        - a leased trial with no new event for `stall_s` is released back to
          the queue. Lease expiry alone only catches a worker that stopped
          renewing; this catches one that is alive and wedged, because it asks
          for evidence of progress rather than evidence of a heartbeat.
        """
        adopted = 0
        released = 0
        rows = self.store.trials_in_states(("queued", "leased"), exp_hash)
        for row in rows:
            bundle_dir = self.bundles / row["trial_key"]
            b = Bundle(bundle_dir)
            if b.sealed:
                self.store.finish_trial(
                    row["trial_key"], "sealed" if b.ok else "errored",
                    str(bundle_dir), b.seal.get("error"))
                adopted += 1
            elif row["state"] == "leased" and _stalled(bundle_dir, stall_s):
                self.store.release_trial(row["trial_key"])
                released += 1
        return {"adopted": adopted, "released": released, "examined": len(rows)}

    def submit(self, spec: ExperimentSpec, plan: Plan) -> str:
        for arch in plan.architectures:
            self.store.put_architecture(arch)
        self.store.freeze_experiment(spec, [
            {"trial_key": ts.trial_key, "arm": arm} for ts, arm in plan.trials
        ])
        for ts, arm in plan.trials:
            self.store.enqueue_trial(ts, spec.hash, arm)
        return spec.hash

    def drain(self, exp_hash: str | None = None, limit: int | None = None,
              on_progress=None) -> dict:
        """Run queued trials to completion in this process."""
        done = 0
        spent = 0.0
        unknown_spend = 0
        cap = None
        if exp_hash:
            exp = self.store.get_experiment(exp_hash)
            cap = (json.loads(exp["budget"]) or {}).get("max_cost_usd") if exp else None

        # Reconcile before dispatching anything: work that is already on disk
        # must not be paid for a second time.
        reconciled = self.reconcile(exp_hash)

        tasks_by_id = {t.id: t for t in load_suite(self.root, "*")}
        arch_cache: dict[str, ArchitectureSpec] = {}

        while True:
            if limit is not None and done >= limit:
                break
            row = self.store.lease_trial(self.worker, exp_hash)
            if row is None:
                break

            if cap is not None and spent > cap:
                self.store.finish_trial(row["trial_key"], "cancelled",
                                        error="budget cap reached")
                continue

            try:
                task = tasks_by_id[row["task_id"]]
                ah = row["arch_hash"]
                if ah not in arch_cache:
                    rec = self.store.get_architecture(ah)
                    if rec is None:
                        raise RuntimeError(f"unknown architecture {ah[:12]}")
                    arch_cache[ah] = ArchitectureSpec.from_dict(json.loads(rec["manifest"]))
                arch = arch_cache[ah]

                spec_row = self.store.one("SELECT * FROM trial WHERE trial_key=?",
                                          row["trial_key"])
                ts = TrialSpec(
                    task_id=task.id, task_hash=task.hash, arch_hash=arch.hash,
                    env_hash=self.env.hash,
                    models={k: v.to_dict() for k, v in arch.models.items()},
                    budget=arch.budget.to_dict(), nonce=spec_row["nonce"],
                )
                bundle_dir = run_trial(ts, task, arch, self.env,
                                       self.bundles, self.sandbox_kind)
                b = Bundle(bundle_dir)
                if b.usage_unknown:
                    unknown_spend += 1
                else:
                    spent += float(b.usage.get("cost_usd") or 0.0)
                # A bundle sealed with status="error" is an infrastructure
                # record, not an agent run. Filing it as "sealed" put provider
                # outages into the pass/fail rates as ordinary agent failures.
                self.store.finish_trial(
                    row["trial_key"], "sealed" if b.ok else "errored",
                    str(bundle_dir), b.seal.get("error"))
            except Exception as exc:  # noqa: BLE001
                self.store.finish_trial(row["trial_key"], "errored", None, str(exc))

            done += 1
            if on_progress:
                on_progress(done, row["trial_key"])

        if exp_hash:
            counts = self.store.counts_by_state(exp_hash)
            if counts.get("queued", 0) == 0 and counts.get("leased", 0) == 0:
                self.store.set_experiment_state(exp_hash, "complete")
        return {"ran": done, "spent_usd": round(spent, 4),
                "trials_with_unknown_spend": unknown_spend,
                "adopted": reconciled["adopted"],
                "released": reconciled["released"]}


# ---------------------------------------------------------------- scoring


def score_experiment(store: Store, root: Path, exp_hash: str | None = None,
                     regrade: bool = False, sandbox_kind: str = "local") -> dict:
    """Run L4 over sealed bundles. Re-runnable at zero agent cost - the whole
    point of the immutable-evidence design."""
    tasks_by_id = {t.id: t for t in load_suite(root, "*")}
    if exp_hash:
        rows = [r for r in store.trials_for_experiment(exp_hash) if r["state"] == "sealed"]
    else:
        rows = [dict(r) for r in store.q("SELECT * FROM trial WHERE state='sealed'")]

    if not regrade:
        have = store.get_scores([r["trial_key"] for r in rows], ANALYSIS_VERSION)
        rows = [r for r in rows if r["trial_key"] not in have]

    n = 0
    for r in rows:
        if not r["bundle_path"]:
            continue
        b = Bundle(Path(r["bundle_path"]))
        task = tasks_by_id.get(r["task_id"])
        if task is None:
            continue
        s = score_bundle(b, task, sandbox_kind, ANALYSIS_VERSION)
        b.write_derived("score.json", s.to_dict())
        store.put_score(s)
        n += 1
    return {"scored": n}


# ---------------------------------------------------------------- analyzer

VERDICTS = ("BETTER", "WORSE", "EQUIVALENT", "INCONCLUSIVE", "UNINTERPRETABLE")


def analyze_experiment(store: Store, exp_hash: str) -> dict:
    """Consumes only sealed, credible scores. Emits an immutable Verdict.

    EQUIVALENT and INCONCLUSIVE are different conclusions and both are success
    states. UNINTERPRETABLE fires when integrity undermines the comparison -
    the system says so instead of reporting a number.
    """
    exp = store.get_experiment(exp_hash)
    if exp is None:
        raise KeyError(f"no experiment {exp_hash}")
    spec = json.loads(exp["spec"])
    plan = {p["trial_key"]: p["arm"] for p in json.loads(exp["plan"])}
    analysis = spec["analysis"]
    margin = float(analysis.get("equivalence_margin", 0.05))

    trials = store.trials_for_experiment(exp["hash"])
    # Excluded from the estimates, counted in the report - the same treatment
    # disqualifying integrity flags already get.
    execution_counts: dict[str, int] = {}
    for t in trials:
        st = t["state"] or "unknown"
        execution_counts[st] = execution_counts.get(st, 0) + 1
    keys = [t["trial_key"] for t in trials]
    scores = store.get_scores(keys, ANALYSIS_VERSION)

    by_arm: dict[str, list[tuple[str, bool]]] = {}
    cost_by_arm: dict[str, list[float]] = {}
    wall_by_arm: dict[str, list[float]] = {}
    flags_by_arm: dict[str, dict[str, int]] = {}
    credibility_counts = {"clean": 0, "suspect": 0, "invalid": 0}

    for t in trials:
        s = scores.get(t["trial_key"])
        if s is None:
            continue
        arm = plan.get(t["trial_key"], t["arm_id"] or "?")
        credibility_counts[s["credibility"]] = credibility_counts.get(s["credibility"], 0) + 1
        fl = flags_by_arm.setdefault(arm, {})
        for f in s["flags"]:
            fl[f] = fl.get(f, 0) + 1
        if s["credibility"] == "invalid":
            continue          # excluded from the estimate, counted in the report
        by_arm.setdefault(arm, []).append((s["task_id"], bool(s["outcomes"]["resolved"])))
        cost_by_arm.setdefault(arm, []).append(float(s["outcomes"].get("cost_usd") or 0))
        wall_by_arm.setdefault(arm, []).append(float(s["outcomes"].get("wall_s") or 0))

    arm_ids = [a["id"] for a in spec["arms"]]
    estimates = {}
    for arm in arm_ids:
        obs = by_arm.get(arm, [])
        est = stats.estimate_arm(arm, obs)
        estimates[arm] = {
            **est.to_dict(),
            "cost_usd_mean": round(stats.mean(cost_by_arm.get(arm, [])), 4),
            "wall_s_mean": round(stats.mean(wall_by_arm.get(arm, [])), 2),
            "flags": flags_by_arm.get(arm, {}),
        }

    baseline = arm_ids[0]
    comparisons = []
    pvals = []
    MIN_PAIRS = 3
    for arm in arm_ids[1:]:
        a = stats.per_task_rates(by_arm.get(baseline, []))
        b = stats.per_task_rates(by_arm.get(arm, []))
        paired = stats.paired_cluster_bootstrap(a, b)
        # An arm with too few paired observations cannot support any claim -
        # least of all an equivalence claim, which is what a naive zero-width
        # interval would otherwise produce.
        insufficient = paired.n_pairs < MIN_PAIRS
        eq = stats.tost(paired.ci, margin)
        comparisons.append({
            "baseline": baseline, "candidate": arm,
            **paired.to_dict(),
            "equivalence": {"equivalent": False, "margin": margin,
                            "ci": list(paired.ci)} if insufficient else eq.to_dict(),
            "insufficient_data": insufficient,
            "cost_delta_usd": round(
                stats.mean(cost_by_arm.get(arm, [])) - stats.mean(cost_by_arm.get(baseline, [])), 4),
        })
        pvals.append(1.0 if insufficient else paired.p_value)

    rejects = stats.benjamini_hochberg(pvals) if pvals else []
    for c, r in zip(comparisons, rejects, strict=False):
        c["significant_after_fdr"] = bool(r)

    # ---- verdict
    invalid_rate = credibility_counts["invalid"] / max(1, sum(credibility_counts.values()))
    n_tasks = max((e["n_tasks"] for e in estimates.values()), default=0)
    mde = stats.minimum_detectable_effect(n_tasks) if n_tasks else 1.0

    if invalid_rate > 0.15:
        result = "UNINTERPRETABLE"
        reason = (f"{invalid_rate:.0%} of trials carry disqualifying integrity flags; "
                  f"the grader is not measuring what the comparison claims")
    elif not comparisons:
        result = "INCONCLUSIVE"
        reason = "single arm; nothing to compare"
    elif all(c["insufficient_data"] for c in comparisons):
        result = "UNINTERPRETABLE"
        reason = ("no arm has enough paired observations; the run did not "
                  "produce the evidence its own design asked for")
    else:
        primary = comparisons[0]
        if primary["insufficient_data"]:
            result = "UNINTERPRETABLE"
            reason = (f"primary comparison has only {primary['n_pairs']} paired "
                      f"tasks; nothing can be concluded from it")
        elif primary["significant_after_fdr"] and primary["delta"] > 0:
            result = "BETTER"
        elif primary["significant_after_fdr"] and primary["delta"] < 0:
            result = "WORSE"
        elif primary["equivalence"]["equivalent"]:
            result = "EQUIVALENT"
        else:
            result = "INCONCLUSIVE"
        if not primary["insufficient_data"]:
            reason = (f"delta={primary['delta']:+.3f} "
                      f"CI[{primary['ci'][0]:+.3f},{primary['ci'][1]:+.3f}] "
                      f"p={primary['p_value']:.3f}; design MDE={mde:.1%}")

    payload = {
        "experiment": exp["name"],
        "hypothesis": spec.get("hypothesis", ""),
        "arms": estimates,
        "comparisons": comparisons,
        "credibility": credibility_counts,
        "execution": execution_counts,
        "n_tasks": n_tasks,
        "minimum_detectable_effect": mde,
        "equivalence_margin": margin,
        "reason": reason,
        "analysis_version": ANALYSIS_VERSION,
        "created_at": now_iso(),
    }
    bundle_set_hash = hash_obj(sorted(scores))
    verdict = {
        "hash": hash_obj({"e": exp["hash"], "b": bundle_set_hash, "a": ANALYSIS_VERSION}),
        "experiment_hash": exp["hash"],
        "bundle_set_hash": bundle_set_hash,
        "analysis_version": ANALYSIS_VERSION,
        "result": result,
        "payload": payload,
    }
    store.put_verdict(verdict)
    return verdict
