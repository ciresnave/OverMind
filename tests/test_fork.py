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


def gate_and_executor(reversible, tools=None, facts=None, extra=()):
    from overmind.gate import DenyUnlessDeclared, Gate, GatedExecutor, Ledger
    gate = Gate([*extra, DenyUnlessDeclared(reversible=frozenset(reversible))],
                facts=facts, ledger=Ledger())
    return GatedExecutor(gate, tools or {})


class Spy:
    def __init__(self, result="ok"):
        self.calls = []
        self.result = result

    def __call__(self, **kw):
        self.calls.append(kw)
        return self.result


TOOLS = [
    {"type": "function", "function": {"name": "write_file", "description": "w",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "list_entities", "description": "l",
     "parameters": {"type": "object", "properties": {}}}},
]

HISTORY = [{"role": "system", "content": "rules"},
           {"role": "user", "content": "the parent's task"},
           tool_turn("fork", '{"task": "investigate"}', call_id="cF")]


def fork(client, executor, *, tools=None, pending=(), config=None, task="investigate"):
    from overmind.fork import ForkConfig, fork_schema, run_fork
    return run_fork(client, executor, tools if tools is not None else TOOLS + [fork_schema()],
                    history=HISTORY, fork_call_id="cF", pending=pending, task=task,
                    fork_id="f1", config=config or ForkConfig(), time_sensitive=False,
                    parent_call_index=0, parent_model="scripted")


