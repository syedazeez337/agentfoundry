"""Adversarial: hostile input to the L1 boundary.

The question this tier asks is not "does the feature work" but "what happens
when something tries to break it, and does the system tell the truth about the
result". The task admissibility contract already applies that standard to
graders - a grader is not trusted until a null patch and a cheat patch have
been thrown at it. These are the same checks aimed at the sandbox.
"""

from __future__ import annotations

import shutil
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from af.env import (
    ADVISORY, KERNEL, ContainmentError, Limits, NetPolicy, contained_path,
    make_sandbox, PYTHON,
)

# Connects with a raw socket, which consults no proxy variable. Deliberately
# aimed at a local listener so the test is hermetic: it proves the escape
# without needing the internet, and cannot pass for the wrong reason on a CI
# runner with no egress.
RAW_SOCKET_PROBE = """\
import socket, sys
try:
    s = socket.create_connection(("127.0.0.1", {port}), timeout=5)
    s.close()
    print("REACHED")
except Exception as exc:
    print("BLOCKED", type(exc).__name__)
"""


class _Listener:
    """A local TCP server that accepts one connection."""

    def __enter__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()
        return self

    def _accept(self):
        try:
            conn, _ = self._srv.accept()
            conn.close()
        except OSError:
            pass

    def __exit__(self, *exc):
        self._srv.close()
        self._thread.join(timeout=2)


class SandboxCase(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(tempfile.mkdtemp(prefix="af-adv-"))
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


class TestNetworkEscape(SandboxCase):
    def test_raw_socket_escapes_the_local_sandbox(self):
        """Proxy variables do not stop a socket, and the record must not claim
        they do.

        This asserts the escape *succeeds*. That is not an endorsement: it is
        the fact the integrity record has to be consistent with. The moment
        LocalSandbox gains real network enforcement this test fails, and the
        fix is to update the declared level in the same commit.
        """
        with _Listener() as lis:
            (self.fixture / "probe.py").write_text(
                RAW_SOCKET_PROBE.format(port=lis.port), encoding="utf-8")
            r = self.sb.exec(self.start("deny"), [PYTHON, "probe.py"], timeout=30)
        self.assertIn("REACHED", r.stdout,
                      "expected a raw socket to bypass proxy-env denial")

    def test_local_sandbox_does_not_claim_to_enforce_the_network(self):
        """The claim, not the mechanism, is what this tier protects."""
        enf = self.sb.enforcement(NetPolicy("deny"))
        self.assertEqual(enf.network, ADVISORY)
        self.assertFalse(enf.is_security_boundary)
        self.assertFalse(enf.meets(KERNEL),
                         "local sandbox must never satisfy a kernel requirement")

    def test_not_requested_is_distinct_from_unenforced(self):
        enf = self.sb.enforcement(NetPolicy("open"))
        self.assertEqual(enf.network, "not_requested")


class TestPathContainment(SandboxCase):
    def test_put_refuses_parent_traversal(self):
        h = self.start()
        src = self.fixture / "payload.txt"
        src.write_text("x", encoding="utf-8")
        with self.assertRaises(ContainmentError):
            self.sb.put(h, src, "../../escaped.txt")

    def test_get_refuses_parent_traversal(self):
        h = self.start()
        with self.assertRaises(ContainmentError):
            self.sb.get(h, "../../../etc/passwd")

    def test_absolute_path_is_refused(self):
        h = self.start()
        with self.assertRaises(ContainmentError):
            self.sb.get(h, "/etc/passwd")

    def test_ordinary_nested_path_is_allowed(self):
        h = self.start()
        src = self.fixture / "payload.txt"
        src.write_text("hello", encoding="utf-8")
        self.sb.put(h, src, "deep/nested/payload.txt")
        self.assertEqual(self.sb.get(h, "deep/nested/payload.txt"), b"hello")

    def test_symlink_target_outside_root_is_refused(self):
        """Resolution happens before the prefix check, so a symlink cannot be
        used to launder an escape."""
        h = self.start()
        outside = Path(tempfile.mkdtemp(prefix="af-outside-"))
        try:
            (outside / "secret.txt").write_text("s", encoding="utf-8")
            link = h.workdir / "link"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            with self.assertRaises(ContainmentError):
                self.sb.get(h, "link/secret.txt")
        finally:
            shutil.rmtree(outside, ignore_errors=True)


class TestContainedPath(unittest.TestCase):
    def test_root_itself_is_permitted(self):
        root = Path(tempfile.mkdtemp(prefix="af-cp-"))
        try:
            self.assertEqual(contained_path(root, "."), root.resolve())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_sibling_prefix_is_not_containment(self):
        """`/tmp/work-evil` must not count as inside `/tmp/work`."""
        base = Path(tempfile.mkdtemp(prefix="af-cp-"))
        try:
            root = base / "work"
            root.mkdir()
            (base / "work-evil").mkdir()
            with self.assertRaises(ContainmentError):
                contained_path(root, "../work-evil/x")
        finally:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
