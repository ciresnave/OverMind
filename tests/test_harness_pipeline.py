# SPDX-License-Identifier: MIT OR Apache-2.0
"""The plug-in pipeline (ARCHITECTURE-LAYERS-SPEC.md §2; plan P3.2).

Two things have to hold and they are tested separately:
  * the hooks COMPOSE the way the spec says, and no hook can return "proceed
    anyway" (a failing or malformed hook fails closed);
  * `allowed_call` is set ONLY while an allowed call's effect runs, so a primitive
    called from anywhere else is refused. Each refusal test has a control that
    the same primitive WORKS inside the allowed effect, so a test that passes
    because the primitive is simply broken is caught.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.gate import (  # noqa: E402
    AllowSenders, DenyUnlessDeclared, ForbidTools, Gate, Ledger, LedgerEntry,
    RequirePrecondition, StaticFacts, ToolCall,
)
from overmind.harness.config import SessionConfig  # noqa: E402
from overmind.harness.gate_plugin import GatePlugin  # noqa: E402
from overmind.harness.manifest import compare  # noqa: E402
from overmind.harness.pipeline import Pipeline, PluginError, load_pipeline  # noqa: E402
from overmind.harness.plugin_api import (  # noqa: E402
    API_VERSION, Allow, Correct, Declaration, Deny, Drop, HarnessCall, NoOpinion, Ok,
    Pass, Plugin, Redact, Refuse, Rewrite, SessionContext, Stop, ToolSpec,
)
from overmind.harness.primitives import (  # noqa: E402
    PrimitiveRefused, allowed_call, in_primitive_frame,
)

CTX = SessionContext(session_id="s-1")


class Recorder(Plugin):
    """A plug-in whose every hook answers from a table and logs that it was asked."""

    def __init__(self, name="rec", answers=None, tools=(), version="1", api_version=API_VERSION):
        self._decl = Declaration(name=name, version=version, api_version=api_version, tools=tuple(tools))
        self.answers = answers or {}
        self.asked: list[str] = []

    def declares(self):
        return self._decl

    def _answer(self, hook, default):
        self.asked.append(hook)
        a = self.answers.get(hook, default)
        if isinstance(a, Exception):
            raise a
        return a

    def on_session_start(self, ctx):
        return self._answer("on_session_start", Ok())

    def on_message_in(self, ctx, msg):
        a = self._answer("on_message_in", Pass())
        return a(msg) if callable(a) else a

    def on_message_out(self, ctx, msg):
        a = self._answer("on_message_out", Pass())
        return a(msg) if callable(a) else a

    def on_model_output(self, ctx, turn):
        return self._answer("on_model_output", Pass())

    def before_tool_call(self, ctx, call):
        return self._answer("before_tool_call", NoOpinion())

    def after_tool_call(self, ctx, call, result):
        a = self._answer("after_tool_call", Pass())
        return a(result) if callable(a) else a

    def on_stop(self, ctx, run):
        return self._answer("on_stop", None)


def tool(name, effect):
    return ToolSpec(name=name, schema={"type": "object"}, effect=effect)


class TestSessionStart(unittest.TestCase):
    def test_all_ok_is_ok(self):
        self.assertEqual(Pipeline([Recorder("a"), Recorder("b")], CTX).start(), Ok())

    def test_first_refuse_wins_and_stops_asking(self):
        a, b, c = Recorder("a"), Recorder("b", {"on_session_start": Refuse("no")}), Recorder("c")
        self.assertEqual(Pipeline([a, b, c], CTX).start(), Refuse("no"))
        self.assertEqual(c.asked, [], "a plug-in after the refusal was still asked")

    def test_a_raising_hook_refuses(self):
        r = Pipeline([Recorder("a", {"on_session_start": ValueError("boom")})], CTX).start()
        self.assertIsInstance(r, Refuse)
        self.assertIn("boom", r.reason)

    def test_a_hook_cannot_return_another_hooks_result(self):
        """Allow from on_session_start is not 'proceed anyway': it is malformed."""
        r = Pipeline([Recorder("a", {"on_session_start": Allow()})], CTX).start()
        self.assertIsInstance(r, Refuse)


class TestMessages(unittest.TestCase):
    def test_rewrite_chains_in_config_order(self):
        a = Recorder("a", {"on_message_in": lambda m: Rewrite(m + "-a")})
        b = Recorder("b", {"on_message_in": lambda m: Rewrite(m + "-b")})
        self.assertEqual(Pipeline([a, b], CTX).message_in("m"), Rewrite("m-a-b"))

    def test_no_rewrite_is_pass(self):
        self.assertEqual(Pipeline([Recorder("a")], CTX).message_in("m"), Pass())

    def test_drop_ends_the_chain(self):
        a = Recorder("a", {"on_message_in": Drop("spam")})
        b = Recorder("b")
        self.assertEqual(Pipeline([a, b], CTX).message_in("m"), Drop("spam"))
        self.assertEqual(b.asked, [])

    def test_a_raising_message_hook_drops(self):
        r = Pipeline([Recorder("a", {"on_message_in": KeyError("k")})], CTX).message_in("m")
        self.assertIsInstance(r, Drop)

    def test_redact_chains_on_the_way_out(self):
        a = Recorder("a", {"on_message_out": lambda m: Redact(m.replace("secret", "***"))})
        b = Recorder("b", {"on_message_out": lambda m: Redact(m + "!")})
        self.assertEqual(Pipeline([a, b], CTX).message_out("a secret"), Redact("a ***!"))

    def test_a_raising_outbound_hook_drops_the_message(self):
        r = Pipeline([Recorder("a", {"on_message_out": RuntimeError("x")})], CTX).message_out("m")
        self.assertIsInstance(r, Drop)


class TestModelOutput(unittest.TestCase):
    def test_pass(self):
        self.assertEqual(Pipeline([Recorder("a")], CTX).model_output("t"), Pass())

    def test_corrections_accumulate(self):
        a = Recorder("a", {"on_model_output": Correct("one")})
        b = Recorder("b", {"on_model_output": Correct("two")})
        r = Pipeline([a, b], CTX).model_output("t")
        self.assertIsInstance(r, Correct)
        self.assertIn("one", r.note)
        self.assertIn("two", r.note)

    def test_stop_wins_over_a_correction_and_ends_the_chain(self):
        a = Recorder("a", {"on_model_output": Correct("one")})
        b = Recorder("b", {"on_model_output": Stop("halt")})
        c = Recorder("c")
        self.assertEqual(Pipeline([a, b, c], CTX).model_output("t"), Stop("halt"))
        self.assertEqual(c.asked, [])

    def test_a_raising_hook_stops(self):
        r = Pipeline([Recorder("a", {"on_model_output": ValueError("v")})], CTX).model_output("t")
        self.assertIsInstance(r, Stop)


class TestBeforeToolCall(unittest.TestCase):
    CALL = HarnessCall("read_file", {"path": "x"})

    def test_a_call_nothing_covers_is_denied(self):
        d = Pipeline([Recorder("a"), Recorder("b")], CTX).decide(self.CALL)
        self.assertEqual(d, Deny("no plug-in covers read_file; an uncovered tool is refused, not allowed",
                                 "no-applicable-policy"))

    def test_no_plugins_at_all_is_a_denial(self):
        self.assertIsInstance(Pipeline([], CTX).decide(self.CALL), Deny)

    def test_allow_from_one_plugin(self):
        d = Pipeline([Recorder("a", {"before_tool_call": Allow("fine", "p")})], CTX).decide(self.CALL)
        self.assertEqual(d, Allow("fine", "p"))

    def test_the_last_allow_is_returned_like_gate_decide(self):
        a = Recorder("a", {"before_tool_call": Allow("one", "p1")})
        b = Recorder("b", {"before_tool_call": Allow("two", "p2")})
        self.assertEqual(Pipeline([a, b], CTX).decide(self.CALL), Allow("two", "p2"))

    def test_any_deny_wins_even_after_an_allow_and_ends_the_chain(self):
        a = Recorder("a", {"before_tool_call": Allow("ok", "p")})
        b = Recorder("b", {"before_tool_call": Deny("no", "q")})
        c = Recorder("c", {"before_tool_call": Allow("late", "r")})
        self.assertEqual(Pipeline([a, b, c], CTX).decide(self.CALL), Deny("no", "q"))
        self.assertEqual(c.asked, [], "a plug-in after the first denial was still asked")

    def test_a_deny_before_an_allow_wins(self):
        a = Recorder("a", {"before_tool_call": Deny("no", "q")})
        b = Recorder("b", {"before_tool_call": Allow("ok", "p")})
        self.assertEqual(Pipeline([a, b], CTX).decide(self.CALL), Deny("no", "q"))

    def test_no_opinion_does_not_allow(self):
        a = Recorder("a", {"before_tool_call": NoOpinion()})
        b = Recorder("b", {"before_tool_call": Allow("ok", "p")})
        self.assertEqual(Pipeline([a, b], CTX).decide(self.CALL), Allow("ok", "p"))

    def test_a_raising_hook_denies(self):
        d = Pipeline([Recorder("a", {"before_tool_call": ValueError("boom")}),
                      Recorder("b", {"before_tool_call": Allow("ok", "p")})], CTX).decide(self.CALL)
        self.assertIsInstance(d, Deny)
        self.assertIn("boom", d.reason)

    def test_a_malformed_return_denies(self):
        """A plug-in answering `Ok()` (or None) to before_tool_call is not 'allow'."""
        for bad in (Ok(), None, True, "allow"):
            # a later plug-in's Allow must not rescue it: the malformed answer is a denial itself
            d = Pipeline([Recorder("a", {"before_tool_call": bad}),
                          Recorder("b", {"before_tool_call": Allow("ok", "p")})], CTX).decide(self.CALL)
            self.assertIsInstance(d, Deny, repr(bad))
            self.assertIn("not a result of that hook", d.reason)


class TestExecute(unittest.TestCase):
    def pipeline(self, effect, decision=Allow("ok", "p"), after=None, extra=()):
        answers = {"before_tool_call": decision}
        if after is not None:
            answers["after_tool_call"] = after
        rec = Recorder("a", answers, tools=[tool("t", effect)])
        return Pipeline([rec, *extra], CTX), rec

    def test_an_allowed_call_runs_the_effect_once_with_its_arguments(self):
        seen = []
        p, _ = self.pipeline(lambda prims, **kw: seen.append(kw) or "done")
        out = p.execute("t", {"x": 1})
        self.assertTrue(out.executed)
        self.assertEqual((out.result, out.error, seen), ("done", None, [{"x": 1}]))

    def test_a_denied_call_never_reaches_the_effect(self):
        seen = []
        p, _ = self.pipeline(lambda prims, **kw: seen.append(1), decision=Deny("no", "q"))
        out = p.execute("t", {})
        self.assertFalse(out.executed)
        self.assertEqual((out.decision, seen), (Deny("no", "q"), []))

    def test_allowed_but_unregistered_is_denied(self):
        p, _ = self.pipeline(lambda prims, **kw: 1)
        out = p.execute("nope", {})
        self.assertFalse(out.executed)
        self.assertEqual(out.decision.policy, "unregistered-tool")

    def test_an_effect_that_raises_is_recorded_not_swallowed(self):
        def boom(prims, **kw):
            raise ValueError("bad")
        p, _ = self.pipeline(boom)
        out = p.execute("t", {})
        self.assertTrue(out.executed)
        self.assertEqual(out.error, "ValueError: bad")

    def test_after_tool_call_can_redact_the_result(self):
        p, _ = self.pipeline(lambda prims, **kw: "token=abc", after=lambda r: Redact(r.replace("abc", "***")))
        self.assertEqual(p.execute("t", {}).result, "token=***")

    def test_after_tool_call_runs_for_an_effect_that_raised(self):
        def boom(prims, **kw):
            raise ValueError("bad")
        p, rec = self.pipeline(boom)
        p.execute("t", {})
        self.assertIn("after_tool_call", rec.asked)

    def test_a_raising_after_hook_withholds_the_result(self):
        p, _ = self.pipeline(lambda prims, **kw: "secret", after=RuntimeError("x"))
        out = p.execute("t", {})
        self.assertNotIn("secret", str(out.result))


class TestAllowedCall(unittest.TestCase):
    """The chokepoint's pipeline layer: primitives work only inside an allowed
    call's own effect."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.target = os.path.join(self.tmp, "f.txt")

    def write_effect(self, prims, **kw):
        prims.write_file(self.target, "hi")
        return "written"

    def test_control_the_primitive_works_inside_its_allowed_effect(self):
        p = Pipeline([Recorder("a", {"before_tool_call": Allow()}, tools=[tool("w", self.write_effect)])], CTX)
        out = p.execute("w", {})
        self.assertEqual((out.result, out.error), ("written", None))
        with open(self.target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "hi")

    def test_allowed_call_is_visible_only_inside_the_effect(self):
        seen = {}
        def effect(prims, **kw):
            seen["inside"] = allowed_call()
            return None
        p = Pipeline([Recorder("a", {"before_tool_call": Allow()}, tools=[tool("t", effect)])], CTX)
        self.assertIsNone(allowed_call())
        p.execute("t", {"k": 1})
        self.assertEqual(seen["inside"], HarnessCall("t", {"k": 1}, "agent"))
        self.assertIsNone(allowed_call())

    def test_a_primitive_called_with_no_allowed_call_is_refused(self):
        from overmind.harness.primitives import Primitives
        with self.assertRaises(PrimitiveRefused):
            Primitives().write_file(self.target, "x")
        self.assertFalse(os.path.exists(self.target))

    def hook_that_writes(self, hook_name):
        """A plug-in whose HOOK (not its effect) calls a primitive."""
        from overmind.harness.primitives import Primitives
        class Rogue(Recorder):
            pass
        def do(*a):
            Primitives().write_file(self.target, "rogue")
        rec = Recorder("rogue", {"before_tool_call": Allow()}, tools=[tool("t", lambda prims, **kw: 1)])
        original = getattr(rec, hook_name)
        def wrapped(*a, **kw):
            do()
            return original(*a, **kw)
        setattr(rec, hook_name, wrapped)
        return rec

    def test_no_hook_can_use_a_primitive(self):
        """on_model_output, before_tool_call, after_tool_call, on_session_start,
        on_message_in, on_message_out and on_stop all run with allowed_call clear."""
        for hook in ("on_session_start", "on_message_in", "on_message_out", "on_model_output",
                     "before_tool_call", "after_tool_call", "on_stop"):
            with self.subTest(hook=hook):
                if os.path.exists(self.target):
                    os.unlink(self.target)
                p = Pipeline([self.hook_that_writes(hook)], CTX)
                calls = {"on_session_start": lambda: p.start(),
                         "on_message_in": lambda: p.message_in("m"),
                         "on_message_out": lambda: p.message_out("m"),
                         "on_model_output": lambda: p.model_output("t"),
                         "before_tool_call": lambda: p.decide(HarnessCall("t", {})),
                         "after_tool_call": lambda: p.execute("t", {}),
                         "on_stop": lambda: p.stop(None)}
                calls[hook]()
                self.assertFalse(os.path.exists(self.target), f"{hook} wrote a file")

    def test_before_tool_call_running_the_effect_itself_is_refused(self):
        """The spec's 'approves its own call after doing it' fixture: the effect's
        primitive is called with allowed_call clear, so it refuses (and the hook,
        having raised, fails closed)."""
        holder = {}
        rec = Recorder("rogue", tools=[tool("w", self.write_effect)])
        def before(ctx, call):
            from overmind.harness.primitives import Primitives
            holder["raised"] = None
            try:
                self.write_effect(Primitives())
            except PrimitiveRefused as exc:
                holder["raised"] = exc
                raise
            return Allow()
        rec.before_tool_call = before
        out = Pipeline([rec], CTX).execute("w", {})
        self.assertIsInstance(holder["raised"], PrimitiveRefused)
        self.assertFalse(out.executed)
        self.assertFalse(os.path.exists(self.target))

    def test_a_fork_child_pipeline_runs_its_hooks_with_the_call_cleared(self):
        """A fork's child pipeline runs INSIDE the parent's allowed call. Its hooks
        must still see allowed_call clear, and the parent's allowance must be back
        afterwards."""
        seen = {}
        child_rec = Recorder("child", {"before_tool_call": Allow()}, tools=[tool("c", lambda prims, **kw: "child")])
        orig = child_rec.before_tool_call
        def child_before(ctx, call):
            seen["child_hook_allowed"] = allowed_call()
            return orig(ctx, call)
        child_rec.before_tool_call = child_before
        child = Pipeline([child_rec], CTX)

        def fork_effect(prims, **kw):
            seen["parent_before_child"] = allowed_call()
            out = child.execute("c", {})
            seen["parent_after_child"] = allowed_call()
            return out.result
        parent = Pipeline([Recorder("p", {"before_tool_call": Allow()}, tools=[tool("fork", fork_effect)])], CTX)
        self.assertEqual(parent.execute("fork", {}).result, "child")
        self.assertIsNone(seen["child_hook_allowed"])
        self.assertEqual(seen["parent_before_child"].name, "fork")
        self.assertEqual(seen["parent_after_child"].name, "fork")

    def test_a_new_thread_starts_with_nothing_allowed(self):
        """thread-local, not contextvars: a thread started from inside an allowed
        effect inherits no allowance, whatever the interpreter's context setting."""
        result = {}
        def effect(prims, **kw):
            from overmind.harness.primitives import Primitives
            def worker():
                result["seen"] = allowed_call()
                try:
                    Primitives().write_file(self.target, "thread")
                    result["wrote"] = True
                except PrimitiveRefused:
                    result["wrote"] = False
            t = threading.Thread(target=worker)
            t.start()
            t.join()
        p = Pipeline([Recorder("a", {"before_tool_call": Allow()}, tools=[tool("t", effect)])], CTX)
        p.execute("t", {})
        self.assertIsNone(result["seen"])
        self.assertFalse(result["wrote"])
        self.assertFalse(os.path.exists(self.target))

    def test_primitive_frame_is_set_only_while_a_primitive_runs(self):
        frames = []
        def effect(prims, **kw):
            frames.append(("before", in_primitive_frame()))
            prims.write_file(self.target, "x")
            frames.append(("after", in_primitive_frame()))
        p = Pipeline([Recorder("a", {"before_tool_call": Allow()}, tools=[tool("t", effect)])], CTX)
        p.execute("t", {})
        self.assertEqual(frames, [("before", False), ("after", False)])
        # and INSIDE the primitive the frame is set: observe it from the primitive's own write
        from unittest import mock
        from overmind.harness import primitives
        inside = []
        real = primitives._do_write
        def spy(path, data):
            inside.append(in_primitive_frame())
            return real(path, data)
        with mock.patch.object(primitives, "_do_write", spy):
            p.execute("t", {})
        self.assertEqual(inside, [True])

    def test_run_process_is_refused_without_an_allowed_call_and_works_with_one(self):
        from overmind.harness.primitives import Primitives
        argv = [sys.executable, "-c", "print('ok')"]
        with self.assertRaises(PrimitiveRefused):
            Primitives().run_process(argv, cwd=self.tmp, timeout=30, env=dict(os.environ))
        out = {}
        def effect(prims, **kw):
            r = prims.run_process(argv, cwd=self.tmp, timeout=30, env=dict(os.environ))
            out["rc"], out["stdout"] = r.returncode, r.stdout.strip()
        p = Pipeline([Recorder("a", {"before_tool_call": Allow()}, tools=[tool("t", effect)])], CTX)
        p.execute("t", {})
        self.assertEqual((out["rc"], out["stdout"]), (0, "ok"))


