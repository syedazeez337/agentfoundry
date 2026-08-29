"""The scheduler trusts the evidence plane, not its own memory.

`af reindex` already rebuilt the whole control plane from bundles, which is the
project's own statement that the database is disposable. Reconciliation runs
that idea as a supervision step instead of a recovery command, so a crash
between sealing a bundle and updating a row costs nothing and a re-run does not
pay twice for work already on disk.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from af.evidence import BundleWriter
from af.experiment import Scheduler
from af.spec.models import TrialSpec
from af.store import Store


def _trial(nonce: int = 0) -> TrialSpec:
    return TrialSpec(
        task_id="t", task_hash="a" * 64, arch_hash="b" * 64, env_hash="c" * 64,
        models={}, budget={}, nonce=nonce,
    )


class ReconcileCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="af-recon-"))
        self.bundles = self.root / "bundles"
        self.bundles.mkdir()
        self.store = Store(self.root / "control.db")
        self.sched = Scheduler(self.store, self.root, self.bundles)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _seal(self, trial_key: str, status: str = "ok") -> Path:
        w = BundleWriter(self.bundles / trial_key)
        w.open(manifest={"trial": {"task_hash": "a" * 64, "nonce": 0},
                         "task": {"id": "t"}, "architecture": {"arch_hash": "b" * 64},
                         "environment": {}, "backend": {"name": "fake", "version": "1"}})
        w.write_text("events.jsonl", "")
        w.seal(status=status, error=None if status == "ok" else "provider outage")
        return w.dir

    def _state(self, trial_key: str) -> str:
        return self.store.one("SELECT state FROM trial WHERE trial_key=?",
                              trial_key)["state"]


class TestAdoption(ReconcileCase):
    def test_a_queued_trial_with_a_sealed_bundle_is_adopted(self):
        ts = _trial()
        self.store.enqueue_trial(ts)
        self._seal(ts.trial_key)
        res = self.sched.reconcile()
        self.assertEqual(res["adopted"], 1)
        self.assertEqual(self._state(ts.trial_key), "sealed")

    def test_an_errored_bundle_is_adopted_as_errored(self):
        """Seal status decides, here exactly as it does in drain."""
        ts = _trial()
        self.store.enqueue_trial(ts)
        self._seal(ts.trial_key, status="error")
        self.sched.reconcile()
        self.assertEqual(self._state(ts.trial_key), "errored")

    def test_adoption_records_the_bundle_path(self):
        ts = _trial()
        self.store.enqueue_trial(ts)
        d = self._seal(ts.trial_key)
        self.sched.reconcile()
        row = self.store.one("SELECT bundle_path FROM trial WHERE trial_key=?",
                             ts.trial_key)
        self.assertEqual(row["bundle_path"], str(d))

    def test_a_queued_trial_without_a_bundle_is_left_alone(self):
        ts = _trial()
        self.store.enqueue_trial(ts)
        res = self.sched.reconcile()
        self.assertEqual(res["adopted"], 0)
        self.assertEqual(self._state(ts.trial_key), "queued")

    def test_reconcile_is_idempotent(self):
        ts = _trial()
        self.store.enqueue_trial(ts)
        self._seal(ts.trial_key)
        self.sched.reconcile()
        second = self.sched.reconcile()
        self.assertEqual(second["adopted"], 0)
        self.assertEqual(second["examined"], 0)


class TestStallRelease(ReconcileCase):
    def _lease(self, ts: TrialSpec) -> None:
        self.store.enqueue_trial(ts)
        leased = self.store.lease_trial("worker-1")
        self.assertIsNotNone(leased)

    def test_a_leased_trial_with_no_recent_progress_is_released(self):
        ts = _trial()
        self._lease(ts)
        d = self.bundles / ts.trial_key
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text("", encoding="utf-8")
        old = time.time() - 5000
        os.utime(d / "events.jsonl", (old, old))
        res = self.sched.reconcile(stall_s=900)
        self.assertEqual(res["released"], 1)
        self.assertEqual(self._state(ts.trial_key), "queued")

    def test_a_leased_trial_making_progress_is_not_released(self):
        ts = _trial()
        self._lease(ts)
        d = self.bundles / ts.trial_key
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text("", encoding="utf-8")
        res = self.sched.reconcile(stall_s=900)
        self.assertEqual(res["released"], 0)
        self.assertEqual(self._state(ts.trial_key), "leased")

    def test_a_leased_trial_that_never_started_is_not_called_stalled(self):
        """Lease expiry is the right mechanism for a trial with no evidence."""
        ts = _trial()
        self._lease(ts)
        res = self.sched.reconcile(stall_s=0.0)
        self.assertEqual(res["released"], 0)

    def test_a_sealed_bundle_wins_over_stall_detection(self):
        ts = _trial()
        self._lease(ts)
        d = self._seal(ts.trial_key)
        old = time.time() - 5000
        os.utime(d / "events.jsonl", (old, old))
        res = self.sched.reconcile(stall_s=900)
        self.assertEqual((res["adopted"], res["released"]), (1, 0))
        self.assertEqual(self._state(ts.trial_key), "sealed")


class TestDrainReconcilesFirst(ReconcileCase):
    def test_drain_adopts_existing_evidence_instead_of_rerunning(self):
        """The property that makes a re-run cheap after a crash."""
        ts = _trial()
        self.store.enqueue_trial(ts)
        self._seal(ts.trial_key)
        res = self.sched.drain()
        self.assertEqual(res["adopted"], 1)
        self.assertEqual(res["ran"], 0, "an already-sealed trial was re-run")
        self.assertEqual(self._state(ts.trial_key), "sealed")


if __name__ == "__main__":
    unittest.main()
