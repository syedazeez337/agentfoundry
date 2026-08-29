"""Dependency and environment audit.

Four tiers, because the answers differ:

  core      - needed to run anything at all
  simulator - needed for `af demo run` (no keys, no containers)
  real      - needed to drive an actual coding agent on a real repository
  optional  - capabilities that widen what is possible, never blockers

Docker sits in `optional` on purpose. It provides kernel-level isolation and
access to prebuilt benchmark images, but a real repository can be cloned at a
pinned commit and its test suite reconstructed with `uv` in about nine seconds.
Reporting it as a hard requirement was wrong and this module used to do it.

A check reports PASS / WARN / FAIL / SKIP / INFO plus the exact remedy. INFO is
never a blocker. Nothing here mutates the system.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PASS, WARN, FAIL, SKIP, INFO = "PASS", "WARN", "FAIL", "SKIP", "INFO"
CORE, SIM, REAL, OPT = "core", "simulator", "real", "optional"


@dataclass
class Check:
    name: str
    tier: str
    status: str
    detail: str = ""
    remedy: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "tier": self.tier, "status": self.status,
                "detail": self.detail, "remedy": self.remedy, **self.extra}


def _run(argv: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as exc:
        return 1, str(exc)


# --------------------------------------------------------------------- core


def check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    return Check(
        "python >= 3.11", CORE, PASS if ok else FAIL,
        f"{platform.python_version()} at {sys.executable}",
        "" if ok else "install Python 3.11+ (uv python install 3.12)",
    )


def check_pyyaml() -> Check:
    try:
        import yaml  # noqa: F401
        return Check("pyyaml", CORE, PASS, "importable")
    except ImportError:
        return Check("pyyaml", CORE, FAIL, "missing", "uv pip install pyyaml")


def check_stdlib() -> Check:
    """Everything the core depends on beyond pyyaml is stdlib. Prove it."""
    mods = ["sqlite3", "hashlib", "json", "difflib", "shutil", "subprocess",
            "tempfile", "http.server", "urllib.request", "concurrent.futures",
            "statistics", "unittest"]
    missing = []
    for m in mods:
        try:
            __import__(m)
        except ImportError:
            missing.append(m)
    return Check(
        "stdlib modules", CORE, PASS if not missing else FAIL,
        f"{len(mods) - len(missing)}/{len(mods)} available"
        + (f"; missing {missing}" if missing else ""),
        "reinstall Python with full stdlib" if missing else "",
    )


def check_sqlite() -> Check:
    import sqlite3

    try:
        c = sqlite3.connect(":memory:")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE t(a)")
        c.execute("INSERT INTO t VALUES (1)")
        c.close()
        return Check("sqlite3 writable", CORE, PASS,
                     f"sqlite {sqlite3.sqlite_version}")
    except Exception as exc:  # noqa: BLE001
        return Check("sqlite3 writable", CORE, FAIL, str(exc))


def check_workspace(root: Path) -> Check:
    """Probe writability without scaffolding the project.

    This used to call `Paths.ensure()`, which also creates `architectures/`,
    `tasks/` and `experiments/` in the project root. That is `af init`'s job.
    An audit that silently scaffolds directories is a side effect nobody asked
    for, and it makes the audit non-repeatable on a fresh tree.
    """
    from af.util import Paths

    p = Paths(root)
    try:
        p.state.mkdir(parents=True, exist_ok=True)
        probe = p.state / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return Check("workspace writable", CORE, PASS, str(p.state))
    except Exception as exc:  # noqa: BLE001
        return Check("workspace writable", CORE, FAIL, str(exc),
                     "check permissions on the project directory")


# ---------------------------------------------------------------- simulator


def check_local_sandbox(root: Path) -> Check:
    """End-to-end: start a sandbox, execute, snapshot, stop."""
    import tempfile

    from af.env import Limits, NetPolicy, PYTHON, make_sandbox

    fixture = Path(tempfile.mkdtemp(prefix="af-doctor-"))
    (fixture / "mod.py").write_text("VALUE = 41\n", encoding="utf-8")
    sb = make_sandbox("local")
    h = None
    try:
        h = sb.start(fixture, Limits(wall_s=60), NetPolicy("deny"))
        r = sb.exec(h, [PYTHON, "-c", "import mod; print(mod.VALUE + 1)"], timeout=30)
        snap = sb.snapshot(h)
        ok = r.ok and r.stdout.strip() == "42" and "mod.py" in snap
        return Check(
            "local sandbox", SIM, PASS if ok else FAIL,
            f"exec rc={r.returncode} out={r.stdout.strip()!r} files={len(snap)}",
            "" if ok else "subprocess execution or temp dir is blocked",
        )
    except Exception as exc:  # noqa: BLE001
        return Check("local sandbox", SIM, FAIL, str(exc))
    finally:
        if h is not None:
            sb.stop(h)
        shutil.rmtree(fixture, ignore_errors=True)


def check_unittest_runner() -> Check:
    """The graders shell out to unittest discover. Prove it works here."""
    import tempfile

    from af.env import PYTHON

    d = Path(tempfile.mkdtemp(prefix="af-doctor-ut-"))
    (d / "tests").mkdir()
    (d / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (d / "tests" / "test_x.py").write_text(
        "import unittest\n\nclass T(unittest.TestCase):\n"
        "    def test_a(self):\n        self.assertEqual(1 + 1, 2)\n",
        encoding="utf-8")
    try:
        p = subprocess.run(
            [PYTHON, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
            cwd=str(d), capture_output=True, text=True, timeout=60)
        ok = p.returncode == 0
        return Check("unittest discover", SIM, PASS if ok else FAIL,
                     f"rc={p.returncode}")
    except Exception as exc:  # noqa: BLE001
        return Check("unittest discover", SIM, FAIL, str(exc))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_backends() -> Check:
    from af.exec import conformance_check, get_backend, list_backends

    names = list_backends()
    bad = {}
    for n in names:
        problems = conformance_check(get_backend(n))
        if problems:
            bad[n] = problems
    return Check("backend conformance", SIM, PASS if not bad else FAIL,
                 f"{len(names)} registered: {', '.join(names)}"
                 + (f"; failures {bad}" if bad else ""))


def check_resources() -> Check:
    cores = os.cpu_count() or 1
    free_gb = shutil.disk_usage(Path.cwd()).free / 1e9
    ram_gb = _total_ram_gb()
    notes = [f"cores={cores}", f"ram={ram_gb:.1f}GB" if ram_gb else "ram=?",
             f"disk_free={free_gb:.1f}GB"]
    status = PASS
    remedy = ""
    if free_gb < 5:
        status, remedy = FAIL, "free up disk; bundles and images need room"
    elif free_gb < 40:
        status, remedy = WARN, "40GB+ recommended before pulling task images"
    return Check("host resources", SIM, status, "  ".join(notes), remedy,
                 {"cores": cores, "disk_free_gb": round(free_gb, 1),
                  "suggested_workers": max(1, min(int(cores * 0.75), 24))})


def _total_ram_gb() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MS()
            st.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return st.ullTotalPhys / 1e9
        return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / 1e9
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------- real


def check_git() -> Check:
    path = shutil.which("git")
    if not path:
        return Check("git", REAL, FAIL, "not found",
                     "install git - needed to clone task fixtures, extract "
                     "patches, and truncate history so the gold patch is not "
                     "on disk")
    rc, out = _run(["git", "--version"])
    return Check("git", REAL, PASS if rc == 0 else FAIL, out, extra={"path": path})


def check_docker() -> Check:
    """Docker is an option, not a requirement.

    Measured: a real repository can be cloned at a pinned commit and its test
    suite reconstructed with `uv` in about nine seconds and sixteen megabytes.
    Containers solve dependency reconstruction across heterogeneous repos, and
    they give kernel-level isolation. Neither is required to run AgentFoundry.
    """
    path = shutil.which("docker")
    if not path:
        return Check(
            "docker CLI", OPT, INFO, "not installed",
            "optional. Enables --sandbox docker (kernel-level network and "
            "filesystem isolation) and prebuilt benchmark images. Without it "
            "the local sandbox is used, which is a lifecycle boundary and not "
            "a security one.")
    rc, out = _run(["docker", "--version"])
    return Check("docker CLI", OPT, PASS if rc == 0 else INFO, out,
                 extra={"path": path})


def check_docker_daemon() -> Check:
    if not shutil.which("docker"):
        return Check("docker daemon", OPT, SKIP, "docker CLI absent")
    rc, out = _run(["docker", "info", "--format",
                    "{{.ServerVersion}}|{{.OSType}}|{{.Architecture}}|{{.NCPU}}|{{.MemTotal}}"],
                   timeout=45)
    if rc != 0:
        return Check("docker daemon", OPT, INFO,
                     out.splitlines()[0] if out else "unreachable",
                     "start Docker Desktop to use --sandbox docker")
    parts = out.strip().split("|")
    detail = out.strip()
    extra = {}
    status = PASS
    remedy = ""
    if len(parts) >= 5:
        ver, ostype, arch, ncpu, mem = parts[:5]
        mem_gb = int(mem) / 1e9 if mem.isdigit() else 0
        extra = {"server": ver, "os": ostype, "arch": arch,
                 "cpus": ncpu, "mem_gb": round(mem_gb, 1)}
        detail = f"v{ver} os={ostype} arch={arch} cpus={ncpu} mem={mem_gb:.1f}GB"
        if arch not in ("x86_64", "amd64"):
            status = WARN
            remedy = ("task images are amd64 only; this host will need "
                      "emulation and will be slow")
        elif mem_gb and mem_gb < 8:
            status = WARN
            remedy = "allocate 16GB to Docker Desktop for real task images"
    return Check("docker daemon", OPT, status, detail, remedy, extra)


def check_wsl() -> Check:
    if sys.platform != "win32":
        return Check("wsl2", OPT, SKIP, "not Windows")
    if not shutil.which("wsl"):
        return Check("wsl2", OPT, INFO, "wsl not found",
                     "only needed if you want Docker Desktop on Windows")
    rc, out = _run(["wsl", "--status"], timeout=30)
    txt = out.replace("\x00", "")
    ok = "2" in txt
    return Check("wsl2", OPT, PASS if ok else INFO,
                 " ".join(txt.split())[:80] or "present")


def check_uv() -> Check:
    """`uv` reconstructs a real repository's environment without a container.

    This is the mechanism that makes Docker optional rather than required.
    """
    path = shutil.which("uv")
    if not path:
        return Check("uv", REAL, FAIL, "not found",
                     "install uv - it builds per-task Python environments, "
                     "which is what replaces prebuilt container images")
    rc, out = _run(["uv", "--version"])
    return Check("uv", REAL, PASS if rc == 0 else FAIL, out,
                 extra={"path": path})


def check_anthropic_sdk() -> Check:
    """The vendor SDK is an option, not a requirement.

    `af/exec/adapters/llm_bash.py` speaks to both providers over `urllib` and
    imports nothing outside the stdlib, so no code path in this project needs
    the SDK. Tiering it REAL blocked real runs on a package nothing imports,
    and blocked them for a non-Anthropic key that could never need it.
    """
    try:
        import anthropic  # noqa: F401

        return Check("anthropic SDK", OPT, PASS,
                     f"v{getattr(anthropic, '__version__', '?')}")
    except ImportError:
        return Check(
            "anthropic SDK", OPT, INFO, "not installed",
            "optional. The llm-bash backend calls the HTTP API directly with "
            "urllib; install it only if you add a backend that wants it.")


def check_api_keys() -> Check:
    """Delegates to af.auth so there is one credential resolver, not two."""
    from af import auth

    creds = [c for c in auth.resolve_all() if c.available]
    # A local server being reachable is not evidence of a usable model provider.
    remote = [c for c in creds if c.provider.api != auth.API_LOCAL]
    if remote:
        detail = ", ".join(f"{c.provider.name} ({c.source_detail or c.source})"
                           for c in remote[:4])
        if len(remote) > 4:
            detail += f", +{len(remote) - 4} more"
        return Check("provider credentials", REAL, PASS, detail,
                     extra={"available": [c.provider.name for c in remote]})
    return Check("provider credentials", REAL, FAIL, "none found",
                 "af auth add <key>  (provider is inferred from the key)")


def check_network() -> Check:
    import urllib.request

    try:
        req = urllib.request.Request("https://api.anthropic.com/v1/models",
                                     method="GET")
        try:
            urllib.request.urlopen(req, timeout=15)
            return Check("api reachable", REAL, PASS, "api.anthropic.com reachable")
        except urllib.error.HTTPError as e:
            # 401 means we reached it and were rejected - that is a PASS for
            # connectivity purposes.
            return Check("api reachable", REAL, PASS,
                         f"api.anthropic.com reachable (HTTP {e.code})")
    except Exception as exc:  # noqa: BLE001
        return Check("api reachable", REAL, WARN, str(exc)[:80],
                     "check proxy/firewall")


def check_hf_datasets() -> Check:
    """Task supply comes from HuggingFace. Confirm it is reachable."""
    import json
    import urllib.request

    url = ("https://datasets-server.huggingface.co/info"
           "?dataset=SWE-bench-Live%2FSWE-bench-Live")
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.load(r)
        splits = list((d.get("dataset_info", {}).get("default", {})
                       .get("splits", {})).keys())
        return Check("task source reachable", REAL, PASS,
                     f"SWE-bench-Live splits: {', '.join(splits) or 'ok'}")
    except Exception as exc:  # noqa: BLE001
        return Check("task source reachable", REAL, WARN, str(exc)[:80],
                     "needed only when importing real tasks")


# ------------------------------------------------------------------- runner


def run_all(root: Path,
            tiers: tuple[str, ...] = (CORE, SIM, REAL, OPT)) -> list[Check]:
    checks: list[Check] = []
    if CORE in tiers:
        checks += [check_python(), check_pyyaml(), check_stdlib(),
                   check_sqlite(), check_workspace(root)]
    if SIM in tiers:
        checks += [check_local_sandbox(root), check_unittest_runner(),
                   check_backends(), check_resources()]
    if REAL in tiers:
        checks += [check_git(), check_uv(),
                   check_api_keys(), check_network(), check_hf_datasets()]
    if OPT in tiers:
        checks += [check_docker(), check_docker_daemon(), check_wsl(),
                   check_anthropic_sdk()]
    return checks


def summarize(checks: list[Check]) -> dict:
    """Readiness is only reported for tiers that actually ran.

    A filtered run must not vacuously claim readiness for a tier whose checks
    were never executed.
    """
    by_tier: dict[str, dict[str, int]] = {}
    for c in checks:
        t = by_tier.setdefault(
            c.tier, {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0, INFO: 0})
        t[c.status] = t.get(c.status, 0) + 1

    ran = set(by_tier)
    # INFO is never a blocker: it describes an optional capability.
    ok = lambda tiers: all(  # noqa: E731
        c.status in (PASS, WARN, SKIP, INFO)
        for c in checks if c.tier in tiers)

    ready: dict[str, bool | None] = {}
    ready["simulator"] = ok({CORE, SIM}) if {CORE, SIM} <= ran else None
    ready["real_runs"] = ok({CORE, SIM, REAL}) if {CORE, SIM, REAL} <= ran else None
    return {"by_tier": by_tier, "tiers_run": sorted(ran), "ready": ready}
