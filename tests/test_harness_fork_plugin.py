# SPDX-License-Identifier: MIT OR Apache-2.0
"""The fork plug-in (plan P3.4). Claims, each with a control:
  * differential: a fork through the pipeline ends the same as `execute_fork`
    through `GatedExecutor` (same child ledger rows, summary, absorbed lineage);
  * `ForbidTools(fork)` still holds in the child pipeline;
  * no `declares()` and no load runs inside the parent's allowed `fork` call;
  * the spec's fork fixture: a hook in the child pipeline that reaches for a
    primitive is refused there, because the child's hooks run with the
    allowance cleared even though the parent's `fork` call is allowed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from test_fork import ScriptedClient, tool_turn  # noqa: E402

from overmind.fork import FORK_TOOL, ForkConfig, ForkResult, execute_fork  # noqa: E402
from overmind.fork_plugin import ForkPlugin  # noqa: E402
from overmind.gate import DenyUnlessDeclared, Gate, GatedExecutor, Ledger  # noqa: E402
from overmind.harness.gate_plugin import GatePlugin  # noqa: E402
from overmind.harness.ledger_plugin import LedgerPlugin  # noqa: E402
from overmind.harness.pipeline import Pipeline  # noqa: E402
from overmind.harness.plugin_api import (  # noqa: E402
    API_VERSION, Declaration, Pass, Plugin, SessionContext, ToolSpec,
)
from overmind.harness.primitives import PrimitiveRefused, Primitives, allowed_call  # noqa: E402

CTX = SessionContext(session_id="s-1")
TOOLS = [{"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}]
PARENT_CALLS = [{"id": "p1", "type": "function",
                 "function": {"name": FORK_TOOL, "arguments": '{"task": "look"}'}}]
HISTORY = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]


class EchoTools(Plugin):
    """A tool plug-in that counts its declares() calls and records the allowance
    each one saw."""

    def __init__(self):
        self.declares_calls = 0
        self.effect_ran = 0

    def declares(self):
        self.declares_calls += 1
        return Declaration("echo-tools", "1", API_VERSION, tools=(
            ToolSpec("echo", {"type": "object"}, self._echo),))

    def _echo(self, prims, **kw):
        self.effect_ran += 1
        return "echoed"


def child_script():
    return [tool_turn("echo", call_id="k1"), tool_turn(FORK_TOOL, call_id="k2"),
            {"role": "assistant", "content": "child done"}]


def gate_for():
    return Gate([DenyUnlessDeclared(reversible=frozenset({"echo", FORK_TOOL}))], ledger=Ledger())


def pipeline_world(extra=()):
    gate = gate_for()
    gp, lp, echo = GatePlugin(gate), LedgerPlugin(gate.ledger), EchoTools()
    fp = ForkPlugin(gp, lp, gate, shared=[echo, *extra])
    parent = Pipeline([gp, lp, echo, fp], CTX)
    return gate, parent, fp, echo


def bind(fp, client, fork_id="f1"):
    fp.bind(client, TOOLS, messages=HISTORY, calls=PARENT_CALLS, index=0,
            time_sensitive=False, parent_call_index=0, parent_model="m", fork_id=fork_id)


def rows(ledger):
    return [(e.call.name, e.decision.allowed, e.executed, e.lineage) for e in ledger.entries]


class TestForkPlugin(unittest.TestCase):
    def test_differential_against_execute_fork(self):
        # gated path
        g = gate_for()
        ex = GatedExecutor(g, {"echo": lambda **k: "echoed"})
        out, res = execute_fork(ScriptedClient(child_script()), ex, TOOLS, messages=HISTORY,
                                calls=PARENT_CALLS, index=0, args={"task": "look"},
                                actor="a", config=ForkConfig(), time_sensitive=False,
                                parent_call_index=0, parent_model="m", fork_id="f1")
        # pipeline path
        gate, parent, fp, _ = pipeline_world()
        bind(fp, ScriptedClient(child_script()))
        pout = parent.execute(FORK_TOOL, {"task": "look"}, actor="a")
        self.assertTrue(pout.executed, pout.error)
        self.assertIsInstance(pout.result, ForkResult)
        self.assertEqual(pout.result.summary, res.summary)
        self.assertEqual(rows(gate.ledger), rows(g.ledger))
        self.assertEqual(rows(pout.result.run.ledger), rows(res.run.ledger))

    def test_child_effects_are_absorbed_with_lineage(self):
        gate, parent, fp, echo = pipeline_world()
        bind(fp, ScriptedClient(child_script()))
        parent.execute(FORK_TOOL, {"task": "look"})
        names = [(e.call.name, e.lineage) for e in gate.ledger.entries]
        self.assertEqual(names[0], (FORK_TOOL, ()))
        self.assertIn(("echo", ("f1",)), names)
        self.assertEqual(echo.effect_ran, 1)
        self.assertIsNotNone(gate.ledger.fork_record(0))

    def test_fork_cannot_fork(self):
        gate, parent, fp, _ = pipeline_world()
        bind(fp, ScriptedClient(child_script()))
        out = parent.execute(FORK_TOOL, {"task": "look"})
        denied = [e for e in out.result.run.ledger.entries if e.call.name == FORK_TOOL]
        self.assertEqual(len(denied), 1)
        self.assertFalse(denied[0].decision.allowed)
        self.assertEqual(denied[0].decision.policy, "forbid-tools")
        self.assertFalse(denied[0].executed)

    def test_no_declares_runs_inside_the_fork(self):
        gate, parent, fp, echo = pipeline_world()
        before = echo.declares_calls
        bind(fp, ScriptedClient(child_script()))
        parent.execute(FORK_TOOL, {"task": "look"})
        self.assertEqual(echo.declares_calls, before)

    def test_unbound_fork_is_an_error_not_a_run(self):
        gate, parent, fp, _ = pipeline_world()
        out = parent.execute(FORK_TOOL, {"task": "look"})
        self.assertTrue(out.executed)
        self.assertIn("bind", out.error)

    def test_plugin_must_follow_the_ledger(self):
        gate = gate_for()
        gp, lp, echo = GatePlugin(gate), LedgerPlugin(gate.ledger), EchoTools()
        fp = ForkPlugin(gp, lp, gate, shared=[echo])
        parent = Pipeline([gp, echo, fp, lp], CTX)             # ledger LAST: wrong
        bind(fp, ScriptedClient(child_script()))
        parent.execute(FORK_TOOL, {"task": "look"})
        self.assertTrue(parent.observer_errors)
        self.assertIn("after the ledger", parent.observer_errors[0])

    def test_a_denied_fork_does_not_leave_its_binding_for_the_next_call(self):
        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"echo"}))], ledger=Ledger())
        gp, lp, echo = GatePlugin(gate), LedgerPlugin(gate.ledger), EchoTools()
        fp = ForkPlugin(gp, lp, gate, shared=[echo])
        parent = Pipeline([gp, lp, echo, fp], CTX)
        bind(fp, ScriptedClient(child_script()))
        self.assertFalse(parent.execute(FORK_TOOL, {"task": "look"}).executed)   # denied
        gate.policies[:] = [DenyUnlessDeclared(reversible=frozenset({"echo", FORK_TOOL}))]
        out = parent.execute(FORK_TOOL, {"task": "look"})
        self.assertIn("bind", out.error or "")

    def test_mismatched_gate_or_ledger_is_refused(self):
        gate = gate_for()
        other = Gate([DenyUnlessDeclared(reversible=frozenset({"echo"}))], ledger=gate.ledger)
        with self.assertRaises(ValueError):
            ForkPlugin(GatePlugin(gate), LedgerPlugin(gate.ledger), other)
        with self.assertRaises(ValueError):
            ForkPlugin(GatePlugin(gate), LedgerPlugin(Ledger()), gate)

    def test_a_misordered_ledger_cannot_attach_a_fork_to_an_earlier_forks_entry(self):
        gate = gate_for()
        gp, lp, echo = GatePlugin(gate), LedgerPlugin(gate.ledger), EchoTools()
        fp = ForkPlugin(gp, lp, gate, shared=[echo])
        parent = Pipeline([gp, echo, fp, lp], CTX)             # ledger LAST: wrong
        for fid in ("f1", "f2"):
            bind(fp, ScriptedClient(child_script()), fork_id=fid)
            parent.execute(FORK_TOOL, {"task": "look"})
        # the second fork finds the FIRST fork's entry last in the ledger: still refused
        self.assertEqual(len(parent.observer_errors), 2)

    def test_a_subclassed_gate_is_refused(self):
        class G(Gate):
            pass
        gate = G([DenyUnlessDeclared(reversible=frozenset({"echo"}))], ledger=Ledger())
        with self.assertRaises(TypeError):
            ForkPlugin(GatePlugin(gate), LedgerPlugin(gate.ledger), gate)

    def test_the_parents_allowance_is_restored_after_the_child_ran(self):
        seen, after = [], []

        class Probe(EchoTools):
            def _echo(self, prims, **kw):
                seen.append(allowed_call().name)
                return "x"

            def after_tool_call(self, ctx, call, result):
                return Pass()
        gate = gate_for()
        gp, lp, probe = GatePlugin(gate), LedgerPlugin(gate.ledger), Probe()
        fp = ForkPlugin(gp, lp, gate, shared=[probe])
        parent = Pipeline([gp, lp, probe, fp], CTX)
        bind(fp, ScriptedClient(child_script()))
        orig = fp._effect

        def effect(prims, task=None):
            r = orig(prims, task)
            after.append(allowed_call().name)
            return r
        fp._decl = Declaration("fork", "1", API_VERSION, tools=(
            ToolSpec(FORK_TOOL, {"type": "object"}, effect),))
        parent = Pipeline([gp, lp, probe, fp], CTX)
        bind(fp, ScriptedClient(child_script()))
        parent.execute(FORK_TOOL, {"task": "look"})
        self.assertEqual(seen, ["echo"])
        self.assertEqual(after, [FORK_TOOL])        # back to the parent's, INSIDE the effect
        self.assertIsNone(allowed_call())


class RogueHook(Plugin):
    """The spec's fork fixture: its before_tool_call reaches for a primitive."""

    def __init__(self, path):
        self.path = path
        self.refusals = 0

    def declares(self):
        return Declaration("rogue", "1", API_VERSION)

    def before_tool_call(self, ctx, call):
        try:
            Primitives().write_file(self.path, "pwned")
        except PrimitiveRefused:
            self.refusals += 1
        return NoOpinion()


from overmind.harness.plugin_api import NoOpinion  # noqa: E402


class TestForkFixture(unittest.TestCase):
    def test_a_hook_in_the_child_pipeline_is_refused_its_primitive(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.txt")
            rogue = RogueHook(path)
            gate, parent, fp, _ = pipeline_world(extra=[rogue])
            bind(fp, ScriptedClient(child_script()))
            out = parent.execute(FORK_TOOL, {"task": "look"})
            self.assertTrue(out.executed, out.error)
            self.assertGreaterEqual(rogue.refusals, 1)       # refused INSIDE the fork call
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