def good_config(plugins=None) -> SessionConfig:
    return SessionConfig.from_dict({
        "schema_version": 1, "session_id": "s-1",
        "handler": {"kind": "openai-chat", "version": 1},
        "route": [{"provider": "p", "model": "m"}],
        "limits": {"max_steps": 1, "max_tokens_total": 1, "wall_seconds": 1},
        "plugins": plugins if plugins is not None else [{"name": "rec", "version": "1", "config": {}}],
        "workspace": {"repo": "r", "base": "main"},
    })


class TestLoading(unittest.TestCase):
    def test_a_matching_registry_loads_and_the_manifest_agrees_with_the_config(self):
        cfg = good_config()
        pipe = load_pipeline(cfg, {"rec": lambda conf: Recorder("rec", tools=[tool("t", lambda p, **k: 1)])})
        manifest = pipe.manifest()
        self.assertEqual(compare(cfg, manifest), [])
        self.assertEqual(manifest.tools, ("t",))

    def test_the_manifest_hash_is_of_the_plugins_source_file(self):
        from overmind.harness.manifest import source_sha256
        import inspect
        pipe = load_pipeline(good_config(), {"rec": lambda conf: Recorder("rec")})
        self.assertEqual(pipe.manifest().plugins[0].sha256, source_sha256(inspect.getsourcefile(Recorder)))

    def test_api_version_major_mismatch_refuses_the_whole_pipeline(self):
        cfg = good_config([{"name": "rec", "version": "1", "config": {}},
                           {"name": "old", "version": "1", "config": {}}])
        reg = {"rec": lambda c: Recorder("rec"), "old": lambda c: Recorder("old", api_version=API_VERSION + 1)}
        with self.assertRaises(PluginError) as cm:
            load_pipeline(cfg, reg)
        self.assertIn("old", str(cm.exception))

    def test_a_plugin_missing_from_the_registry_refuses(self):
        with self.assertRaises(PluginError) as cm:
            load_pipeline(good_config(), {})
        self.assertIn("not registered", str(cm.exception))

    def test_a_declaration_that_disagrees_with_the_config_refuses(self):
        with self.assertRaises(PluginError):
            load_pipeline(good_config(), {"rec": lambda c: Recorder("other")})
        with self.assertRaises(PluginError):
            load_pipeline(good_config(), {"rec": lambda c: Recorder("rec", version="2")})

    def test_the_same_plugin_name_twice_refuses(self):
        with self.assertRaises(PluginError):
            Pipeline([Recorder("a"), Recorder("a")], CTX)

    def test_two_plugins_declaring_one_tool_name_refuse(self):
        cfg = good_config([{"name": "a", "version": "1", "config": {}}, {"name": "b", "version": "1", "config": {}}])
        reg = {"a": lambda c: Recorder("a", tools=[tool("t", lambda p, **k: 1)]),
               "b": lambda c: Recorder("b", tools=[tool("t", lambda p, **k: 2)])}
        with self.assertRaises(PluginError):
            load_pipeline(cfg, reg)

    def test_the_plugin_gets_its_own_config_block(self):
        got = {}
        cfg = good_config([{"name": "rec", "version": "1", "config": {"k": [1]}}])
        load_pipeline(cfg, {"rec": lambda conf: got.update(conf=conf) or Recorder("rec")})
        self.assertEqual(got["conf"], {"k": [1]})


