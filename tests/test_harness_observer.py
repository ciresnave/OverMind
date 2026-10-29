# SPDX-License-Identifier: MIT OR Apache-2.0
"""The eighth, observe-only hook `on_tool_outcome` (plan P3.4, PM ruling 2026-10-28).

It must see every submitted call (denied ones too), receive a private copy, be
unable to change or veto anything, and never fail silently.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.harness.pipeline import MAX_OBSERVER_ERRORS, Pipeline, PluginError  # noqa: E402
from overmind.harness.plugin_api import (  # noqa: E402
    API_VERSION, Allow, Declaration, Deny, NoOpinion, PipelineOutcome, Plugin, Redact,
    SessionContext, ToolSpec,
)

CTX = SessionContext(session_id="s-1")


class Policy(Plugin):
    def __init__(self, verdict, tools=()):
        self._verdict = verdict
        self._decl = Declaration("policy", "1", API_VERSION, tools=tuple(tools))

    def declares(self):
        return self._decl

    def before_tool_call(self, ctx, call):
        return self._verdict


class Observer(Plugin):
    def __init__(self, name="obs", action=None):
        self._decl = Declaration(name, "1", API_VERSION)
        self.seen: list[PipelineOutcome] = []
        self.action = action

    def declares(self):
        return self._decl

    def on_tool_outcome(self, ctx, outcome):
        self.seen.append(outcome)
        if self.action is not None:
            return self.action(outcome)


def tool(name, effect):
    return ToolSpec(name=name, schema={"type": "object"}, effect=effect)


class TestOutcomeHook(unittest.TestCase):
    def test_denied_call_is_seen(self):
        obs = Observer()
        p = Pipeline([Policy(Deny("no", "p")), obs], CTX)
        out = p.execute("t", {"a": 1})
        self.assertEqual(len(obs.seen), 1)
        self.assertFalse(obs.seen[0].executed)
        self.assertEqual(obs.seen[0].decision, Deny("no", "p"))
        self.assertEqual(obs.seen[0], out)

    def test_uncovered_call_is_seen_as_denied(self):
        obs = Observer()
        Pipeline([obs], CTX).execute("t")
        self.assertFalse(obs.seen[0].executed)
        self.assertIsInstance(obs.seen[0].decision, Deny)

    def test_executed_result_and_error_are_seen(self):
        obs = Observer()

        def boom():
            raise RuntimeError("bad")

        p = Pipeline([Policy(Allow(), [tool("ok", lambda prim: 7), tool("bad", lambda prim: boom())]), obs], CTX)
        p.execute("ok")
        p.execute("bad")
        self.assertEqual([(o.executed, o.result, o.error) for o in obs.seen],
                         [(True, 7, None), (True, None, "RuntimeError: bad")])

    def test_unregistered_tool_is_seen(self):
        obs = Observer()
        Pipeline([Policy(Allow()), obs], CTX).execute("ghost")
        self.assertEqual(obs.seen[0].decision.policy, "unregistered-tool")

    def test_observer_sees_what_the_model_sees(self):
        class Redactor(Plugin):
            def declares(self):
                return Declaration("redactor", "1", API_VERSION)

            def after_tool_call(self, ctx, call, result):
                return Redact("[hidden]")

        obs = Observer()
        Pipeline([Policy(Allow(), [tool("t", lambda prim: "secret")]), Redactor(), obs], CTX).execute("t")
        self.assertEqual(obs.seen[0].result, "[hidden]")

    def test_outcome_is_frozen(self):
        obs = Observer()
        Pipeline([Policy(Deny("n")), obs], CTX).execute("t")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            obs.seen[0].executed = True  # type: ignore[misc]

    def test_observer_gets_a_private_copy(self):
        def scribble(outcome):
            outcome.call.arguments["x"] = "tampered"
            outcome.result.append("tampered")

        first = Observer("first", scribble)
        second = Observer("second")
        p = Pipeline([Policy(Allow(), [tool("t", lambda prim, x: [x])]), first, second], CTX)
        out = p.execute("t", {"x": "orig"})
        self.assertEqual(second.seen[0].call.arguments, {"x": "orig"})
        self.assertEqual(second.seen[0].result, ["orig"])
        self.assertEqual(out.result, ["orig"])
        self.assertEqual(out.call.arguments, {"x": "orig"})

    def test_return_value_cannot_change_or_veto(self):
        for ret in (Deny("veto"), Allow(), Redact("x"), "anything", 1):
            obs = Observer(action=lambda o, ret=ret: ret)
            p = Pipeline([Policy(Allow(), [tool("t", lambda prim: 5)]), obs], CTX)
            out = p.execute("t")
            self.assertTrue(out.executed, ret)
            self.assertEqual(out.result, 5, ret)
            self.assertEqual(p.observer_errors, (), ret)
        denied = Observer(action=lambda o: Allow())
        out = Pipeline([Policy(Deny("n")), denied], CTX).execute("t")
        self.assertIsInstance(out.decision, Deny)
        self.assertFalse(out.executed)

    def test_raising_observer_is_recorded_and_decision_stands(self):
        def raises(o):
            raise ValueError("observer broke")

        later = Observer("later")
        p = Pipeline([Policy(Allow(), [tool("t", lambda prim: 5)]), Observer("bad", raises), later], CTX)
        out = p.execute("t")
        self.assertTrue(out.executed)
        self.assertEqual(out.result, 5)
        self.assertEqual(len(later.seen), 1, "a raising observer stopped the others")
        self.assertEqual(len(p.observer_errors), 1)
        self.assertIn("observer broke", p.observer_errors[0])
        self.assertIn("'bad'", p.observer_errors[0])
        self.assertIn("observer broke", str(p.stop(None)["<observer-errors>"]))

    def test_stop_has_no_error_key_when_none_raised(self):
        p = Pipeline([Policy(Allow()), Observer()], CTX)
        p.execute("t")
        self.assertNotIn("<observer-errors>", p.stop(None))

    def test_uncopyable_result_still_reaches_observers(self):
        import threading
        obs = Observer()
        p = Pipeline([Policy(Allow(), [tool("t", lambda prim: threading.Lock())]), obs], CTX)
        out = p.execute("t")
        self.assertTrue(out.executed)
        self.assertEqual(len(obs.seen), 1)
        self.assertTrue(obs.seen[0].executed)
        self.assertIn("lock", obs.seen[0].result)          # the repr
        self.assertEqual(len(p.observer_errors), 1)

    def test_reserved_plugin_name_is_refused(self):
        class Squatter(Plugin):
            def declares(self):
                return Declaration("<observer-errors>", "1", API_VERSION)

        with self.assertRaises(PluginError):
            Pipeline([Squatter()], CTX)

    def test_observer_errors_are_capped_and_counted(self):
        def raises(o):
            raise ValueError("x")

        p = Pipeline([Policy(Deny("n")), Observer("bad", raises)], CTX)
        for _ in range(MAX_OBSERVER_ERRORS + 5):
            p.execute("t")
        self.assertEqual(len(p.observer_errors), MAX_OBSERVER_ERRORS)
        self.assertEqual(p.observer_errors_dropped, 5)
        self.assertIn("5 more", p.stop(None)["<observer-errors>"][-1])

    def test_base_plugin_default_is_a_noop(self):
        self.assertIsNone(Plugin.on_tool_outcome(Observer(), CTX, None))  # type: ignore[arg-type]
        self.assertEqual(API_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
