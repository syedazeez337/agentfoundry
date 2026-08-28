"""Credential resolution guards.

The two properties that matter and would be expensive to get wrong:
  - a raw key never appears in any output
  - precedence is what the documentation claims it is

Everything here runs against a temporary store and a scrubbed environment. No
network, no real keys.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from af import auth

ALL_ENV = sorted({v for p in auth.PROVIDERS
                  for v in (*p.env, *p.base_url_env, *p.extra_env)}
                 | {"AGENTFOUNDRY_AUTH_FILE"})

FAKE = {
    "anthropic": "sk-ant-api03-" + "a" * 80,
    "openai": "sk-proj-" + "b" * 60,
    "google": "AIza" + "c" * 35,
    "groq": "gsk_" + "d" * 48,
    "openrouter": "sk-or-v1-" + "e" * 40,
    "xai": "xai-" + "f" * 40,
    "fireworks": "fw_" + "g" * 30,
    "cerebras": "csk-" + "h" * 40,
    "perplexity": "pplx-" + "i" * 40,
    "huggingface": "hf_" + "j" * 34,
}


class AuthTestCase(unittest.TestCase):
    """Isolates every test from the developer's real environment."""

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in ALL_ENV}
        self.tmp = Path(tempfile.mkdtemp(prefix="af-auth-"))
        os.environ["AGENTFOUNDRY_AUTH_FILE"] = str(self.tmp / "auth.json")

    def tearDown(self):
        import shutil

        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestRedaction(AuthTestCase):
    def test_key_never_appears_in_full(self):
        for name, key in FAKE.items():
            r = auth.redact(key)
            self.assertNotIn(key, r, name)
            self.assertLess(len(r), len(key), name)

    def test_short_secret_is_fully_masked(self):
        self.assertEqual(auth.redact("abc123"), "******")

    def test_unset_is_explicit(self):
        self.assertEqual(auth.redact(None), "<unset>")
        self.assertEqual(auth.redact(""), "<unset>")

    def test_credential_dict_carries_no_raw_key(self):
        auth.put_key("anthropic", FAKE["anthropic"])
        blob = json.dumps(auth.resolve("anthropic").to_dict())
        self.assertNotIn(FAKE["anthropic"], blob)

    def test_verify_result_carries_no_raw_key(self):
        r = auth.VerifyResult("openai", False, "no credential")
        self.assertNotIn("sk-", json.dumps(r.to_dict()))


class TestDetection(AuthTestCase):
    def test_detects_every_known_prefix(self):
        for expected, key in FAKE.items():
            got = auth.detect(key)
            self.assertIsNotNone(got, f"{expected} not detected")
            self.assertEqual(got.name, expected, f"{key[:12]}...")

    def test_longest_prefix_wins(self):
        """sk-ant- must not be swallowed by a bare sk- match."""
        self.assertEqual(auth.detect("sk-ant-api03-xxxx").name, "anthropic")
        self.assertEqual(auth.detect("sk-or-v1-xxxx").name, "openrouter")
        self.assertEqual(auth.detect("sk-proj-xxxx").name, "openai")

    def test_legacy_bare_sk_falls_back_to_openai(self):
        self.assertEqual(auth.detect("sk-" + "z" * 40).name, "openai")

    def test_unknown_key_returns_none(self):
        self.assertIsNone(auth.detect("not-a-known-key-format"))
        self.assertIsNone(auth.detect(""))
        self.assertIsNone(auth.detect(None))


class TestPrecedence(AuthTestCase):
    def test_argument_beats_environment_and_store(self):
        os.environ["ANTHROPIC_API_KEY"] = FAKE["anthropic"]
        auth.put_key("anthropic", "stored-key")
        c = auth.resolve("anthropic", key="explicit-key")
        self.assertEqual(c.key, "explicit-key")
        self.assertEqual(c.source, auth.SOURCE_ARG)

    def test_environment_beats_store(self):
        os.environ["OPENAI_API_KEY"] = "env-key"
        auth.put_key("openai", "stored-key")
        c = auth.resolve("openai")
        self.assertEqual(c.key, "env-key")
        self.assertEqual(c.source, auth.SOURCE_ENV)
        self.assertEqual(c.source_detail, "OPENAI_API_KEY")

    def test_store_used_when_environment_absent(self):
        auth.put_key("groq", FAKE["groq"])
        c = auth.resolve("groq")
        self.assertEqual(c.key, FAKE["groq"])
        self.assertEqual(c.source, auth.SOURCE_FILE)

    def test_first_env_var_in_list_wins(self):
        os.environ["GEMINI_API_KEY"] = "second"
        c = auth.resolve("google")
        self.assertEqual(c.source_detail, "GEMINI_API_KEY")
        os.environ["GOOGLE_API_KEY"] = "first"
        c = auth.resolve("google")
        self.assertEqual(c.source_detail, "GOOGLE_API_KEY")

    def test_missing_credential_reports_what_to_set(self):
        c = auth.resolve("mistral")
        self.assertFalse(c.available)
        self.assertEqual(c.source, auth.SOURCE_NONE)
        self.assertIn("MISTRAL_API_KEY", c.missing)