class TestRunFork(unittest.TestCase):
    def setUp(self):
        self.write = Spy("written")
        self.ex = gate_and_executor({"write_file", "list_entities", "fork"},
                                    tools={"write_file": self.write})

    def test_seed_is_parent_history_then_fork_result_then_the_task(self):
        client = ScriptedClient([{"role": "assistant", "content": "S"}])
        fork(client, self.ex, task="investigate X")
        first = client.seen[0]
        self.assertEqual(first[:3], HISTORY)
        self.assertEqual(first[3]["role"], "tool")
        self.assertEqual(first[3]["tool_call_id"], "cF")
        self.assertEqual(first[-1]["role"], "user")
        self.assertTrue(first[-1]["content"].startswith("investigate X"))

    def test_fork_sees_the_same_tools_as_the_parent(self):
        from overmind.fork import fork_schema
        client = ScriptedClient([{"role": "assistant", "content": "S"}])
        offered = TOOLS + [fork_schema()]
        fork(client, self.ex, tools=offered)
        self.assertEqual(client.seen_tools[0], offered)

    def test_a_completed_fork_curates_itself(self):
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 {"role": "assistant", "content": "wrote a"}])
        res = fork(client, self.ex)
        self.assertEqual(res.summary, "wrote a")
        self.assertEqual(res.curated_by, "completion")
        self.assertEqual(self.write.calls, [{"path": "a"}])
        self.assertTrue(res.run.did("write_file"))

    def test_the_parent_ledger_is_untouched(self):
        """Absorbing is the caller's job (execute_fork), after the fork call
        itself has its own seq on the parent's ledger."""
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 {"role": "assistant", "content": "wrote a"}])
        fork(client, self.ex)
        self.assertEqual(len(self.ex.gate.ledger), 0)

    def test_a_fork_that_runs_out_of_steps_is_asked_to_curate(self):
        from overmind.fork import CURATE_INSTRUCTION, ForkConfig
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}', "c1"),
                                 tool_turn("write_file", '{"path": "b"}', "c2"),
                                 {"role": "assistant", "content": "interim: a and b"}])
        res = fork(client, self.ex, config=ForkConfig(max_steps=2))
        self.assertEqual(res.run.stop_reason, "max-steps")
        self.assertEqual(client.seen[-1][-1], {"role": "user", "content": CURATE_INSTRUCTION})
        self.assertEqual(res.summary, "interim: a and b")
        self.assertEqual(res.curated_by, "curate_now")

    def test_provider_failure_mid_fork_keeps_the_evidence(self):
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 RuntimeError("down"), RuntimeError("still down")])
        res = fork(client, self.ex)
        self.assertEqual(res.run.stop_reason, "provider-error")
        self.assertEqual(res.curated_by, "none")
        self.assertEqual(res.summary, "")
        self.assertTrue(res.run.did("write_file"), "the effect that DID run was lost")

    def test_a_fork_cannot_fork(self):
        client = ScriptedClient([tool_turn("fork", '{"task": "deeper"}'),
                                 {"role": "assistant", "content": "S"}])
        res = fork(client, self.ex)
        denied = res.run.ledger.denied()
        self.assertEqual([e.call.name for e in denied], ["fork"])
        self.assertEqual(denied[0].decision.policy, "forbid-tools")
        self.assertEqual(len(client.seen), 2, "a nested fork reached a model")

    def test_extra_policies_only_take_away(self):
        from overmind.fork import ForkConfig
        from overmind.gate import ForbidTools
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 {"role": "assistant", "content": "S"}])
        res = fork(client, self.ex, config=ForkConfig(
            extra_policies=(ForbidTools(frozenset({"write_file"})),)))
        self.assertEqual(self.write.calls, [])
        self.assertFalse(res.run.did("write_file"))

    def test_the_parents_policies_still_bind_the_fork(self):
        from overmind.gate import ForbidTools
        ex = gate_and_executor({"write_file", "fork"}, tools={"write_file": self.write},
                               extra=(ForbidTools(frozenset({"write_file"})),))
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 {"role": "assistant", "content": "S"}])
        fork(client, ex)
        self.assertEqual(self.write.calls, [])

    def test_calls_after_fork_in_the_same_turn_get_not_run_placeholders(self):
        client = ScriptedClient([{"role": "assistant", "content": "S"}])
        fork(client, self.ex, pending=[("c9", "list_entities")])
        first = client.seen[0]
        placeholder = first[4]
        self.assertEqual(placeholder["tool_call_id"], "c9")
        self.assertIn("NOT RUN", placeholder["content"])

    def test_cost_is_recorded_unfinalised(self):
        client = ScriptedClient([({"role": "assistant", "content": "S"}, U(500, 20))])
        res = fork(client, self.ex)
        self.assertEqual(res.cost.fork_id, "f1")
        self.assertEqual([u.total_tokens for u in res.cost.work_calls], [520])
        self.assertTrue(res.cost.curation.reported)
        self.assertEqual(res.cost.curation.total_tokens, 0)
        self.assertIsNone(res.cost.parent_calls_after)
        self.assertEqual(res.cost.fork_model, "scripted")

    def test_str_shows_summary_and_ledger_labelled(self):
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 {"role": "assistant", "content": "wrote a"}])
        text = str(fork(client, self.ex))
        self.assertIn("testimony", text)
        self.assertIn("evidence", text)
        self.assertIn("wrote a", text)
        self.assertIn("write_file(path='a') -> OK", text)


