"""Schema-identity guard.

Identity in AgentFoundry is the SHA-256 of a spec's `canonical()` form. Change
canonical(), and every stored bundle, score and verdict is orphaned: the same
task under the same architecture now hashes differently, so previously recorded
evidence can never be matched to a new run.

That is a schema migration, not a refactor. These tests exist so it fails loudly
here instead of silently reseeding downstream behaviour.

If a test in this file fails, the correct response is NOT to update the expected
hash. It is to decide, deliberately, whether the identity change is intended,
bump SCHEMA_VERSION, record the break in CHANGELOG.md, and only then re-pin.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from af.spec.models import (
    ArchitectureSpec, Budget, EnvironmentSpec, ModelBinding, SlotBinding,
    TrialSpec,
)
from af.util import hash_obj

# Bump when an identity-affecting change is made ON PURPOSE.
SCHEMA_VERSION = 1


def _fixed_architecture() -> ArchitectureSpec:
    """A spec pinned by value, independent of anything on disk."""
    return ArchitectureSpec(
        name="identity-fixture",
        backend="scripted",
        models={"default": ModelBinding("scripted", "sim-medium", "medium")},
        slots={
            "action": SlotBinding("bash-only", {"stateful": False, "timeout_s": 120}),
            "context": SlotBinding("full-history", {"on_overflow": "fail"}),
            "memory": SlotBinding("none", {}),
            "control": SlotBinding("single", {"max_turns": 60}),
            "verification": SlotBinding("project-tests", {
                "command": "python -m unittest discover -s tests",
                "run_before_finish": True}),
        },
        budget=Budget(400_000, 1800, 4.0, 60),
    )


class TestCanonicalShape(unittest.TestCase):
    """The set of keys that participate in identity is itself the contract."""

    def test_architecture_keys(self):
        self.assertEqual(
            sorted(_fixed_architecture().canonical()),
            ["backend", "budget", "models", "slots"],
        )

    def test_task_keys(self):
        from af.spec.models import TaskSpec

        # Built by hand so the test does not depend on scaffolded content.
        t = TaskSpec(id="x", suite="s", instruction="i", difficulty=0.5,
                     dir=Path("."), env={})
        self.assertEqual(
            sorted(k for k in t.canonical()),
            ["env", "fixture_digest", "id", "instruction"],
        )

    def test_environment_keys(self):
        self.assertEqual(sorted(EnvironmentSpec().to_dict()),
                         ["allowlist", "image", "net", "require_enforcement"])

    def test_trial_keys(self):
        ts = TrialSpec(task_id="t", task_hash="a" * 64, arch_hash="b" * 64,
                       env_hash="c" * 64, models={}, budget={}, nonce=0,
                       foundry_version="0.1.0")
        self.assertEqual(
            sorted(ts.canonical()),
            ["arch_hash", "budget", "env_hash", "foundry_version", "models",
             "task_hash"],
        )


class TestPinnedHashes(unittest.TestCase):
    """Exact values. Any drift is a deliberate decision or a bug."""

    def test_architecture_hash_stable(self):
        got = _fixed_architecture().hash
        self.assertEqual(
            got, EXPECTED_ARCHITECTURE_HASH,
            "\nArchitectureSpec identity changed.\n"
            "Do not simply re-pin this value. Decide whether the change is\n"
            "intended, bump SCHEMA_VERSION, and record it in CHANGELOG.md.",
        )

    def test_trial_key_derivation(self):
        """Replicates must share a trial_spec hash and differ in trial_key.

        This is what gives the analysis layer its clustering structure for
        free; if it breaks, cluster-aware statistics silently become wrong.
        """
        base = dict(task_id="t", task_hash="a" * 64, arch_hash="b" * 64,
                    env_hash="c" * 64, models={"default": {"id": "m"}},
                    budget={"max_tokens": 1}, foundry_version="0.1.0")
        r0 = TrialSpec(nonce=0, **base)
        r1 = TrialSpec(nonce=1, **base)
        self.assertEqual(r0.hash, r1.hash, "replicates must share spec identity")
        self.assertNotEqual(r0.trial_key, r1.trial_key,
                            "replicates must have distinct trial keys")
        self.assertEqual(r0.trial_key,
                         hash_obj({"spec": r0.hash, "nonce": 0}))

    def test_hash_is_order_independent(self):
        """Canonical serialization must not depend on dict insertion order."""
        a = _fixed_architecture()
        reordered = ArchitectureSpec(
            name=a.name, backend=a.backend, models=dict(a.models),
            slots={k: a.slots[k] for k in reversed(list(a.slots))},
            budget=a.budget)
        self.assertEqual(a.hash, reordered.hash)

    def test_name_does_not_affect_identity(self):
        """Renaming is not a behavioural change and must not re-hash."""
        import dataclasses

        a = _fixed_architecture()
        b = dataclasses.replace(a, name="different-name", description="x")
        self.assertEqual(a.hash, b.hash)


# Computed once at import so the expected value lives in one place.
EXPECTED_ARCHITECTURE_HASH = "abd305971a529cc43279b3bcf8c3093739ab34ee120463a0da97dc622c605803"


if __name__ == "__main__":
    unittest.main()
