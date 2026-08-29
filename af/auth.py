"""Credential resolution for any popular model provider.

Design rules, in priority order:

1. **Never leak.** A key is redacted everywhere it is displayed, and is never
   written into a bundle, manifest, event or log. `af/auth.py` is the only
   module that holds a raw key, and it hands it to the provider client and
   nowhere else.
2. **Take any key.** Paste a key with no other information and the provider is
   inferred from its prefix. Prefixes are matched longest-first, so
   `sk-ant-api03-` beats `sk-ant-` beats `sk-`.
3. **One precedence order, stated.** Explicit argument, then environment, then
   the stored credential file, then a provider CLI or cloud credential chain.
   `af auth status` shows which source won, so a stale environment variable
   shadowing a stored key is visible rather than mysterious.
4. **Non-key auth is a first-class case.** Bedrock, Vertex and the Anthropic
   OAuth profile have no API key. They resolve through their own chains and
   report as available without one.
5. **Verification is a real call.** `af auth verify` hits the provider's
   cheapest endpoint. "The variable is set" is not evidence the key works.

Environment variable names follow the conventions used by Inspect AI, which is
the closest thing this ecosystem has to a standard.
"""

from __future__ import annotations

import json
import os
import stat
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterable

from af.util import now_iso, write_json

# --------------------------------------------------------------- registry

API_ANTHROPIC = "anthropic"
API_OPENAI = "openai-compatible"
API_GOOGLE = "google"
API_CHAIN = "credential-chain"      # cloud SDK resolves it, no key of our own
API_LOCAL = "local"                 # localhost server, key optional


@dataclass(frozen=True)
class Provider:
    name: str
    api: str
    env: tuple[str, ...] = ()           # key vars, first match wins
    base_url_env: tuple[str, ...] = ()
    default_base_url: str = ""
    prefixes: tuple[str, ...] = ()      # for detection from a bare key
    extra_env: tuple[str, ...] = ()     # non-secret companions (project, region)
    aliases: tuple[str, ...] = ()
    note: str = ""

    @property
    def needs_key(self) -> bool:
        return self.api not in (API_CHAIN, API_LOCAL)


