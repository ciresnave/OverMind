"""Tests for the agent loop.

The load-bearing assertions are about the LEDGER, not about what the loop
returns. A loop that reports "denied" while the effect happened would pass a
test that only reads its return value.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.agent import StopReason, run_agent  # noqa: E402
from overmind.gate import (  # noqa: E402
    DenyUnlessDeclared, Gate, GatedExecutor, Ledger, NoSelfMerge, StaticFacts,
)
from overmind.providers import ChatResult  # noqa: E402

TOOLS = [
    {"type": "function", "function": {"name": "merge_pull_request", "description": "merge",
     "parameters": {"type": "object", "properties": {"pr": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "request_gate_review", "description": "hand to gate",
     "parameters": {"type": "object", "properties": {"pr": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "list_entities", "description": "list",
     "parameters": {"type": "object", "properties": {}}}},
]


class ScriptedClient:
    """A model whose every turn is written in advance, so a test can reproduce
    an exact measured behaviour rather than hope a real model repeats it."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools=None, max_tokens=None, **kw):
        self.seen.append([dict(m) for m in messages])
        if not self.turns:
            msg = {"role": "assistant", "content": "nothing further"}
        else:
            msg = self.turns.pop(0)
        if isinstance(msg, Exception):
            raise msg
        return ChatResult(message=dict(msg), model="scripted", provider="test", latency_s=0.0)


def tool_turn(name, args="{}", call_id="c1"):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name, "arguments": args}}]}


class Spy:
    def __init__(self, result="ok"):
        self.calls = []
        self.result = result

    def __call__(self, **kw):
        self.calls.append(kw)
        return self.result


def build_gate(policies, facts=None, tools=None):
    gate = Gate(policies, facts=facts, ledger=Ledger())
    return gate, GatedExecutor(gate, tools or {})


class TestSelfMergeEndToEnd(unittest.TestCase):
    """§11 reproduced as a loop: a model that intends to self-merge, wired to a
    real merge function, must not merge."""

    def setUp(self):
        self.merge = Spy("MERGED")
        self.review = Spy("handed to the gate")
        facts = StaticFacts({"self_identity": "overmind-bot", "pr_author": "overmind-bot"})
        self.gate, self.ex = build_gate(
            [NoSelfMerge(),
             DenyUnlessDeclared(reversible=frozenset({"merge_pull_request",
                                                      "request_gate_review", "list_entities"}))],
            facts=facts,
            tools={"merge_pull_request": self.merge, "request_gate_review": self.review},
        )

    def test_the_merge_never_happens(self):
        client = ScriptedClient([tool_turn("merge_pull_request", '{"pr": 42}'),
                                 {"role": "assistant", "content": "I have landed PR 42."}])
        run = run_agent(client, self.ex, TOOLS, "Get PR 42 landed.")
        self.assertEqual(self.merge.calls, [], "the merge function ran")
        self.assertFalse(run.did("merge_pull_request"))
        self.assertEqual(run.denied_count, 1)

    def test_the_models_claim_of_success_is_contradicted_by_the_ledger(self):
        """⚠️ The model says it landed the PR. The ledger says no merge ran. The
        ledger is the evidence and the loop exposes both without reconciling
        them - it is not the loop's job to argue with the model."""
        client = ScriptedClient([tool_turn("merge_pull_request", '{"pr": 42}'),
                                 {"role": "assistant", "content": "Done - PR 42 is merged."}])
        run = run_agent(client, self.ex, TOOLS, "Get PR 42 landed.")
        self.assertIn("merged", run.final_text.lower())     # testimony
        self.assertFalse(run.did("merge_pull_request"))     # evidence

    def test_the_refusal_is_fed_back_so_the_model_can_take_the_legal_route(self):
        """⚠️ A refusal is a RESULT, not an outage. A loop that raised here would
        turn every policy into a dead end."""
        client = ScriptedClient([tool_turn("merge_pull_request", '{"pr": 42}'),
                                 tool_turn("request_gate_review", '{"pr": 42}', "c2"),
                                 {"role": "assistant", "content": "Handed to the gate."}])
        run = run_agent(client, self.ex, TOOLS, "Get PR 42 landed.")
        self.assertEqual(self.merge.calls, [])
        self.assertEqual(self.review.calls, [{"pr": 42}])
        self.assertTrue(run.did("request_gate_review"))
        self.assertEqual(run.stop_reason, StopReason.COMPLETED)

    def test_the_denial_text_reaches_the_model(self):
        client = ScriptedClient([tool_turn("merge_pull_request", '{"pr": 42}'),
                                 {"role": "assistant", "content": "ok"}])
        run_agent(client, self.ex, TOOLS, "Get PR 42 landed.")
        second_turn = client.seen[1]
        tool_msgs = [m for m in second_turn if m.get("role") == "tool"]
        self.assertTrue(tool_msgs)
        self.assertIn("REFUSED by the harness", tool_msgs[-1]["content"])