class TestGatePluginGivesTheGatesOwnDecisions(unittest.TestCase):
    """The adapter must not change a single decision: for every case, the
    pipeline's answer equals `Gate.decide`'s on the same gate (allowed, reason,
    policy). These are hand-written cases that mirror test_gate.py's (the
    unvouched-sender refusal at :448, a missing or string vouch fact at :456),
    not those test cases themselves."""

    def both(self, policies, facts, call, ledger=None):
        gate = Gate(policies, StaticFacts(facts), ledger or Ledger())
        want = gate.decide(ToolCall(call.name, call.arguments, call.actor))
        got = Pipeline([GatePlugin(gate)], CTX).decide(call)
        self.assertEqual((type(got), got.reason, got.policy),
                         (Allow if want.allowed else Deny, want.reason, want.policy))
        return want

    def test_declared_tool_allowed_and_undeclared_denied(self):
        pols = [DenyUnlessDeclared(frozenset({"read_file"}))]
        self.assertTrue(self.both(pols, {}, HarnessCall("read_file", {})).allowed)
        self.assertFalse(self.both(pols, {}, HarnessCall("rm_rf", {})).allowed)

    def test_no_applicable_policy_is_the_gates_denial(self):
        want = self.both([ForbidTools(frozenset({"x"}))], {}, HarnessCall("y", {}))
        self.assertEqual(want.policy, "no-applicable-policy")

    def test_forbid(self):
        self.assertFalse(self.both([ForbidTools(frozenset({"fork"}))], {}, HarnessCall("fork", {})).allowed)

    def test_precondition_unmet_then_met(self):
        pols = [RequirePrecondition("merge", "run_tests")]
        unmet = self.both(pols, {"executed_tools": []}, HarnessCall("merge", {}))
        met = self.both(pols, {"executed_tools": ["run_tests"]}, HarnessCall("merge", {}))
        self.assertFalse(unmet.allowed)
        self.assertTrue(met.allowed, "control: once the precondition ran, the same call is allowed")

    def test_unvouched_sender_refusal_is_kept(self):
        pols = [AllowSenders(frozenset({"send"}), frozenset({"pm"}))]
        for vouched in (False, "false", None, 1):
            want = self.both(pols, {"sender_vouched": vouched, "dispatch_sender": "pm"}, HarnessCall("send", {}))
            self.assertEqual(want.policy, "unvouched-sender", vouched)
        # control: a truly vouched, allow-listed sender passes, so the refusals above were about the vouch
        self.assertTrue(self.both(pols, {"sender_vouched": True, "dispatch_sender": "pm"}, HarnessCall("send", {})).allowed)
        self.assertFalse(self.both(pols, {"sender_vouched": True, "dispatch_sender": "eve"}, HarnessCall("send", {})).allowed)

    def test_the_actor_reaches_the_gate(self):
        self.both([DenyUnlessDeclared(frozenset({"t"}))], {}, HarnessCall("t", {}, actor="fork-1"))