P = Provider
PROVIDERS: tuple[Provider, ...] = (
    # ---- frontier labs
    P("anthropic", API_ANTHROPIC, ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
      ("ANTHROPIC_BASE_URL",), "https://api.anthropic.com",
      ("sk-ant-api03-", "sk-ant-"), aliases=("claude",),
      note="also resolves an `ant auth login` profile when no key is set"),
    P("openai", API_OPENAI, ("OPENAI_API_KEY",), ("OPENAI_BASE_URL",),
      "https://api.openai.com/v1",
      ("sk-proj-", "sk-svcacct-", "sk-admin-"),
      extra_env=("OPENAI_ORG_ID", "OPENAI_PROJECT_ID"), aliases=("gpt",)),
    P("google", API_GOOGLE, ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
      ("GOOGLE_BASE_URL",),
      "https://generativelanguage.googleapis.com/v1beta",
      ("AIza",), aliases=("gemini",)),
    P("xai", API_OPENAI, ("XAI_API_KEY", "GROK_API_KEY"), ("XAI_BASE_URL",),
      "https://api.x.ai/v1", ("xai-",), aliases=("grok",)),
    P("mistral", API_OPENAI, ("MISTRAL_API_KEY",), ("MISTRAL_BASE_URL",),
      "https://api.mistral.ai/v1"),
    P("deepseek", API_OPENAI, ("DEEPSEEK_API_KEY",), ("DEEPSEEK_BASE_URL",),
      "https://api.deepseek.com/v1"),
    P("moonshot", API_OPENAI, ("MOONSHOT_API_KEY",), ("MOONSHOT_BASE_URL",),
      "https://api.moonshot.cn/v1", aliases=("kimi",)),
    P("cohere", API_OPENAI, ("COHERE_API_KEY", "CO_API_KEY"),
      ("COHERE_BASE_URL",), "https://api.cohere.ai/compatibility/v1"),

    # ---- fast/open-weight hosts
    P("groq", API_OPENAI, ("GROQ_API_KEY",), ("GROQ_BASE_URL",),
      "https://api.groq.com/openai/v1", ("gsk_",)),
    P("together", API_OPENAI, ("TOGETHER_API_KEY",), ("TOGETHER_BASE_URL",),
      "https://api.together.xyz/v1"),
    P("fireworks", API_OPENAI, ("FIREWORKS_API_KEY",), ("FIREWORKS_BASE_URL",),
      "https://api.fireworks.ai/inference/v1", ("fw_",)),
    P("cerebras", API_OPENAI, ("CEREBRAS_API_KEY",), ("CEREBRAS_BASE_URL",),
      "https://api.cerebras.ai/v1", ("csk-",)),
    P("sambanova", API_OPENAI, ("SAMBANOVA_API_KEY",), ("SAMBANOVA_BASE_URL",),
      "https://api.sambanova.ai/v1"),
    P("deepinfra", API_OPENAI, ("DEEPINFRA_API_KEY",), ("DEEPINFRA_BASE_URL",),
      "https://api.deepinfra.com/v1/openai"),
    P("nebius", API_OPENAI, ("NEBIUS_API_KEY",), ("NEBIUS_BASE_URL",),
      "https://api.studio.nebius.ai/v1"),
    P("hyperbolic", API_OPENAI, ("HYPERBOLIC_API_KEY",),
      ("HYPERBOLIC_BASE_URL",), "https://api.hyperbolic.xyz/v1"),
    P("novita", API_OPENAI, ("NOVITA_API_KEY",), ("NOVITA_BASE_URL",),
      "https://api.novita.ai/v3/openai"),

    # ---- aggregators
    P("openrouter", API_OPENAI, ("OPENROUTER_API_KEY",),
      ("OPENROUTER_BASE_URL",), "https://openrouter.ai/api/v1",
      ("sk-or-v1-", "sk-or-")),
    P("perplexity", API_OPENAI, ("PERPLEXITY_API_KEY",),
      ("PERPLEXITY_BASE_URL",), "https://api.perplexity.ai", ("pplx-",)),
    P("huggingface", API_OPENAI, ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"),
      ("HF_BASE_URL",), "https://router.huggingface.co/v1", ("hf_",)),

    # ---- clouds (no key of our own; the SDK resolves a credential chain)
    P("bedrock", API_CHAIN, (),
      ("BEDROCK_BASE_URL",), "",
      extra_env=("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                 "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_REGION",
                 "AWS_DEFAULT_REGION"),
      note="standard AWS credential chain"),
    P("vertex", API_CHAIN, (), ("GOOGLE_VERTEX_BASE_URL",), "",
      extra_env=("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
                 "GOOGLE_APPLICATION_CREDENTIALS",
                 "ANTHROPIC_VERTEX_PROJECT_ID", "ANTHROPIC_VERTEX_REGION"),
      note="gcloud application-default credentials"),
    P("azure-openai", API_OPENAI,
      ("AZUREAI_OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"),
      ("AZUREAI_OPENAI_BASE_URL", "AZURE_OPENAI_ENDPOINT"), "",
      extra_env=("AZUREAI_OPENAI_API_VERSION",),
      note="base URL is per-resource and required"),
    P("azure-anthropic", API_ANTHROPIC,
      ("AZUREAI_ANTHROPIC_API_KEY", "AZURE_ANTHROPIC_API_KEY"),
      ("AZUREAI_ANTHROPIC_BASE_URL",), ""),

    # ---- local servers
    P("ollama", API_LOCAL, ("OLLAMA_API_KEY",), ("OLLAMA_BASE_URL",),
      "http://localhost:11434/v1", note="no key required"),
    P("vllm", API_LOCAL, ("VLLM_API_KEY",), ("VLLM_BASE_URL",),
      "http://localhost:8000/v1"),
    P("sglang", API_LOCAL, ("SGLANG_API_KEY",), ("SGLANG_BASE_URL",),
      "http://localhost:30000/v1"),
    P("lmstudio", API_LOCAL, ("LMSTUDIO_API_KEY",), ("LMSTUDIO_BASE_URL",),
      "http://localhost:1234/v1"),
)

BY_NAME: dict[str, Provider] = {}
for _p in PROVIDERS:
    BY_NAME[_p.name] = _p
    for _a in _p.aliases:
        BY_NAME[_a] = _p


def get_provider(name: str) -> Provider:
    key = name.strip().lower()
    if key in BY_NAME:
        return BY_NAME[key]
    raise KeyError(f"unknown provider {name!r}. known: "
                   f"{', '.join(p.name for p in PROVIDERS)}")


# ------------------------------------------------------------- redaction


