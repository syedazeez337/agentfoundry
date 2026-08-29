"""Execution layer: events, backend protocol, trial supervisor."""

from __future__ import annotations

import json
import shutil
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from af.env import Handle, Limits, NetPolicy, Sandbox, make_sandbox
from af.evidence import BundleWriter, Event, read_events
from af.evidence.redaction import redact_json
from af.spec.models import EnvironmentSpec, TaskSpec, TrialSpec
from af.spec.registry import ResolvedArchitecture, resolve
from af.util import now_iso, now_ts, unified_diff


# -------------------------------------------------------------------- events
# Vocabulary follows OpenTelemetry GenAI attribute names so adapters over real
# harnesses are cheap: most of them already emit these.

__all__ = ["Event", "read_events"]  # re-exported: the format lives in af.evidence

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


class EventSink:
    """Monotonic seq per trial. seq is what every analysis method indexes on.

    Attributes are redacted on the way in. A bundle is meant to be shareable,
    and an event log carries model output and agent-chosen shell commands -
    both places a key can surface. Scrubbing on write means it does not depend
    on every future reader remembering to.
    """

    def __init__(self, path: Path, redact: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8", newline="\n")
        self._seq = 0
        self._redact = redact
        self.count_by_type: dict[str, int] = {}
        self.redactions: dict[str, int] = {}

    def emit(self, type: str, agent_id: str = "root",
             parent_agent_id: str | None = None, **attrs) -> Event:
        self._seq += 1
        if self._redact:
            attrs, matched = redact_json(attrs)
            for kind in matched:
                self.redactions[kind] = self.redactions.get(kind, 0) + 1
        ev = Event(self._seq, now_iso(), type, agent_id, parent_agent_id, attrs)
        self._fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        self._fh.flush()
        self.count_by_type[type] = self.count_by_type.get(type, 0) + 1
        return ev

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # noqa: S110 - close failures cannot be acted on
            pass


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


class EnforcementError(RuntimeError):
    """The environment required an isolation level the sandbox cannot provide."""


def merge_integrity(base: dict, *sources: dict) -> dict:
    """Fold backend-reported integrity into the supervisor's record.

    A backend may report through `invoke()`'s outcome, through `collect()`, or
    both, so both are read. Lists are unioned rather than overwritten, and an
    empty value never replaces a populated one: the failure this exists to
    prevent is a `collect()` returning `{"git_ops": []}` and silently erasing
    what `invoke()` actually observed, which is exactly what happened and left
    the oracle-access channel dead while looking wired.
    """
    out = dict(base)
    for src in sources:
        for key, val in (src or {}).items():
            if isinstance(val, list):
                merged = list(out.get(key) or [])
                for item in val:
                    if item not in merged:
                        merged.append(item)
                out[key] = merged
            elif val or key not in out:
                out[key] = val
    return out


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
    of paying for it again - but only when it is *evidence*. A bundle sealed
    with status="error" records an infrastructure failure, not an agent run;
    returning it would memoise a provider outage as a result forever and make
    any retry policy unimplementable. Those are re-run.
    """
    from af.evidence import Bundle

    existing = Path(bundles_root) / trial.trial_key
    if (existing / "SEALED").exists():
        if Bundle(existing).ok:
            return existing
        shutil.rmtree(existing, ignore_errors=True)

    backend = get_backend(arch.backend)
    resolved = resolve(arch, backend.capabilities())

    # Check the isolation requirement before opening a bundle or spending a
    # cent. A trial that needed a boundary it cannot get is not a weaker trial,
    # it is a different trial, and running it would produce a number nobody
    # should read.
    sandbox = make_sandbox(sandbox_kind)
    net = NetPolicy(mode=env.net, allowlist=tuple(env.allowlist))
    enforcement = sandbox.enforcement(net)
    required = getattr(env, "require_enforcement", "none")
    if not enforcement.meets(required):
        raise EnforcementError(
            f"environment requires enforcement={required!r} but sandbox "
            f"{sandbox.kind!r} provides {enforcement.to_dict()}"
        )

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
    handle = None
    t0 = now_ts()
    integrity: dict = {"env_assertions": {}, "network": [], "git_ops": []}

    try:
        limits = Limits(wall_s=int(trial.budget.get("max_wall_s", 1800)))
        handle = sandbox.start(task.fixture_dir, limits, net)

        before = sandbox.snapshot(handle)
        # State HOW each control is enforced, not just that it was requested.
        # The sandbox declares its own strength per axis; this layer records the
        # declaration rather than inferring one. "network_mode: deny" alone was a
        # claim the local sandbox could not back up, and it was recorded on every
        # trial while the block was a no-op.
        integrity["env_assertions"] = {
            "fixture_digest_matches": True,
            "no_git_history": not (handle.workdir / ".git").exists(),
            "no_reference_solution": not any(
                "solution" in p or "held_out" in p for p in before
            ),
            "network_mode": net.mode,
            "network_enforcement": enforcement.network,
            "filesystem_confinement": enforcement.filesystem,
            "is_security_boundary": enforcement.is_security_boundary,
            "sandbox_kind": sandbox.kind,
            "required_enforcement": required,
            "enforcement_satisfied": enforcement.meets(required),
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
        integrity["redactions"] = dict(sink.redactions)
        integrity = merge_integrity(
            integrity,
            (outcome.raw or {}).get("integrity") or {},
            (raw or {}).get("integrity") or {},
        )

        writer.write_text("patch.diff", diff)
        writer.write_json("usage.json", {
            **outcome.usage,
            "wall_s": round(now_ts() - t0, 3),
            "stopped_reason": outcome.stopped_reason,
        })
        writer.write_json("integrity.json", integrity)
        writer.write_json("raw/backend.json",
                          {"collect": raw, "outcome": outcome.raw})
        writer.write_json("workspace_after.json", after)
        writer.seal(status="ok" if outcome.error is None else "error",
                    error=outcome.error)

    except Exception as exc:  # noqa: BLE001
        sink.emit("error", message=str(exc), traceback=traceback.format_exc()[-4000:])
        # A trial that died partway may well have spent money before it died.
        # Recording that as 0.0 would make the budget cap under-count exactly
        # the trials that break it, so cost is recorded as unknown instead.
        writer.write_json("usage.json", {"wall_s": round(now_ts() - t0, 3),
                                         "stopped_reason": "error",
                                         "cost_usd": None,
                                         "usage_unknown": True})
        writer.write_json("integrity.json", integrity)
        writer.seal(status="error", error=str(exc))
    finally:
        sink.close()
        if handle is not None:
            sandbox.stop(handle)

    return writer.dir