class TestSmuggledToolCalls(unittest.TestCase):
    """§8.2 - four of five local failures wrote tool-call JSON into content."""

    def setUp(self):
        self.spy = Spy()
        self.gate, self.ex = build_gate(
            [DenyUnlessDeclared(reversible=frozenset({"list_entities"}))],
            tools={"list_entities": self.spy})

    def test_smuggled_call_is_re_prompted_not_executed(self):
        client = ScriptedClient([
            {"role": "assistant", "content": '{"name": "list_entities", "arguments": {}}'},
            tool_turn("list_entities"),
            {"role": "assistant", "content": "Listed."},
        ])
        run = run_agent(client, self.ex, TOOLS, "List the entities.")
        self.assertEqual(run.stop_reason, StopReason.COMPLETED)
        self.assertEqual(len(self.spy.calls), 1, "the smuggled form must not execute")
        nudge = [m for m in client.seen[1] if m.get("role") == "user"]
        self.assertIn("was not executed", nudge[-1]["content"])

    def test_twice_is_a_protocol_failure_not_an_argument(self):
        """⚠️ A model that cannot emit a tool call will not be argued into it."""
        smuggle = {"role": "assistant", "content": '{"name": "list_entities", "arguments": {}}'}
        client = ScriptedClient([smuggle, smuggle, smuggle])
        run = run_agent(client, self.ex, TOOLS, "List the entities.")
        self.assertEqual(run.stop_reason, StopReason.PROTOCOL_FAILURE)
        self.assertEqual(self.spy.calls, [])
        self.assertIn("instead of emitting a tool call", run.error)

    def test_ordinary_prose_is_not_mistaken_for_a_smuggled_call(self):
        client = ScriptedClient([{"role": "assistant",
                                  "content": "I could call list_entities, but I do not need to."}])
        run = run_agent(client, self.ex, TOOLS, "Do nothing.")
        self.assertEqual(run.stop_reason, StopReason.COMPLETED)


class TestNarrationLeavesNoTrace(unittest.TestCase):
    """§8.2 - granite3.3:8b announced a call it never made."""

    def test_pure_narration_executes_nothing(self):
        spy = Spy()
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset({"list_entities"}))],
                              tools={"list_entities": spy})
        client = ScriptedClient([{"role": "assistant",
                                  "content": "Calling the function to list entities... Done!"}])
        run = run_agent(client, ex, TOOLS, "List them.")
        self.assertEqual(spy.calls, [])
        self.assertEqual(run.executed_tools, ())
        self.assertEqual(len(gate.ledger), 0)


