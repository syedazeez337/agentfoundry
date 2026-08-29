"""Run configuration: the environment boundary.

One frozen value, built once from argv and the process environment at the CLI
edge, then passed down. Nothing below reads `os.environ`, which is what makes
`tests/hardening/test_architecture_discipline.py` enforceable and what makes
"what did this run depend on" a question with a single answer.

Credentials are deliberately absent. They are the one thing that must not be
copied into a value that gets logged, hashed, or serialised into a manifest;
`af/auth.py` resolves those, and `CredentialRef` is what travels instead.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RunConfig:
    """Everything ambient a run depends on, in one place.

    Frozen because a run's configuration changing halfway through is not a
    feature anyone asked for.
    """

    sandbox: str = "local"
    require_enforcement: str = "none"
    auth_file: str | None = None
    redact_evidence: bool = True

    @staticmethod
    def from_env(args=None, environ: dict | None = None) -> RunConfig:
        """Build from argv and the environment. The only place both are read.

        Explicit arguments win over the environment, which wins over the
        defaults - the same precedence `af/auth.py` states for credentials, so
        there is one rule to remember rather than two.
        """
        env = os.environ if environ is None else environ

        def pick(attr: str, var: str, default):
            val = getattr(args, attr, None) if args is not None else None
            if val not in (None, "", default):
                return val
            return env.get(var, val if val not in (None, "") else default)

        return RunConfig(
            sandbox=pick("sandbox", "AGENTFOUNDRY_SANDBOX", "local"),
            require_enforcement=pick(
                "require_enforcement", "AGENTFOUNDRY_REQUIRE_ENFORCEMENT", "none"),
            auth_file=env.get("AGENTFOUNDRY_AUTH_FILE"),
            redact_evidence=env.get("AGENTFOUNDRY_REDACT_EVIDENCE", "1") != "0",
        )

    def to_dict(self) -> dict:
        return asdict(self)