class TestForkFacts(unittest.TestCase):
    def ledger_with(self, *names):
        from overmind.gate import Decision, Ledger, LedgerEntry, ToolCall
        ledger = Ledger()
        for i, n in enumerate(names):
            ledger.append(LedgerEntry(i, ToolCall(name=n), Decision.allow(), executed=True))
        return ledger

    def test_executed_tools_is_parent_then_child(self):
        from overmind.fork import ForkFacts
        from overmind.gate import LedgerFacts
        parent = LedgerFacts(self.ledger_with("list_entities"))
        facts = ForkFacts(parent, self.ledger_with("write_file"))
        self.assertEqual(facts.fact("executed_tools"), ("list_entities", "write_file"))

    def test_no_escalation_when_parent_executed_tools_is_static(self):
        """⚠️ A STATIC executed_tools never grows, so a parent holding one can
        never newly satisfy a RequirePrecondition. Appending the fork's own
        ledger to it would let the fork do what its parent cannot."""
        from overmind.fork import ForkFacts
        from overmind.gate import StaticFacts
        facts = ForkFacts(StaticFacts({"executed_tools": ()}), self.ledger_with("list_entities"))
        self.assertEqual(facts.fact("executed_tools"), ())

    def test_a_static_parent_precondition_still_binds_the_fork(self):
        from overmind.gate import RequirePrecondition, StaticFacts
        write = Spy()
        ex = gate_and_executor({"write_file", "list_entities", "fork"},
                               tools={"write_file": write, "list_entities": Spy()},
                               facts=StaticFacts({"executed_tools": ()}),
                               extra=(RequirePrecondition(tool="write_file",
                                                          requires="list_entities"),))
        client = ScriptedClient([tool_turn("list_entities", call_id="k1"),
                                 tool_turn("write_file", '{"path": "f"}', "k2"),
                                 {"role": "assistant", "content": "S"}])
        fork(client, ex)
        self.assertEqual(write.calls, [], "the fork escaped a precondition its parent cannot meet")

    def test_no_escalation_when_parent_does_not_wire_executed_tools(self):
        """⚠️ A parent whose facts never report executed_tools can never
        satisfy RequirePrecondition. Its fork must not gain that power from
        its own ledger - that would make the fork stronger than its parent."""
        from overmind.fork import ForkFacts
        from overmind.gate import StaticFacts
        facts = ForkFacts(StaticFacts(), self.ledger_with("list_entities"))
        self.assertIsNone(facts.fact("executed_tools"))

    def test_other_facts_pass_through(self):
        from overmind.fork import ForkFacts
        from overmind.gate import StaticFacts
        facts = ForkFacts(StaticFacts({"sender_vouched": True}), self.ledger_with())
        self.assertIs(facts.fact("sender_vouched"), True)

    def test_a_precondition_met_in_the_parent_holds_in_the_fork(self):
        from overmind.fork import ForkFacts
        from overmind.gate import (DenyUnlessDeclared, Gate, GatedExecutor, Ledger,
                                   RequirePrecondition, StaticFacts)
        child = Ledger()
        gate = Gate([RequirePrecondition(tool="write_file", requires="list_entities"),
                     DenyUnlessDeclared(reversible=frozenset({"write_file"}))],
                    facts=ForkFacts(StaticFacts({"executed_tools": ("list_entities",)}), child),
                    ledger=child)
        out = GatedExecutor(gate, {"write_file": Spy()}).execute("write_file", {"path": "a"})
        self.assertTrue(out.executed)