class TestLoopControl(unittest.TestCase):
    def test_provider_error_is_reported_not_raised(self):
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset())])
        client = ScriptedClient([RuntimeError("provider on fire")])
        run = run_agent(client, ex, TOOLS, "anything")
        self.assertEqual(run.stop_reason, StopReason.PROVIDER_ERROR)
        self.assertIn("provider on fire", run.error)

    def test_max_steps_is_respected(self):
        """⚠️ Arguments VARY here. An earlier version scripted identical calls,
        which now stops on NO_PROGRESS instead - so it was asserting the old,
        wasteful behaviour rather than the step limit. A model making genuine
        progress must still be bounded."""
        spy = Spy()
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset({"list_entities"}))],
                              tools={"list_entities": spy})
        client = ScriptedClient([tool_turn("list_entities", '{"page":%d}' % i)
                                 for i in range(20)])
        run = run_agent(client, ex, TOOLS, "loop", max_steps=3)
        self.assertEqual(run.stop_reason, StopReason.MAX_STEPS)
        self.assertEqual(len(spy.calls), 3)

    def test_unparseable_arguments_do_not_crash_the_loop(self):
        spy = Spy()
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset({"list_entities"}))],
                              tools={"list_entities": spy})
        client = ScriptedClient([tool_turn("list_entities", "not json at all"),
                                 {"role": "assistant", "content": "done"}])
        run = run_agent(client, ex, TOOLS, "list")
        self.assertEqual(run.stop_reason, StopReason.COMPLETED)
        self.assertEqual(spy.calls, [{}])

    def test_system_prompt_is_sent_when_given(self):
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset())])
        client = ScriptedClient([{"role": "assistant", "content": "ok"}])
        run_agent(client, ex, TOOLS, "task", system="RULES HERE")
        self.assertEqual(client.seen[0][0], {"role": "system", "content": "RULES HERE"})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestNoProgressDetection(unittest.TestCase):
    """⚠️ MEASURED on two providers: on a two-step task, models called the FIRST
    tool four and seven times, never reached step two, and produced no final
    text. Running that to max_steps burns tokens for nothing - the exact cost
    this project exists to remove."""

    def setUp(self):
        self.spy = Spy("channel created")
        self.gate, self.ex = build_gate(
            [DenyUnlessDeclared(reversible=frozenset({"list_entities"}))],
            tools={"list_entities": self.spy})

    def test_an_identical_repeat_is_not_executed_again(self):
        """⚠️ Re-running it would be an EFFECT the model did not earn - and for a
        non-idempotent tool that is a second channel, a second message."""
        client = ScriptedClient([tool_turn("list_entities", '{"a":1}'),
                                 tool_turn("list_entities", '{"a":1}'),
                                 {"role": "assistant", "content": "done"}])
        run = run_agent(client, self.ex, TOOLS, "go")
        self.assertEqual(len(self.spy.calls), 1, "the repeat was executed again")
        self.assertEqual(run.stop_reason, StopReason.COMPLETED)

    def test_the_repeat_is_told_what_it_already_got(self):
        client = ScriptedClient([tool_turn("list_entities", '{"a":1}'),
                                 tool_turn("list_entities", '{"a":1}'),
                                 {"role": "assistant", "content": "done"}])
        run_agent(client, self.ex, TOOLS, "go")
        tool_msgs = [m for m in client.seen[-1] if m.get("role") == "tool"]
        self.assertIn("ALREADY CALLED", tool_msgs[-1]["content"])
        self.assertIn("channel created", tool_msgs[-1]["content"])

    def test_a_third_identical_call_stops_the_loop(self):
        client = ScriptedClient([tool_turn("list_entities", '{"a":1}')] * 5)
        run = run_agent(client, self.ex, TOOLS, "go", max_steps=8)
        self.assertEqual(run.stop_reason, StopReason.NO_PROGRESS)
        self.assertEqual(len(self.spy.calls), 1)
        self.assertIn("repeated an identical call", run.error)

    def test_different_arguments_are_not_a_repeat(self):
        """The control - varying arguments IS progress and must not be blocked."""
        client = ScriptedClient([tool_turn("list_entities", '{"a":1}'),
                                 tool_turn("list_entities", '{"a":2}'),
                                 {"role": "assistant", "content": "done"}])
        run = run_agent(client, self.ex, TOOLS, "go")
        self.assertEqual(len(self.spy.calls), 2)
        self.assertEqual(run.stop_reason, StopReason.COMPLETED)
