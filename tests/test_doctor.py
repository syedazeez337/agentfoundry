"""Environment audit guards.

The specific defect these lock in: `af doctor` reported Docker as a hard
requirement for real runs, contradicting the project's own measured finding
that a real repository can be reconstructed with `uv` in seconds. A tool that
misreports its own dependencies sends people to install things they do not
need.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from af import doctor
from af.doctor import CORE, FAIL, INFO, OPT, PASS, REAL, SIM, SKIP, WARN


class TestTierAssignment(unittest.TestCase):
    def test_docker_is_optional_not_required(self):
        for check in (doctor.check_docker(), doctor.check_docker_daemon()):
            self.assertEqual(check.tier, OPT,
                             f"{check.name} must not gate real runs")

    def test_docker_absence_is_never_a_failure(self):
        """Absent Docker is information, not a problem to be fixed."""
        self.assertIn(doctor.check_docker().status, (PASS, INFO, SKIP))

    def test_wsl_is_optional(self):
        """WSL matters only as a Docker backend on Windows."""
        self.assertEqual(doctor.check_wsl().tier, OPT)

    def test_uv_is_required_for_real_runs(self):
        """uv is what replaces prebuilt images, so it is the real dependency."""
        self.assertEqual(doctor.check_uv().tier, REAL)

    def test_git_is_required_for_real_runs(self):
        self.assertEqual(doctor.check_git().tier, REAL)


class TestReadiness(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for k in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                  "AGENTFOUNDRY_AUTH_FILE"):
            self._saved[k] = os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_info_never_blocks(self):
        checks = [doctor.Check("x", OPT, INFO, "optional thing"),
                  doctor.Check("y", CORE, PASS)]
        self.assertTrue(summary_ready(checks, "simulator") in (True, None))

    def test_only_tiers_that_ran_report_readiness(self):
        checks = doctor.run_all(Path.cwd(), tiers=(CORE, SIM))
        s = doctor.summarize(checks)
        self.assertIsNotNone(s["ready"]["simulator"])
        self.assertIsNone(s["ready"]["real_runs"],
                          "must not claim readiness for a tier that never ran")

    def test_credentials_are_what_block_real_runs_not_docker(self):
        checks = doctor.run_all(Path.cwd())
        failures = {c.name for c in checks if c.status == FAIL}
        self.assertNotIn("docker CLI", failures)
        self.assertNotIn("docker daemon", failures)

    def test_a_key_alone_makes_real_runs_ready(self):
        """No Docker installed on this host, so this asserts the whole point."""
        os.environ["GROQ_API_KEY"] = "gsk_" + "z" * 48
        s = doctor.summarize(doctor.run_all(Path.cwd()))
        self.assertTrue(s["ready"]["real_runs"],
                        "a provider key should be sufficient without Docker")


class TestChecksAreHonest(unittest.TestCase):
    def test_sandbox_check_actually_executes(self):
        c = doctor.check_local_sandbox(Path.cwd())
        self.assertEqual(c.status, PASS)
        self.assertIn("42", c.detail, "the check must run code, not guess")

    def test_no_check_mutates_the_system(self):
        """Every check is read-only apart from the workspace probe, which
        cleans up after itself."""
        before = set(Path.cwd().iterdir())
        doctor.run_all(Path.cwd())
        self.assertEqual(before | {Path.cwd() / ".agentfoundry"},
                         set(Path.cwd().iterdir()) | {Path.cwd() / ".agentfoundry"})


def summary_ready(checks, key):
    return doctor.summarize(checks)["ready"][key]


if __name__ == "__main__":
    unittest.main()
