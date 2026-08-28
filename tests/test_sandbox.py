"""Sandbox isolation guards.

Written after discovering that `net: deny` was a complete no-op: the local
sandbox set `no_proxy="*"`, which means "bypass the proxy for every host", so
the dead-port proxy was never consulted. Trials reached the internet while
their integrity record claimed `network_mode: deny`.

A control that is recorded but not enforced is worse than no control, because
the evidence asserts something false. These tests exercise the control rather
than reading the setting.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from af.env import PYTHON, Limits, NetPolicy, make_sandbox

NET_PROBE = """\
import urllib.request
try:
    urllib.request.urlopen("https://api.github.com", timeout=8)
    print("REACHED")
except Exception as exc:
    print("BLOCKED", type(exc).__name__)
"""


class SandboxTestCase(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(tempfile.mkdtemp(prefix="af-sbtest-"))
        self.sb = make_sandbox("local")
        self.handles = []

    def tearDown(self):
        for h in self.handles:
            self.sb.stop(h)
        shutil.rmtree(self.fixture, ignore_errors=True)

    def start(self, net="deny"):
        h = self.sb.start(self.fixture, Limits(wall_s=60), NetPolicy(net))
        self.handles.append(h)
        return h

    def write(self, rel, text):
        p = self.fixture / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


class TestNetworkDenial(SandboxTestCase):
    def test_deny_blocks_outbound_http(self):
        """The regression. Previously printed REACHED."""
        self.write("probe.py", NET_PROBE)
        r = self.sb.exec(self.start("deny"), [PYTHON, "probe.py"], timeout=45)
        self.assertIn("BLOCKED", r.stdout,
                      f"network was reachable under net=deny: {r.stdout!r}")

    def test_no_proxy_is_not_a_wildcard(self):
        """`no_proxy=*` was the exact defect. Assert it never comes back."""
        self.write("env.py",
                   "import os\n"
                   "print('no_proxy=' + repr(os.environ.get('no_proxy')))\n"
                   "print('HTTPS_PROXY=' + repr(os.environ.get('HTTPS_PROXY')))\n")
        r = self.sb.exec(self.start("deny"), [PYTHON, "env.py"], timeout=30)
        self.assertNotIn("no_proxy='*'", r.stdout, "no_proxy wildcard is back")
        self.assertIn("127.0.0.1:9", r.stdout, "proxy not pointed at a dead port")


class TestWorkspaceIsolation(SandboxTestCase):
    def test_each_trial_gets_a_distinct_workdir(self):
        a, b = self.start(), self.start()
        self.assertNotEqual(a.workdir, b.workdir)

    def test_writes_do_not_leak_between_trials(self):
        self.write("mod.py", "V = 1\n")
        a = self.start()
        (a.workdir / "scratch.txt").write_text("dirty", encoding="utf-8")
        b = self.start()
        self.assertFalse((b.workdir / "scratch.txt").exists(),
                         "a later trial saw an earlier trial's writes")

    def test_fixture_is_copied_not_referenced(self):
        self.write("mod.py", "V = 1\n")
        h = self.start()
        (h.workdir / "mod.py").write_text("V = 999\n", encoding="utf-8")
        self.assertEqual((self.fixture / "mod.py").read_text(encoding="utf-8"),
                         "V = 1\n", "the agent mutated the source fixture")

    def test_stop_removes_the_workspace(self):
        h = self.sb.start(self.fixture, Limits(wall_s=60), NetPolicy("deny"))
        workdir = h.workdir
        self.sb.stop(h)
        self.assertFalse(workdir.exists())

    def test_snapshot_sees_agent_writes(self):
        self.write("mod.py", "V = 1\n")
        h = self.start()
        (h.workdir / "new.py").write_text("x = 2\n", encoding="utf-8")
        snap = self.sb.snapshot(h)
        self.assertIn("new.py", snap)
        self.assertEqual(snap["new.py"], "x = 2\n")


class TestExecution(SandboxTestCase):
    def test_no_shell_interpolation(self):
        """argv lists, never a shell string. Protects against injection from
        model-generated content."""
        self.write("mod.py", "")
        h = self.start()
        r = self.sb.exec(h, [PYTHON, "-c", "print('a && echo b')"], timeout=30)
        self.assertEqual(r.stdout.strip(), "a && echo b")

    def test_timeout_is_enforced(self):
        self.write("slow.py", "import time; time.sleep(30)\n")
        r = self.sb.exec(self.start(), [PYTHON, "slow.py"], timeout=3)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)

    def test_missing_executable_is_reported_not_raised(self):
        r = self.sb.exec(self.start(), ["definitely-not-a-real-binary"], timeout=10)
        self.assertEqual(r.returncode, 127)
        self.assertFalse(r.ok)

    def test_child_environment_is_usable(self):
        """A scrubbed environment must still let Python start; on Windows that
        needs SYSTEMROOT and TEMP."""
        self.write("t.py", "import tempfile, socket; "
                           "tempfile.mkstemp(); print('ok')\n")
        r = self.sb.exec(self.start(), [PYTHON, "t.py"], timeout=30)
        self.assertEqual(r.stdout.strip(), "ok", r.stderr[:300])


class TestHonestReporting(unittest.TestCase):
    def test_local_sandbox_does_not_claim_to_be_a_boundary(self):
        sb = make_sandbox("local")
        self.assertEqual(sb.kind, "local")
        self.assertIn("NOT a security boundary", (sb.__doc__ or ""))


if __name__ == "__main__":
    unittest.main()
