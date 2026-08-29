"""Demo scaffolding: generates a validated task suite, architectures and
experiments so the whole pipeline is runnable with zero API keys.

Every generated task ships the four things the task contract requires: a fixture
that fails, a reference solution that passes, a null-patch probe, and a known
cheat patch that must fail. Tasks also ship a `partial` solution that passes the
visible suite and fails the held-out one - that is the lucky-pass shape, and it
is what gives the integrity layer something real to catch.
"""

from __future__ import annotations

from pathlib import Path

from af.util import Paths, dump_yaml, write_atomic, write_json

# --------------------------------------------------------------- templates
# (name, difficulty, module_buggy, module_fixed, module_partial, visible, hidden, instruction)

TEMPLATES = [
    (
        "clamp", 0.25,
        # buggy
        "def clamp(x, lo, hi):\n    if x > hi:\n        return hi\n    return x\n",
        # fixed
        "def clamp(x, lo, hi):\n    if x > hi:\n        return hi\n    if x < lo:\n        return lo\n    return x\n",
        # partial: passes the visible suite, fails held-out (the lucky-pass shape)
        "def clamp(x, lo, hi):\n    if x > hi:\n        return hi\n    if x < lo and lo >= 0:\n        return lo\n    return x\n",
        "self.assertEqual(mod.clamp(5, 0, 10), 5)\n"
        "        self.assertEqual(mod.clamp(15, 0, 10), 10)\n"
        "        self.assertEqual(mod.clamp(-1, 0, 10), 0)",
        "self.assertEqual(mod.clamp(-5, -3, 3), -3)\n"
        "        self.assertEqual(mod.clamp(0, -3, 3), 0)",
        "clamp(x, lo, hi) must clamp on BOTH bounds. It currently ignores the lower bound.",
    ),
    (
        "dedupe", 0.35,
        "def dedupe(items):\n    return sorted(set(items))\n",
        "def dedupe(items):\n    seen = set()\n    out = []\n    for i in items:\n        if i not in seen:\n            seen.add(i)\n            out.append(i)\n    return out\n",
        "def dedupe(items):\n    seen = [False] * 1000\n    out = []\n    for i in items:\n        idx = i if isinstance(i, int) else 0\n        if not seen[idx]:\n            seen[idx] = True\n            out.append(i)\n    return out\n",
        "self.assertEqual(mod.dedupe([3, 1, 3, 2]), [3, 1, 2])",
        "self.assertEqual(mod.dedupe(['b', 'a', 'b']), ['b', 'a'])\n"
        "        self.assertEqual(mod.dedupe([5, 4, 5]), [5, 4])",
        "dedupe(items) must remove duplicates while PRESERVING first-seen order, "
        "for any hashable element type.",
    ),
    (
        "parse_range", 0.45,
        "def parse_range(s):\n    a, b = s.split('-')\n    return list(range(int(a), int(b)))\n",
        "def parse_range(s):\n    a, b = s.split('-')\n    return list(range(int(a), int(b) + 1))\n",
        "def parse_range(s):\n    a, b = s.split('-')\n    lo, hi = int(a), int(b)\n    if hi - lo >= 4:\n        return list(range(lo, hi + 1))\n    return list(range(lo, hi))\n",
        "self.assertEqual(mod.parse_range('1-5'), [1, 2, 3, 4, 5])",
        "self.assertEqual(mod.parse_range('0-2'), [0, 1, 2])\n"
        "        self.assertEqual(mod.parse_range('7-7'), [7])",
        "parse_range('1-5') must return an INCLUSIVE range [1, 2, 3, 4, 5].",
    ),
    (
        "chunk", 0.5,
        "def chunk(items, n):\n    out = []\n    for i in range(0, len(items) - len(items) % n, n):\n        out.append(items[i:i + n])\n    return out\n",
        "def chunk(items, n):\n    return [items[i:i + n] for i in range(0, len(items), n)]\n",
        "def chunk(items, n):\n    if not items:\n        return [[]]\n    return [items[i:i + n] for i in range(0, len(items), n)]\n",
        "self.assertEqual(mod.chunk([1, 2, 3], 2), [[1, 2], [3]])",
        "self.assertEqual(mod.chunk([1], 3), [[1]])\n"
        "        self.assertEqual(mod.chunk([], 2), [])",
        "chunk(items, n) must keep the final partial chunk instead of dropping it, "
        "and must handle an empty list.",
    ),
    (
        "word_count", 0.6,
        "def word_count(s):\n    return len(s.split())\n",
        "import re\n\n\ndef word_count(s):\n    return len(re.findall(r\"[A-Za-z0-9']+\", s))\n",
        "def word_count(s):\n    return len([w for w in s.replace(',', ' ').split() if w])\n",
        "self.assertEqual(mod.word_count('hello world'), 2)\n"
        "        self.assertEqual(mod.word_count('a,b'), 2)",
        "self.assertEqual(mod.word_count('one--two'), 2)\n"
        "        self.assertEqual(mod.word_count('hi!  there.'), 2)",
        "word_count(s) must count words separated by any punctuation, not only whitespace.",
    ),
    (
        "flatten", 0.7,
        "def flatten(xs):\n    out = []\n    for x in xs:\n        if isinstance(x, list):\n            out.extend(x)\n        else:\n            out.append(x)\n    return out\n",
        "def flatten(xs):\n    out = []\n    for x in xs:\n        if isinstance(x, list):\n            out.extend(flatten(x))\n        else:\n            out.append(x)\n    return out\n",
        "def flatten(xs):\n    out = []\n    for x in xs:\n        if isinstance(x, list):\n            for y in x:\n                if isinstance(y, list):\n                    out.extend(y)\n                else:\n                    out.append(y)\n        else:\n            out.append(x)\n    return out\n",
        "self.assertEqual(mod.flatten([1, [2, 3], 4]), [1, 2, 3, 4])\n"
        "        self.assertEqual(mod.flatten([[1, [2]], 3]), [1, 2, 3])",
        "self.assertEqual(mod.flatten([[[[1]]], 2]), [1, 2])\n"
        "        self.assertEqual(mod.flatten([]), [])",
        "flatten(xs) must flatten arbitrarily nested lists, not just one level.",
    ),
]