class TestForkInTheAgentLoop(unittest.TestCase):
    """End to end. ONE client serves parent and fork - sequential, so the
    scripted turns are consumed parent, fork..., parent."""

    def setUp(self):
        self.write = Spy("written")
        self.listing = Spy("alice")
        self.ex = gate_and_executor({"write_file", "list_entities", "fork"},
                                    tools={"write_file": self.write,
                                           "list_entities": self.listing})

    def run_parent(self, turns, **kw):
        from overmind.agent import run_agent
        from overmind.fork import ForkConfig
        self.client = ScriptedClient(turns)
        return run_agent(self.client, self.ex, TOOLS, "parent task",
                         fork=kw.pop("fork", ForkConfig()), **kw)

    def happy(self):
        return [
            tool_turn("fork", '{"task": "write a"}', "cF"),             # parent
            tool_turn("write_file", '{"path": "a"}', "k1",
                      content="SCRATCH-THOUGHT-XYZ"),                     # fork
            {"role": "assistant", "content": "wrote a"},                 # fork summary
            {"role": "assistant", "content": "parent done"},             # parent
        ]

    def test_the_forks_effects_land_on_the_parents_ledger_with_lineage(self):
        run = self.run_parent(self.happy())
        self.assertEqual(run.stop_reason, "completed")
        self.assertEqual(self.write.calls, [{"path": "a"}])
        names = [(e.call.name, e.lineage) for e in run.ledger.entries]
        self.assertEqual(names, [("fork", ()), ("write_file", ("f1",))])
        self.assertTrue(run.did("write_file"))
        self.assertEqual(len(run.forks), 1)

    def test_the_parent_never_sees_the_forks_transcript(self):
        run = self.run_parent(self.happy())
        parent_next = self.client.seen[-1]
        flat = chr(10).join(str(m.get("content")) for m in parent_next)
        self.assertNotIn("SCRATCH-THOUGHT-XYZ", flat)
        self.assertIn("wrote a", flat)                       # testimony
        self.assertIn("write_file(path='a') -> OK", flat)     # evidence

    def test_fork_and_parent_share_the_prefix(self):
        """The fork's first request and the parent's next request both begin
        with the parent's first request plus the assistant turn calling fork."""
        self.run_parent(self.happy())
        parent_first, fork_first, parent_next = (self.client.seen[0], self.client.seen[1],
                                                 self.client.seen[3])
        n = len(parent_first) + 1           # + the assistant turn that called fork
        self.assertEqual(fork_first[:n], parent_next[:n])
        self.assertEqual(fork_first[:len(parent_first)], parent_first)
        self.assertEqual(fork_first[n - 1]["tool_calls"][0]["id"], "cF")
        self.assertEqual(self.client.seen_tools[1], self.client.seen_tools[0])

    def test_cost_is_finalised_and_on_the_ledger(self):
        import json
        turns = [
            (tool_turn("fork", '{"task": "write a"}', "cF"), U(1000, 50)),
            (tool_turn("write_file", '{"path": "a"}', "k1"), U(1100, 60)),
            ({"role": "assistant", "content": "wrote a"}, U(1300, 40)),
            ({"role": "assistant", "content": "parent done"}, U(1200, 10)),
        ]
        run = self.run_parent(turns, time_sensitive=True)
        cost = run.forks[0].cost
        self.assertEqual(cost.parent_calls_after, 1)
        self.assertEqual(cost.returned_tokens, 1200 - 1000 - 50)
        rows = json.loads(run.ledger.to_json())
        self.assertIs(rows[0]["fork"]["time_sensitive"], True)
        self.assertEqual(rows[0]["fork"]["inline_tokens"], cost.inline_tokens)
        self.assertEqual(rows[0]["fork"]["fork_tokens"], cost.fork_tokens)

    def test_calls_after_fork_in_the_same_turn(self):
        both = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "cF", "type": "function",
             "function": {"name": "fork", "arguments": '{"task": "write a"}'}},
            {"id": "cL", "type": "function",
             "function": {"name": "list_entities", "arguments": "{}"}}]}
        run = self.run_parent([both,
                               {"role": "assistant", "content": "wrote nothing"},
                               {"role": "assistant", "content": "parent done"}])
        self.assertEqual(len(self.listing.calls), 1, "the later call was not run exactly once")
        names = [(e.call.name, e.lineage) for e in run.ledger.entries]
        self.assertEqual(names, [("fork", ()), ("list_entities", ())])
        fork_first = self.client.seen[1]
        self.assertTrue(any(m.get("tool_call_id") == "cL" and "NOT RUN" in m["content"]
                            for m in fork_first))

    def test_fork_with_bad_arguments_starts_nothing(self):
        run = self.run_parent([tool_turn("fork", '{"oops": 1}', "cF"),
                               {"role": "assistant", "content": "parent done"}])
        entry = run.ledger.entries[0]
        self.assertEqual(entry.call.name, "fork")
        self.assertIsNotNone(entry.error)
        self.assertEqual(len(self.client.seen), 2, "a child run was started")
        self.assertEqual(run.forks, [])

    def test_a_gate_that_does_not_cover_fork_refuses_it(self):
        from overmind.agent import run_agent
        from overmind.fork import ForkConfig
        ex = gate_and_executor({"write_file"}, tools={"write_file": self.write})
        client = ScriptedClient([tool_turn("fork", '{"task": "x"}', "cF"),
                                 {"role": "assistant", "content": "parent done"}])
        run = run_agent(client, ex, TOOLS, "t", fork=ForkConfig())
        self.assertEqual(len(client.seen), 2)
        self.assertFalse(run.ledger.entries[0].decision.allowed)
        self.assertEqual(run.forks, [])

    def test_without_fork_config_no_fork_tool_is_offered(self):
        from overmind.agent import run_agent
        client = ScriptedClient([{"role": "assistant", "content": "done"}])
        run_agent(client, self.ex, TOOLS, "t")
        self.assertEqual(client.seen_tools[0], TOOLS)

    def test_refused_configurations(self):
        from overmind.agent import run_agent
        from overmind.fork import ForkConfig, fork_schema
        with self.assertRaises(ValueError):
            run_agent(ScriptedClient([]), self.ex, TOOLS, "t", fork=ForkConfig(),
                      context_mode="ledger")
        with self.assertRaises(ValueError):
            run_agent(ScriptedClient([]), self.ex, TOOLS + [fork_schema()], "t",
                      fork=ForkConfig())
        ex = gate_and_executor({"fork"}, tools={"fork": Spy()})
        with self.assertRaises(ValueError):
            run_agent(ScriptedClient([]), ex, TOOLS, "t", fork=ForkConfig())

    def test_an_unexpected_exception_in_the_fork_keeps_the_evidence(self):
        """⚠️ Not a provider error: a policy (or fact source, or anything else
        inside the child loop) raising. The write that already ran must still
        reach the parent's ledger - the summary is lost, the evidence is not."""
        from overmind.agent import run_agent
        from overmind.fork import ForkConfig
        from overmind.gate import DenyUnlessDeclared, Gate, GatedExecutor, Ledger

        class Boom:
            name = "boom"

            def applies_to(self, call):
                return call.name == "list_entities"

            def decide(self, call, facts):
                raise RuntimeError("policy bug")

        gate = Gate([Boom(), DenyUnlessDeclared(reversible=frozenset(
            {"write_file", "list_entities", "fork"}))], ledger=Ledger())
        ex = GatedExecutor(gate, {"write_file": self.write, "list_entities": self.listing})
        client = ScriptedClient([tool_turn("fork", '{"task": "w"}', "cF"),
                                 tool_turn("write_file", '{"path": "a"}', "k1"),
                                 tool_turn("list_entities", call_id="k2"),
                                 {"role": "assistant", "content": "parent done"}])
        run = run_agent(client, ex, TOOLS, "t", fork=ForkConfig())
        self.assertEqual(self.write.calls, [{"path": "a"}])
        self.assertIn(("write_file", ("f1",)),
                      [(e.call.name, e.lineage) for e in run.ledger.entries])
        self.assertTrue(run.did("write_file"))
        self.assertEqual(run.forks[0].curated_by, "none")
        self.assertIn("policy bug", run.forks[0].run.error)

    def test_parent_usage_includes_fork_spend(self):
        """`AgentRun.usage` is what the run COST. A fork's calls are the
        parent's spend; `call_usage` stays the parent's own calls (the cost
        model needs that shape)."""
        turns = [
            (tool_turn("fork", '{"task": "write a"}', "cF"), U(1000, 50)),
            (tool_turn("write_file", '{"path": "a"}', "k1"), U(1100, 60)),
            ({"role": "assistant", "content": "wrote a"}, U(1300, 40)),
            ({"role": "assistant", "content": "parent done"}, U(1200, 10)),
        ]
        run = self.run_parent(turns)
        self.assertEqual(run.usage.total_tokens, 1050 + 1160 + 1340 + 1210)
        self.assertTrue(run.usage.reported)
        self.assertEqual([u.total_tokens for u in run.call_usage], [1050, 1210])

    def test_two_forks_get_distinct_ids(self):
        run = self.run_parent([
            tool_turn("fork", '{"task": "one"}', "cF1"),
            {"role": "assistant", "content": "did one"},
            tool_turn("fork", '{"task": "two"}', "cF2"),
            {"role": "assistant", "content": "did two"},
            {"role": "assistant", "content": "parent done"}])
        self.assertEqual([f.fork_id for f in run.forks], ["f1", "f2"])


if __name__ == "__main__":
    unittest.main()
