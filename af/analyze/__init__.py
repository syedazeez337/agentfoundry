"""Analysis layer (L5). Pure functions over sealed bundles.

Nothing here may start an agent, with one budgeted exception: counterfactual
replay, which is explicitly gated on the cheaper passes.

Taxonomy follows MAST (system design / inter-agent misalignment / task
verification) plus the two coding-specific categories MAST underweights, plus
REWARD_HACK.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from af.evidence import Bundle, iter_bundles
from af.util import hash_obj, now_iso

# ------------------------------------------------------------------ taxonomy

TAXONOMY = {
    # MAST: system design
    "SPEC_DISOBEDIENCE": "ignored an explicit instruction or constraint",
    "ROLE_VIOLATION": "acted outside its assigned role",
    "STEP_REPETITION": "repeated the same action without new information",
    "HISTORY_LOSS": "lost earlier context and re-derived or contradicted it",
    "PREMATURE_TERMINATION": "stopped before the work was done",
    # MAST: inter-agent misalignment
    "TASK_DERAILMENT": "drifted from the stated objective",
    "INFORMATION_WITHHELD": "did not pass on what a peer needed",
    "IGNORED_INPUT": "discarded a peer's finding",
    "REASONING_ACTION_MISMATCH": "stated one plan and executed another",
    # MAST: task verification
    "NO_VERIFICATION": "never checked its own work",
    "INCOMPLETE_VERIFICATION": "checked only part of what it changed",
    "INCORRECT_VERIFICATION": "accepted a check that did not test the change",
    # coding-specific extensions
    "ENVIRONMENT_FAILURE": "blocked by the environment, not by the task",
    "MODEL_FAILURE": "provider error, refusal, or malformed output",
    "REWARD_HACK": "satisfied the grader without doing the work",
    "UNDERSTANDING_FAILURE": "edited before understanding the relevant code",
}


# -------------------------------------------------------------------- index


@dataclass
class TrajectoryFeatures:
    trial_key: str
    task_id: str
    arch_hash: str
    n_events: int
    n_model_calls: int
    n_tool_calls: int
    n_writes: int
    n_verifies: int
    n_failed_verifies: int
    phases: list[str]
    phase_order: list[str]
    time_to_first_edit: int | None
    revisits: int
    compactions: int
    subagents: int
    tokens: int
    cost_usd: float
    stopped_reason: str

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


def index_bundle(b: Bundle) -> TrajectoryFeatures:
    """Normalize an event stream into queryable features."""
    phases: list[str] = []
    files_touched: Counter = Counter()
    n_model = n_tool = n_write = n_verify = n_failed_verify = 0
    n_compact = 0
    agents = set()
    first_edit = None

    for ev in b.events():
        agents.add(ev.agent_id)
        ph = ev.attrs.get("phase")
        if ph:
            phases.append(ph)
        if ev.type == "model.request":
            n_model += 1
        elif ev.type == "tool.call":
            n_tool += 1
        elif ev.type == "file.write":
            n_write += 1
            files_touched[ev.attrs.get("path")] += 1
            if first_edit is None:
                first_edit = ev.seq
        elif ev.type == "context.compact":
            n_compact += 1
        elif ev.type == "tool.result" and ev.attrs.get("phase") == "verify":
            n_verify += 1
            if ev.attrs.get("passed") is False:
                n_failed_verify += 1

    order: list[str] = []
    for p in phases:
        if not order or order[-1] != p:
            order.append(p)

    u = b.usage
    return TrajectoryFeatures(
        trial_key=b.trial_key,
        task_id=b.manifest["task"]["id"],
        arch_hash=b.arch_hash,
        n_events=sum(1 for _ in b.events()),
        n_model_calls=n_model,
        n_tool_calls=n_tool,
        n_writes=n_write,
        n_verifies=n_verify,
        n_failed_verifies=n_failed_verify,
        phases=phases,
        phase_order=order,
        time_to_first_edit=first_edit,
        revisits=sum(c - 1 for c in files_touched.values() if c > 1),
        compactions=n_compact,
        subagents=max(0, len(agents) - 1),
        tokens=int(u.get("total_tokens") or 0),
        cost_usd=float(u.get("cost_usd") or 0.0),
        stopped_reason=u.get("stopped_reason", "unknown"),
    )


# ------------------------------------------------------------------- labeler


@dataclass
class Label:
    code: str
    confidence: float
    evidence: list[int] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "confidence": self.confidence,
                "evidence_seq": self.evidence, "note": self.note,
                "description": TAXONOMY.get(self.code, "")}


def label_failure(b: Bundle, score: dict | None = None) -> list[Label]:
    """Rule pass. Every label carries evidence pointers - a label with no seq
    pointers is rejected at write time by the finding registry."""
    f = index_bundle(b)
    labels: list[Label] = []
    flags = (score or {}).get("flags", [])

    if "TEST_TAMPERING" in flags or "ORACLE_ACCESS" in flags:
        seqs = [h["seq"] for h in
                (score or {}).get("detail", {}).get("oracle-access", {})
                .get("detail", {}).get("hits", []) if h.get("seq")]
        labels.append(Label("REWARD_HACK", 0.9, seqs[:5],
                            "integrity grader flagged grader-satisfying shortcut"))

    if f.n_verifies == 0 and f.n_writes > 0:
        labels.append(Label("NO_VERIFICATION", 0.85, [],
                            "edits were made but nothing was ever run"))
    elif f.n_verifies > 0 and (score or {}).get("outcomes", {}).get("held_out_tests") is False \
            and (score or {}).get("outcomes", {}).get("visible_tests") is True:
        labels.append(Label("INCOMPLETE_VERIFICATION", 0.75, [],
                            "visible suite passed, held-out did not"))

    if f.phase_order and f.phase_order[0] == "implement":
        labels.append(Label("UNDERSTANDING_FAILURE", 0.7, [f.time_to_first_edit or 0],
                            "first action was an edit, with no exploration first"))

    if f.revisits >= 3:
        labels.append(Label("STEP_REPETITION", 0.65, [],
                            f"{f.revisits} repeated writes to the same files"))

    if f.n_failed_verifies >= 2 and f.n_model_calls < f.n_failed_verifies * 2:
        labels.append(Label("INCORRECT_VERIFICATION", 0.55, [],
                            "retried after failures without new investigation"))

    if f.stopped_reason == "budget":
        labels.append(Label("PREMATURE_TERMINATION", 0.8, [], "hit a budget cap"))
    if f.stopped_reason == "error":
        labels.append(Label("MODEL_FAILURE", 0.8, [], "run ended in an error"))

    if f.subagents > 0 and f.n_writes == 0:
        labels.append(Label("INFORMATION_WITHHELD", 0.4, [],
                            "subagents ran but nothing was written"))

    if not labels:
        labels.append(Label("UNDERSTANDING_FAILURE", 0.3, [],
                            "no specific signal; default attribution"))
    return labels


CALIBRATION = {
    "method": "rule-pass-v1",
    "measured_on": None,
    "step_level_accuracy": None,
    "note": "UNCALIBRATED. Run `af analyze calibrate` against a labelled corpus "
            "(Who&When / TRAIL / MAST-Data) before trusting these confidences. "
            "Uncalibrated labels are marked provisional and may not feed search.",
}


# ----------------------------------------------------------------- landscape


def build_landscape(bundles: list[Bundle], scores: dict[str, dict]) -> dict:
    """Pool rollouts of the same task into a shared decision landscape.

    Nodes are observable states (phase + action signature), not embeddings, so
    'trap region' means something checkable. Regions where most visits end in
    failure are traps; regions where most end in success are productive cores.
    """
    nodes: dict[str, dict] = defaultdict(
        lambda: {"visits": 0, "success": 0, "trials": set()})
    edges: Counter = Counter()

    for b in bundles:
        ok = bool((scores.get(b.trial_key) or {}).get("outcomes", {}).get("resolved"))
        prev = "START"
        nodes[prev]["visits"] += 1
        nodes[prev]["success"] += int(ok)
        for ev in b.events():
            if ev.type not in ("tool.call", "file.write", "context.compact"):
                continue
            sig = _state_signature(ev)
            n = nodes[sig]
            n["visits"] += 1
            n["success"] += int(ok)
            n["trials"].add(b.trial_key)
            edges[(prev, sig)] += 1
            prev = sig

    out_nodes = []
    for sig, n in nodes.items():
        rate = n["success"] / n["visits"] if n["visits"] else 0.0
        kind = "trap" if (n["visits"] >= 3 and rate < 0.25) else \
               "productive" if (n["visits"] >= 3 and rate > 0.7) else "neutral"
        out_nodes.append({"state": sig, "visits": n["visits"],
                          "success_rate": round(rate, 3), "kind": kind,
                          "n_trials": len(n["trials"])})
    out_nodes.sort(key=lambda d: -d["visits"])
    return {
        "nodes": out_nodes[:200],
        "edges": [{"from": a, "to": b, "n": c}
                  for (a, b), c in edges.most_common(200)],
        "traps": [n for n in out_nodes if n["kind"] == "trap"][:20],
        "productive": [n for n in out_nodes if n["kind"] == "productive"][:20],
    }


def _state_signature(ev) -> str:
    if ev.type == "file.write":
        return f"write:{ev.attrs.get('path')}"
    if ev.type == "context.compact":
        return "compact"
    cmd = str(ev.attrs.get("command", ""))
    head = cmd.split()[0] if cmd else ev.attrs.get("gen_ai.tool.name", "tool")
    phase = ev.attrs.get("phase", "?")
    return f"{phase}:{head}"


# ------------------------------------------------------------------ cluster


def cluster_failures(bundles: list[Bundle], scores: dict[str, dict]) -> dict:
    """Failure-signature clustering with drill-down. One failure is useful;
    thousands are the point."""
    counts: Counter = Counter()
    by_arch: dict[str, Counter] = defaultdict(Counter)
    by_task: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[str]] = defaultdict(list)

    for b in bundles:
        s = scores.get(b.trial_key)
        if s is None or s["outcomes"].get("resolved"):
            continue
        for lab in label_failure(b, s):
            counts[lab.code] += 1
            by_arch[b.arch_hash[:12]][lab.code] += 1
            by_task[b.manifest["task"]["id"]][lab.code] += 1
            if len(examples[lab.code]) < 5:
                examples[lab.code].append(b.trial_key)

    total = sum(counts.values()) or 1
    return {
        "n_failures": sum(1 for b in bundles
                          if not (scores.get(b.trial_key) or {})
                          .get("outcomes", {}).get("resolved", True)),
        "clusters": [
            {"code": code, "n": n, "share": round(n / total, 3),
             "description": TAXONOMY.get(code, ""), "examples": examples[code]}
            for code, n in counts.most_common()
        ],
        "by_architecture": {k: dict(v) for k, v in by_arch.items()},
        "by_task": {k: dict(v) for k, v in by_task.items()},
        "calibration": CALIBRATION,
    }


# ----------------------------------------------------------------- autopsy


def autopsy(b: Bundle, score: dict | None = None) -> dict:
    f = index_bundle(b)
    labels = label_failure(b, score)
    return {
        "trial_key": b.trial_key,
        "task": b.manifest["task"]["id"],
        "architecture": b.arch_hash[:12],
        "outcome": (score or {}).get("outcomes", {}),
        "flags": (score or {}).get("flags", []),
        "credibility": (score or {}).get("credibility"),
        "features": f.to_dict(),
        "diagnoses": [l.to_dict() for l in labels],
        "calibration": CALIBRATION,
        "created_at": now_iso(),
    }


# ------------------------------------------------------- findings registry


def make_finding(kind: str, label: str, evidence: list[dict],
                 confidence: float) -> dict:
    """A finding with no evidence pointers is rejected here, not later."""
    if not evidence:
        raise ValueError("a finding must carry evidence pointers")
    return {
        "id": hash_obj({"kind": kind, "label": label, "evidence": evidence})[:16],
        "kind": kind,
        "label": label,
        "evidence": evidence,
        "confidence": confidence,
        "calibration": CALIBRATION,
    }


def findings_from_clusters(clusters: dict, min_share: float = 0.15) -> list[dict]:
    """Turn a failure distribution into candidate findings for L7."""
    out = []
    for c in clusters.get("clusters", []):
        if c["share"] < min_share or c["n"] < 3:
            continue
        out.append(make_finding(
            kind="failure_cluster",
            label=c["code"],
            evidence=[{"trial_key": tk} for tk in c["examples"]],
            confidence=round(min(0.9, c["share"] + 0.2), 3),
        ))
    return out


# ---------------------------------------------- counterfactual replay (paid)


def replay_plan(b: Bundle, step: int, n: int = 8,
                intervention: str = "do_resample") -> dict:
    """Describe a counterfactual replay without running it.

    Interventions: do_resample, do_action, do_observation, do_context, do_policy.
    Reports an action-match rate rather than claiming reproducibility, because
    hosted inference is not deterministic even at temperature zero.
    """
    events = list(b.events())
    if step < 1 or step > len(events):
        raise IndexError(f"step {step} out of range 1..{len(events)}")
    ev = events[step - 1]
    return {
        "trial_key": b.trial_key,
        "step": step,
        "event": ev.to_dict(),
        "intervention": intervention,
        "replicates": n,
        "estimated_cost_usd": round(n * float(b.usage.get("cost_usd") or 0.02)
                                    * (1 - step / max(1, len(events))), 4),
        "note": "reproducibility is bounded: hosted inference is not "
                "deterministic at temperature 0 (batch-invariance). This "
                "reports an action-match rate, not exact replay.",
        "status": "planned",
    }