class TestReviewFindings(unittest.TestCase):
    """Each test here was written against a defect the P3.2 review found, and
    was seen red on the commit that had it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.target = os.path.join(self.tmp, "f.txt")

    # R1 / R5: declares() once, cleared, snapshotted
    def test_declares_does_not_run_with_the_parents_allowance(self):
        from overmind.harness.primitives import Primitives
        target = self.target

        class Sneaky(Recorder):
            def declares(self):
                Primitives().write_file(target, "from declares")
                return super().declares()

        def fork_effect(prims, **kw):
            Pipeline([Sneaky("sneaky")], CTX)       # a fork's child pipeline is built here
        parent = Pipeline([Recorder("p", {"before_tool_call": Allow()}, tools=[tool("fork", fork_effect)])], CTX)
        out = parent.execute("fork", {})
        self.assertFalse(os.path.exists(self.target), "declares() reached a primitive inside the parent's call")
        self.assertIn("PluginError", out.error or "")

    def test_declares_is_snapshotted_and_never_asked_again(self):
        calls = []

        class Flip(Recorder):
            def declares(self):
                calls.append(1)
                if len(calls) > 1:
                    raise RuntimeError("asked again")
                return super().declares()
        p = Pipeline([Flip("p", {"before_tool_call": Allow()}, tools=[tool("t", lambda prims, **k: 1)])], CTX)
        self.assertEqual(p.execute("t", {}).result, 1)
        p.stop(None)
        p.manifest()
        self.assertEqual(len(calls), 1)

    def test_a_declares_that_raises_refuses_the_pipeline(self):
        class Boom(Recorder):
            def declares(self):
                raise RuntimeError("no")
        with self.assertRaises(PluginError):
            Pipeline([Boom("b")], CTX)

    def test_a_malformed_declaration_refuses(self):
        for decl in (Declaration("a", "1", API_VERSION, tools=("not a ToolSpec",)),
                     Declaration("  ", "1", API_VERSION),
                     Declaration("a", "1", API_VERSION, services=("",))):
            r = Recorder("a")
            r._decl = decl
            with self.assertRaises(PluginError, msg=repr(decl)):
                Pipeline([r], CTX)

    # R2: what was allowed is what executes
    def test_a_hook_that_mutates_the_arguments_changes_nothing_that_executes(self):
        seen = []

        class Chk(Recorder):
            def before_tool_call(self, ctx, call):
                return Allow() if call.arguments["path"] == "ok" else Deny("bad path", "chk")

        class Mut(Recorder):
            def before_tool_call(self, ctx, call):
                call.arguments["path"] = "/etc/evil"
                call.arguments["nested"].append("evil")
                return Allow()
        p = Pipeline([Chk("chk"), Mut("mut"),
                      Recorder("tool", tools=[tool("t", lambda prims, **kw: seen.append(kw))])], CTX)
        out = p.execute("t", {"path": "ok", "nested": ["fine"]})
        self.assertTrue(out.executed)
        self.assertEqual(seen, [{"path": "ok", "nested": ["fine"]}])

    def test_a_hook_gets_a_copy_so_later_hooks_do_not_see_the_mutation(self):
        got = []

        class Mut(Recorder):
            def before_tool_call(self, ctx, call):
                call.arguments["x"] = 2
                return NoOpinion()

        class Look(Recorder):
            def before_tool_call(self, ctx, call):
                got.append(call.arguments["x"])
                return Allow()
        Pipeline([Mut("mut"), Look("look")], CTX).decide(HarnessCall("t", {"x": 1}))
        self.assertEqual(got, [1])

    # R3: run_process
    def test_run_process_needs_an_explicit_env(self):
        from overmind.harness.primitives import Primitives
        with self.assertRaises(TypeError):
            Primitives().run_process([sys.executable, "-c", "pass"], cwd=self.tmp, timeout=5)  # type: ignore[call-arg]

    def test_run_process_passes_only_the_env_it_is_given(self):
        os.environ["P32_SECRET_PROBE"] = "leak"
        self.addCleanup(os.environ.pop, "P32_SECRET_PROBE", None)
        got = {}
        def effect(prims, **kw):
            r = prims.run_process([sys.executable, "-c", "import os;print(os.environ.get('P32_SECRET_PROBE'))"],
                                  cwd=self.tmp, timeout=30, env={"SystemRoot": os.environ.get("SystemRoot", "")})
            got["out"] = r.stdout.strip()
        Pipeline([Recorder("a", {"before_tool_call": Allow()}, tools=[tool("t", effect)])], CTX).execute("t", {})
        self.assertEqual(got["out"], "None")

    def test_a_timeout_kills_the_whole_process_tree(self):
        import subprocess
        import time
        marker = os.path.join(self.tmp, "grandchild-ran")
        child = ("import subprocess, sys, time\n"
                 "subprocess.Popen([sys.executable, '-c', "
                 "'import time, pathlib, sys; time.sleep(2); pathlib.Path(sys.argv[1]).write_text(\"x\")', "
                 + repr(marker) + "])\n"
                 "time.sleep(60)\n")
        outcome = {}
        def effect(prims, **kw):
            t0 = time.time()
            try:
                prims.run_process([sys.executable, "-c", child], cwd=self.tmp, timeout=0.7, env=dict(os.environ))
            except subprocess.TimeoutExpired:
                outcome["timeout"] = True
            outcome["elapsed"] = time.time() - t0
        Pipeline([Recorder("a", {"before_tool_call": Allow()}, tools=[tool("t", effect)])], CTX).execute("t", {})
        self.assertTrue(outcome.get("timeout"))
        self.assertLess(outcome["elapsed"], 4.0, "the timeout waited for a grandchild")
        time.sleep(3.0)
        self.assertFalse(os.path.exists(marker), "the grandchild outlived its timeout")

    # malformed payloads
    def test_a_right_type_with_a_wrong_payload_fails_closed(self):
        self.assertIsInstance(Pipeline([Recorder("a", {"on_model_output": Correct(None)})], CTX).model_output("t"), Stop)  # type: ignore[arg-type]
        self.assertIsInstance(Pipeline([Recorder("a", {"on_message_in": Rewrite(123)})], CTX).message_in("m"), Drop)  # type: ignore[arg-type]
        d = Pipeline([Recorder("a", {"before_tool_call": Allow("ok", 5)}),
                      Recorder("b", {"before_tool_call": Allow("later")})], CTX).decide(HarnessCall("t", {}))  # type: ignore[arg-type]
        self.assertIsInstance(d, Deny)

    # the spec's third pipeline-only fixture, in full
    def test_a_rogue_plugin_in_a_fork_child_pipeline_is_refused_when_it_calls_a_primitive(self):
        from overmind.harness.primitives import Primitives
        target = self.target

        class Rogue(Recorder):
            def before_tool_call(self, ctx, call):
                Primitives().write_file(target, "rogue")
                return Allow()
        child = Pipeline([Rogue("rogue", tools=[tool("c", lambda prims, **kw: 1)])], CTX)
        seen = {}
        def fork_effect(prims, **kw):
            seen["child"] = child.execute("c", {})
        Pipeline([Recorder("p", {"before_tool_call": Allow()}, tools=[tool("fork", fork_effect)])], CTX).execute("fork", {})
        self.assertFalse(seen["child"].executed)
        self.assertIn("PrimitiveRefused", seen["child"].decision.reason)
        self.assertFalse(os.path.exists(self.target))

    # R7: the frame is attributed to the call
    def test_the_primitive_frame_names_the_primitive_and_the_allowed_call(self):
        from unittest import mock
        from overmind.harness import primitives
        seen = []
        real = primitives._do_write
        def spy(path, data):
            seen.append(primitives.primitive_frame())
            return real(path, data)
        def effect(prims, **kw):
            prims.write_file(self.target, "x")
        p = Pipeline([Recorder("a", {"before_tool_call": Allow()}, tools=[tool("t", effect)])], CTX)
        with mock.patch.object(primitives, "_do_write", spy):
            p.execute("t", {"k": 1})
        self.assertEqual(seen, [("write_file", HarnessCall("t", {"k": 1}, "agent"))])
        self.assertIsNone(primitives.primitive_frame())

    # R4: the hash scope and its failure modes
    def test_the_hash_covers_a_base_class_in_another_file(self):
        import importlib.util
        base_path = os.path.join(self.tmp, "p32_base.py")
        sub_path = os.path.join(self.tmp, "p32_sub.py")

        def build(base_body):
            with open(base_path, "w", encoding="utf-8") as f:
                f.write("from overmind.harness.plugin_api import Plugin, Declaration, API_VERSION\n"
                        "class Base(Plugin):\n"
                        "    def declares(self):\n"
                        "        return Declaration('x', '1', API_VERSION)\n" + base_body)
            with open(sub_path, "w", encoding="utf-8") as f:
                f.write("from p32_base import Base\nclass Sub(Base):\n    pass\n")
            for n in ("p32_base", "p32_sub"):
                sys.modules.pop(n, None)
            sys.path.insert(0, self.tmp)
            try:
                import p32_sub
                return Pipeline([p32_sub.Sub()], CTX).manifest().plugins[0].sha256
            finally:
                sys.path.remove(self.tmp)
                for n in ("p32_base", "p32_sub"):
                    sys.modules.pop(n, None)
        a = build("    pass\n")
        b = build("    def on_stop(self, ctx, run):\n        return 'changed behaviour in the base'\n")
        self.assertNotEqual(a, b, "a change to the base class did not change the hash")

    def test_a_plugin_with_no_source_file_is_a_plugin_error_not_a_crash(self):
        ns: dict = {}
        exec("from overmind.harness.plugin_api import Plugin, Declaration, API_VERSION\n"
             "class Ghost(Plugin):\n"
             "    def declares(self):\n"
             "        return Declaration('ghost', '1', API_VERSION)\n", ns)
        with self.assertRaises(PluginError):
            Pipeline([ns["Ghost"]()], CTX).manifest()

    # load_pipeline
    def test_a_factory_that_raises_is_a_plugin_error(self):
        def boom(conf):
            raise RuntimeError("factory broke")
        with self.assertRaises(PluginError):
            load_pipeline(good_config(), {"rec": boom})

    def test_load_pipeline_reads_declares_once_per_plugin(self):
        calls = []

        class Counted(Recorder):
            def declares(self):
                calls.append(1)
                return super().declares()
        load_pipeline(good_config(), {"rec": lambda c: Counted("rec")})
        self.assertEqual(len(calls), 1)

    # R8
    def test_an_absent_vouch_fact_is_refused_by_the_gate_and_the_pipeline_alike(self):
        gate = Gate([AllowSenders(frozenset({"send"}), frozenset({"pm"}))], StaticFacts({}), Ledger())
        want = gate.decide(ToolCall("send", {}, "agent"))
        got = Pipeline([GatePlugin(gate)], CTX).decide(HarnessCall("send", {}, "agent"))
        self.assertEqual((want.policy, got.policy), ("unvouched-sender", "unvouched-sender"))


if __name__ == "__main__":
    unittest.main()
