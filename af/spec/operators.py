"""Operator algebra. An architecture is `base + [operators]`.

Operators are total functions ArchitectureSpec -> ArchitectureSpec. They are the
diff format, the changelog, the lineage edge, and the only thing a search
proposer is allowed to emit.

Removal operators are first class on purpose: the evidence says subtraction wins
as often as addition, and a system whose operators only grow architectures will
only ever discover growth.
"""

from __future__ import annotations

from dataclasses import replace
from collections.abc import Callable

from af.spec.models import ArchitectureSpec, Budget, ModelBinding, SlotBinding

OperatorFn = Callable[[ArchitectureSpec, dict], ArchitectureSpec]
OPERATORS: dict[str, OperatorFn] = {}


def operator(name: str) -> Callable[[OperatorFn], OperatorFn]:
    def deco(fn: OperatorFn) -> OperatorFn:
        OPERATORS[name] = fn
        return fn

    return deco


def _rename(arch: ArchitectureSpec, suffix: str) -> str:
    base = arch.name.split("+")[0]
    return f"{base}+{suffix}"


# ------------------------------------------------------------------ set/get


@operator("SetComponent")
def set_component(arch: ArchitectureSpec, args: dict) -> ArchitectureSpec:
    slot = args["slot"]
    binding = SlotBinding(component=args["component"], params=dict(args.get("params") or {}))
    out = arch.with_slot(slot, binding)
    return replace(out, name=_rename(out, f"{slot}={args['component']}"))


@operator("SetParam")
def set_param(arch: ArchitectureSpec, args: dict) -> ArchitectureSpec:
    """path is 'slot.params.key' or 'budget.key'."""
    path = args["path"].split(".")
    value = args["value"]
    if path[0] == "budget":
        b = arch.budget.to_dict()
        b[path[1]] = value
        return replace(arch, budget=Budget.from_dict(b))
    slot = path[0]
    key = path[-1]
    binding = arch.slots.get(slot)
    if binding is None:
        return arch
    params = dict(binding.params)
    params[key] = value
    return arch.with_slot(slot, SlotBinding(binding.component, params))


@operator("SetModel")
def set_model(arch: ArchitectureSpec, args: dict) -> ArchitectureSpec:
    models = dict(arch.models)
    role = args.get("role", "default")
    cur = models.get(role) or models.get("default") or ModelBinding()
    models[role] = ModelBinding(
        provider=args.get("provider", cur.provider),
        id=args.get("id", cur.id),
        effort=args.get("effort", cur.effort),
    )
    return replace(arch, models=models)


@operator("SetEffort")
def set_effort(arch: ArchitectureSpec, args: dict) -> ArchitectureSpec:
    out = set_model(arch, {"role": args.get("role", "default"), "effort": args["effort"]})
    return replace(out, name=_rename(out, f"effort={args['effort']}"))


# ---------------------------------------------------------------- structure


@operator("AddStage")
def add_stage(arch: ArchitectureSpec, args: dict) -> ArchitectureSpec:
    stage = args["stage"]
    binding = arch.slots.get("control") or SlotBinding("single", {})
    stages = list(binding.params.get("stages") or ["implement"])
    if stage in stages:
        return arch
    before = args.get("before")
    after = args.get("after")
    if before and before in stages:
        stages.insert(stages.index(before), stage)
    elif after and after in stages:
        stages.insert(stages.index(after) + 1, stage)
    else:
        stages.append(stage)
    params = dict(binding.params)
    params["stages"] = stages
    out = arch.with_slot("control", SlotBinding("staged", params))
    return replace(out, name=_rename(out, stage))


@operator("RemoveStage")
def remove_stage(arch: ArchitectureSpec, args: dict) -> ArchitectureSpec:
    stage = args["stage"]
    binding = arch.slots.get("control")
    if binding is None:
        return arch
    stages = [s for s in (binding.params.get("stages") or []) if s != stage]
    params = dict(binding.params)
    params["stages"] = stages or ["implement"]
    component = "staged" if len(params["stages"]) > 1 else "single"
    if component == "single":
        params = {"max_turns": int(arch.budget.max_turns)}
    out = arch.with_slot("control", SlotBinding(component, params))
    return replace(out, name=_rename(out, f"no-{stage}"))


@operator("RemoveTool")
def remove_tool(arch: ArchitectureSpec, args: dict) -> ArchitectureSpec:
    binding = arch.slots.get("action")
    if binding is None:
        return arch
    params = dict(binding.params)
    if "tool_count" in params:
        params["tool_count"] = max(1, int(params["tool_count"]) - int(args.get("n", 1)))
    return arch.with_slot("action", SlotBinding(binding.component, params))


@operator("RemoveMemory")
def remove_memory(arch: ArchitectureSpec, args: dict) -> ArchitectureSpec:
    out = arch.with_slot("memory", SlotBinding("none", {}))
    return replace(out, name=_rename(out, "no-memory"))


@operator("ScaleBudget")
def scale_budget(arch: ArchitectureSpec, args: dict) -> ArchitectureSpec:
    f = float(args["factor"])
    b = arch.budget
    out = replace(
        arch,
        budget=Budget(
            max_tokens=int(b.max_tokens * f),
            max_wall_s=int(b.max_wall_s * f),
            max_cost_usd=round(b.max_cost_usd * f, 4),
            max_turns=int(b.max_turns * f),
        ),
    )
    return replace(out, name=_rename(out, f"budget-x{f:g}"))


@operator("Rename")
def rename(arch: ArchitectureSpec, args: dict) -> ArchitectureSpec:
    return replace(arch, name=args["name"])


# ------------------------------------------------------------------- apply


def apply_operators(arch: ArchitectureSpec, ops: list[dict]) -> ArchitectureSpec:
    """Apply an ordered operator list, recording lineage."""
    base_hash = arch.hash
    out = arch
    for op in ops:
        name = op["op"]
        if name not in OPERATORS:
            raise KeyError(f"unknown operator {name!r}; known: {sorted(OPERATORS)}")
        args = {k: v for k, v in op.items() if k != "op"}
        out = OPERATORS[name](out, args)
    return replace(out, base_hash=base_hash, operators=tuple(ops))


def diff_architectures(a: ArchitectureSpec, b: ArchitectureSpec) -> list[str]:
    """Human-readable diff, slot by slot."""
    lines: list[str] = []
    if a.backend != b.backend:
        lines.append(f"runtime.backend: {a.backend} -> {b.backend}")
    for role in sorted(set(a.models) | set(b.models)):
        ma, mb = a.models.get(role), b.models.get(role)
        if ma != mb:
            lines.append(f"models.{role}: {ma} -> {mb}")
    for slot in sorted(set(a.slots) | set(b.slots)):
        sa, sb = a.slots.get(slot), b.slots.get(slot)
        if sa is None:
            lines.append(f"{slot}: <absent> -> {sb.component} {sb.params}")
        elif sb is None:
            lines.append(f"{slot}: {sa.component} -> <absent>")
        elif sa.component != sb.component:
            lines.append(f"{slot}: {sa.component} -> {sb.component} {sb.params}")
        elif sa.params != sb.params:
            for k in sorted(set(sa.params) | set(sb.params)):
                if sa.params.get(k) != sb.params.get(k):
                    lines.append(f"{slot}.params.{k}: {sa.params.get(k)!r} -> {sb.params.get(k)!r}")
    if a.budget != b.budget:
        for k in a.budget.to_dict():
            va, vb = getattr(a.budget, k), getattr(b.budget, k)
            if va != vb:
                lines.append(f"budget.{k}: {va} -> {vb}")
    return lines or ["(identical)"]