def redact(secret: str | None) -> str:
    """The only representation of a key that may be printed or stored."""
    if not secret:
        return "<unset>"
    s = str(secret)
    if len(s) <= 12:
        return "*" * len(s)
    return f"{s[:7]}...{s[-4:]} ({len(s)} chars)"


# ------------------------------------------------------------- detection


def detect(key: str) -> Provider | None:
    """Infer the provider from a bare key.

    Longest prefix wins, so `sk-ant-api03-` is not swallowed by `sk-`.
    """
    k = (key or "").strip()
    if not k:
        return None
    best: tuple[int, Provider] | None = None
    for p in PROVIDERS:
        for pref in p.prefixes:
            if k.startswith(pref) and (best is None or len(pref) > best[0]):
                best = (len(pref), p)
    if best:
        return best[1]
    # Bare `sk-` with no more specific match is a legacy OpenAI key.
    if k.startswith("sk-"):
        return BY_NAME["openai"]
    return None


# -------------------------------------------------------------- storage


def store_path() -> Path:
    """User-level, deliberately outside the project so it cannot be committed."""
    override = os.environ.get("AGENTFOUNDRY_AUTH_FILE")
    if override:
        return Path(override)
    return Path.home() / ".agentfoundry" / "auth.json"


def load_store() -> dict:
    p = store_path()
    if not p.exists():
        return {"version": 1, "providers": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "providers": {}}


def save_store(data: dict) -> Path:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(p, data)
    try:  # best effort; Windows ACLs are not POSIX modes
        p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return p


def put_key(provider: str, key: str, base_url: str = "") -> dict:
    p = get_provider(provider)
    data = load_store()
    entry = {"key": key, "added_at": now_iso()}
    if base_url:
        entry["base_url"] = base_url
    data.setdefault("providers", {})[p.name] = entry
    save_store(data)
    return {"provider": p.name, "key": redact(key), "path": str(store_path())}


def remove_key(provider: str) -> bool:
    p = get_provider(provider)
    data = load_store()
    existed = data.get("providers", {}).pop(p.name, None) is not None
    save_store(data)
    return existed


# ------------------------------------------------------------ resolution

SOURCE_ARG = "argument"
SOURCE_ENV = "environment"
SOURCE_FILE = "stored"
SOURCE_CHAIN = "credential-chain"
SOURCE_NONE = "none"


@dataclass
class Credential:
    provider: Provider
    key: str | None
    source: str
    source_detail: str = ""
    base_url: str = ""
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def available(self) -> bool:
        if self.provider.api == API_LOCAL:
            return True
        if self.provider.api == API_CHAIN:
            return self.source == SOURCE_CHAIN
        return bool(self.key)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider.name,
            "api": self.provider.api,
            "available": self.available,
            "source": self.source,
            "source_detail": self.source_detail,
            "key": redact(self.key),
            "base_url": self.base_url,
            "missing": list(self.missing),
        }


def _env_first(names: Iterable[str]) -> tuple[str | None, str]:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v, n
    return None, ""


def _has_anthropic_profile() -> bool:
    import shutil
    import subprocess

    if not shutil.which("ant"):
        return False
    try:
        p = subprocess.run(["ant", "auth", "status"], capture_output=True,
                           text=True, timeout=15)
        return p.returncode == 0 and "no active" not in (p.stdout + p.stderr).lower()
    except Exception:  # noqa: BLE001
        return False


def _aws_chain_ok() -> tuple[bool, tuple[str, ...]]:
    if os.environ.get("AWS_PROFILE"):
        return True, ()
    have_keys = bool(os.environ.get("AWS_ACCESS_KEY_ID")
                     and os.environ.get("AWS_SECRET_ACCESS_KEY"))
    if have_keys:
        return True, ()
    if (Path.home() / ".aws" / "credentials").exists():
        return True, ()
    return False, ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def _gcp_chain_ok() -> tuple[bool, tuple[str, ...]]:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return True, ()
    adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    if adc.exists():
        return True, ()
    if os.name == "nt":
        win = (Path(os.environ.get("APPDATA", "")) / "gcloud"
               / "application_default_credentials.json")
        if win.exists():
            return True, ()
    return False, ("gcloud auth application-default login",)


