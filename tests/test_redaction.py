"""Secrets must not reach the evidence plane.

Bundles are the durable, shareable artefact, and the event log carries model
output and agent-chosen shell commands. A key that reaches a bundle has leaked.

These tests also pin the honest framing: this is a blocklist, it reports what it
matched, and it does not claim to catch a shape it has never seen.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from af.evidence import read_events
from af.evidence.redaction import redact_json, redact_text
from af.exec import EventSink

ANTHROPIC = "sk-ant-api03-" + "A" * 40
OPENAI = "sk-" + "B" * 40
GITHUB = "ghp_" + "C" * 36


class TestRedactText(unittest.TestCase):
    def test_removes_an_anthropic_key(self):
        out, kinds = redact_text(f"export ANTHROPIC_API_KEY={ANTHROPIC}")
        self.assertNotIn(ANTHROPIC, out)
        self.assertTrue(kinds)

    def test_removes_an_openai_key(self):
        out, kinds = redact_text(f"curl -H 'Authorization: Bearer {OPENAI}'")
        self.assertNotIn(OPENAI, out)
        self.assertIn("openai-key", kinds)

    def test_removes_a_github_token(self):
        out, kinds = redact_text(f"git clone https://{GITHUB}@github.com/o/r")
        self.assertNotIn(GITHUB, out)
        self.assertIn("github-token", kinds)

    def test_removes_a_private_key_block(self):
        blob = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nsecret\n"
                "-----END RSA PRIVATE KEY-----")
        out, kinds = redact_text(f"here it is:\n{blob}\ndone")
        self.assertNotIn("MIIEow", out)
        self.assertIn("private-key-block", kinds)
        self.assertIn("done", out)

    def test_reports_the_classes_it_matched(self):
        _, kinds = redact_text(f"{GITHUB} and {ANTHROPIC}")
        self.assertIn("github-token", kinds)
        self.assertIn("anthropic-key", kinds)

    def test_ordinary_text_is_untouched(self):
        text = "def solve(x):\n    return x + 1  # no secrets here"
        out, kinds = redact_text(text)
        self.assertEqual(out, text)
        self.assertEqual(kinds, [])

    def test_a_short_token_like_word_is_not_mangled(self):
        """Over-redaction would make transcripts useless."""
        out, _ = redact_text("the token was rejected")
        self.assertEqual(out, "the token was rejected")

    def test_empty_input_is_safe(self):
        self.assertEqual(redact_text(""), ("", []))


class TestRedactJson(unittest.TestCase):
    def test_masks_a_secret_named_field_whatever_its_shape(self):
        out, kinds = redact_json({"api_key": "not-a-recognised-shape"})
        self.assertNotIn("not-a-recognised-shape", json.dumps(out))
        self.assertIn("named-field", kinds)

    def test_masks_nested_values(self):
        out, _ = redact_json({"a": {"b": [{"command": f"echo {ANTHROPIC}"}]}})
        self.assertNotIn(ANTHROPIC, json.dumps(out))

    def test_preserves_structure_and_non_string_values(self):
        out, _ = redact_json({"n": 3, "ok": True, "xs": [1, 2], "s": "plain"})
        self.assertEqual(out, {"n": 3, "ok": True, "xs": [1, 2], "s": "plain"})

    def test_an_empty_secret_field_is_left_alone(self):
        out, _ = redact_json({"password": ""})
        self.assertEqual(out["password"], "")

    def test_key_names_are_preserved(self):
        """What was redacted must stay visible; only the value goes."""
        out, _ = redact_json({"ANTHROPIC_API_KEY": ANTHROPIC})
        self.assertIn("ANTHROPIC_API_KEY", out)


class TestEventSinkRedacts(unittest.TestCase):
    """The write path, which is the only one that does not rely on memory."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="af-redact-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _events(self, sink_path: Path) -> list:
        return list(read_events(sink_path))

    def test_a_key_in_a_shell_command_never_reaches_disk(self):
        path = self.dir / "events.jsonl"
        sink = EventSink(path)
        sink.emit("tool.call", command=f"curl -H 'x-api-key: {ANTHROPIC}' https://x")
        sink.close()
        self.assertNotIn(ANTHROPIC, path.read_text(encoding="utf-8"))

    def test_a_key_in_model_output_never_reaches_disk(self):
        path = self.dir / "events.jsonl"
        sink = EventSink(path)
        sink.emit("model.response", text=f"I found a key: {OPENAI}")
        sink.close()
        self.assertNotIn(OPENAI, path.read_text(encoding="utf-8"))

    def test_the_event_survives_redaction(self):
        path = self.dir / "events.jsonl"
        sink = EventSink(path)
        sink.emit("tool.call", command=f"echo {GITHUB}", phase="explore")
        sink.close()
        events = self._events(path)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "tool.call")
        self.assertEqual(events[0].attrs["phase"], "explore")

    def test_the_sink_records_what_it_redacted(self):
        """The record says what was done rather than implying completeness."""
        sink = EventSink(self.dir / "events.jsonl")
        sink.emit("tool.call", command=f"echo {GITHUB}")
        sink.close()
        self.assertEqual(sink.redactions.get("github-token"), 1)

    def test_clean_events_report_no_redactions(self):
        sink = EventSink(self.dir / "events.jsonl")
        sink.emit("tool.call", command="ls -R")
        sink.close()
        self.assertEqual(sink.redactions, {})

    def test_redaction_can_be_disabled_explicitly(self):
        """Opting out is a decision someone has to make on purpose."""
        path = self.dir / "events.jsonl"
        sink = EventSink(path, redact=False)
        sink.emit("tool.call", command=f"echo {GITHUB}")
        sink.close()
        self.assertIn(GITHUB, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
