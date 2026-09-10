"""Tests for the provider client.

Each test names the measurement it protects (MEASUREMENTS.md §12, §12.1, §12.2).
No network: the transport is injected.
"""

from __future__ import annotations

import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.providers import (  # noqa: E402
    NoUsableModel, Provider, ProviderClient, RateLimited, normalise_for_echo,
    select_models,
)


def http_error(code: int, body: str = "{}") -> urllib.error.HTTPError:
    import io
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


class FakeTransport:
    """Scripted responses keyed by (path-suffix, model)."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[dict] = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": dict(headers)})
        key = "models" if url.endswith("/models") else (payload or {}).get("model")
        outcome = self.responses.get(key)
        if outcome is None:
            raise http_error(404, '{"detail":"Not found for account"}')
        if isinstance(outcome, Exception):
            raise outcome
        return 200, outcome


def chat_body(text="hi", tool_calls=None):
    msg = {"role": "assistant", "content": text}
    if tool_calls is not None:
        msg["content"] = None            # ⚠️ what a real API returns on a tool call
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


class TestEchoNormalisation(unittest.TestCase):
    """§12.2 - the assistant message an API hands you is not always a valid
    message to hand back. Cloudflare rejects `content: null`; four providers
    tolerate it, which is why a verbatim echo survives four integrations."""

    def test_null_content_becomes_empty_string(self):
        msg = {"role": "assistant", "content": None,
               "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]}
        out = normalise_for_echo(msg)
        self.assertEqual(out["content"], "")
        self.assertEqual(out["tool_calls"], msg["tool_calls"])

    def test_missing_content_key_becomes_empty_string(self):
        self.assertEqual(normalise_for_echo({"role": "assistant"})["content"], "")

    def test_real_content_is_preserved(self):
        self.assertEqual(normalise_for_echo({"role": "assistant", "content": "x"})["content"], "x")

    def test_no_tool_calls_key_when_absent(self):
        self.assertNotIn("tool_calls", normalise_for_echo({"role": "assistant", "content": "x"}))

    def test_client_normalises_echoed_assistant_messages(self):
        prov = Provider(key="strict", base_url="http://x", secret_name="NOPE",
                        fallback_models=("m1",), models_path=None,
                        strict_message_schema=True)
        t = FakeTransport({"m1": chat_body("done")})
        client = ProviderClient(prov, opener=t)
        client.chat([{"role": "user", "content": "hi"},
                     {"role": "assistant", "content": None,
                      "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]},
                     {"role": "tool", "tool_call_id": "1", "name": "f", "content": "r"}])
        sent = t.calls[-1]["payload"]["messages"]
        self.assertEqual(sent[1]["content"], "", "content:null was echoed back verbatim")


class TestModelSelection(unittest.TestCase):
    """§12.1 - a roster is not a statement of what you may call, and not every
    id on it is a chat model."""

    def test_non_chat_models_are_excluded(self):
        roster = ["nvidia/llama-3.1-nemoguard-8b-content-safety",
                  "nvidia/nv-embedqa-mistral-7b-v2",
                  "meta/llama-3.1-8b-instruct"]
        picked = select_models(roster, ("llama-3.1",))
        self.assertEqual(picked, ["meta/llama-3.1-8b-instruct"])

    def test_preference_order_is_respected(self):
        roster = ["b-model", "a-model"]
        self.assertEqual(select_models(roster, ("a-", "b-"))[0], "a-model")

    def test_returns_several_candidates_not_one(self):
        """⚠️ A naive single pick fails ~4 times in 5 on NVIDIA."""
        roster = [f"m{i}-instruct" for i in range(6)]
        self.assertGreater(len(select_models(roster, ("m",), limit=4)), 1)

    def test_unpreferred_models_still_offered_as_fallback(self):
        self.assertEqual(select_models(["zzz"], ("nomatch",)), ["zzz"])


class TestFailover(unittest.TestCase):
    """§12.1 / §12 - unentitled models 404 and free models 429 transiently."""

    def setUp(self):
        self.prov = Provider(key="p", base_url="http://x", secret_name="NOPE",
                             fallback_models=("bad1", "bad2", "good"), models_path=None)

    def test_404_falls_through_to_the_next_model(self):
        t = FakeTransport({"good": chat_body("ok")})     # bad1/bad2 raise 404
        client = ProviderClient(self.prov, opener=t)
        result = client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(result.model, "good")
        self.assertEqual(result.content, "ok")

    def test_429_is_retried_then_falls_through(self):
        t = FakeTransport({"bad1": http_error(429, "slow down"), "good": chat_body("ok")})
        client = ProviderClient(self.prov, opener=t)
        result = client.chat([{"role": "user", "content": "hi"}], retries_on_429=0)
        self.assertEqual(result.model, "good")

    def test_all_models_failing_reports_each_reason(self):
        """⚠️ 'no model worked' and 'this account is entitled to none of them'
        need different fixes and must not look identical."""
        t = FakeTransport({})
        client = ProviderClient(self.prov, opener=t)
        with self.assertRaises(NoUsableModel) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(len(ctx.exception.attempts), 3)
        self.assertIn("HTTP 404", str(ctx.exception))

    def test_a_pinned_model_is_not_failed_over(self):
        t = FakeTransport({})
        client = ProviderClient(self.prov, opener=t, model="pinned")
        with self.assertRaises(NoUsableModel) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual([m for m, _ in ctx.exception.attempts], ["pinned"])


class TestTransportDetails(unittest.TestCase):
    def test_user_agent_is_sent(self):
        """§12 - Groq's edge answers 403 'error code: 1010' to urllib's default
        User-Agent, which reads exactly like a rejected key."""
        prov = Provider(key="p", base_url="http://x", secret_name="NOPE",
                        fallback_models=("m",), models_path=None)
        t = FakeTransport({"m": chat_body()})
        ProviderClient(prov, opener=t).chat([{"role": "user", "content": "hi"}])
        self.assertIn("OverMind", t.calls[-1]["headers"]["User-Agent"])

    def test_extra_headers_are_sent(self):
        prov = Provider(key="p", base_url="http://x", secret_name="NOPE",
                        fallback_models=("m",), models_path=None,
                        extra_headers={"X-Title": "OverMind"})
        t = FakeTransport({"m": chat_body()})
        ProviderClient(prov, opener=t).chat([{"role": "user", "content": "hi"}])
        self.assertEqual(t.calls[-1]["headers"]["X-Title"], "OverMind")

    def test_roster_405_falls_back_to_declared_models(self):
        """§12.2 - Cloudflare's OpenAI-compatible /models answers 405."""
        prov = Provider(key="cf", base_url="http://x", secret_name="NOPE",
                        fallback_models=("@cf/a", "@cf/b"), models_path=None)
        client = ProviderClient(prov, opener=FakeTransport({}))
        self.assertEqual(client.roster(), ["@cf/a", "@cf/b"])

    def test_roster_failure_does_not_prevent_a_call(self):
        prov = Provider(key="p", base_url="http://x", secret_name="NOPE",
                        fallback_models=("m",), models_path="/models")
        t = FakeTransport({"m": chat_body("ok")})       # /models raises 404
        result = ProviderClient(prov, opener=t).chat([{"role": "user", "content": "hi"}])
        self.assertEqual(result.content, "ok")


class TestAccountScopedBase(unittest.TestCase):
    """§12.2 - Cloudflare's path carries the account id."""

    def test_missing_account_id_is_a_clear_error(self):
        prov = Provider(key="cf", base_url="http://x/{account}/ai/v1",
                        secret_name="NOPE", account_secret_name="DEFINITELY_NOT_SET_12345",
                        fallback_models=("m",), models_path=None)
        client = ProviderClient(prov, opener=FakeTransport({"m": chat_body()}))
        with self.assertRaises(Exception) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("account id", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
