"""Core specs. Everything here is immutable data whose identity is its hash."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from af import FOUNDRY_VERSION
from af.util import hash_obj, load_yaml


# ---------------------------------------------------------------- primitives


@dataclass(frozen=True)
class Budget:
    max_tokens: int = 400_000
    max_wall_s: int = 1800
    max_cost_usd: float = 4.0
    max_turns: int = 60

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict | None) -> "Budget":
        d = d or {}
        return Budget(
            max_tokens=int(d.get("max_tokens", 400_000)),
            max_wall_s=int(d.get("max_wall_s", 1800)),
            max_cost_usd=float(d.get("max_cost_usd", 4.0)),
            max_turns=int(d.get("max_turns", 60)),
        )


@dataclass(frozen=True)
class ModelBinding:
    provider: str = "scripted"
    id: str = "scripted-v1"
    effort: str = "medium"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ModelBinding":
        return ModelBinding(
            provider=d.get("provider", "scripted"),
            id=d.get("id", "scripted-v1"),
            effort=d.get("effort", "medium"),
        )


@dataclass(frozen=True)
class SlotBinding:
    component: str
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"component": self.component, "params": dict(self.params)}

    @staticmethod
    def from_dict(d: Any) -> "SlotBinding":
        if isinstance(d, str):
            return SlotBinding(component=d, params={})
        return SlotBinding(component=d["component"], params=dict(d.get("params") or {}))


# ------------------------------------------------------------- architecture


@dataclass(frozen=True)
class ArchitectureSpec:
    name: str
    backend: str
    models: dict[str, ModelBinding]
    slots: dict[str, SlotBinding]
    budget: Budget = field(default_factory=Budget)
    description: str = ""
    base_hash: str | None = None
    operators: tuple = ()

    # ---- identity

    def canonical(self) -> dict:
        """Only fields that change behaviour participate in the hash."""
        return {
            "backend": self.backend,
            "models": {k: v.to_dict() for k, v in sorted(self.models.items())},
            "slots": {k: v.to_dict() for k, v in sorted(self.slots.items())},
            "budget": self.budget.to_dict(),
        }

    @property
    def hash(self) -> str:
        return hash_obj(self.canonical())

    def to_dict(self) -> dict:
        d = {
            "apiVersion": "agentfoundry/v1",
            "kind": "Architecture",
            "name": self.name,
            "description": self.description,
            "runtime": {"backend": self.backend},
            "models": {k: v.to_dict() for k, v in self.models.items()},
            "budget": self.budget.to_dict(),
        }
        for slot, binding in self.slots.items():
            d[slot] = binding.to_dict()
        if self.base_hash:
            d["lineage"] = {"base": self.base_hash, "operators": list(self.operators)}
        return d

    @staticmethod
    def from_dict(d: dict) -> "ArchitectureSpec":
        from af.spec.registry import SLOTS

        slots = {}
        for slot in SLOTS:
            if slot in d and d[slot] is not None:
                slots[slot] = SlotBinding.from_dict(d[slot])
        models = {k: ModelBinding.from_dict(v) for k, v in (d.get("models") or {}).items()}
        if not models:
            models = {"default": ModelBinding()}
        lineage = d.get("lineage") or {}
        return ArchitectureSpec(
            name=d.get("name", "unnamed"),
            backend=(d.get("runtime") or {}).get("backend", "scripted"),
            models=models,
            slots=slots,
            budget=Budget.from_dict(d.get("budget")),
            description=d.get("description", ""),
            base_hash=lineage.get("base"),
            operators=tuple(lineage.get("operators") or ()),
        )

    @staticmethod
    def load(path: Path) -> "ArchitectureSpec":
        return ArchitectureSpec.from_dict(load_yaml(path))

    def with_slot(self, slot: str, binding: SlotBinding) -> "ArchitectureSpec":
        slots = dict(self.slots)
        slots[slot] = binding
        return ArchitectureSpec(
            name=self.name,
            backend=self.backend,
            models=dict(self.models),
            slots=slots,
            budget=self.budget,
            description=self.description,
            base_hash=self.base_hash,
            operators=self.operators,
        )


# --------------------------------------------------------------------- task


@dataclass(frozen=True)
class TaskSpec:
    id: str
    suite: str
    instruction: str
    difficulty: float
    dir: Path
    category: str = "bugfix"
    env: dict = field(default_factory=dict)

    @property
    def fixture_dir(self) -> Path:
        return self.dir / "fixture"

    @property
    def held_out_dir(self) -> Path:
        return self.dir / "held_out"

    @property
    def solution_path(self) -> Path:
        return self.dir / "solution.json"

    @property
    def cheat_path(self) -> Path:
        return self.dir / "cheat.json"

    def fixture_digest(self) -> str:
        from af.util import snapshot_tree

        return hash_obj(snapshot_tree(self.fixture_dir))

    def canonical(self) -> dict:
        return {
            "id": self.id,
            "instruction": self.instruction,
            "fixture_digest": self.fixture_digest(),
            "env": self.env,
        }

    @property
    def hash(self) -> str:
        return hash_obj(self.canonical())

    @staticmethod
    def load(task_dir: Path) -> "TaskSpec":
        task_dir = Path(task_dir)
        d = load_yaml(task_dir / "task.yaml")
        return TaskSpec(
            id=d["id"],
            suite=d.get("suite", "default"),
            instruction=d["instruction"],
            difficulty=float(d.get("difficulty", 0.5)),
            category=d.get("category", "bugfix"),
            env=d.get("env") or {},
            dir=task_dir,
        )


# -------------------------------------------------------------- environment


@dataclass(frozen=True)
class EnvironmentSpec:
    image: str = "local:python"
    net: str = "deny"
    allowlist: tuple = ()

    def to_dict(self) -> dict:
        return {"image": self.image, "net": self.net, "allowlist": list(self.allowlist)}

    @property
    def hash(self) -> str:
        return hash_obj(self.to_dict())


# --------------------------------------------------------------- trial spec


@dataclass(frozen=True)
class TrialSpec:
    task_id: str
    task_hash: str
    arch_hash: str
    env_hash: str
    models: dict
    budget: dict
    nonce: int
    foundry_version: str = FOUNDRY_VERSION

    def canonical(self) -> dict:
        return {
            "task_hash": self.task_hash,
            "arch_hash": self.arch_hash,
            "env_hash": self.env_hash,
            "models": self.models,
            "budget": self.budget,
            "foundry_version": self.foundry_version,
        }

    @property
    def hash(self) -> str:
        """Identity of the *intended* experiment. Replicates share this."""
        return hash_obj(self.canonical())

    @property
    def trial_key(self) -> str:
        """Identity of one concrete run. Replicate index folded in."""
        return hash_obj({"spec": self.hash, "nonce": self.nonce})

    def to_dict(self) -> dict:
        d = self.canonical()
        d.update({"task_id": self.task_id, "nonce": self.nonce, "trial_key": self.trial_key})
        return d


# ---------------------------------------------------------------- experiment


@dataclass(frozen=True)
class Arm:
    id: str
    architecture: str  # path or name
    overrides: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "architecture": self.architecture, "overrides": self.overrides}


@dataclass(frozen=True)
class ExperimentSpec:
    name: str
    arms: tuple[Arm, ...]
    suite: str
    replicates: int = 3
    design: str = "paired"
    primary: str = "resolved"
    secondary: tuple = ("cost_usd", "wall_s")
    cluster_on: str = "task"
    test: str = "cluster_bootstrap"
    equivalence_margin: float = 0.05
    correction: str = "benjamini_hochberg"
    stopping: str = "fixed"
    max_trials: int = 10_000
    budget: dict = field(default_factory=dict)
    hypothesis: str = ""

    def canonical(self) -> dict:
        return {
            "name": self.name,
            "design": self.design,
            "arms": [a.to_dict() for a in self.arms],
            "suite": self.suite,
            "replicates": self.replicates,
            "analysis": {
                "primary": self.primary,
                "secondary": list(self.secondary),
                "cluster_on": self.cluster_on,
                "test": self.test,
                "equivalence_margin": self.equivalence_margin,
                "correction": self.correction,
            },
            "stopping": {"rule": self.stopping, "max_trials": self.max_trials},
            "budget": self.budget,
            "hypothesis": self.hypothesis,
        }

    @property
    def hash(self) -> str:
        return hash_obj(self.canonical())

    @staticmethod
    def load(path: Path) -> "ExperimentSpec":
        d = load_yaml(path)
        analysis = d.get("analysis") or {}
        stopping = d.get("stopping") or {}
        design = d.get("design") or {}
        arms = tuple(
            Arm(
                id=a["id"],
                architecture=a["architecture"],
                overrides=a.get("overrides") or {},
            )
            for a in design.get("arms", d.get("arms", []))
        )
        return ExperimentSpec(
            name=d.get("name", Path(path).stem),
            arms=arms,
            suite=design.get("suite", d.get("suite", "demo")),
            replicates=int(design.get("replicates", d.get("replicates", 3))),
            design=design.get("type", "paired"),
            primary=analysis.get("primary", "resolved"),
            secondary=tuple(analysis.get("secondary", ["cost_usd", "wall_s"])),
            cluster_on=analysis.get("cluster_on", "task"),
            test=analysis.get("test", "cluster_bootstrap"),
            equivalence_margin=float(analysis.get("equivalence_margin", 0.05)),
            correction=analysis.get("correction", "benjamini_hochberg"),
            stopping=stopping.get("rule", "fixed"),
            max_trials=int(stopping.get("max_trials", 10_000)),
            budget=d.get("budget") or {},
            hypothesis=d.get("hypothesis", ""),
        )
