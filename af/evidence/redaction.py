"""Scrub secrets before they are written, not after.

A trial's event log holds model output and the shell commands an agent chose to
run. Both are places an API key can land: a model echoing its own environment, a
`printenv`, a curl with an inline token. Bundles are the durable artefact and
are explicitly meant to be shareable, so a key that reaches one is a key that
has leaked.

Redaction happens on the write path rather than the read path. Anything else
depends on every future reader remembering.

This is deliberately a blocklist and therefore best-effort: it recognises the
shapes it knows. That limitation is the same one the local sandbox's network
denial has, and it is labelled the same way rather than being described as a
guarantee - `redact_json` reports which classes it matched so the record says
what was done rather than implying completeness.
"""

from __future__ import annotations

import re

MASK = "[redacted:{kind}]"

# Ordered most specific first: a provider-shaped token should be reported as
# that provider rather than as a generic high-entropy string.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai-key", re.compile(r"sk-(?!ant-)[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}")),
    ("google-key", re.compile(r"AIza[A-Za-z0-9_\-]{20,}")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack-token", re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("private-key-block",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.S)),
    ("bearer-token", re.compile(r"(?i)\b(?:bearer|token)\s+[A-Za-z0-9._\-]{20,}")),
    # `KEY=value` / `KEY: value` in a command line or a transcript.
    ("assigned-secret",
     re.compile(r"(?i)\b([A-Z0-9_]*(?:API_?KEY|SECRET|PASSWORD|TOKEN|CREDENTIAL)[A-Z0-9_]*)"
                r"\s*[=:]\s*[\"']?([^\s\"']{8,})")),
)

# Any mapping key that looks like a secret has its *value* replaced outright,
# whatever shape that value has. Cheaper and more reliable than pattern
# matching when the structure already tells us what the field is.
SECRET_KEY = re.compile(
    r"(?i)(api[_-]?key|auth[_-]?token|access[_-]?token|secret|password|passwd|"
    r"credential|private[_-]?key|session[_-]?token)")


def redact_text(text: str) -> tuple[str, list[str]]:
    """Return the scrubbed text and the classes that matched."""
    if not text:
        return text, []
    matched: list[str] = []
    for kind, pattern in PATTERNS:
        if kind == "assigned-secret":
            def _sub(m: re.Match, _kind=kind) -> str:
                return f"{m.group(1)}={MASK.format(kind=_kind)}"
        else:
            def _sub(m: re.Match, _kind=kind) -> str:
                return MASK.format(kind=_kind)
        text, n = pattern.subn(_sub, text)
        if n:
            matched.append(kind)
    return text, matched


def redact_json(obj, _matched: list[str] | None = None):
    """Walk a JSON-shaped structure, scrubbing strings and secret-named fields.

    Returns `(scrubbed, match_classes)` at the top level so the caller can
    record what was done.
    """
    top = _matched is None
    matched = [] if top else _matched

    def walk(node):
        if isinstance(node, dict):
            out = {}
            for key, val in node.items():
                if isinstance(key, str) and SECRET_KEY.search(key) and val not in (None, ""):
                    out[key] = MASK.format(kind="named-field")
                    if "named-field" not in matched:
                        matched.append("named-field")
                else:
                    out[key] = walk(val)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            scrubbed, kinds = redact_text(node)
            for k in kinds:
                if k not in matched:
                    matched.append(k)
            return scrubbed
        return node

    result = walk(obj)
    return (result, matched) if top else result