class TestBaseUrl(AuthTestCase):
    def test_default_applies(self):
        self.assertEqual(auth.resolve("groq").base_url,
                         "https://api.groq.com/openai/v1")

    def test_env_overrides_default(self):
        os.environ["GROQ_BASE_URL"] = "http://localhost:9999/v1"
        self.assertEqual(auth.resolve("groq").base_url,
                         "http://localhost:9999/v1")

    def test_argument_overrides_env(self):
        os.environ["GROQ_BASE_URL"] = "http://env/v1"
        self.assertEqual(auth.resolve("groq", base_url="http://arg/v1").base_url,
                         "http://arg/v1")


class TestNonKeyProviders(AuthTestCase):
    def test_local_provider_is_available_without_a_key(self):
        c = auth.resolve("ollama")
        self.assertTrue(c.available)
        self.assertIsNone(c.key)

    def test_cloud_chain_reports_missing_pieces(self):
        c = auth.resolve("bedrock")
        self.assertEqual(c.provider.api, auth.API_CHAIN)
        if not c.available:
            self.assertTrue(c.missing)

    def test_aws_env_satisfies_the_chain(self):
        os.environ["AWS_ACCESS_KEY_ID"] = "AKIAEXAMPLE"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "secret"
        c = auth.resolve("bedrock")
        self.assertTrue(c.available)
        self.assertEqual(c.source, auth.SOURCE_CHAIN)


class TestStore(AuthTestCase):
    def test_add_infers_provider(self):
        r = auth.add(FAKE["fireworks"])
        self.assertEqual(r["provider"], "fireworks")
        self.assertTrue(r["detected"])
        self.assertNotIn(FAKE["fireworks"], json.dumps(r))

    def test_add_rejects_unknown_without_explicit_provider(self):
        with self.assertRaises(ValueError):
            auth.add("mystery-key-value")

    def test_add_accepts_explicit_provider_for_unknown_format(self):
        r = auth.add("mystery-key-value", provider="together")
        self.assertEqual(r["provider"], "together")
        self.assertFalse(r["detected"])

    def test_remove(self):
        auth.add(FAKE["openai"])
        self.assertTrue(auth.remove_key("openai"))
        self.assertFalse(auth.remove_key("openai"))

    def test_store_lives_outside_the_project(self):
        os.environ.pop("AGENTFOUNDRY_AUTH_FILE")
        p = auth.store_path()
        self.assertTrue(str(p).startswith(str(Path.home())),
                        "credential store must not sit inside the repo")

    def test_alias_resolves(self):
        self.assertEqual(auth.get_provider("claude").name, "anthropic")
        self.assertEqual(auth.get_provider("gemini").name, "google")
        self.assertEqual(auth.get_provider("grok").name, "xai")


class TestRegistryIntegrity(AuthTestCase):
    def test_no_duplicate_names_or_aliases(self):
        seen = set()
        for p in auth.PROVIDERS:
            for n in (p.name, *p.aliases):
                self.assertNotIn(n, seen, f"duplicate provider name {n!r}")
                seen.add(n)

    def test_key_providers_declare_env_vars(self):
        for p in auth.PROVIDERS:
            if p.needs_key:
                self.assertTrue(p.env, f"{p.name} needs a key but declares no env var")

    def test_openai_compatible_have_base_urls(self):
        for p in auth.PROVIDERS:
            if p.api == auth.API_OPENAI and p.name not in ("azure-openai",):
                self.assertTrue(p.default_base_url, f"{p.name} has no base URL")

    def test_every_provider_resolves_without_error(self):
        for p in auth.PROVIDERS:
            auth.resolve(p.name).to_dict()


if __name__ == "__main__":
    unittest.main()
