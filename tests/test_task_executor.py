# SPDX-License-Identifier: MIT OR Apache-2.0
"""`run_task` drives the plug-in pipeline, not a `GatedExecutor` (plan P3.4).

`test_lanework.py` is the differential: its 41 tests were written against the
`GatedExecutor` and pass unchanged on the pipeline. They cannot see WHICH executor
ran, nor the gate policy where the workspace tools repeat it, so these do:
  * `run_task` hands the agent a `PipelineExecutor`;
  * the gate policy itself refuses a write outside `writable` (the tool's own
    check would refuse it too, so the reason names the policy);
  * `DispatchService` works over a `PipelineExecutor` (it takes any executor).
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from test_lanework import RepoCase, Scripted, call, say  # noqa: E402

from overmind import lanework as lw  # noqa: E402
from overmind import task_executor  # noqa: E402
from overmind.gate import GatedExecutor  # noqa: E402
from overmind.harness.pipeline_executor import PipelineExecutor  # noqa: E402


class TestTheTaskRunsOnThePipeline(RepoCase):
    def test_run_task_gives_the_agent_a_pipeline_executor(self):
        seen = []
        real = task_executor.build_task_executor

        def spy(*a, **kw):
            seen.append(real(*a, **kw))
            return seen[-1]
        with mock.patch.object(task_executor, "build_task_executor", spy):
            r = self.run_it(call(("run_check", {})), say("done"))
        self.assertEqual(r.verdict, "NO_CHANGE", r.error)
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], PipelineExecutor)
        self.assertIsInstance(seen[0], GatedExecutor)         # still the shape run_agent drives

    def test_the_gate_policy_refuses_a_write_outside_writable(self):
        task = self.task(writable=["a.txt"])
        ex = task_executor.build_task_executor(self.repo, task, ["a.txt", "Cargo.toml"])
        out = ex.execute("write_file", {"path": "new.txt", "content": "x"})
        self.assertFalse(out.executed)
        self.assertEqual(out.decision.policy, "workspace-confined")
        self.assertFalse((self.repo / "new.txt").exists())
        # control: the same call on a writable path is allowed by the same executor
        ok = ex.execute("replace_in_file", {"path": "a.txt", "old": "broken", "new": "fixed"})
        self.assertTrue(ok.decision.allowed)

    def test_a_tool_the_pipeline_does_not_have_is_refused_not_run(self):
        ex = task_executor.build_task_executor(self.repo, self.task(), ["a.txt"])
        out = ex.execute("delete_everything", {})
        self.assertFalse(out.executed)

    def test_the_ledger_records_every_call_denied_ones_included(self):
        ex = task_executor.build_task_executor(self.repo, self.task(writable=["a.txt"]), ["a.txt"])
        ex.execute("write_file", {"path": "new.txt", "content": "x"})
        ex.execute("list_files", {})
        rows = [(e.call.name, e.executed) for e in ex.gate.ledger.entries]
        self.assertEqual(rows, [("write_file", False), ("list_files", True)])


class TestDispatchOverThePipeline(unittest.TestCase):
    def test_a_dispatch_service_runs_over_a_pipeline_executor(self):
        from overmind.dispatch import Dispatch, DispatchService
        from overmind.harness.pipeline import Pipeline
        from overmind.harness.plugin_api import API_VERSION, Allow, Declaration, Plugin, SessionContext, ToolSpec
        from overmind.gate import Gate, DenyUnlessDeclared, Ledger, StaticFacts

        class Tools(Plugin):
            def declares(self):
                return Declaration("tools", "1", API_VERSION, tools=(
                    ToolSpec("fam_send_message", {"type": "object"}, lambda prims, **kw: "sent"),))

            def before_tool_call(self, ctx, call):
                return Allow()
        pipeline = Pipeline([Tools()], SessionContext(session_id="d"))
        gate = Gate([DenyUnlessDeclared(reversible=frozenset({"fam_send_message"}))],
                    facts=StaticFacts(), ledger=Ledger())
        executor = PipelineExecutor(pipeline, gate)

        class Source:
            def schemas(self):
                return [{"type": "function", "function": {"name": "fam_send_message",
                                                          "parameters": {"type": "object"}}}]
        svc = DispatchService(Source(), executor, Scripted(say("done")), max_steps=2)
        result = svc.handle(Dispatch(from_entity="a", content="hi"), reply=True)
        self.assertEqual(result.run.final_text, "done")
        self.assertTrue(result.replied)
        self.assertIsNone(result.reply_error)


if __name__ == "__main__":
    unittest.main()
