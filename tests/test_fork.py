# SPDX-License-Identifier: MIT OR Apache-2.0
"""Tests for forking. As in test_agent.py, the load-bearing assertions are about
the LEDGER: a fork's summary is testimony, and only its ledger is evidence."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.providers import ChatResult, Usage  # noqa: E402


def U(prompt, completion):
    return Usage(prompt, completion, prompt + completion, reported=True)


class ScriptedClient:
    """Every turn written in advance. A turn is a message dict, a
    (message, Usage) pair, or an Exception to raise. Records what it was sent,
    tools included, so a test can check the shared prefix."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen: list[list[dict]] = []
        self.seen_tools: list = []

    def chat(self, messages, tools=None, max_tokens=None, **kw):
        self.seen.append([dict(m) for m in messages])
        self.seen_tools.append(tools)
        item = self.turns.pop(0) if self.turns else {"role": "assistant",
                                                     "content": "nothing further"}
        if isinstance(item, Exception):
            raise item
        msg, usage = item if isinstance(item, tuple) else (item, Usage())
        return ChatResult(message=dict(msg), model="scripted", provider="test",
                          latency_s=0.0, usage=usage)


def tool_turn(name, args="{}", call_id="c1", content=None):
    return {"role": "assistant", "content": content,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name, "arguments": args}}]}


class TestCurateNow(unittest.TestCase):
    MESSAGES = [{"role": "user", "content": "task"},
                {"role": "assistant", "content": "found three things"}]

    def test_asks_for_a_summary_after_the_messages(self):
        from overmind.fork import CURATE_INSTRUCTION, curate_now
        client = ScriptedClient([({"role": "assistant", "content": "SUMMARY"}, U(50, 5))])
        cur = curate_now(client, self.MESSAGES)
        self.assertEqual(client.seen[0], self.MESSAGES +
                         [{"role": "user", "content": CURATE_INSTRUCTION}])
        self.assertEqual(cur.text, "SUMMARY")
        self.assertEqual(cur.usage.total_tokens, 55)
        self.assertIsNone(cur.error)
        self.assertFalse(cur.truncated)

    def test_does_not_mutate_the_messages(self):
        from overmind.fork import curate_now
        messages = [dict(m) for m in self.MESSAGES]
        curate_now(ScriptedClient([{"role": "assistant", "content": "S"}]), messages)
        self.assertEqual(messages, self.MESSAGES)

    def test_passes_the_same_tools_so_the_prefix_still_matches(self):
        """⚠️ Tool schemas are part of the rendered prompt. Dropping them here
        would change the prefix and defeat provider-side prefix caching."""
        from overmind.fork import curate_now
        tools = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
        client = ScriptedClient([{"role": "assistant", "content": "S"}])
        curate_now(client, self.MESSAGES, tools=tools)
        self.assertEqual(client.seen_tools[0], tools)

    def test_a_tool_call_instead_of_a_summary_is_flagged_not_run(self):
        from overmind.fork import curate_now
        client = ScriptedClient([tool_turn("write_file", content="partial")])
        cur = curate_now(client, self.MESSAGES)
        self.assertEqual(cur.text, "partial")
        self.assertIn("tool", cur.error)

    def test_a_provider_error_is_returned_not_raised(self):
        from overmind.fork import curate_now
        cur = curate_now(ScriptedClient([RuntimeError("down")]), self.MESSAGES)
        self.assertEqual(cur.text, "")
        self.assertIn("RuntimeError: down", cur.error)
        self.assertFalse(cur.usage.reported)

    def test_truncation_is_flagged(self):
        from overmind.fork import curate_now

        class Cut(ScriptedClient):
            def chat(self, messages, tools=None, max_tokens=None, **kw):
                r = super().chat(messages, tools, max_tokens)
                r.finish_reason = "length"
                return r
        cur = curate_now(Cut([{"role": "assistant", "content": "half a sum"}]), self.MESSAGES)
        self.assertTrue(cur.truncated)

    def test_unanswered_tool_calls_are_refused(self):
        """A history ending in tool calls with no results is rejected by
        OpenAI-style providers, so refuse it here with a clear reason."""
        from overmind.fork import curate_now
        with self.assertRaises(ValueError):
            curate_now(ScriptedClient([]), self.MESSAGES + [tool_turn("x")])


if __name__ == "__main__":
    unittest.main()
