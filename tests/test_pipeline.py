"""End-to-end pipeline guard.

Runs the full stack against a temporary workspace and pins the verdict. The
scripted backend is seeded on trial_key, so this is deterministic: any change
that shifts identity, grading, statistics or the verdict rule moves the hash and
fails here.

This is the test that would have caught the TaskSpec.canonical() change, which
silently altered the demo verdict from 35d1951a to 95884a0f.

Slow by unit-test standards (~15s). Kept small on purpose: 6 tasks, 2
replicates, 2 arms.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from af import ANALYSIS_VERSION
from af.demo import scaffold
from af.evidence import iter_bundles
from af.experiment import (
    Scheduler, analyze_experiment, plan_experiment, score_experiment,
)
from af.spec.models import ExperimentSpec
from af.store import Store
from af.util import Paths, dump_yaml, write_atomic

SMOKE = {
    "apiVersion": "agentfoundry/v1",
    "kind": "Experiment",
    "name": "smoke",
    "hypothesis": "fixed design used only to pin pipeline behaviour",
    "design": {
        "type": "paired",
        "arms": [
            {"id": "baseline", "architecture": "minimal-bash"},
            {"id": "candidate", "architecture": "review-stage"},
        ],
        "suite": "demo",
        "replicates": 2,
    },
    "analysis": {
        "primary": "resolved",
        "secondary": ["cost_usd"],
        "cluster_on": "task",
        "test": "cluster_bootstrap",
        "equivalence_margin": 0.05,
        "correction": "benjamini_hochberg",
    },
    "stopping": {"rule": "fixed", "max_trials": 100},
    "budget": {"max_cost_usd": 100.0},
}

# MEASURED on the known-good tree at commit 3da5a87, twice, identical both
# times. Not guessed. Treat a mismatch the same way as an identity break:
# investigate, decide, then re-pin deliberately.
#
# 6 tasks is deliberate: it spans the full difficulty range of the demo
# templates, so the run contains both successes and failures. A design where
# every arm scores 1.0 would pass no matter what broke downstream.
N_TASKS = 6
EXPECTED = {
    "n_trials": 24,
    "result": "INCONCLUSIVE",
    "baseline_rate": 0.9167,
    "candidate_rate": 1.0000,
    "delta": 0.0833,
}


class TestPipelineSmoke(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="af-smoke-"))
        scaffold(cls.root, n_tasks=N_TASKS)
        write_atomic(cls.root / "experiments" / "smoke.yaml", dump_yaml(SMOKE))
        cls.paths = Paths(cls.root).ensure()
        cls.store = Store(cls.paths.db)

        spec = ExperimentSpec.load(cls.root / "experiments" / "smoke.yaml")
        for p in sorted(cls.paths.architectures.glob("*.yaml")):
            from af.spec.models import ArchitectureSpec

            cls.store.put_architecture(ArchitectureSpec.load(p))

        cls.plan = plan_experiment(cls.root, spec)
        sched = Scheduler(cls.store, cls.root, cls.paths.bundles, "local")
        cls.exp_hash = sched.submit(spec, cls.plan)
        cls.run_result = sched.drain(cls.exp_hash)
        cls.scored = score_experiment(cls.store, cls.root, cls.exp_hash)
        cls.verdict = analyze_experiment(cls.store, cls.exp_hash)

    @classmethod
    def tearDownClass(cls):
        cls.store.conn.close()
        shutil.rmtree(cls.root, ignore_errors=True)

    # ---- execution

    def test_all_trials_ran(self):
        self.assertEqual(len(self.plan.trials), EXPECTED["n_trials"])
        self.assertEqual(self.run_result["ran"], EXPECTED["n_trials"])
        states = self.store.counts_by_state(self.exp_hash)
        self.assertEqual(states.get("sealed"), EXPECTED["n_trials"], states)
        self.assertNotIn("failed", states)

    def test_every_bundle_is_sealed_and_complete(self):
        bundles = list(iter_bundles(self.paths.bundles))
        self.assertEqual(len(bundles), EXPECTED["n_trials"])
        for b in bundles:
            self.assertTrue(b.sealed, b.trial_key)
            for required in ("manifest.json", "events.jsonl", "patch.diff",
                             "usage.json", "integrity.json"):
                self.assertTrue((b.dir / required).exists(),
                                f"{b.trial_key} missing {required}")

    def test_replicates_share_spec_identity(self):
        """The clustering structure the statistics depend on."""
        by_spec: dict[str, set] = {}
        for ts, _arm in self.plan.trials:
            by_spec.setdefault(ts.hash, set()).add(ts.trial_key)
        self.assertTrue(by_spec, "no trials planned")
        for spec_hash, keys in by_spec.items():
            self.assertEqual(len(keys), SMOKE["design"]["replicates"],
                             f"{spec_hash[:12]} has {len(keys)} replicates")

    # ---- grading

    def test_all_trials_scored(self):
        self.assertEqual(self.scored["scored"], EXPECTED["n_trials"])
        keys = [t["trial_key"] for t in self.store.trials_for_experiment(self.exp_hash)]
        scores = self.store.get_scores(keys, ANALYSIS_VERSION)
        self.assertEqual(len(scores), EXPECTED["n_trials"])
        for s in scores.values():
            self.assertIn(s["credibility"], ("clean", "suspect", "invalid"))
            self.assertIn("resolved", s["outcomes"])

    def test_integrity_graders_all_ran(self):
        """Every integrity grader must execute on every trial.

        Deliberately asserts that they RAN, not that they FIRED. The scripted
        backend produces cheating probabilistically, so asserting on a flag
        appearing would make this test flaky at small sample sizes. Whether the
        graders correctly detect cheating is tested deterministically in
        test_graders.py against synthetic bundles.
        """
        keys = [t["trial_key"] for t in self.store.trials_for_experiment(self.exp_hash)]
        scores = self.store.get_scores(keys, ANALYSIS_VERSION)
        expected = {"visible-tests", "held-out-tests", "test-tampering",
                    "oracle-access", "process-quality", "eval-awareness"}
        for key, s in scores.items():
            self.assertEqual(expected, set(s["detail"]), f"trial {key[:12]}")

    # ---- analysis

    def test_verdict_shape(self):
        v = self.verdict
        self.assertIn(v["result"],
                      ("BETTER", "WORSE", "EQUIVALENT", "INCONCLUSIVE",
                       "UNINTERPRETABLE"))
        p = v["payload"]
        self.assertEqual(sorted(p["arms"]), ["baseline", "candidate"])
        self.assertEqual(len(p["comparisons"]), 1)
        cmp0 = p["comparisons"][0]
        for field in ("delta", "ci", "p_value", "n_pairs",
                      "significant_after_fdr", "equivalence",
                      "insufficient_data", "cost_delta_usd"):
            self.assertIn(field, cmp0)
        self.assertEqual(v["analysis_version"], ANALYSIS_VERSION)

    def test_verdict_is_deterministic(self):
        """The pinned outcome. A change here means behaviour moved.

        Pins the numbers as well as the label: a verdict can stay
        INCONCLUSIVE while the underlying rates drift, and that drift is
        exactly what silently changed when TaskSpec.canonical() gained fields.
        """
        p = self.verdict["payload"]
        drift = (
            "\nPipeline behaviour changed.\n"
            "Something altered identity, grading, statistics or the verdict\n"
            "rule. Investigate the cause before re-pinning these values."
        )
        self.assertEqual(self.verdict["result"], EXPECTED["result"], drift)
        self.assertAlmostEqual(p["arms"]["baseline"]["rate"],
                               EXPECTED["baseline_rate"], places=3, msg=drift)
        self.assertAlmostEqual(p["arms"]["candidate"]["rate"],
                               EXPECTED["candidate_rate"], places=3, msg=drift)
        self.assertAlmostEqual(p["comparisons"][0]["delta"],
                               EXPECTED["delta"], places=3, msg=drift)

    def test_mde_is_reported(self):
        """Refusing to overclaim is a feature, so it must not silently vanish."""
        p = self.verdict["payload"]
        self.assertGreater(p["minimum_detectable_effect"], 0.0)
        self.assertTrue(any("detect" in w for w in self.plan.warnings),
                        self.plan.warnings)

    def test_reindex_rebuilds_control_plane(self):
        """The disposable-database invariant."""
        from af.store import reindex

        fresh = Store(self.paths.state / "reindex-probe.db")
        try:
            counts = reindex(fresh, self.paths.bundles, self.paths.tasks,
                             self.paths.architectures)
            self.assertEqual(counts["trials"], EXPECTED["n_trials"])
            self.assertEqual(counts["tasks"], N_TASKS)
        finally:
            fresh.conn.close()


if __name__ == "__main__":
    unittest.main()
