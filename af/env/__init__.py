"""Environment layer: sandboxes.

One protocol, two implementations. Nothing above L1 knows which is in use.

The rules this layer is responsible for:
  - fixture is a pinned digest, never a tag
  - no gold patch on disk, no upstream git history
  - network deny-by-default
  - held-out tests injected only after the workspace is captured
  - every trial starts from a clean copy; no container reuse

A sandbox does not get to *claim* those rules; it declares, per axis, how
strongly it can enforce each one, and `enforcement()` is what lands in
integrity.json. A requested control that a sandbox cannot enforce is recorded as
unenforced rather than asserted as true. See `Enforcement`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from af.util import snapshot_tree


@dataclass
class ExecResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def to_dict(self) -> dict:
        return {
            "argv": self.argv,
            "returncode": self.returncode,
            "stdout": self.stdout[-8000:],
            "stderr": self.stderr[-8000:],
            "duration_s": round(self.duration_s, 3),
            "timed_out": self.timed_out,
        }


@dataclass
class Limits:
    wall_s: int = 1800
    cpu: float = 2.0
    mem_mb: int = 4096


@dataclass
class NetPolicy:
    mode: str = "deny"          # deny | allowlist | open
    allowlist: tuple = ()


# ------------------------------------------------------------- enforcement

# How strongly a control is actually backed, from strongest to weakest. This
# vocabulary is the whole point: "requested" and "enforced" are different facts
# and the evidence bundle records the second one.
KERNEL = "kernel"          # the OS refuses the operation
ADVISORY = "advisory"      # only cooperating software honours it
UNENFORCED = "unenforced"  # requested, nothing backs it
NOT_REQUESTED = "not_requested"

ENFORCEMENT_RANK = {NOT_REQUESTED: 0, UNENFORCED: 0, ADVISORY: 1, KERNEL: 2}


@dataclass(frozen=True)
class Enforcement:
    """What a sandbox can actually back, per axis.

    Modelled on a three-axis intent vocabulary rather than per-backend flags, so
    a control's strength is comparable across sandboxes and can be checked
    against an experiment's requirement before any money is spent.
    """

    filesystem: str
    network: str
    is_security_boundary: bool

    def to_dict(self) -> dict:
        return {
            "filesystem": self.filesystem,
            "network": self.network,
            "is_security_boundary": self.is_security_boundary,
        }

    def meets(self, required: str) -> bool:
        """Does every axis reach `required`? `none` requires nothing."""
        if required in (None, "", "none"):
            return True
        want = ENFORCEMENT_RANK.get(required)
        if want is None:
            raise ValueError(f"unknown enforcement level {required!r}")
        return all(ENFORCEMENT_RANK.get(axis, 0) >= want
                   for axis in (self.filesystem, self.network))


class ContainmentError(RuntimeError):
    """A path escaped the workspace root."""


def contained_path(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, refusing anything that escapes.

    Normalise to absolute, then require the workspace root to be a strict
    prefix. `put`/`get` take relative paths from callers that may be driven by
    agent output, so this is the only thing standing between a `../` and the
    rest of the filesystem.
    """
    root = Path(root).resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ContainmentError(f"path {rel!r} escapes workspace {root}")
    return target


@dataclass
class Handle:
    id: str
    workdir: Path
    kind: str
    meta: dict = field(default_factory=dict)


class Sandbox(Protocol):
    kind: str

    def enforcement(self, net: NetPolicy) -> Enforcement: ...
    def start(self, fixture: Path, limits: Limits, net: NetPolicy) -> Handle: ...
    def exec(self, h: Handle, argv: list[str], timeout: int = 120) -> ExecResult: ...
    def put(self, h: Handle, src: Path, dst: str) -> None: ...
    def get(self, h: Handle, src: str) -> bytes: ...
    def snapshot(self, h: Handle) -> dict[str, str]: ...
    def stop(self, h: Handle) -> None: ...


# ------------------------------------------------------------------- local