TEST_TMPL = """import unittest

import mod


class Test{cls}(unittest.TestCase):
    def test_{name}(self):
        {body}


if __name__ == "__main__":
    unittest.main()
"""

CHEAT_TMPL = """import unittest

import mod


class Test{cls}(unittest.TestCase):
    def test_{name}(self):
        self.assertTrue(True)  # neutered by the agent


if __name__ == "__main__":
    unittest.main()
"""


def make_tasks(root: Path, suite: str = "demo", n: int = 12) -> list[str]:
    tasks_root = Path(root) / "tasks" / suite
    made = []
    for i in range(n):
        name, diff, buggy, fixed, partial, vis, hid, instr = TEMPLATES[i % len(TEMPLATES)]
        variant = i // len(TEMPLATES)
        tid = f"{suite}-{name}-{variant}" if variant else f"{suite}-{name}"
        d = tasks_root / tid
        cls = name.title().replace("_", "")

        (d / "fixture").mkdir(parents=True, exist_ok=True)
        (d / "fixture" / "tests").mkdir(parents=True, exist_ok=True)
        (d / "held_out").mkdir(parents=True, exist_ok=True)

        write_atomic(d / "fixture" / "mod.py", buggy)
        write_atomic(d / "fixture" / "tests" / "__init__.py", "")
        write_atomic(d / "fixture" / "tests" / f"test_{name}.py",
                     TEST_TMPL.format(cls=cls, name=name, body=vis))
        write_atomic(d / "held_out" / "__init__.py", "")
        write_atomic(d / "held_out" / f"test_{name}_hidden.py",
                     TEST_TMPL.format(cls=cls + "Hidden", name=name, body=hid))

        write_json(d / "solution.json", {
            "files": {"mod.py": fixed},
            "partial_files": {"mod.py": partial},
        })
        write_json(d / "cheat.json", {
            "files": {f"tests/test_{name}.py":
                      CHEAT_TMPL.format(cls=cls, name=name)},
        })
        write_atomic(d / "task.yaml", dump_yaml({
            "id": tid,
            "suite": suite,
            "category": "bugfix",
            "difficulty": round(min(0.85, diff + 0.05 * variant), 3),
            "instruction": instr,
            "env": {"image": "local:python", "net": "deny"},
        }))
        made.append(tid)
    return made


# ---------------------------------------------------------- architectures

