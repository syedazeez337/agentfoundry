"""Hashing, canonical serialization, paths, small helpers."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical_json(obj: Any) -> str:
    """Deterministic serialization used for every hash in the system."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def short(h: str, n: int = 8) -> str:
    return h[:n]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", s.lower()).strip("-")


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    write_atomic(path, json.dumps(obj, indent=2, sort_keys=False) + "\n")


def seeded_rng(*parts: Any) -> random.Random:
    """A deterministic RNG derived from arbitrary identity parts."""
    seed = int(sha256_hex(canonical_json(list(parts)))[:16], 16)
    return random.Random(seed)


def project_root(start: Path | None = None) -> Path:
    """Find the AgentFoundry working root (dir containing .agentfoundry/ or cwd)."""
    cur = (start or Path.cwd()).resolve()
    for cand in [cur, *cur.parents]:
        if (cand / ".agentfoundry").exists():
            return cand
    return cur


class Paths:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.state = self.root / ".agentfoundry"
        self.db = self.state / "control.db"
        self.bundles = self.state / "bundles"
        self.corpora = self.state / "corpora"
        self.logs = self.state / "logs"
        self.architectures = self.root / "architectures"
        self.tasks = self.root / "tasks"
        self.experiments = self.root / "experiments"

    def ensure(self) -> "Paths":
        for p in (
            self.state,
            self.bundles,
            self.corpora,
            self.logs,
            self.architectures,
            self.tasks,
            self.experiments,
        ):
            p.mkdir(parents=True, exist_ok=True)
        return self


def load_yaml(path: Path) -> Any:
    import yaml

    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def dump_yaml(obj: Any) -> str:
    import yaml

    return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False)


def unified_diff(before: dict[str, str], after: dict[str, str]) -> str:
    """Unified diff between two {relpath: content} snapshots."""
    import difflib

    out: list[str] = []
    for path in sorted(set(before) | set(after)):
        a = before.get(path, "")
        b = after.get(path, "")
        if a == b:
            continue
        out.extend(
            difflib.unified_diff(
                a.splitlines(keepends=True),
                b.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                n=3,
            )
        )
    return "".join(out)


def snapshot_tree(root: Path, skip: tuple[str, ...] = ("__pycache__", ".git", ".pytest_cache")) -> dict[str, str]:
    """Read a directory tree into {relpath: text}. Binary files are skipped."""
    snap: dict[str, str] = {}
    root = Path(root)
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part in skip for part in rel.split("/")):
            continue
        try:
            snap[rel] = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return snap
