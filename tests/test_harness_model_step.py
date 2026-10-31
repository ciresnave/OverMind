# SPDX-License-Identifier: MIT OR Apache-2.0
"""The ninth, observe-only hook `on_model_step` (PM ruling 2026-10-31). Claims, each with a control:
  * `run_agent` on a pipeline executor reports every model call that returned, in
    order, before the tool calls of that turn run;
  * the report carries no content (no text, no arguments);
  * a plug-in that raises changes nothing: the run, its ledger and its other
    observers are the same as without it, and the error is recorded;
  * a model call that FAILED is not a step; a plain `GatedExecutor` run is unchanged.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from test_fork import ScriptedClient, tool_turn  # noqa: E402
from test_harness_fork_plugin import TOOLS, EchoTools, gate_for, rows  # noqa: E402

from overmind.agent import StopReason, run_agent  # noqa: E402
from overmind.gate import GatedExecutor  # noqa: E402
from overmind.harness.gate_plugin import GatePlugin  # noqa: E402
from overmind.harness.ledger_plugin import LedgerPlugin  # noqa: E402
from overmind.harness.pipeline import Pipeline  # noqa: E402
from overmind.harness.pipeline_executor import PipelineExecutor  # noqa: E402
from overmind.harness.plugin_api import (  # noqa: E402
    API_VERSION, Declaration, ModelStep, Plugin, SessionContext,
)

CTX = SessionContext(session_id="s-1")
SCRIPT = [tool_turn("echo", call_id="a"), tool_turn("echo", '{"x": 1}', call_id="b"),
          {"role": "assistant", "content": "all done"}]


class Recorder(Plugin):
    def __init__(self, log, name="rec"):
        self.log = log
        self._decl = Declaration(name, "1", API_VERSION)

    def declares(self):
        return self._decl

    def on_model_step(self, ctx, step):
        self.log.append(step)

    def on_tool_outcome(self, ctx, outcome):
        self.log.append("tool")


class Exploder(Plugin):
    def declares(self):
        return Declaration("exploder", "1", API_VERSION)

    def on_model_step(self, ctx, step):
        raise RuntimeError("reporter down")


def world(*extra):
    gate = gate_for()
    echo = EchoTools()
    parent = Pipeline([GatePlugin(gate), LedgerPlugin(gate.ledger), echo, *extra], CTX)
    return gate, parent


class TestModelStep(unittest.TestCase):
    def test_every_returned_model_call_is_reported_in_order_before_its_tools(self):
        log: list = []
        gate, parent = world(Recorder(log))
        run = run_agent(ScriptedClient(SCRIPT), PipelineExecutor(parent, gate), TOOLS, "t")
        self.assertEqual(run.stop_reason, StopReason.COMPLETED)
        self.assertEqual([s.step for s in log if isinstance(s, ModelStep)], [0, 1, 2])
        self.assertEqual([s.tool_calls for s in log if isinstance(s, ModelStep)], [1, 1, 0])
        self.assertEqual([("tool" if x == "tool" else x.step) for x in log], [0, "tool", 1, "tool", 2])
        first = log[0]
        self.assertEqual((first.model, first.provider, first.truncated), ("scripted", "test", False))

    def test_the_report_carries_no_content(self):
        self.assertEqual({f.name for f in dataclasses.fields(ModelStep)},
                         {"step", "model", "provider", "tool_calls", "truncated"})

    def test_a_plugin_that_raises_changes_nothing(self):
        log: list = []
        gate, parent = world(Exploder(), Recorder(log))
        got = run_agent(ScriptedClient(SCRIPT), PipelineExecutor(parent, gate), TOOLS, "t")
        # control: the same run with no exploder
        gate0, parent0 = world()
        want = run_agent(ScriptedClient(SCRIPT), PipelineExecutor(parent0, gate0), TOOLS, "t")
        self.assertEqual(got.stop_reason, want.stop_reason)
        self.assertEqual(got.final_text, want.final_text)
        self.assertEqual(rows(got.ledger), rows(want.ledger))
        self.assertEqual(got.steps, want.steps)
        self.assertEqual(len([s for s in log if isinstance(s, ModelStep)]), 3)   # the next observer still ran
        errors = parent.observer_errors
        self.assertEqual(len(errors), 3)
        self.assertTrue(all("exploder" in e and "on_model_step" in e for e in errors), errors)

    def test_a_plugin_cannot_alter_what_the_next_one_sees(self):
        seen: list = []

        class Tamper(Plugin):
            def declares(self):
                return Declaration("tamper", "1", API_VERSION)

            def on_model_step(self, ctx, step):
                object.__setattr__(step, "tool_calls", 99)      # frozen, but not tamper-proof

        gate, parent = world(Tamper(), Recorder(seen))
        run_agent(ScriptedClient(SCRIPT), PipelineExecutor(parent, gate), TOOLS, "t")
        self.assertEqual([s.tool_calls for s in seen if isinstance(s, ModelStep)], [1, 1, 0])

    def test_a_failed_model_call_is_not_a_step(self):
        log: list = []
        gate, parent = world(Recorder(log))
        script = [tool_turn("echo", call_id="a"), RuntimeError("provider down")]
        run = run_agent(ScriptedClient(script), PipelineExecutor(parent, gate), TOOLS, "t")
        self.assertEqual(run.stop_reason, StopReason.PROVIDER_ERROR)
        self.assertEqual([s.step for s in log if isinstance(s, ModelStep)], [0])

    def test_a_plain_gated_run_is_unchanged(self):
        g = gate_for()
        run = run_agent(ScriptedClient(SCRIPT), GatedExecutor(g, {"echo": lambda **k: "echoed"}),
                        TOOLS, "t")
        self.assertEqual(run.stop_reason, StopReason.COMPLETED)
        self.assertEqual(run.steps, 3)


if __name__ == "__main__":
    unittest.main()
