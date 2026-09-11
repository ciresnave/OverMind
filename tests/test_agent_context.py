# SPDX-License-Identifier: MIT OR Apache-2.0
"""Does the agent still work when its only memory is the ledger?

⚠️ THE ARCHITECTURE THIS PROJECT IMPLIES, MEASURED RATHER THAN ASSERTED. A
Claude session gets "what have I already done" for free by keeping its
transcript. An OverMind agent is a cheap model with a small context and one
dispatch, and it has no such luxury - so the state has to live somewhere else.

The ledger already holds it. It was built as evidence FOR the reconciler; these
tests are about pointing it the other way and handing it back to the model.

⚠️ SAME MOVE AS THE GATE, ONE LAYER OVER: the gate refuses instead of trusting
the model to refuse; this remembers instead of trusting the model to remember.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.agent import run_agent                                   # noqa: E402
from overmind.gate import (                                            # noqa: E402
    DenyUnlessDeclared, Gate, GatedExecutor, Ledger, StaticFacts,
)
from overmind.providers import ChatResult, Usage                       # noqa: E402

SCHEMAS = [{"type": "function",
            "function": {"name": "ping", "description": "ping",
                         "parameters": {"type": "object", "properties": {}}}},
           {"type": "function",
            "function": {"name": "pong", "description": "pong",
                         "parameters": {"type": "object", "properties": {}}}}]


def _result(content="", calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return ChatResult(message=msg, model="fake", provider="fake", latency_s=0.0,
                      usage=Usage.zero(),
                      finish_reason="tool_calls" if calls else "stop")


def _call(cid, name):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": "{}"}}


class Recorder:
    """A client that records exactly what it was SHOWN, per step."""

    def __init__(self, script):
        self.script = list(script)
        self.prompts = []

    def chat(self, messages, tools=None, max_tokens=None):
        self.prompts.append([dict(m) for m in messages])
        return self.script.pop(0) if self.script else _result("done")


def _wire(script):
    gate = Gate([DenyUnlessDeclared(reversible=frozenset({"ping", "pong"}))],
                facts=StaticFacts(), ledger=Ledger())
    executor = GatedExecutor(gate, {"ping": lambda: "PING-OK",
                                    "pong": lambda: "PONG-OK"})
    return Recorder(script), executor, gate


class TestLedgerContextMode(unittest.TestCase):

    def test_transcript_grows_and_ledger_stays_flat(self):
        """⚠️ THE WHOLE POINT, made visible. Transcript mode accumulates every
        turn; ledger mode rebuilds the same small prompt every step regardless
        of how much has already happened."""
        script = [_result(calls=[_call("1", "ping")]),
                  _result(calls=[_call("2", "pong")]),
                  _result("finished")]
        client, executor, _ = _wire(list(script))
        run_agent(client, executor, SCHEMAS, "do both", context_mode="transcript")
        grew = [len(p) for p in client.prompts]

        client2, executor2, _ = _wire(list(script))
        run_agent(client2, executor2, SCHEMAS, "do both", context_mode="ledger")
        flat = [len(p) for p in client2.prompts]

        self.assertEqual(grew, sorted(grew), "transcript mode should not shrink")
        self.assertGreater(grew[-1], grew[0], "transcript mode did not accumulate")
        self.assertEqual(set(flat), {1}, "ledger mode did not stay flat: %r" % flat)

    def test_the_model_is_actually_told_what_it_already_did(self):
        """A flat prompt that says nothing would be flat AND useless. The
        content is the claim, not the length."""
        script = [_result(calls=[_call("1", "ping")]), _result("finished")]
        client, executor, _ = _wire(script)
        run_agent(client, executor, SCHEMAS, "ping once", context_mode="ledger")
        second = client.prompts[1][0]["content"]
        self.assertIn("ping", second)
        self.assertIn("PING-OK", second, "the ledger digest omitted the RESULT")

    def test_the_first_step_says_nothing_has_happened(self):
        """⚠️ Not an empty section. An absent digest and a digest saying nothing
        has happened are different messages, and only one is honest about being
        the first step."""
        client, executor, _ = _wire([_result("done")])
        run_agent(client, executor, SCHEMAS, "task", context_mode="ledger")
        self.assertIn("Nothing has been done yet",
                      client.prompts[0][0]["content"])

    def test_a_refused_call_is_in_the_digest(self):
        """⚠️ A model that cannot see its own refusals will re-attempt them -
        costing a round trip and, on a metered provider, a request from a daily
        budget."""
        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"ping"}))],
                    facts=StaticFacts(), ledger=Ledger())
        executor = GatedExecutor(gate, {"ping": lambda: "PING-OK",
                                        "pong": lambda: "PONG-OK"})
        client = Recorder([_result(calls=[_call("1", "pong")]), _result("done")])
        run_agent(client, executor, SCHEMAS, "task", context_mode="ledger")
        second = client.prompts[1][0]["content"]
        self.assertIn("pong", second)
        self.assertIn("REFUSED", second)

    def test_a_harness_correction_survives_the_rebuild(self):
        """⚠️ The one thing the ledger CANNOT hold. A smuggled-call correction
        is the harness talking to the model, not a record of a tool call - so it
        has no ledger entry. Dropping it on rebuild would leave the model making
        the same protocol error forever while the harness re-issued the same
        correction, with neither side able to see the loop."""
        smuggle = _result('I will call {"name": "ping", "arguments": {}} now')
        client, executor, _ = _wire([smuggle, _result("done")])
        run_agent(client, executor, SCHEMAS, "task", context_mode="ledger")
        second = client.prompts[1][0]["content"]
        self.assertIn("Notes from the harness", second)
        self.assertIn("not executed", second)

    def test_the_system_prompt_survives_the_rebuild(self):
        client, executor, _ = _wire([_result(calls=[_call("1", "ping")]),
                                     _result("done")])
        run_agent(client, executor, SCHEMAS, "task", system="BE TERSE",
                  context_mode="ledger")
        self.assertEqual(client.prompts[1][0]["role"], "system")
        self.assertEqual(client.prompts[1][0]["content"], "BE TERSE")

    def test_an_unknown_mode_is_refused_rather_than_defaulted(self):
        """⚠️ A typo must not silently select the control. An experiment whose
        treatment arm falls back to the control reports no difference, and that
        report is indistinguishable from a real null result."""
        client, executor, _ = _wire([_result("done")])
        with self.assertRaises(ValueError):
            run_agent(client, executor, SCHEMAS, "task", context_mode="ledgerr")

    def test_both_modes_reach_the_same_verdict_with_no_state_to_carry(self):
        """The control. If ledger mode changed the OUTCOME on a task with no
        state to carry, the mechanism would be doing something other than what
        it says on the tin."""
        for mode in ("transcript", "ledger"):
            with self.subTest(mode=mode):
                client, executor, gate = _wire([_result(calls=[_call("1", "ping")]),
                                                _result("done")])
                run = run_agent(client, executor, SCHEMAS, "ping",
                                context_mode=mode)
                self.assertEqual(str(run.stop_reason), "completed")
                self.assertEqual(gate.ledger.executed_tools(), ("ping",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
