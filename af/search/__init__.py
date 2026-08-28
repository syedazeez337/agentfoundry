"""Search (L7). Optional and removable.

Proposers emit operator lists, never free-form code. The controller is a racing
loop (statistical elimination on a shared task subset, survivors promoted),
which is the mature answer to "configure a system when each evaluation is
expensive and noisy" - and it maps straight onto the existing scheduler.

Survivors go into a behaviour-keyed archive, not a leaderboard, so the system
illuminates which architectures win where instead of collapsing to one winner.
Search proposes; only experiments conclude.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from af.experiment import stats
from af.spec.models import ArchitectureSpec
from af.spec.operators import apply_operators


@dataclass
class Proposal:
    base_hash: str
    operators: list[dict]
    hypothesis: str
    expected_effect: str = ""
    source: str = "manual"

    def to_dict(self) -> dict:
        return {"base_hash": self.base_hash, "operators": self.operators,
                "hypothesis": self.hypothesis,
                "expected_effect": self.expected_effect, "source": self.source}


@dataclass
class SearchContext:
    base: ArchitectureSpec
    findings: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)


class Proposer(Protocol):
    name: str

    def propose(self, ctx: SearchContext) -> list[Proposal]: ...


# ---------------------------------------------------------------- proposers


class MutationProposer:
    """A deliberately balanced operator menu.

    Removal operators are listed first because the evidence says subtraction
    wins as often as addition, and a proposer biased toward growth will only
    ever discover growth.
    """

    name = "mutation"

    MENU = [
        # subtraction first, on purpose
        ({"op": "RemoveStage", "stage": "review"}, "drop the review stage"),
        ({"op": "RemoveStage", "stage": "plan"}, "drop the planning stage"),
        ({"op": "RemoveMemory"}, "drop memory"),
        ({"op": "SetComponent", "slot": "action", "component": "bash-only"},
         "collapse to a single tool"),
        ({"op": "SetEffort", "effort": "low"}, "reduce reasoning effort"),
        ({"op": "ScaleBudget", "factor": 0.6}, "cut the budget"),
        # addition
        ({"op": "AddStage", "stage": "research", "before": "implement"},
         "add a reconnaissance stage"),
        ({"op": "AddStage", "stage": "review", "after": "implement"},
         "add an adversarial review stage"),
        ({"op": "SetComponent", "slot": "context",
          "component": "compact-on-threshold"}, "manage context by compaction"),
        ({"op": "SetComponent", "slot": "context",
          "component": "progressive-disclosure"}, "load context on demand"),
        ({"op": "SetComponent", "slot": "verification",
          "component": "tests-plus-review"}, "strengthen verification"),
        ({"op": "SetEffort", "effort": "high"}, "raise reasoning effort"),
    ]

    def propose(self, ctx: SearchContext) -> list[Proposal]:
        return [
            Proposal(ctx.base.hash, [op], why, source=self.name)
            for op, why in self.MENU
        ]


class FindingDrivenProposer:
    """Turns L5 failure clusters into targeted interventions."""

    name = "finding-driven"

    MAP = {
        "UNDERSTANDING_FAILURE": (
            {"op": "AddStage", "stage": "research", "before": "implement"},
            "repository comprehension failures dominate; add reconnaissance"),
        "NO_VERIFICATION": (
            {"op": "SetComponent", "slot": "verification", "component": "project-tests"},
            "edits went unverified; require a test run"),
        "INCOMPLETE_VERIFICATION": (
            {"op": "SetComponent", "slot": "verification", "component": "tests-plus-review"},
            "visible-only passes; strengthen verification"),
        "STEP_REPETITION": (
            {"op": "SetComponent", "slot": "context", "component": "compact-on-threshold"},
            "repeated actions suggest context loss; manage context"),
        "PREMATURE_TERMINATION": (
            {"op": "ScaleBudget", "factor": 1.5}, "runs hit budget caps"),
        "REWARD_HACK": (
            {"op": "SetComponent", "slot": "verification", "component": "tests-plus-review"},
            "grader-satisfying shortcuts; harden verification"),
        "HISTORY_LOSS": (
            {"op": "SetComponent", "slot": "context", "component": "progressive-disclosure"},
            "context loss; load on demand instead of carrying everything"),
    }

    def propose(self, ctx: SearchContext) -> list[Proposal]:
        out = []
        for f in ctx.findings:
            entry = self.MAP.get(f.get("label"))
            if not entry:
                continue
            op, why = entry
            out.append(Proposal(ctx.base.hash, [op],
                                f"{why} (finding {f['id']}, conf {f.get('confidence')})",
                                source=self.name))
        return out


PROPOSERS = {p.name: p for p in (MutationProposer(), FindingDrivenProposer())}


# ------------------------------------------------------------------- racing


@dataclass
class Candidate:
    arch: ArchitectureSpec
    proposal: Proposal
    obs: list[tuple[str, bool]] = field(default_factory=list)
    cost: list[float] = field(default_factory=list)
    alive: bool = True
    eliminated_at: int | None = None

    @property
    def rate(self) -> float:
        rates = stats.per_task_rates(self.obs)
        return stats.mean(rates.values()) if rates else 0.0

    @property
    def mean_cost(self) -> float:
        return stats.mean(self.cost)


class Race:
    """Iterated racing with statistical elimination.

    Bad candidates die after a handful of tasks rather than the whole suite.
    Elimination is a paired test against the current leader, not a heuristic
    threshold.
    """

    def __init__(self, base: ArchitectureSpec, proposals: list[Proposal],
                 alpha: float = 0.10):
        self.base = base
        self.alpha = alpha
        self.candidates: list[Candidate] = []
        for p in proposals:
            try:
                arch = apply_operators(base, p.operators)
            except Exception:
                continue
            self.candidates.append(Candidate(arch, p))
        self.rounds: list[dict] = []

    def alive(self) -> list[Candidate]:
        return [c for c in self.candidates if c.alive]

    def record(self, cand: Candidate, task_id: str, ok: bool, cost: float) -> None:
        cand.obs.append((task_id, ok))
        cand.cost.append(cost)

    def eliminate(self, round_idx: int, min_obs: int = 5) -> list[Candidate]:
        """Kill candidates significantly worse than the current leader."""
        alive = [c for c in self.alive() if len(c.obs) >= min_obs]
        if len(alive) < 2:
            return []
        leader = max(alive, key=lambda c: c.rate)
        killed = []
        lead_rates = stats.per_task_rates(leader.obs)
        for c in alive:
            if c is leader:
                continue
            r = stats.paired_cluster_bootstrap(
                stats.per_task_rates(c.obs), lead_rates, iters=1500)
            if r.p_value < self.alpha and r.delta > 0:
                c.alive = False
                c.eliminated_at = round_idx
                killed.append(c)
        self.rounds.append({
            "round": round_idx,
            "leader": leader.arch.name,
            "leader_rate": round(leader.rate, 3),
            "eliminated": [k.arch.name for k in killed],
            "alive": len(self.alive()),
        })
        return killed

    def report(self) -> dict:
        rows = sorted(self.candidates, key=lambda c: -c.rate)
        return {
            "base": self.base.name,
            "rounds": self.rounds,
            "candidates": [
                {"name": c.arch.name, "arch_hash": c.arch.hash[:12],
                 "operators": c.proposal.operators,
                 "hypothesis": c.proposal.hypothesis,
                 "rate": round(c.rate, 3), "cost": round(c.mean_cost, 4),
                 "n": len(c.obs), "alive": c.alive,
                 "eliminated_at": c.eliminated_at}
                for c in rows
            ],
            "note": "racing selects candidates; it does not conclude. "
                    "Promotion requires a full experiment with a transfer gate.",
        }


# ------------------------------------------------------------------ archive


def niche_of(arch, resolved) -> str:
    """Behaviour descriptor, not score. Cheap/expensive x thorough/direct x
    verified/unverified."""
    cost = "cheap" if resolved.cost_mult < 1.2 else \
           "moderate" if resolved.cost_mult < 2.0 else "expensive"
    depth = "thorough" if len(resolved.stages) > 2 else "direct"
    ver = "verified" if resolved.slots.get("verification", {}).get("component") != "none" \
        else "unverified"
    return f"{cost}/{depth}/{ver}"
