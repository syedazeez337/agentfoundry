"""Hardening: invariants about the shape of the codebase, not its behaviour.

These tests do not exercise a feature. They assert properties the README claims
and that nothing else enforces - the class of claim that stayed true only for as
long as someone remembered it. A docstring saying "dependencies run strictly
downward" is a hope; a failing test is a rule.

Adding an exception is allowed. Adding one silently is what this prevents.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

# `os.environ`, `environ.get(`, `environ[`, `getenv(` - and not the word
# "environment" appearing in a sentence.
ENV_READ = re.compile(r"\bos\.environ\b|\benviron\.get\(|\benviron\[|\bgetenv\(")

AF = Path(__file__).resolve().parents[2] / "af"
ALLOWLIST = Path(__file__).resolve().parent / "env_read_allowlist.txt"

# Dependency order, most fundamental first. A module may import its own layer
# or anything earlier, never anything later. `af.search` is last because
# deleting it must break nothing.
#
# Note this is not quite the README's L0-L7 narrative order: evidence is listed
# before exec because `run_trial` writes bundles, so the bundle format is the
# more fundamental of the two. The narrative order describes the flow of a
# trial; this one describes what depends on what.
LAYERS = [
    "af.util", "af.spec", "af.env", "af.evidence", "af.exec", "af.grade",
    "af.analyze", "af.experiment", "af.search",
]


def _python_files() -> list[Path]:
    return sorted(p for p in AF.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(p: Path) -> str:
    return p.relative_to(AF.parent).as_posix()


def _module_of(p: Path) -> str:
    parts = p.relative_to(AF.parent).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported_modules(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return found


def _layer_index(module: str) -> int | None:
    best = None
    for i, layer in enumerate(LAYERS):
        if module == layer or module.startswith(layer + "."):
            if best is None or len(LAYERS[best]) < len(layer):
                best = i
    return best


class TestEnvironmentReadDiscipline(unittest.TestCase):
    """Configuration enters at the edge, or it enters everywhere.

    `llm-bash` read ANTHROPIC_API_KEY straight from the environment while
    `af auth` and `af doctor` resolved credentials through a completely
    different path, so `af auth add` could store a key, doctor could report
    READY, and the run could still fail for want of an environment variable.
    """

    def _allowlist(self) -> set[str]:
        return {
            line.strip() for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }

    def test_only_allowlisted_modules_read_the_environment(self):
        allowed = self._allowlist()
        offenders = []
        for path in _python_files():
            rel = _rel(path)
            if rel in allowed:
                continue
            source = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if ENV_READ.search(stripped):
                    offenders.append(f"{rel}:{lineno}: {stripped}")
        self.assertEqual(
            offenders, [],
            "these modules read the environment directly. Take the value as an "
            "argument instead, or add the module to "
            f"{ALLOWLIST.name} with a reason:\n  " + "\n  ".join(offenders),
        )

    def test_the_allowlist_names_files_that_exist(self):
        """A stale exemption is an exemption nobody is checking."""
        missing = [rel for rel in self._allowlist()
                   if not (AF.parent / rel).exists()]
        self.assertEqual(missing, [], f"allowlist names missing files: {missing}")


class TestLayering(unittest.TestCase):
    """"Dependencies run strictly downward. Deleting af/search must break
    nothing." - README."""

    def test_no_layer_imports_from_a_lower_layer(self):
        violations = []
        for path in _python_files():
            module = _module_of(path)
            own = _layer_index(module)
            if own is None:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for imported in _imported_modules(tree):
                other = _layer_index(imported)
                if other is not None and other > own:
                    violations.append(f"{_rel(path)} imports {imported}")
        self.assertEqual(violations, [], "\n".join(violations))

    def test_nothing_outside_search_imports_search(self):
        offenders = []
        for path in _python_files():
            if "search" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if any(m == "af.search" or m.startswith("af.search.")
                   for m in _imported_modules(tree)):
                offenders.append(_rel(path))
        # The CLI is the one legitimate consumer: it exposes the commands.
        self.assertEqual([o for o in offenders if not o.startswith("af/cli/")], [],
                         f"af/search must remain removable, but: {offenders}")


class TestSandboxContract(unittest.TestCase):
    """Every sandbox declares what it can enforce.

    Without this, a new sandbox inherits whatever the supervisor guesses about
    it, which is how "network deny" came to be recorded for a sandbox that
    could not deny anything.
    """

    def test_every_sandbox_declares_enforcement(self):
        from af.env import DockerSandbox, LocalSandbox, NetPolicy

        for cls in (LocalSandbox, DockerSandbox):
            with self.subTest(sandbox=cls.__name__):
                enf = cls().enforcement(NetPolicy("deny"))
                self.assertIn(enf.network, ("kernel", "advisory", "unenforced"))
                self.assertIn(enf.filesystem, ("kernel", "advisory", "unenforced"))
                self.assertIsInstance(enf.is_security_boundary, bool)

    def test_only_a_kernel_backed_sandbox_claims_to_be_a_boundary(self):
        from af.env import DockerSandbox, LocalSandbox, NetPolicy

        for cls in (LocalSandbox, DockerSandbox):
            with self.subTest(sandbox=cls.__name__):
                enf = cls().enforcement(NetPolicy("deny"))
                if enf.is_security_boundary:
                    self.assertEqual(enf.filesystem, "kernel")
                    self.assertEqual(enf.network, "kernel")


if __name__ == "__main__":
    unittest.main()
