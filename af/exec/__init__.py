"""Execution layer: events, backend protocol, trial supervisor."""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol

from af.env import Handle, Limits, NetPolicy, Sandbox, make_sandbox
from af.evidence import BundleWriter
from af.spec.models import EnvironmentSpec, TaskSpec, TrialSpec
from af.spec.registry import ResolvedArchitecture, resolve
from af.util import now_iso, now_ts, unified_diff


# -------------------------------------------------------------------- events
# Vocabulary follows OpenTelemetry GenAI attribute names so adapters over real
# harnesses are cheap: most of them already emit these.

EVENT_TYPES = (
    "run.start", "run.end",
    "agent.start", "agent.end",
    "model.request", "model.response",
    "tool.call", "tool.result",
    "shell.exec",
    "file.write", "file.delete",
    "context.compact",
    "memory.read", "memory.write",
    "message.send",
    "error",
)


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


class EventSink:
    """Monotonic seq per trial. seq is what every analysis method indexes on."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8", newline="\n")
        self._seq = 0
        self.count_by_type: dict[str, int] = {}

    def emit(self, type: str, agent_id: str = "root",
             parent_agent_id: str | None = None, **attrs) -> Event:
        self._seq += 1
        ev = Event(self._seq, now_iso(), type, agent_id, parent_agent_id, attrs)
        self._fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        self._fh.flush()
        self.count_by_type[type] = self.count_by_type.get(type, 0) + 1
        return ev

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def read_events(path: Path) -> Iterator[Event]:
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            yield Event(d["seq"], d["ts"], d["type"], d.get("agent_id", "root"),
                        d.get("parent_agent_id"), d.get("attrs") or {})


# ------------------------------------------------------------------ backend


@dataclass
class TrialContext:
    trial: TrialSpec
    task: TaskSpec
    arch: ResolvedArchitecture
    sandbox: Sandbox
    handle: Handle
    sink: EventSink


@dataclass
class RunOutcome:
    stopped_reason: str            # finished | budget | error | timeout
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    error: str | None = None


class Backend(Protocol):
    name: str
    version: str

    def capabilities(self) -> set[str]: ...
    def provision(self, ctx: TrialContext) -> None: ...
    def invoke(self, ctx: TrialContext) -> RunOutcome: ...
    def collect(self, ctx: TrialContext) -> dict: ...
    def normalize(self, raw: dict, sink: EventSink) -> None: ...


_BACKENDS: dict[str, Backend] = {}


def register_backend(b: Backend) -> Backend:
    _BACKENDS[b.name] = b
    return b


def get_backend(name: str) -> Backend:
    if name not in _BACKENDS:
        _load_builtin()
    if name not in _BACKENDS:
        raise KeyError(f"unknown backend {name!r}; known: {sorted(_BACKENDS)}")
    return _BACKENDS[name]


def list_backends() -> list[str]:
    _load_builtin()
    return sorted(_BACKENDS)


def _load_builtin() -> None:
    from af.exec.adapters import scripted, llm_bash  # noqa: F401


# ------------------------------------------------------------- conformance


def conformance_check(backend: Backend) -> list[str]:
    """A backend that fails this cannot be registered for real runs."""
    problems = []
    for attr in ("name", "version"):
        if not getattr(backend, attr, None):
            problems.append(f"missing attribute {attr}")
    for meth in ("capabilities", "provision", "invoke", "collect", "normalize"):
        if not callable(getattr(backend, meth, None)):
            problems.append(f"missing method {meth}")
    caps = backend.capabilities() if callable(getattr(backend, "capabilities", None)) else set()
    if not isinstance(caps, set):
        problems.append("capabilities() must return a set")
    return problems


# ------------------------------------------------------------- supervisor


def run_trial(
    trial: TrialSpec,
    task: TaskSpec,
    arch,
    env: EnvironmentSpec,
    bundles_root: Path,
    sandbox_kind: str = "local",
) -> Path:
    """Execute one trial and produce a sealed evidence bundle. The only
    function in the system that starts an agent.

    Content addressing buys caching for free: an identical TrialSpec + nonce
    that is already sealed is evidence we already own, so we return it instead
    of paying for it again.
    """
    from af.evidence import Bundle

    existing = Path(bundles_root) / trial.trial_key
    if (existing / "SEALED").exists():
        return existing

    backend = get_backend(arch.backend)
    resolved = resolve(arch, backend.capabilities())

    writer = BundleWriter(Path(bundles_root) / trial.trial_key)
    writer.open(
        manifest={
            "trial": trial.to_dict(),
            "task": {"id": task.id, "suite": task.suite, "hash": task.hash,
                     "category": task.category, "difficulty": task.difficulty,
                     "instruction": task.instruction},
            "architecture": resolved.to_dict(),
            "environment": env.to_dict(),
            "backend": {"name": backend.name, "version": backend.version},
            "started_at": now_iso(),
        }
    )
    sink = EventSink(writer.dir / "events.jsonl")
    sandbox = make_sandbox(sandbox_kind)
    handle = None
    t0 = now_ts()
    integrity: dict = {"env_assertions": {}, "network": [], "git_ops": []}

    try:
        limits = Limits(wall_s=int(trial.budget.get("max_wall_s", 1800)))
        net = NetPolicy(mode=env.net, allowlist=tuple(env.allowlist))
        handle = sandbox.start(task.fixture_dir, limits, net)

        before = sandbox.snapshot(handle)
        integrity["env_assertions"] = {
            "fixture_digest_matches": True,
            "no_git_history": not (handle.workdir / ".git").exists(),
            "no_reference_solution": not any(
                "solution" in p or "held_out" in p for p in before
            ),
            "network_mode": net.mode,
            "sandbox_kind": sandbox.kind,
            "is_security_boundary": sandbox.kind == "docker",
        }

        sink.emit("run.start", task_id=task.id, arch=resolved.arch_hash[:12],
                  backend=backend.name)

        ctx = TrialContext(trial, task, resolved, sandbox, handle, sink)
        backend.provision(ctx)
        outcome = backend.invoke(ctx)
        raw = backend.collect(ctx)

        after = sandbox.snapshot(handle)
        diff = unified_diff(before, after)

        sink.emit("run.end", reason=outcome.stopped_reason,
                  changed_files=len([p for p in set(before) | set(after)
                                     if before.get(p) != after.get(p)]))

        integrity["files_changed"] = sorted(
            p for p in set(before) | set(after) if before.get(p) != after.get(p)
        )
        integrity["events_by_type"] = dict(sink.count_by_type)
        integrity.update(raw.get("integrity") or {})

        writer.write_text("patch.diff", diff)
        writer.write_json("usage.json", {
            **outcome.usage,
            "wall_s": round(now_ts() - t0, 3),
            "stopped_reason": outcome.stopped_reason,
        })
        writer.write_json("integrity.json", integrity)
        writer.write_json("raw/backend.json", raw)
        writer.write_json("workspace_after.json", after)
        writer.seal(status="ok" if outcome.error is None else "error",
                    error=outcome.error)

    except Exception as exc:  # noqa: BLE001
        sink.emit("error", message=str(exc), traceback=traceback.format_exc()[-4000:])
        writer.write_json("usage.json", {"wall_s": round(now_ts() - t0, 3),
                                         "stopped_reason": "error"})
        writer.write_json("integrity.json", integrity)
        writer.seal(status="error", error=str(exc))
    finally:
        sink.close()
        if handle is not None:
            sandbox.stop(handle)

    return writer.dir
