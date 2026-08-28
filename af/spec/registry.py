"""Slot/component registry and architecture resolution.

An architecture is slots bound to components. Components declare which backends
can honour them, and (for the scripted backend) how they shift capability and
cost. Resolution fails loudly on unsupported combinations - never silently
degrades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

SLOTS = ("action", "context", "memory", "control", "verification")


class ResolutionError(Exception):
    pass


@dataclass
class ComponentDef:
    slot: str
    name: str
    schema: dict[str, type] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    supports: tuple[str, ...] = ("*",)
    # simulator hints, ignored by real backends
    capability: float = 0.0
    cost_mult: float = 1.0
    turn_mult: float = 1.0
    tags: tuple[str, ...] = ()
    effect: Callable[[dict, dict], dict] | None = None

    def validate(self, params: dict) -> dict:
        merged = dict(self.defaults)
        merged.update(params or {})
        for key, val in merged.items():
            if key in self.schema and not isinstance(val, self.schema[key]):
                # tolerate int where float expected
                if self.schema[key] is float and isinstance(val, int):
                    merged[key] = float(val)
                    continue
                raise ResolutionError(
                    f"{self.slot}.{self.name}: param '{key}' expected "
                    f"{self.schema[key].__name__}, got {type(val).__name__}"
                )
        return merged


class Registry:
    def __init__(self) -> None:
        self._by_slot: dict[str, dict[str, ComponentDef]] = {s: {} for s in SLOTS}

    def register(self, c: ComponentDef) -> ComponentDef:
        if c.slot not in self._by_slot:
            raise ResolutionError(f"unknown slot {c.slot!r}")
        self._by_slot[c.slot][c.name] = c
        return c

    def get(self, slot: str, name: str) -> ComponentDef:
        try:
            return self._by_slot[slot][name]
        except KeyError:
            known = ", ".join(sorted(self._by_slot.get(slot, {}))) or "<none>"
            raise ResolutionError(
                f"no component {name!r} for slot {slot!r}. known: {known}"
            ) from None

    def list(self, slot: str | None = None) -> list[ComponentDef]:
        if slot:
            return sorted(self._by_slot[slot].values(), key=lambda c: c.name)
        out = []
        for s in SLOTS:
            out.extend(sorted(self._by_slot[s].values(), key=lambda c: c.name))
        return out


REGISTRY = Registry()


def _c(**kw) -> ComponentDef:
    return REGISTRY.register(ComponentDef(**kw))


# ------------------------------------------------------------------- action
# Evidence prior: the action interface is the single biggest lever, and it is
# non-monotonic - a few good tools beat many.

_c(slot="action", name="bash-only",
   schema={"stateful": bool, "timeout_s": int},
   defaults={"stateful": False, "timeout_s": 120},
   capability=0.55, cost_mult=1.0, tags=("minimal",))

_c(slot="action", name="repl",
   schema={"language": str, "timeout_s": int},
   defaults={"language": "python", "timeout_s": 120},
   capability=0.62, cost_mult=1.05, tags=("programmatic",))

_c(slot="action", name="typed-tools",
   schema={"tool_count": int},
   defaults={"tool_count": 8},
   capability=0.58, cost_mult=1.1, tags=("typed",))

_c(slot="action", name="many-tools",
   schema={"tool_count": int},
   defaults={"tool_count": 40},
   capability=0.40, cost_mult=1.35, tags=("typed", "bloated"))


# ------------------------------------------------------------------ context

_c(slot="context", name="full-history",
   schema={"on_overflow": str}, defaults={"on_overflow": "fail"},
   capability=0.0, cost_mult=1.25, tags=("baseline",))

_c(slot="context", name="compact-on-threshold",
   schema={"threshold_tokens": int, "keep_last": int},
   defaults={"threshold_tokens": 120_000, "keep_last": 12},
   capability=0.15, cost_mult=0.85, tags=("managed",))

_c(slot="context", name="rolling-summary",
   schema={"window": int}, defaults={"window": 20},
   capability=0.07, cost_mult=0.8, tags=("managed",))

_c(slot="context", name="progressive-disclosure",
   schema={"index_tokens": int}, defaults={"index_tokens": 800},
   capability=0.17, cost_mult=0.72, tags=("managed", "skills"))


# ------------------------------------------------------------------- memory

_c(slot="memory", name="none", capability=0.0, cost_mult=1.0)

_c(slot="memory", name="task-local",
   schema={"max_items": int}, defaults={"max_items": 32},
   capability=0.04, cost_mult=1.05)

_c(slot="memory", name="project-persistent",
   schema={"scope": str}, defaults={"scope": "repo"},
   capability=0.02, cost_mult=1.08, tags=("staleness-risk",))

_c(slot="memory", name="retrieved-trajectory",
   schema={"k": int}, defaults={"k": 5},
   capability=0.06, cost_mult=1.15)


# ------------------------------------------------------------------ control
# Topology lives here as one component among several, not as the organising
# principle of the whole spec.

_c(slot="control", name="single",
   schema={"max_turns": int}, defaults={"max_turns": 60},
   capability=0.0, cost_mult=1.0, turn_mult=1.0)

_c(slot="control", name="staged",
   schema={"stages": list, "max_returns": int},
   defaults={"stages": ["implement"], "max_returns": 1},
   capability=0.0, cost_mult=1.0, turn_mult=1.0)

_c(slot="control", name="debate",
   schema={"rounds": int, "agents": int},
   defaults={"rounds": 2, "agents": 3},
   capability=-0.05, cost_mult=2.1, turn_mult=1.4, tags=("multi-agent",))


def _staged_effect(params: dict, acc: dict) -> dict:
    """Stage list drives capability and cost, so the operator algebra can
    add/remove stages and have it mean something."""
    stages = [str(s) for s in params.get("stages", [])]
    cap, cost = 0.0, 1.0
    for s in stages:
        if s == "research":
            cap += 0.08
            cost *= 1.35
        elif s == "plan":
            cap -= 0.01
            cost *= 1.2
        elif s == "implement":
            cap += 0.0
            cost *= 1.0
        elif s == "review":
            cap += 0.07
            cost *= 1.45
        elif s == "test":
            cap += 0.05
            cost *= 1.15
    acc["capability"] += cap
    acc["cost_mult"] *= cost
    acc["turn_mult"] *= 1.0 + 0.25 * max(0, len(stages) - 1)
    acc["stages"] = stages
    return acc


REGISTRY.get("control", "staged").effect = _staged_effect


# ------------------------------------------------------------- verification

_c(slot="verification", name="none", capability=-0.18, cost_mult=0.9)

_c(slot="verification", name="project-tests",
   schema={"command": str, "run_before_finish": bool},
   defaults={"command": "python -m unittest discover -s tests", "run_before_finish": True},
   capability=0.20, cost_mult=1.12)

_c(slot="verification", name="tests-plus-review",
   schema={"command": str}, defaults={"command": "python -m unittest discover -s tests"},
   capability=0.24, cost_mult=1.30)


# --------------------------------------------------------------- resolution


@dataclass
class ResolvedArchitecture:
    arch_hash: str
    backend: str
    slots: dict[str, dict]          # slot -> {component, params}
    models: dict
    budget: dict
    capability: float               # simulator input
    cost_mult: float
    turn_mult: float
    tags: tuple[str, ...]
    tool_count: int
    stages: list[str]

    def to_dict(self) -> dict:
        return {
            "arch_hash": self.arch_hash,
            "backend": self.backend,
            "slots": self.slots,
            "models": self.models,
            "budget": self.budget,
            "derived": {
                "capability": round(self.capability, 4),
                "cost_mult": round(self.cost_mult, 4),
                "turn_mult": round(self.turn_mult, 4),
                "tool_count": self.tool_count,
                "stages": self.stages,
                "tags": list(self.tags),
            },
        }


def resolve(arch, backend_capabilities: set[str] | None = None) -> ResolvedArchitecture:
    """Fold slot bindings into a concrete backend configuration.

    Raises ResolutionError on any unsupported or malformed combination, before
    a single dollar is spent.
    """
    acc = {"capability": 0.0, "cost_mult": 1.0, "turn_mult": 1.0, "stages": []}
    slots_out: dict[str, dict] = {}
    tags: list[str] = []
    tool_count = 1

    for slot in SLOTS:
        binding = arch.slots.get(slot)
        if binding is None:
            if slot in ("action", "control", "verification"):
                raise ResolutionError(f"architecture {arch.name!r} is missing required slot {slot!r}")
            continue
        comp = REGISTRY.get(slot, binding.component)
        if "*" not in comp.supports and arch.backend not in comp.supports:
            raise ResolutionError(
                f"component {slot}.{comp.name} does not support backend {arch.backend!r}"
            )
        if backend_capabilities is not None:
            key = f"{slot}:{comp.name}"
            if key not in backend_capabilities and "*" not in backend_capabilities:
                raise ResolutionError(
                    f"backend {arch.backend!r} cannot honour {key}"
                )
        params = comp.validate(binding.params)
        slots_out[slot] = {"component": comp.name, "params": params}

        acc["capability"] += comp.capability
        acc["cost_mult"] *= comp.cost_mult
        acc["turn_mult"] *= comp.turn_mult
        tags.extend(comp.tags)
        if "tool_count" in params:
            tool_count = int(params["tool_count"])
        if comp.effect:
            acc = comp.effect(params, acc)

    # Tool-count penalty: few good tools beat many.
    if tool_count > 12:
        acc["capability"] -= 0.012 * (tool_count - 12)

    # Effort is a real axis and is frequently negative past a point.
    effort = (arch.models.get("default").effort if arch.models.get("default") else "medium")
    acc["capability"] += {"low": -0.04, "medium": 0.0, "high": 0.03, "xhigh": -0.02}.get(effort, 0.0)
    acc["cost_mult"] *= {"low": 0.7, "medium": 1.0, "high": 1.6, "xhigh": 2.4}.get(effort, 1.0)

    return ResolvedArchitecture(
        arch_hash=arch.hash,
        backend=arch.backend,
        slots=slots_out,
        models={k: v.to_dict() for k, v in arch.models.items()},
        budget=arch.budget.to_dict(),
        capability=acc["capability"],
        cost_mult=acc["cost_mult"],
        turn_mult=acc["turn_mult"],
        tags=tuple(sorted(set(tags))),
        tool_count=tool_count,
        stages=list(acc.get("stages") or []),
    )
