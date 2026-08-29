"""Conformance: one contract, asserted against every implementation.

`conformance_check` only ever asked whether the five methods exist. That is a
shape check, and shape was never the problem: the scripted backend had all five
methods, computed a real `git_ops` list in `invoke()`, and lost it because the
supervisor read `collect()` instead. The integrity channel was dead for every
trial the system ever ran, and no test noticed because no test asserted that a
signal a backend reports actually arrives in the evidence.

This tier asserts behaviour, and it asserts it for every backend rather than
for one. A future backend that reports through either channel is covered on the
day it is registered.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from af.evidence import Bundle
from af.exec import (
    RunOutcome, conformance_check, get_backend, list_backends, merge_integrity,
    register_backend, run_trial,
)
from af.spec.models import (
    ArchitectureSpec, Budget, EnvironmentSpec, ModelBinding, SlotBinding,
    TaskSpec, TrialSpec,
)


class _ReportingBackend:
    """Reports one oracle attempt through a configurable channel."""

    version = "1.0.0"

    def __init__(self, name: str, via: str):
        self.name = name
        self.via = via

    def capabilities(self) -> set[str]:
        return {"*"}

    def provision(self, ctx) -> None:
        return None

    def invoke(self, ctx) -> RunOutcome:
        ctx.sink.emit("run.start", note="conformance fixture")
        raw = {}
        if self.via in ("invoke", "both"):
            raw = {"integrity": {"git_ops": ["git log --all -p"],
                                 "oracle_attempts": ["git log --all -p"]}}
        return RunOutcome(stopped_reason="finished", usage={"cost_usd": 0.0}, raw=raw)

    def collect(self, ctx) -> dict:
        out: dict = {"backend": self.name, "version": self.version}
        if self.via in ("collect", "both"):
            out["integrity"] = {"git_ops": ["git log --all -p"],
                                "oracle_attempts": ["git log --all -p"]}
        elif self.via == "empty":
            # The exact shape that used to erase real data.
            out["integrity"] = {"git_ops": [], "oracle_attempts": []}
        return out

    def normalize(self, raw: dict, sink) -> None:
        return None


def _architecture(backend: str) -> ArchitectureSpec:
    return ArchitectureSpec(
        name=f"conformance-{backend}",
        backend=backend,
        models={"default": ModelBinding(provider="scripted", id="scripted-v1")},
        slots={
            "action": SlotBinding("bash-only", {}),
            "control": SlotBinding("single", {}),
            "verification": SlotBinding("none", {}),
        },
        budget=Budget(max_wall_s=60, max_turns=4),
    )


class TestRegisteredBackends(unittest.TestCase):
    def test_every_backend_satisfies_the_shape_contract(self):
        names = list_backends()
        self.assertTrue(names, "no backends registered")
        for name in names:
            with self.subTest(backend=name):
                self.assertEqual(conformance_check(get_backend(name)), [])

    def test_every_backend_declares_a_version(self):
        for name in list_backends():
            with self.subTest(backend=name):
                self.assertTrue(get_backend(name).version)


class TestIntegrityChannelReachesEvidence(unittest.TestCase):
    """The contract: a signal a backend reports is a signal the bundle records.

    Which of the two channels carried it is the backend's business. That it
    survives to `integrity.json` is not.
    """

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="af-conf-"))
        fixture = cls.root / "task" / "fixture"
        fixture.mkdir(parents=True)
        (fixture / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        cls.task = TaskSpec(
            id="conformance-task", suite="conformance",
            instruction="do nothing", difficulty=0.5, dir=cls.root / "task",
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def _run(self, via: str) -> Bundle:
        backend = register_backend(_ReportingBackend(f"conformance-{via}", via))
        arch = _architecture(backend.name)
        env = EnvironmentSpec()
        ts = TrialSpec(
            task_id=self.task.id, task_hash=self.task.hash, arch_hash=arch.hash,
            env_hash=env.hash, models={}, budget=arch.budget.to_dict(), nonce=0,
        )
        d = run_trial(ts, self.task, arch, env, self.root / "bundles", "local")
        return Bundle(d)

    def test_signal_reported_via_invoke_reaches_integrity(self):
        b = self._run("invoke")
        self.assertEqual(b.integrity.get("git_ops"), ["git log --all -p"])

    def test_signal_reported_via_collect_reaches_integrity(self):
        b = self._run("collect")
        self.assertEqual(b.integrity.get("git_ops"), ["git log --all -p"])

    def test_signal_reported_via_both_is_not_duplicated(self):
        b = self._run("both")
        self.assertEqual(b.integrity.get("git_ops"), ["git log --all -p"])

    def test_a_backend_reporting_nothing_records_nothing(self):
        b = self._run("none")
        self.assertEqual(b.integrity.get("git_ops"), [])

    def test_empty_collect_cannot_erase_what_invoke_observed(self):
        """The original defect, stated as a contract."""
        merged = merge_integrity(
            {"git_ops": []},
            {"git_ops": ["git log --all -p"]},
            {"git_ops": []},
        )
        self.assertEqual(merged["git_ops"], ["git log --all -p"])


class TestMergeIntegrity(unittest.TestCase):
    def test_lists_are_unioned_in_order(self):
        merged = merge_integrity({"a": ["x"]}, {"a": ["y"]}, {"a": ["x", "z"]})
        self.assertEqual(merged["a"], ["x", "y", "z"])

    def test_scalars_from_later_sources_win_when_populated(self):
        self.assertEqual(merge_integrity({"k": "old"}, {"k": "new"})["k"], "new")

    def test_empty_scalar_does_not_overwrite_a_populated_one(self):
        self.assertEqual(merge_integrity({"k": "old"}, {"k": ""})["k"], "old")

    def test_new_keys_are_added_even_when_empty(self):
        self.assertEqual(merge_integrity({}, {"k": ""})["k"], "")

    def test_sources_may_be_missing(self):
        self.assertEqual(merge_integrity({"a": 1}, {}, None), {"a": 1})


if __name__ == "__main__":
    unittest.main()