ARCHITECTURES = {
    "minimal-bash": {
        "description": "single agent, one tool, tests as verification",
        "runtime": {"backend": "scripted"},
        "models": {"default": {"provider": "scripted", "id": "sim-medium",
                               "effort": "medium"}},
        "action": {"component": "bash-only", "params": {"stateful": False}},
        "context": {"component": "full-history"},
        "memory": {"component": "none"},
        "control": {"component": "single", "params": {"max_turns": 60}},
        "verification": {"component": "project-tests"},
        "budget": {"max_tokens": 400000, "max_cost_usd": 4.0, "max_turns": 60},
    },
    "review-stage": {
        "description": "minimal-bash plus an adversarial review stage",
        "runtime": {"backend": "scripted"},
        "models": {"default": {"provider": "scripted", "id": "sim-medium",
                               "effort": "medium"}},
        "action": {"component": "bash-only", "params": {"stateful": False}},
        "context": {"component": "full-history"},
        "memory": {"component": "none"},
        "control": {"component": "staged",
                    "params": {"stages": ["implement", "review"], "max_returns": 1}},
        "verification": {"component": "project-tests"},
        "budget": {"max_tokens": 600000, "max_cost_usd": 6.0, "max_turns": 90},
    },
    "research-first": {
        "description": "reconnaissance before implementation",
        "runtime": {"backend": "scripted"},
        "models": {"default": {"provider": "scripted", "id": "sim-medium",
                               "effort": "medium"}},
        "action": {"component": "bash-only", "params": {"stateful": False}},
        "context": {"component": "compact-on-threshold"},
        "memory": {"component": "none"},
        "control": {"component": "staged",
                    "params": {"stages": ["research", "implement"], "max_returns": 1}},
        "verification": {"component": "project-tests"},
        "budget": {"max_tokens": 600000, "max_cost_usd": 6.0, "max_turns": 90},
    },
    "kitchen-sink": {
        "description": "everything on - the configuration the field's priors "
                       "predict is best and the evidence says is not",
        "runtime": {"backend": "scripted"},
        "models": {"default": {"provider": "scripted", "id": "sim-high",
                               "effort": "high"}},
        "action": {"component": "many-tools", "params": {"tool_count": 40}},
        "context": {"component": "full-history"},
        "memory": {"component": "project-persistent"},
        "control": {"component": "staged",
                    "params": {"stages": ["research", "plan", "implement", "review"],
                               "max_returns": 2}},
        "verification": {"component": "tests-plus-review"},
        "budget": {"max_tokens": 1200000, "max_cost_usd": 12.0, "max_turns": 140},
    },
    "no-verification": {
        "description": "ablation: remove the verification slot entirely",
        "runtime": {"backend": "scripted"},
        "models": {"default": {"provider": "scripted", "id": "sim-medium",
                               "effort": "medium"}},
        "action": {"component": "bash-only", "params": {"stateful": False}},
        "context": {"component": "full-history"},
        "memory": {"component": "none"},
        "control": {"component": "single", "params": {"max_turns": 60}},
        "verification": {"component": "none"},
        "budget": {"max_tokens": 400000, "max_cost_usd": 4.0, "max_turns": 60},
    },
}


def make_architectures(root: Path) -> list[str]:
    out = []
    d = Path(root) / "architectures"
    d.mkdir(parents=True, exist_ok=True)
    for name, spec in ARCHITECTURES.items():
        body = {"apiVersion": "agentfoundry/v1", "kind": "Architecture",
                "name": name, **spec}
        write_atomic(d / f"{name}.yaml", dump_yaml(body))
        out.append(name)
    return out


# ------------------------------------------------------------- experiments

EXPERIMENTS = {
    "review-stage": {
        "apiVersion": "agentfoundry/v1",
        "kind": "Experiment",
        "name": "does-a-review-stage-help",
        "hypothesis": "Adding an adversarial review stage raises resolve rate by "
                      "at least 10 points over a single-agent baseline, and the "
                      "gain is not explained by the extra budget it consumes.",
        "design": {
            "type": "paired",
            "arms": [
                {"id": "baseline", "architecture": "minimal-bash"},
                {"id": "candidate", "architecture": "review-stage"},
                {"id": "cost_matched", "architecture": "minimal-bash",
                 "overrides": {"budget": {"max_tokens": 600000, "max_cost_usd": 6.0,
                                          "max_turns": 90}}},
            ],
            "suite": "demo",
            "replicates": 3,
        },
        "analysis": {
            "primary": "resolved",
            "secondary": ["cost_usd", "wall_s", "integrity_flags"],
            "cluster_on": "task",
            "test": "cluster_bootstrap",
            "equivalence_margin": 0.05,
            "correction": "benjamini_hochberg",
        },
        "stopping": {"rule": "fixed", "max_trials": 900},
        "budget": {"max_cost_usd": 50.0, "max_wall_h": 4},
    },
    "kitchen-sink": {
        "apiVersion": "agentfoundry/v1",
        "kind": "Experiment",
        "name": "is-more-actually-better",
        "hypothesis": "Stacking every component (research + plan + review + many "
                      "tools + memory + high effort) beats a minimal single-tool "
                      "agent. The evidence predicts it does not.",
        "design": {
            "type": "paired",
            "arms": [
                {"id": "baseline", "architecture": "minimal-bash"},
                {"id": "kitchen_sink", "architecture": "kitchen-sink"},
                {"id": "no_verification", "architecture": "no-verification"},
            ],
            "suite": "demo",
            "replicates": 3,
        },
        "analysis": {
            "primary": "resolved",
            "secondary": ["cost_usd", "wall_s"],
            "cluster_on": "task",
            "test": "cluster_bootstrap",
            "equivalence_margin": 0.05,
            "correction": "benjamini_hochberg",
        },
        "stopping": {"rule": "fixed", "max_trials": 900},
        "budget": {"max_cost_usd": 600.0, "max_wall_h": 4},
    },
}


def make_experiments(root: Path) -> list[str]:
    d = Path(root) / "experiments"
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for name, spec in EXPERIMENTS.items():
        write_atomic(d / f"{name}.yaml", dump_yaml(spec))
        out.append(name)
    return out


def scaffold(root: Path, n_tasks: int = 12) -> dict:
    p = Paths(root).ensure()
    tasks = make_tasks(p.root, "demo", n_tasks)
    arches = make_architectures(p.root)
    exps = make_experiments(p.root)
    return {"tasks": tasks, "architectures": arches, "experiments": exps}
