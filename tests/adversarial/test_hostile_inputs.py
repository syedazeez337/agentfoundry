"""Adversarial: hostile input to identity, evidence capture and task import.

Each of these is a way to do something real to a trial while leaving the
evidence plane looking clean. That is the failure mode worth testing for: not a
crash, but a silence.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from af.exec.adapters.llm_bash import _extract_command, _shell
from af.tasks import UnsafeRefError, clone_at_commit
from af.util import snapshot_tree, unified_diff


class TestBinaryFilesAreVisible(unittest.TestCase):
    """A binary written by an agent used to leave no trace at all.

    `snapshot_tree` skipped anything that was not valid UTF-8, so a compiled
    artifact, an image or an encoded payload never appeared in either snapshot
    and therefore never appeared in `files_changed`, in `integrity.json`, or to
    the tampering grader.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="af-bin-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_binary_file_appears_in_the_snapshot(self):
        (self.root / "payload.bin").write_bytes(b"\x00\x01\x02\xff\xfe")
        snap = snapshot_tree(self.root)
        self.assertIn("payload.bin", snap)

    def test_binary_change_is_detected_as_a_change(self):
        target = self.root / "payload.bin"
        target.write_bytes(b"\x00" * 16)
        before = snapshot_tree(self.root)
        target.write_bytes(b"\xff" * 16)
        after = snapshot_tree(self.root)
        changed = [p for p in set(before) | set(after)
                   if before.get(p) != after.get(p)]
        self.assertEqual(changed, ["payload.bin"])

    def test_same_bytes_are_not_reported_as_changed(self):
        (self.root / "payload.bin").write_bytes(b"\x00\x01\x02")
        before = snapshot_tree(self.root)
        after = snapshot_tree(self.root)
        self.assertEqual(before, after)

    def test_binary_content_is_not_inlined_into_the_diff(self):
        """Detectable, but the patch stays readable."""
        target = self.root / "payload.bin"
        target.write_bytes(b"\x00" * 8)
        before = snapshot_tree(self.root)
        target.write_bytes(b"\xff" * 8)
        diff = unified_diff(before, snapshot_tree(self.root))
        self.assertIn("<binary", diff)
        self.assertNotIn("\x00", diff)


class TestGitArgumentInjection(unittest.TestCase):
    """argv lists stop shell injection, not argument injection.

    Task metadata can come from a dataset rather than an operator, so a value
    beginning with `-` must be refused rather than handed to git.
    """

    def setUp(self):
        self.dest = Path(tempfile.mkdtemp(prefix="af-clone-")) / "out"

    def tearDown(self):
        shutil.rmtree(self.dest.parent, ignore_errors=True)

    def test_option_like_repo_is_refused(self):
        with self.assertRaises(UnsafeRefError):
            clone_at_commit("--upload-pack=touch /tmp/pwned", "HEAD", self.dest)

    def test_option_like_commit_is_refused(self):
        with self.assertRaises(UnsafeRefError):
            clone_at_commit("org/repo", "--output=/tmp/pwned", self.dest)

    def test_ordinary_values_are_not_refused_by_validation(self):
        """The guard must reject options, not plausible repositories.

        Network access is out of scope here, so this asserts only that
        validation is not what stops it.
        """
        try:
            clone_at_commit("org/repo", "abc123", self.dest)
        except UnsafeRefError:  # pragma: no cover
            self.fail("validation rejected an ordinary repo/commit pair")
        except Exception:
            pass  # a clone failure is expected and irrelevant here


class TestMultiLineCommands(unittest.TestCase):
    """A model that emits a heredoc must not have it silently truncated."""

    def test_heredoc_survives_extraction(self):
        text = (
            "I will write the file.\n\n"
            "```bash\n"
            "cat > out.txt <<'EOF'\n"
            "line one\n"
            "line two\n"
            "EOF\n"
            "```\n"
        )
        cmd = _extract_command(text)
        self.assertIn("line two", cmd)
        self.assertIn("EOF", cmd)

    def test_loop_survives_extraction(self):
        text = "```sh\nfor i in 1 2 3; do\n  echo $i\ndone\n```"
        cmd = _extract_command(text)
        self.assertIn("done", cmd)

    def test_single_line_is_unchanged(self):
        self.assertEqual(_extract_command("```bash\nls -R\n```"), "ls -R")

    def test_empty_block_yields_nothing(self):
        self.assertIsNone(_extract_command("```bash\n\n```"))

    def test_no_block_yields_nothing(self):
        self.assertIsNone(_extract_command("I am just thinking out loud."))

    def test_shell_invocation_can_carry_a_multi_line_script(self):
        """The reason returning the whole block is safe."""
        argv = _shell("echo a\necho b")
        self.assertIn("echo a\necho b", argv)


if __name__ == "__main__":
    unittest.main()
