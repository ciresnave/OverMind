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


class TestForkCost(unittest.TestCase):
    """Worked numbers, so the formula is pinned rather than re-derived.
    work: 1000+100, 1300+150, 1700+200 -> work 4450, growth 1700+200-1000 = 900.
    parent: call 0 emitted the fork (1000+50); call 1 prompt 1200.
    returned = 1200 - 1000 - 50 = 150."""

    def cost(self, **kw):
        from overmind.fork import ForkCost
        base = dict(fork_id="f1", time_sensitive=False,
                    work_calls=(U(1000, 100), U(1300, 150), U(1700, 200)),
                    curation=U(1900, 80), parent_call_index=0)
        base.update(kw)
        return ForkCost(**base)

    def test_few_parent_turns_after_makes_inline_cheaper(self):
        c = self.cost()
        c.finalize([U(1000, 50), U(1200, 40), U(1300, 30)])
        self.assertEqual(c.growth, 900)
        self.assertEqual(c.returned_tokens, 150)
        self.assertEqual(c.parent_calls_after, 2)
        self.assertEqual(c.inline_tokens, 4450 + 900 * 2)            # 6250
        self.assertEqual(c.fork_tokens, 4450 + 1980 + 150 * 2)       # 6730
        self.assertEqual(c.cheaper, "inline")

    def test_many_parent_turns_after_makes_the_fork_cheaper(self):
        c = self.cost()
        c.finalize([U(1000, 50), U(1200, 40)] + [U(1300, 30)] * 9)
        self.assertEqual(c.parent_calls_after, 10)
        self.assertEqual(c.inline_tokens, 4450 + 900 * 10)           # 13450
        self.assertEqual(c.fork_tokens, 4450 + 1980 + 150 * 10)      # 7930
        self.assertEqual(c.cheaper, "fork")

    def test_a_fork_that_completed_on_its_own_has_zero_curation(self):
        c = self.cost(curation=Usage.zero())
        c.finalize([U(1000, 50), U(1200, 40)])
        self.assertEqual(c.fork_tokens, 4450 + 0 + 150 * 1)

    def test_unreported_usage_gives_none_not_zero(self):
        c = self.cost(work_calls=(U(1000, 100), Usage()))
        c.finalize([U(1000, 50), U(1200, 40)])
        self.assertIsNone(c.growth)
        self.assertIsNone(c.inline_tokens)
        self.assertIsNone(c.fork_tokens)
        self.assertIsNone(c.cheaper)

    def test_unreported_parent_usage_gives_no_returned_tokens(self):
        c = self.cost()
        c.finalize([U(1000, 50), Usage()])
        self.assertIsNone(c.returned_tokens)
        self.assertIsNone(c.fork_tokens)
        self.assertEqual(c.inline_tokens, 4450 + 900 * 1)

    def test_no_parent_call_after_the_fork(self):
        c = self.cost()
        c.finalize([U(1000, 50)])
        self.assertEqual(c.parent_calls_after, 0)
        self.assertIsNone(c.returned_tokens)
        self.assertEqual(c.inline_tokens, 4450)

    def test_a_fork_that_never_reached_a_model(self):
        c = self.cost(work_calls=())
        c.finalize([U(1000, 50), U(1200, 40)])
        self.assertIsNone(c.growth)
        self.assertIsNone(c.inline_tokens)

    def test_not_finalised_means_unknown(self):
        c = self.cost()
        self.assertIsNone(c.parent_calls_after)
        self.assertIsNone(c.inline_tokens)

    def test_to_dict_carries_the_flag_both_arms_and_the_basis(self):
        c = self.cost(time_sensitive=True, parent_model="m1", fork_model="m1")
        c.finalize([U(1000, 50), U(1200, 40), U(1300, 30)])
        d = c.to_dict()
        self.assertEqual(d["fork_id"], "f1")
        self.assertIs(d["time_sensitive"], True)
        self.assertEqual(d["inline_tokens"], 6250)
        self.assertEqual(d["fork_tokens"], 6730)
        self.assertEqual(d["cheaper"], "inline")
        self.assertEqual(d["parent_model"], "m1")
        self.assertIn("provider-reported", d["basis"])


if __name__ == "__main__":
    unittest.main()
