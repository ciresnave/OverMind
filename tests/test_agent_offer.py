# SPDX-License-Identifier: MIT OR Apache-2.0
"""Does the model get shown tools the gate will always refuse?

⚠️ THIS IS A CORRECTNESS TEST WEARING A COST TEST'S CLOTHES. §22 measured the
money - 6,255 tokens against 2,595 for the identical task, 59% of the bill spent
describing tools that would have been refused. That is the smaller half.

The larger half: DESCRIBING A TOOL THE GATE WILL ALWAYS REFUSE INVITES AN
ATTEMPT, and the attempt costs a round trip to be told no. The permitted set and
the described set drifting apart is a cost bug and a correctness bug at once.

⚠️ AND THE SUBTRACTION MUST STAY CONSERVATIVE. A tool dropped as
certainly-denied that would sometimes have succeeded is a FALSE STATEMENT about
what the system will do; a tool left in that would always be refused is only a
smaller saving. The two errors are not symmetric and the tests below pin the
asymmetry.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.agent import run_agent                                   # noqa: E402
from overmind.gate import (                                            # noqa: E402
    DenyUnlessDeclared, Gate, GatedExecutor, Ledger, NoSelfMerge, StaticFacts,
)
from overmind.providers import ChatResult, Usage                       # noqa: E402


def _schema(name):
    return {"type": "function",
            "function": {"name": name, "description": name,
                         "parameters": {"type": "object", "properties": {}}}}


SCHEMAS = [_schema("allowed_tool"), _schema("forbidden_tool")]


class Recorder:
    """Records the tool schemas the model was actually SHOWN, per step."""

    def __init__(self):
        self.offered = []

    def chat(self, messages, tools=None, max_tokens=None):
        self.offered.append([(t.get("function") or {}).get("name") for t in (tools or [])])
        return ChatResult(message={"role": "assistant", "content": "done"},
                          model="fake", provider="fake", latency_s=0.0,
                          usage=Usage.zero(), finish_reason="stop")


def _wire(policies=None):
    gate = Gate(policies or [DenyUnlessDeclared(reversible=frozenset({"allowed_tool"}))],
                facts=StaticFacts(), ledger=Ledger())
    executor = GatedExecutor(gate, {"allowed_tool": lambda: "ok",
                                    "forbidden_tool": lambda: "ok"})
    return Recorder(), executor


class TestOfferPermitted(unittest.TestCase):

    def test_default_shows_everything_the_caller_passed(self):
        """⚠️ THE DEFAULT IS DELIBERATE. A harness that quietly edits the tool
        list is one whose behaviour cannot be predicted from its inputs."""
        client, executor = _wire()
        run = run_agent(client, executor, SCHEMAS, "task")
        self.assertEqual(client.offered[0], ["allowed_tool", "forbidden_tool"])
        self.assertEqual(run.offered_but_refused, ["forbidden_tool"],
                         "the waste must still be REPORTED even when not dropped")

    def test_permitted_drops_what_the_gate_will_always_refuse(self):
        client, executor = _wire()
        run = run_agent(client, executor, SCHEMAS, "task", offer="permitted")
        self.assertEqual(client.offered[0], ["allowed_tool"])
        self.assertEqual(run.offered_but_refused, ["forbidden_tool"],
                         "dropping must not hide WHAT was dropped")

    def test_an_argument_dependent_policy_is_never_dropped(self):
        """🔴 THE CONTROL THAT MATTERS MOST.

        `NoSelfMerge` refuses a merge of YOUR pull request and allows one of
        somebody else's. "Sometimes refused" is not "always refused", so it must
        survive the subtraction even under `offer="permitted"`.

        ⚠️ A TOOL DROPPED AS CERTAINLY-DENIED THAT WOULD SOMETIMES HAVE
        SUCCEEDED IS A FALSE STATEMENT ABOUT WHAT THE SYSTEM WILL DO. A tool
        left in that would always be refused is merely a smaller saving. The
        errors are not symmetric.
        """
        # ⚠️ BOTH ARMS IN ONE RUN, so the test cannot pass because the
        # subtraction is inert in this configuration. `undeclared_tool` is
        # ALWAYS refused and must go; `merge_pr` is declared and refused only
        # for certain arguments, so it must stay. If the mechanism were doing
        # nothing, the first assertion would fail.
        schemas = [_schema("allowed_tool"), _schema("merge_pr"),
                   _schema("undeclared_tool")]
        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"allowed_tool", "merge_pr"})),
                     NoSelfMerge()],
                    facts=StaticFacts(), ledger=Ledger())
        executor = GatedExecutor(gate, {"allowed_tool": lambda: "ok",
                                        "merge_pr": lambda **k: "ok",
                                        "undeclared_tool": lambda: "ok"})
        client = Recorder()
        run = run_agent(client, executor, schemas, "task", offer="permitted")

        self.assertNotIn("undeclared_tool", client.offered[0],
                         "the subtraction did nothing at all in this run")
        self.assertIn("merge_pr", client.offered[0],
                      "an argument-dependent refusal was dropped as certain")
        self.assertEqual(run.offered_but_refused, ["undeclared_tool"])

    def test_nothing_to_drop_leaves_the_list_identical(self):
        """The control for the control: `permitted` must not be a no-op that
        happens to look right because everything was permitted anyway."""
        gate = Gate([DenyUnlessDeclared(
            reversible=frozenset({"allowed_tool", "forbidden_tool"}))],
            facts=StaticFacts(), ledger=Ledger())
        executor = GatedExecutor(gate, {"allowed_tool": lambda: "ok",
                                        "forbidden_tool": lambda: "ok"})
        client = Recorder()
        run = run_agent(client, executor, SCHEMAS, "task", offer="permitted")
        self.assertEqual(client.offered[0], ["allowed_tool", "forbidden_tool"])
        self.assertEqual(run.offered_but_refused, [])

    def test_dropping_every_tool_still_runs(self):
        """⚠️ An empty tool list is a legitimate state, not an error. The model
        answers in prose, and the run completes. Refusing here would turn a
        restrictive policy into an outage."""
        gate = Gate([DenyUnlessDeclared(reversible=frozenset())],
                    facts=StaticFacts(), ledger=Ledger())
        executor = GatedExecutor(gate, {"allowed_tool": lambda: "ok",
                                        "forbidden_tool": lambda: "ok"})
        client = Recorder()
        run = run_agent(client, executor, SCHEMAS, "task", offer="permitted")
        self.assertEqual(client.offered[0], [])
        self.assertEqual(str(run.stop_reason), "completed")
        self.assertEqual(sorted(run.offered_but_refused),
                         ["allowed_tool", "forbidden_tool"])

    def test_an_unknown_offer_mode_is_refused_rather_than_defaulted(self):
        """⚠️ A typo must not silently select the permissive behaviour - the
        same reason `context_mode` raises."""
        client, executor = _wire()
        with self.assertRaises(ValueError):
            run_agent(client, executor, SCHEMAS, "task", offer="permited")

    def test_the_drop_persists_across_steps(self):
        """Schemas are re-sent every turn, so a subtraction that only applied on
        turn one would give back most of what it saved."""
        class TwoStep(Recorder):
            def __init__(self):
                super().__init__()
                self.n = 0

            def chat(self, messages, tools=None, max_tokens=None):
                super().chat(messages, tools, max_tokens)
                self.n += 1
                if self.n == 1:
                    return ChatResult(
                        message={"role": "assistant", "content": "",
                                 "tool_calls": [{"id": "1", "type": "function",
                                                 "function": {"name": "allowed_tool",
                                                              "arguments": "{}"}}]},
                        model="fake", provider="fake", latency_s=0.0,
                        usage=Usage.zero(), finish_reason="tool_calls")
                return ChatResult(message={"role": "assistant", "content": "done"},
                                  model="fake", provider="fake", latency_s=0.0,
                                  usage=Usage.zero(), finish_reason="stop")

        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"allowed_tool"}))],
                    facts=StaticFacts(), ledger=Ledger())
        executor = GatedExecutor(gate, {"allowed_tool": lambda: "ok",
                                        "forbidden_tool": lambda: "ok"})
        client = TwoStep()
        run_agent(client, executor, SCHEMAS, "task", offer="permitted")
        self.assertGreaterEqual(len(client.offered), 2)
        for step, shown in enumerate(client.offered):
            self.assertEqual(shown, ["allowed_tool"], f"step {step} regained it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