class LocalSandbox:
    """Process isolation only. Fast, no daemon, correct for trusted fixtures.

    This is NOT a security boundary and the system says so rather than
    pretending otherwise.
    """

    kind = "local"

    def __init__(self, base: Path | None = None):
        self.base = Path(base) if base else None

    def enforcement(self, net: NetPolicy) -> Enforcement:
        # Proxy env vars are advisory and nothing more: they stop a cooperating
        # HTTP client and do not stop a raw socket, a DNS lookup, ssh, or
        # `curl --noproxy`. Saying "advisory" is the difference between a record
        # that is useful and a record that is false.
        return Enforcement(
            filesystem=ADVISORY,   # a scratch dir the child is merely pointed at
            network=ADVISORY if net.mode == "deny" else NOT_REQUESTED,
            is_security_boundary=False,
        )

    def start(self, fixture: Path, limits: Limits, net: NetPolicy) -> Handle:
        parent = str(self.base) if self.base else None
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix="af-trial-", dir=parent))
        shutil.copytree(fixture, workdir / "repo", dirs_exist_ok=True)
        return Handle(id=workdir.name, workdir=workdir / "repo", kind=self.kind,
                      meta={"net": net.mode, "limits": limits.__dict__})

    def exec(self, h: Handle, argv: list[str], timeout: int = 120) -> ExecResult:
        import time

        t0 = time.time()
        # Minimal, explicit environment. A handful of OS variables must pass
        # through or the child cannot start: Windows needs SYSTEMROOT for
        # sockets and TEMP for tempfile; POSIX needs HOME and LANG.
        import os as _os

        passthrough = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
                       "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
                       "HOME", "LANG", "LC_ALL", "TZ")
        env = {k: _os.environ[k] for k in passthrough if k in _os.environ}
        env.update({
            "PATH": _os_path(),
            "PYTHONPATH": str(h.workdir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "NO_COLOR": "1",
            "AF_SANDBOX": "1",
        })
        if h.meta.get("net") == "deny":
            # Route every protocol at a closed port. `no_proxy` is deliberately
            # set EMPTY: `no_proxy="*"` means "bypass the proxy for all hosts",
            # which silently turned this block into a no-op and let trials reach
            # the network while their integrity record claimed otherwise.
            dead = "http://127.0.0.1:9"
            for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                        "http_proxy", "https_proxy", "all_proxy"):
                env[var] = dead
            env["no_proxy"] = ""
            env["NO_PROXY"] = ""
        try:
            p = subprocess.run(
                argv, cwd=str(h.workdir), capture_output=True, text=True,
                timeout=timeout, env=env,
            )
            return ExecResult(argv, p.returncode, p.stdout, p.stderr, time.time() - t0)
        except subprocess.TimeoutExpired as e:
            return ExecResult(argv, 124, e.stdout or "", e.stderr or "",
                              time.time() - t0, timed_out=True)
        except FileNotFoundError as e:
            return ExecResult(argv, 127, "", str(e), time.time() - t0)

    def put(self, h: Handle, src: Path, dst: str) -> None:
        target = contained_path(h.workdir, dst)
        target.parent.mkdir(parents=True, exist_ok=True)
        if Path(src).is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copy2(src, target)

    def get(self, h: Handle, src: str) -> bytes:
        return contained_path(h.workdir, src).read_bytes()

    def snapshot(self, h: Handle) -> dict[str, str]:
        return snapshot_tree(h.workdir)

    def stop(self, h: Handle) -> None:
        shutil.rmtree(h.workdir.parent, ignore_errors=True)


def _os_path() -> str:
    import os

    return os.environ.get("PATH", "")


# ------------------------------------------------------------------ docker


class DockerSandbox:
    """Container isolation. Same interface, real boundary."""

    kind = "docker"

    def __init__(self, image: str = "python:3.12-slim"):
        self.image = image

    def enforcement(self, net: NetPolicy) -> Enforcement:
        # `--network none` removes the interface; the bind mount is the only
        # writable path. Both are refused by the kernel, not by convention.
        return Enforcement(
            filesystem=KERNEL,
            network=KERNEL if net.mode == "deny" else NOT_REQUESTED,
            is_security_boundary=True,
        )

    def available(self) -> bool:
        try:
            return subprocess.run(["docker", "version"], capture_output=True).returncode == 0
        except FileNotFoundError:
            return False

    def start(self, fixture: Path, limits: Limits, net: NetPolicy) -> Handle:
        workdir = Path(tempfile.mkdtemp(prefix="af-trial-"))
        shutil.copytree(fixture, workdir / "repo", dirs_exist_ok=True)
        argv = [
            "docker", "run", "-d", "--rm",
            "--network", "none" if net.mode == "deny" else "bridge",
            "--cpus", str(limits.cpu), "--memory", f"{limits.mem_mb}m",
            "-v", f"{workdir / 'repo'}:/repo", "-w", "/repo",
            self.image, "sleep", str(limits.wall_s),
        ]
        p = subprocess.run(argv, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"docker start failed: {p.stderr.strip()}")
        cid = p.stdout.strip()
        return Handle(id=cid, workdir=workdir / "repo", kind=self.kind,
                      meta={"container": cid, "net": net.mode})

    def exec(self, h: Handle, argv: list[str], timeout: int = 120) -> ExecResult:
        import time

        t0 = time.time()
        full = ["docker", "exec", h.meta["container"], *argv]
        try:
            p = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
            return ExecResult(argv, p.returncode, p.stdout, p.stderr, time.time() - t0)
        except subprocess.TimeoutExpired as e:
            return ExecResult(argv, 124, e.stdout or "", e.stderr or "",
                              time.time() - t0, timed_out=True)

    def put(self, h: Handle, src: Path, dst: str) -> None:
        LocalSandbox().put(h, src, dst)

    def get(self, h: Handle, src: str) -> bytes:
        return contained_path(h.workdir, src).read_bytes()

    def snapshot(self, h: Handle) -> dict[str, str]:
        return snapshot_tree(h.workdir)

    def stop(self, h: Handle) -> None:
        subprocess.run(["docker", "kill", h.meta["container"]], capture_output=True)
        shutil.rmtree(h.workdir.parent, ignore_errors=True)


def make_sandbox(kind: str = "local", **kw) -> Sandbox:
    if kind == "docker":
        sb = DockerSandbox(**kw)
        if not sb.available():
            raise RuntimeError("docker requested but not available")
        return sb
    return LocalSandbox(**kw)


PYTHON = sys.executable or "python"
