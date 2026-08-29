"""Evidence plane. Write-once bundles.

A bundle is the product. If everything else burned down, a directory of sealed
bundles would still be a valuable dataset - which is the check that the boundary
is in the right place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterator

from af.util import now_iso, read_json, write_atomic, write_json

SEAL = "SEALED"


# ---------------------------------------------------------------- event log
# The event record is part of the bundle format, so it lives with the format.
# It used to live in af.exec, which meant a bundle could not read its own
# events without importing the execution layer - a cycle that the layering
# check found and that only a deferred import was hiding.


@dataclass
class Event:
    seq: int
    ts: str
    type: str
    agent_id: str = "root"
    parent_agent_id: str | None = None
    attrs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "attrs": self.attrs,
        }


def read_events(path: Path) -> Iterator[Event]:
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            yield Event(d["seq"], d["ts"], d["type"], d.get("agent_id", "root"),
                        d.get("parent_agent_id"), d.get("attrs") or {})


class BundleWriter:
    def __init__(self, dir: Path):
        self.dir = Path(dir)

    def open(self, manifest: dict) -> BundleWriter:
        if (self.dir / SEAL).exists():
            raise RuntimeError(f"bundle already sealed: {self.dir}")
        self.dir.mkdir(parents=True, exist_ok=True)
        write_json(self.dir / "manifest.json", manifest)
        return self

    def write_text(self, rel: str, text: str) -> None:
        write_atomic(self.dir / rel, text)

    def write_json(self, rel: str, obj) -> None:
        write_json(self.dir / rel, obj)

    def seal(self, status: str = "ok", error: str | None = None) -> None:
        write_json(self.dir / SEAL, {"sealed_at": now_iso(), "status": status,
                                     "error": error})


@dataclass
class Bundle:
    dir: Path

    # ---- identity

    @property
    def trial_key(self) -> str:
        return self.dir.name

    @property
    def sealed(self) -> bool:
        return (self.dir / SEAL).exists()

    @property
    def seal(self) -> dict:
        return read_json(self.dir / SEAL) if self.sealed else {}

    @property
    def ok(self) -> bool:
        return self.sealed and self.seal.get("status") == "ok"

    # ---- contents

    @property
    def manifest(self) -> dict:
        return read_json(self.dir / "manifest.json")

    @property
    def usage(self) -> dict:
        p = self.dir / "usage.json"
        return read_json(p) if p.exists() else {}

    @property
    def integrity(self) -> dict:
        p = self.dir / "integrity.json"
        return read_json(p) if p.exists() else {}

    @property
    def usage_unknown(self) -> bool:
        """True when spend happened but could not be measured.

        Distinct from zero: `cost_usd: 0.0` is a measurement, `None` is the
        absence of one, and folding the second into the first is how budget
        accounting quietly stops being true.
        """
        u = self.usage
        return bool(u.get("usage_unknown")) or u.get("cost_usd") is None

    @property
    def patch(self) -> str:
        p = self.dir / "patch.diff"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    @property
    def workspace_after(self) -> dict:
        p = self.dir / "workspace_after.json"
        return read_json(p) if p.exists() else {}

    def events(self):
        p = self.dir / "events.jsonl"
        if not p.exists():
            return iter(())
        return read_events(p)

    # ---- derived artifacts live beside, never inside, the raw evidence

    def derived_path(self, name: str) -> Path:
        return self.dir / "derived" / name

    def write_derived(self, name: str, obj) -> None:
        write_json(self.derived_path(name), obj)

    def read_derived(self, name: str):
        p = self.derived_path(name)
        return read_json(p) if p.exists() else None

    # ---- convenience

    @property
    def task_id(self) -> str:
        return self.manifest["task"]["id"]

    @property
    def arch_hash(self) -> str:
        return self.manifest["architecture"]["arch_hash"]

    @property
    def trial_spec_hash(self) -> str:
        return self.manifest["trial"]["task_hash"] and self.manifest["trial"].get("trial_key", "")

    def summary(self) -> dict:
        m = self.manifest
        return {
            "trial_key": self.trial_key,
            "task": m["task"]["id"],
            "arch": m["architecture"]["arch_hash"][:12],
            "backend": m["backend"]["name"],
            "status": self.seal.get("status"),
            "wall_s": self.usage.get("wall_s"),
            "cost_usd": self.usage.get("cost_usd"),
        }


def iter_bundles(root: Path) -> Iterator[Bundle]:
    root = Path(root)
    if not root.exists():
        return
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "manifest.json").exists():
            yield Bundle(d)


def load_bundle(root: Path, trial_key: str) -> Bundle:
    d = Path(root) / trial_key
    if not (d / "manifest.json").exists():
        # allow prefix lookup
        for b in iter_bundles(root):
            if b.trial_key.startswith(trial_key):
                return b
        raise FileNotFoundError(f"no bundle {trial_key}")
    return Bundle(d)
