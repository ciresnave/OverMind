# SPDX-License-Identifier: MIT OR Apache-2.0
"""Board item 71 / OverMind#(pending): does OverMind's agent loop actually
carry a tool call end to end once a provider emits one?

PM finding, 2026-09-27: `lanework._verdict`'s `if not changed: return
"NO_CHANGE"` collapses two completely different situations into one
observable outcome - "the model never tried to use a tool" and "the model
tried, the harness ran it, and it produced no change" both read as
NO_CHANGE. A test (or a live run) that only reads the verdict cannot tell
them apart, which reproduces the exact blindness that let Lightbulb's
missing tool_calls emission go unnoticed. These tests never assert on a
verdict - they assert directly on `Ledger.executed_tools()` / a `Spy`'s own
call record, so "nothing was ever attempted" and "an attempt happened" stay
distinguishable no matter what a summary number would say.

Three response shapes, one axis - does `tool_calls` carry a real entry:

⚠️ WHAT THESE TESTS DO NOT PROVE, STATED PLAINLY SO NOBODY LATER MISREADS A
GREEN RUN: every response here is a SCRIPTED FIXTURE, not a real completion.
"3 tests passing" means OverMind's OWN consumption of a real `tool_calls`
entry is proven - it does NOT mean Lightbulb (or any provider) has ever
actually EMITTED that shape from a real model. That is a serving-side
question, entirely separate from what's tested here, and as of this
writing it is still open: Lightbulb's live TinyLlama-1.1B-Chat-v1.0
instance was directly observed, twice, never producing a `tool_calls`
entry at all (its chat template has no tool-call scaffolding, measured by
the PM) - the third test below documents what OverMind does WHEN a
real entry arrives, not that one currently does.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.agent import run_agent  # noqa: E402
from overmind.gate import DenyUnlessDeclared, Gate, GatedExecutor, Ledger  # noqa: E402
from overmind.providers import ChatResult  # noqa: E402

WRITE_FILE_TOOL = [{
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write a file's full contents.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
}]


class ScriptedClient:
    """A model whose every turn is written in advance - same pattern as
    test_agent.py's own ScriptedClient, duplicated here rather than
    imported so this file stays a self-contained fixture of the real wire
    shapes it documents."""

    def __init__(self, turns):
        self.turns = list(turns)

    def chat(self, messages, tools=None, max_tokens=None, **kw):
        msg = self.turns.pop(0) if self.turns else {"role": "assistant", "content": "done"}
        return ChatResult(message=dict(msg), model="scripted", provider="test", latency_s=0.0)


def tool_turn(name: str, args: str, call_id: str = "c1") -> dict:
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name, "arguments": args}}]}


class Spy:
    def __init__(self, result: str = "ok"):
        self.calls: list[dict] = []
        self.result = result

    def __call__(self, **kw):
        self.calls.append(kw)
        return self.result


def build_permissive_gate(tools: dict) -> tuple[Gate, GatedExecutor]:
    """⚠️ MEASURED, corrected while writing this file: `Gate.decide()`
    denies BY DEFAULT when no policy covers a tool at all ("an uncovered
    tool is refused, not allowed", gate.py:447-450) - the opposite of what
    an empty policy list first looked like it would do. `DenyUnlessDeclared`
    is what turns that default deny into an explicit allow for the tools
    named here, exactly the same policy `test_agent.py`'s own tests use.
    This isolates these tests to the ONE question that matters: does a real
    `tool_calls` entry reach `executor.execute` at all, never whether an
    unrelated gate policy would also have refused it."""
    gate = Gate([DenyUnlessDeclared(reversible=frozenset(tools))], ledger=Ledger())
    return gate, GatedExecutor(gate, tools)


class TestToolCallSeamDistinguishability(unittest.TestCase):
    """Each scenario is checked against the SPY's own call record and the
    ledger, never the agent run's summary alone - that is the whole point
    of writing these three as separate, explicitly-named tests rather than
    one parametrised assertion on a verdict."""

    def test_tool_calls_key_absent_executes_nothing(self):
        """Mirrors what was ACTUALLY, LIVE observed from Lightbulb's
        TinyLlama-1.1B-Chat-v1.0 instance, 2026-09-27 (both this lane's own
        round-trip attempt and lightbulb's own get_weather test): no
        "tool_calls" key in the response at all, ordinary prose/code in
        `content` instead. Real, not a hypothetical - PM measured
        afterward that this checkpoint's chat template has zero tool-call
        scaffolding, so there was never anything for it to emit."""
        spy = Spy()
        _, executor = build_permissive_gate({"write_file": spy})
        client = ScriptedClient([
            {"role": "assistant",
             "content": "I'll write the file using Python:\n\nwith open('hello.txt', 'w') as f:\n    f.write('hi')"},
        ])
        run = run_agent(client, executor, WRITE_FILE_TOOL, "Write hello.txt containing 'hi'.",
                        max_steps=1)
        self.assertEqual(spy.calls, [], "nothing to execute: no tool_calls key at all")
        self.assertFalse(run.did("write_file"))

    def test_tool_calls_present_but_empty_executes_nothing(self):
        """The other shape that can mean "no tool use this turn" - an
        explicit empty array rather than an absent key. ⚠️ OverMind's own
        `ChatResult.tool_calls` (`self.message.get("tool_calls") or []`)
        cannot currently tell this apart from the absent-key case above -
        both collapse to the same empty list at that layer. Not a bug to
        fix here (both mean the identical thing semantically: no tool
        call was made), but recorded explicitly rather than left to be
        rediscovered as a surprise."""
        spy = Spy()
        _, executor = build_permissive_gate({"write_file": spy})
        client = ScriptedClient([{"role": "assistant", "content": "", "tool_calls": []}])
        run = run_agent(client, executor, WRITE_FILE_TOOL, "Write hello.txt containing 'hi'.",
                        max_steps=1)
        self.assertEqual(spy.calls, [], "nothing to execute: tool_calls present but empty")
        self.assertFalse(run.did("write_file"))

    def test_tool_calls_with_a_real_entry_executes_the_tool(self):
        """The shape Lightbulb's already-merged #99 plumbing is built to
        emit, once served by a model whose chat template actually has
        tool-call scaffolding (Qwen3-4B-Instruct-2507, per the PM's own
        positive control measurement: 6 "tools" + 3 "<tool_call>"
        occurrences in its template, against TinyLlama's zero).

        ⚠️ THIS TEST CURRENTLY PASSES, MEASURED HERE, NOT ASSUMED FROM A
        CODE READING. It was written expecting it might need
        `@unittest.expectedFailure` (a live round trip through Lightbulb's
        real endpoint had not yet produced this shape from any real
        model), but run directly against current `main` it already
        succeeds: OverMind's own consumption path (`ChatResult.tool_calls`,
        `agent.py`'s per-call execution loop) is generic over any
        provider and has no gap here. The seam this test proves is
        OverMind's own; whether Lightbulb's live endpoint ever actually
        PRODUCES this shape from a real model is a separate, still-open
        question - answerable only by a real round trip once Lightbulb
        reports it is serving a model with tool-call output, not by this
        fixture."""
        spy = Spy()
        _, executor = build_permissive_gate({"write_file": spy})
        client = ScriptedClient([
            tool_turn("write_file", '{"path": "hello.txt", "content": "hi"}'),
            {"role": "assistant", "content": "Done."},
        ])
        run = run_agent(client, executor, WRITE_FILE_TOOL, "Write hello.txt containing 'hi'.",
                        max_steps=2)
        self.assertEqual(len(spy.calls), 1, "the tool must actually run, exactly once")
        self.assertEqual(spy.calls[0], {"path": "hello.txt", "content": "hi"})
        self.assertTrue(run.did("write_file"))


if __name__ == "__main__":
    unittest.main()
