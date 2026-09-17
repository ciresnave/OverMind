# SPDX-License-Identifier: MIT OR Apache-2.0
"""Daily free-tier quotas: recognised, remembered, routed around.

⚠️ EVERY "SKIP IT" TEST HAS A "DON'T SKIP IT" ARM. A per-minute 429 clears in
seconds and must still be retried; only a DAILY refusal blocks a model. A book
that blocked on every 429 would pass the daily tests and throw away a model's
whole day on a momentary burst.
"""

import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.providers import (                                      # noqa: E402
    NoUsableModel, Provider, ProviderClient, ProviderError, RoutedClient,
)
from overmind.quota import QuotaBook, daily_limit_in, next_reset       # noqa: E402

# Shapes of real bodies, trimmed. Google's per-day refusal, 2026-09-17:
GOOGLE_DAY = json.dumps([{"error": {"code": 429, "message": "You exceeded your current quota",
                                    "details": [{"violations": [{
                                        "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                                        "quotaValue": "20"}]}]}}])
GOOGLE_MINUTE = json.dumps({"error": {"code": 429, "details": [{"violations": [{
    "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "quotaValue": "10"}]}]}})
OPENROUTER_DAY = json.dumps({"error": {"message": "Rate limit exceeded: free-models-per-day",
                                       "code": 429}})
# Google's refusal for a model with NO free allowance (gemini-pro-latest,
# 2026-09-17): the limit is only in the message; no violation has quotaValue.
GOOGLE_NO_ALLOWANCE = json.dumps([{"error": {"code": 429, "message": (
    "You exceeded your current quota.\n"
    "* Quota exceeded for metric: generativelanguage.googleapis.com/"
    "generate_content_free_tier_input_token_count, limit: 0, model: gemini-3.1-pro\n"
    "* Quota exceeded for metric: generativelanguage.googleapis.com/"
    "generate_content_free_tier_requests, limit: 0, model: gemini-3.1-pro\n"),
    "details": [{"violations": [
        {"quotaId": "GenerateContentInputTokensPerModelPerDay-FreeTier"},
        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}])
# A TOKEN cap listed before the REQUEST cap - the first quotaValue is the wrong one.
GOOGLE_TOKENS_FIRST = json.dumps([{"error": {"code": 429, "details": [{"violations": [
    {"quotaId": "GenerateContentInputTokensPerModelPerDay-FreeTier", "quotaValue": "250000"},
    {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20"}]}]}}])


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc).timestamp()


def http_error(code, body):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


def ok_body(text="ok"):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class Transport:
    """Scripted outcomes per model; each value is a list consumed in order."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, url, payload, headers, timeout):
        model = (payload or {}).get("model")
        self.calls.append(model)
        queue = self.script.get(model) or [http_error(404, "not served")]
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return 200, outcome


def provider(key="google", models=("a", "b")):
    return Provider(key=key, base_url="http://x", secret_name="NOPE",
                    fallback_models=models, models_path=None)


class TestRecognition(unittest.TestCase):

    def test_bodies(self):
        self.assertEqual(daily_limit_in(GOOGLE_DAY), (True, 20))
        self.assertEqual(daily_limit_in(OPENROUTER_DAY), (True, None))
        self.assertEqual(daily_limit_in(GOOGLE_MINUTE), (False, None),
                         "a per-minute refusal is not a daily verdict")
        self.assertEqual(daily_limit_in(""), (False, None))

    def test_the_request_cap_not_the_first_number(self):
        self.assertEqual(daily_limit_in(GOOGLE_TOKENS_FIRST), (True, 20),
                         "a token cap listed first must not be read as the request cap")

    def test_a_cap_stated_only_in_the_message(self):
        self.assertEqual(daily_limit_in(GOOGLE_NO_ALLOWANCE), (True, 0))


class TestReset(unittest.TestCase):
    """Google resets at midnight Pacific; the rest at midnight UTC."""

    def test_pacific_daylight_time(self):
        self.assertEqual(next_reset("google", utc(2026, 9, 17, 12)), utc(2026, 9, 18, 7))

    def test_pacific_standard_time(self):
        self.assertEqual(next_reset("google", utc(2026, 1, 15, 12)), utc(2026, 1, 16, 8))

    def test_late_evening_pacific_resets_within_the_hour(self):
        # 06:30 UTC on the 17th is 23:30 PDT on the 16th
        self.assertEqual(next_reset("google", utc(2026, 9, 17, 6, 30)), utc(2026, 9, 17, 7))

    def test_the_night_daylight_time_begins(self):
        # 2026-03-08 is the second Sunday of March; 01:00 PST is 09:00 UTC
        self.assertEqual(next_reset("google", utc(2026, 3, 8, 9)), utc(2026, 3, 9, 7))

    def test_the_night_daylight_time_ends(self):
        # 2026-11-01 is the first Sunday of November; 01:30 PDT is 08:30 UTC
        self.assertEqual(next_reset("google", utc(2026, 11, 1, 8, 30)), utc(2026, 11, 2, 8))

    def test_utc_providers(self):
        self.assertEqual(next_reset("openrouter", utc(2026, 9, 17, 23, 59, 59)),
                         utc(2026, 9, 18))


class TestBook(unittest.TestCase):

    def setUp(self):
        self.now = [utc(2026, 9, 17, 12)]
        self.book = QuotaBook(clock=lambda: self.now[0])

    def test_a_daily_refusal_blocks_and_teaches_the_cap(self):
        self.book.record_request("google", "m")
        self.assertTrue(self.book.record_refusal("google", "m", GOOGLE_DAY))
        self.assertTrue(self.book.blocked("google", "m"))
        self.assertEqual(self.book.cap("google", "m"), 20)
        self.assertEqual(self.book.remaining("google", "m"), 0)

    def test_a_per_minute_refusal_blocks_nothing(self):
        self.assertFalse(self.book.record_refusal("google", "m", GOOGLE_MINUTE))
        self.assertFalse(self.book.blocked("google", "m"))

    def test_the_block_lifts_at_the_reset_and_the_cap_stays(self):
        self.book.record_request("google", "m")
        self.book.record_refusal("google", "m", GOOGLE_DAY)
        self.now[0] = utc(2026, 9, 18, 6, 59)
        self.assertTrue(self.book.blocked("google", "m"), "still 23:59 Pacific")
        self.now[0] = utc(2026, 9, 18, 7)
        self.assertFalse(self.book.blocked("google", "m"))
        self.assertEqual(self.book.used("google", "m"), 0)
        self.assertEqual(self.book.remaining("google", "m"), 20, "a learned cap describes the model")

    def test_a_model_with_no_allowance_stays_blocked_after_the_reset(self):
        """Both arms: cap 0 persists across the reset; cap 20 does not block."""
        self.book.record_refusal("google", "pro", GOOGLE_NO_ALLOWANCE)
        self.book.record_refusal("google", "flash", GOOGLE_DAY)
        self.now[0] = utc(2026, 9, 18, 7)
        self.assertTrue(self.book.blocked("google", "pro"))
        self.assertEqual(self.book.remaining("google", "pro"), 0)
        self.assertFalse(self.book.blocked("google", "flash"))

    def test_reading_stores_nothing(self):
        """Measured live: asking about a 58-model roster filled the book."""
        for m in ("a", "b", "c"):
            self.assertFalse(self.book.blocked("google", m))
            self.assertIsNone(self.book.remaining("google", m))
        self.assertEqual(self.book.snapshot(), {})
        self.book.record_request("google", "b")
        self.assertEqual(list(self.book.snapshot()), ["google/b"], "control: a write stores")

    def test_a_saved_book_keeps_only_entries_that_say_something(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "quota.json"
            day_ends = utc(2026, 9, 18, 7)
            path.write_text(json.dumps({
                "google/noise": {"day_ends": day_ends, "used": 0, "blocked": False, "cap": None},
                "google/capped": {"day_ends": day_ends, "used": 0, "blocked": False, "cap": 20},
            }), encoding="utf-8")
            book = QuotaBook(path=path, clock=lambda: self.now[0])
            book.record_request("google", "used")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(sorted(saved), ["google/capped", "google/used"])

    def test_an_unknown_cap_is_unknown_not_unlimited(self):
        self.book.record_request("openrouter", "m")
        self.assertIsNone(self.book.remaining("openrouter", "m"))
        self.assertEqual(self.book.used("openrouter", "m"), 1)

    def test_the_book_persists_and_survives_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sub" / "quota.json"
            first = QuotaBook(path=path, clock=lambda: self.now[0])
            first.record_request("google", "m")
            first.record_refusal("google", "m", GOOGLE_DAY)
            second = QuotaBook(path=path, clock=lambda: self.now[0])
            self.assertTrue(second.blocked("google", "m"), "a new process must know")
            path.write_text("{not json", encoding="utf-8")
            third = QuotaBook(path=path, clock=lambda: self.now[0])
            self.assertFalse(third.blocked("google", "m"), "a corrupt book is an empty one")


class TestClientUsesTheBook(unittest.TestCase):

    def setUp(self):
        self.now = [utc(2026, 9, 17, 12)]
        self.book = QuotaBook(clock=lambda: self.now[0])

    def test_a_daily_refusal_is_not_retried_and_not_asked_again(self):
        t = Transport({"a": [http_error(429, GOOGLE_DAY)], "b": [ok_body("from b")]})
        client = ProviderClient(provider(), opener=t, quota=self.book)
        first = client.chat([{"role": "user", "content": "hi"}], retries_on_429=2)
        self.assertEqual(first.model, "b")
        self.assertEqual(t.calls, ["a", "b"], "no retry of a spent day")
        client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(t.calls, ["a", "b", "b"], "a is not asked again today")
        self.assertEqual(self.book.used("google", "a"), 1)
        self.assertEqual(self.book.used("google", "b"), 2)

    def test_a_per_minute_refusal_is_still_retried(self):
        """The arm that stops the book blocking on every 429."""
        t = Transport({"a": [http_error(429, GOOGLE_MINUTE), ok_body("a again")]})
        client = ProviderClient(provider(), opener=t, quota=self.book)
        result = client.chat([{"role": "user", "content": "hi"}], retries_on_429=1)
        self.assertEqual(result.model, "a")
        self.assertEqual(t.calls, ["a", "a"])
        self.assertFalse(self.book.blocked("google", "a"))

    def test_without_a_book_a_daily_refusal_is_still_not_retried(self):
        t = Transport({"a": [http_error(429, GOOGLE_DAY)], "b": [ok_body()]})
        ProviderClient(provider(), opener=t).chat([{"role": "user", "content": "hi"}],
                                                  retries_on_429=2)
        self.assertEqual(t.calls, ["a", "b"])

    def test_a_blocked_model_gives_up_its_candidate_slot(self):
        """Filtered from the roster AND from the fallbacks appended after it."""
        self.book.record_refusal("google", "a", GOOGLE_DAY)
        client = ProviderClient(provider(models=("a", "b", "c")), quota=self.book)
        self.assertEqual(client.candidates(limit=1), ["b", "c"])
        fresh = ProviderClient(provider(models=("a", "b", "c")))
        self.assertEqual(fresh.candidates(limit=1), ["a", "b", "c"],
                         "control: with no book, every fallback is offered")

    def test_a_pinned_model_that_is_spent_is_not_asked(self):
        """A pinned model skips the candidate filter, so this is the only path
        on which the per-request check is what stops the wasted call."""
        self.book.record_refusal("google", "a", GOOGLE_DAY)
        t = Transport({"a": [ok_body()]})
        client = ProviderClient(provider(), opener=t, quota=self.book, model="a")
        with self.assertRaises(NoUsableModel) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(t.calls, [])
        self.assertEqual(ctx.exception.attempts, [("a", "daily quota spent (recorded)")])

    def test_the_block_lifts_for_the_client_at_the_reset(self):
        self.book.record_refusal("google", "a", GOOGLE_DAY)
        self.now[0] = utc(2026, 9, 18, 7)
        t = Transport({"a": [ok_body()]})
        result = ProviderClient(provider(), opener=t, quota=self.book).chat(
            [{"role": "user", "content": "hi"}])
        self.assertEqual(result.model, "a")


class TestRouting(unittest.TestCase):

    def setUp(self):
        self.book = QuotaBook(clock=lambda: utc(2026, 9, 17, 12))

    def test_a_spent_provider_hands_over_to_the_next(self):
        g = Transport({"a": [http_error(429, GOOGLE_DAY)], "b": [http_error(429, GOOGLE_DAY)]})
        o = Transport({"x": [ok_body("openrouter answered")]})
        routed = RoutedClient([
            ProviderClient(provider("google", ("a", "b")), opener=g, quota=self.book),
            ProviderClient(provider("openrouter", ("x",)), opener=o, quota=self.book)])
        result = routed.chat([{"role": "user", "content": "hi"}])
        self.assertEqual((result.provider, result.model), ("openrouter", "x"))
        routed.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(g.calls, ["a", "b"], "google is not asked again today")

    def test_every_provider_failing_reports_every_reason(self):
        g = Transport({"a": [http_error(429, GOOGLE_DAY)]})
        o = Transport({"x": [http_error(404, "not served")]})
        routed = RoutedClient([
            ProviderClient(provider("google", ("a",)), opener=g, quota=self.book),
            ProviderClient(provider("openrouter", ("x",)), opener=o, quota=self.book)])
        with self.assertRaises(NoUsableModel) as ctx:
            routed.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.provider, "google+openrouter")
        reasons = dict(ctx.exception.attempts)
        self.assertEqual(reasons["google/a"], "daily quota spent")
        self.assertIn("404", reasons["openrouter/x"])

    def test_a_misconfigured_provider_is_skipped_not_fatal(self):
        broken = Provider(key="cloudflare", base_url="http://x/{account}", secret_name="NOPE",
                          account_secret_name="OVERMIND_TEST_NO_SUCH_ACCOUNT_VAR",
                          fallback_models=("m",), models_path=None)
        o = Transport({"x": [ok_body()]})
        routed = RoutedClient([ProviderClient(broken),
                               ProviderClient(provider("openrouter", ("x",)), opener=o)])
        self.assertEqual(routed.chat([{"role": "user", "content": "hi"}]).provider, "openrouter")

    def test_an_empty_route_is_refused(self):
        with self.assertRaises(ValueError):
            RoutedClient([])


if __name__ == "__main__":
    unittest.main(verbosity=2)
