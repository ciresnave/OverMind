# SPDX-License-Identifier: MIT OR Apache-2.0
"""Provider tokens live in the with-secret vault, not in the environment.

A process sees one only when launched `with-secret NAME --reason ... -- <cmd>`
(one secret per invocation). So a missing token must be an explicit, NAMED
error that says how to run it, never a silent skip, an empty string or the
placeholder `unused` sent as a bearer token. Dummy values only: no real
secret is read or printed here, and no network is used.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from overmind.providers import (  # noqa: E402
    PROVIDERS, ProviderClient, ProviderError, SecretNotSet, read_secret, require_secret,
)

HOSTED_TOKENS = sorted({p.secret_name for k, p in PROVIDERS.items() if not p.local})


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in HOSTED_TOKENS}
    env["PYTHONUTF8"] = "1"
    env.update(extra)
    return env


def run_script(name: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "probe", name), *args],
        capture_output=True, text=True, timeout=60, env=clean_env(**env),
    )


def load_script(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "probe_" + name[:-3], os.path.join(ROOT, "probe", name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RequireSecret(unittest.TestCase):
    def test_present_value_is_returned(self):
        with mock.patch.dict(os.environ, {"DUMMY_API_TOKEN": "dummy-value"}):
            self.assertEqual(require_secret("DUMMY_API_TOKEN"), "dummy-value")

    def test_missing_is_a_named_error_that_says_how_to_run(self):
        env = {k: v for k, v in os.environ.items() if k != "DUMMY_API_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SecretNotSet) as cm:
                require_secret("DUMMY_API_TOKEN")
        text = str(cm.exception)
        self.assertIn("DUMMY_API_TOKEN not set", text)
        self.assertIn("with-secret DUMMY_API_TOKEN", text)
        self.assertEqual(cm.exception.secret_name, "DUMMY_API_TOKEN")
        # a provider failover loop catches ProviderError and moves on
        self.assertIsInstance(cm.exception, ProviderError)

    def test_empty_string_is_missing_not_a_token(self):
        with mock.patch.dict(os.environ, {"DUMMY_API_TOKEN": ""}):
            with self.assertRaises(SecretNotSet):
                require_secret("DUMMY_API_TOKEN")
            self.assertIsNone(read_secret("DUMMY_API_TOKEN"))

    def test_the_registry_is_not_consulted(self):
        # the tokens were moved OUT of the User environment on purpose: a
        # leftover registry value must not be used behind the vault's back
        boom = types.ModuleType("winreg")
        boom.HKEY_CURRENT_USER = object()

        def opened(*a, **k):
            raise AssertionError("read_secret opened the registry")
        boom.OpenKey = opened
        env = {k: v for k, v in os.environ.items() if k != "DUMMY_API_TOKEN"}
        with mock.patch.dict(sys.modules, {"winreg": boom}), \
                mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(read_secret("DUMMY_API_TOKEN"))


class ClientWithoutItsToken(unittest.TestCase):
    def test_a_hosted_provider_without_its_token_never_sends_a_request(self):
        sent = []
        client = ProviderClient("groq", opener=lambda *a: sent.append(a) or (200, {}))
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            with self.assertRaises(SecretNotSet) as cm:
                client._headers()
            self.assertIn("GROQ_CLOUD_API_TOKEN", str(cm.exception))
            with self.assertRaises(ProviderError):
                client._request("/models")
        self.assertEqual(sent, [], "no request may leave without a real token")

    def test_chat_names_the_missing_secret_not_http_none(self):
        # review finding: the named error was swallowed by the per-model
        # handler and surfaced as "no usable model ... HTTP None"
        sent = []
        client = ProviderClient("groq", opener=lambda *a: sent.append(a) or (200, {}),
                              model="llama-3.3-70b-versatile")
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            with self.assertRaises(SecretNotSet) as cm:
                client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("GROQ_CLOUD_API_TOKEN", str(cm.exception))
        self.assertEqual(sent, [])
        self.assertEqual(client._known_bad, set())

    def test_roster_does_not_hide_the_missing_secret(self):
        client = ProviderClient("openrouter", opener=lambda *a: (200, {}))
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            with self.assertRaises(SecretNotSet):
                client.roster()

    def test_a_routed_client_reports_the_missing_secret_by_name(self):
        from overmind.providers import RoutedClient, NoUsableModel
        rc = RoutedClient([ProviderClient("groq", opener=lambda *a: (200, {}))])
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            with self.assertRaises(NoUsableModel) as cm:
                rc.chat([{"role": "user", "content": "hi"}])
        self.assertIn("GROQ_CLOUD_API_TOKEN not set", str(cm.exception.attempts))

    def test_the_local_provider_needs_no_token(self):
        client = ProviderClient("ollama")
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            self.assertEqual(client._headers()["Authorization"], "Bearer unused")

    def test_every_hosted_provider_names_its_own_token(self):
        for key, prov in PROVIDERS.items():
            if prov.local:
                continue
            with mock.patch.dict(os.environ, clean_env(), clear=True):
                with self.assertRaises(SecretNotSet, msg=key) as cm:
                    ProviderClient(key)._headers()
            self.assertEqual(cm.exception.secret_name, prov.secret_name, key)


class ProbeEntryPoints(unittest.TestCase):
    """One provider = one secret. Run in a child with the tokens removed."""

    def test_provider_probe_names_the_missing_token_for_one_provider(self):
        r = run_script("provider_probe.py", "groq")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("GROQ_CLOUD_API_TOKEN not set", r.stderr)
        self.assertIn("with-secret GROQ_CLOUD_API_TOKEN", r.stderr)
        # asking for groq must not complain about the other four
        self.assertNotIn("NVIDIA_NIM_API_TOKEN", r.stderr)

    def test_provider_probe_needs_a_provider_and_lists_them(self):
        r = run_script("provider_probe.py")
        self.assertEqual(r.returncode, 2)
        for name in ("groq", "nvidia", "openrouter", "google", "cloudflare"):
            self.assertIn(name, r.stderr)

    def test_provider_probe_rejects_an_unknown_provider(self):
        r = run_script("provider_probe.py", "nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("nope", r.stderr)

    def test_provider_probe_names_every_missing_token_before_any_request(self):
        r = run_script("provider_probe.py", "groq", "openrouter")
        self.assertEqual(r.returncode, 2)
        self.assertIn("GROQ_CLOUD_API_TOKEN", r.stderr)
        self.assertIn("OPENROUTER_API_TOKEN", r.stderr)

    def test_provider_probe_needs_exactly_the_selected_secrets(self):
        mod = load_script("provider_probe.py")
        self.assertEqual(mod.secrets_for(["groq"]), ["GROQ_CLOUD_API_TOKEN"])
        self.assertEqual(mod.secrets_for(["cloudflare"]),
                         ["CLOUDFLARE_WORKERS_AI_API_TOKEN"])
        self.assertEqual(mod.secrets_for(["nvidia", "google"]),
                         ["NVIDIA_NIM_API_TOKEN", "GOOGLE_AI_STUDIO_API_TOKEN"])

    def test_compliance_api_names_the_missing_token(self):
        r = run_script("compliance_api.py", "openrouter")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("OPENROUTER_API_TOKEN not set", r.stderr)
        self.assertIn("with-secret OPENROUTER_API_TOKEN", r.stderr)

    def test_compliance_api_rejects_an_unknown_provider_by_name(self):
        r = run_script("compliance_api.py", "nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("nope", r.stderr)

    def test_nvidia_entitlement_names_the_missing_token(self):
        r = run_script("nvidia_entitlement.py")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("NVIDIA_NIM_API_TOKEN not set", r.stderr)

    def test_no_probe_reads_the_registry(self):
        for name in ("provider_probe.py", "compliance_api.py", "nvidia_entitlement.py"):
            with open(os.path.join(ROOT, "probe", name), encoding="utf-8") as f:
                self.assertNotIn("winreg", f.read(), name)


if __name__ == "__main__":
    unittest.main()