def resolve(provider: str, key: str | None = None,
            base_url: str | None = None) -> Credential:
    """Resolve one provider's credential. Precedence is fixed and reported."""
    p = get_provider(provider)

    resolved_base = (base_url
                     or _env_first(p.base_url_env)[0]
                     or (load_store().get("providers", {})
                         .get(p.name, {}).get("base_url"))
                     or p.default_base_url)

    if key:
        return Credential(p, key, SOURCE_ARG, "explicit", resolved_base)

    if p.api == API_CHAIN:
        ok, missing = (_aws_chain_ok() if p.name == "bedrock" else _gcp_chain_ok())
        return Credential(p, None, SOURCE_CHAIN if ok else SOURCE_NONE,
                          p.note, resolved_base, () if ok else missing)

    env_key, env_name = _env_first(p.env)
    if env_key:
        return Credential(p, env_key, SOURCE_ENV, env_name, resolved_base)

    stored = load_store().get("providers", {}).get(p.name)
    if stored and stored.get("key"):
        return Credential(p, stored["key"], SOURCE_FILE, str(store_path()),
                          resolved_base)

    if p.name == "anthropic" and _has_anthropic_profile():
        return Credential(p, None, SOURCE_CHAIN, "ant auth profile",
                          resolved_base)

    if p.api == API_LOCAL:
        return Credential(p, None, SOURCE_NONE, "no key required", resolved_base)

    return Credential(p, None, SOURCE_NONE, "", resolved_base, p.env)


def resolve_all() -> list[Credential]:
    return [resolve(p.name) for p in PROVIDERS]


def available_providers() -> list[str]:
    return [c.provider.name for c in resolve_all() if c.available]


# ----------------------------------------------------------- verification


@dataclass
class VerifyResult:
    provider: str
    ok: bool
    detail: str
    status: int | None = None
    models_seen: int | None = None

    def to_dict(self) -> dict:
        return {"provider": self.provider, "ok": self.ok, "detail": self.detail,
                "status": self.status, "models_seen": self.models_seen}


def verify(provider: str, timeout: int = 20) -> VerifyResult:
    """Make the cheapest real call the provider offers.

    "The environment variable is set" is not evidence that a key works, and
    discovering otherwise partway through a paid run is expensive.
    """
    cred = resolve(provider)
    p = cred.provider

    if not cred.available:
        return VerifyResult(p.name, False,
                            f"no credential; set {' or '.join(p.env) or p.note}")
    if p.api == API_CHAIN:
        return VerifyResult(p.name, True,
                            f"{cred.source_detail} present (not called)")

    if p.api == API_ANTHROPIC:
        url = f"{cred.base_url.rstrip('/')}/v1/models"
        headers = {"anthropic-version": "2023-06-01"}
        if cred.key:
            headers["x-api-key"] = cred.key
        else:
            return VerifyResult(p.name, True, "oauth profile (not called)")
    elif p.api == API_GOOGLE:
        url = f"{cred.base_url.rstrip('/')}/models?key={cred.key}"
        headers = {}
    else:  # openai-compatible and local
        if not cred.base_url:
            return VerifyResult(p.name, False,
                                f"base URL required; set {' or '.join(p.base_url_env)}")
        url = f"{cred.base_url.rstrip('/')}/models"
        headers = {"authorization": f"Bearer {cred.key}"} if cred.key else {}

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode() or "{}")
        n = len(body.get("data") or body.get("models") or [])
        return VerifyResult(p.name, True, "key accepted", resp.status, n)
    except urllib.error.HTTPError as e:
        reason = {401: "key rejected", 403: "key lacks permission",
                  404: "endpoint not found; check base URL",
                  429: "rate limited (key is valid)"}.get(e.code, f"HTTP {e.code}")
        # A 429 proves authentication succeeded.
        return VerifyResult(p.name, e.code == 429, reason, e.code)
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(p.name, False, f"unreachable: {str(exc)[:80]}")


# ------------------------------------------------------------ add by key


def add(key: str, provider: str | None = None,
        base_url: str = "") -> dict:
    """Store a key, inferring the provider when it was not given."""
    key = (key or "").strip()
    if not key:
        raise ValueError("empty key")

    p = get_provider(provider) if provider else detect(key)
    if p is None:
        raise ValueError(
            f"could not infer a provider from {redact(key)}. "
            f"Pass --provider explicitly. Known: "
            f"{', '.join(x.name for x in PROVIDERS)}")

    result = put_key(p.name, key, base_url)
    result["detected"] = provider is None
    return result
