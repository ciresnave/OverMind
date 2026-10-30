# SPDX-License-Identifier: MIT OR Apache-2.0
"""Factories that declare what they need, and the default registry built on them
(plan P3.4, slice 2).

Two claims, each with a negative control:
  * the loader: the original `factory(config)` still works (a v1 plug-in), a
    factory may declare `requires` and gets exactly those plug-ins, built first;
    a missing need and a cycle are refused, and so is a v1 factory that declares
    a need it cannot receive;
  * the registry: ledger, gate, workspace-tools, fork and mcp build from JSON; an
    unknown policy type or key anywhere is an error, never ignored; the gate
    reads the ledger plug-in's ledger live; a run forks through a pipeline the
    registry built; and a real spawned session loads it.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from test_fork import ScriptedClient, tool_turn  # noqa: E402

from overmind.agent import run_agent  # noqa: E402
from overmind.default_registry import POLICY_TYPES, REGISTRY  # noqa: E402
from overmind.harness import services  # noqa: E402
from overmind.harness.config import SessionConfig  # noqa: E402
from overmind.harness.pipeline import PluginError, load_pipeline  # noqa: E402
from overmind.harness.pipeline_executor import PipelineExecutor  # noqa: E402
from overmind.harness.plugin_api import API_VERSION, BuildContext, Declaration, Plugin  # noqa: E402
from overmind.harness.registry import requires  # noqa: E402
from overmind.harness.session import spawn  # noqa: E402
from overmind.fork import FORK_TOOL  # noqa: E402

CHECK = [sys.executable, "-c", "print('ok')"]


def cfg(*plugins, services=None):
    """plugins: (name, config) pairs."""
    extra = {"services": services} if services is not None else {}
    return SessionConfig.from_dict({**extra,
        "schema_version": 1, "session_id": "s-1",
        "handler": {"kind": "openai-chat", "version": 1},
        "route": [{"provider": "p", "model": "m"}],
        "limits": {"max_steps": 1, "max_tokens_total": 1, "wall_seconds": 1},
        "plugins": [{"name": n, "version": "1", "config": c} for n, c in plugins],
        "workspace": {"repo": "r", "base": "main"}})


class Named(Plugin):
    def __init__(self, name):
        self.name = name
        self.got = None

    def declares(self):
        return Declaration(self.name, "1", API_VERSION)


# --- the loader --------------------------------------------------------------

class TestLoader(unittest.TestCase):
    def test_a_v1_factory_still_works(self):
        seen = []

        def factory(conf):                      # the ORIGINAL form
            seen.append(conf)
            return Named("a")
        p = load_pipeline(cfg(("a", {"k": 1})), {"a": factory})
        self.assertEqual(seen, [{"k": 1}])
        self.assertEqual([d.name for d in p.declarations], ["a"])

    def test_a_factory_gets_exactly_what_it_requires_and_it_is_built_first(self):
        order, got = [], {}

        @requires("a")
        def b(conf, ctx: BuildContext):
            order.append("b"); got["b"] = dict(ctx.built)
            return Named("b")

        def a(conf):
            order.append("a")
            return Named("a")

        def c(conf):
            order.append("c")
            return Named("c")
        p = load_pipeline(cfg(("c", {}), ("b", {}), ("a", {})), {"a": a, "b": b, "c": c})
        self.assertEqual(order, ["c", "a", "b"])                       # dependency order: c is built before b
        self.assertEqual(sorted(got["b"]), ["a"])                      # ...but b sees only what it asked for
        self.assertEqual([d.name for d in p.declarations], ["c", "b", "a"])   # hooks: config order

    def test_a_need_can_depend_on_the_config(self):
        got = {}

        @requires(lambda conf: tuple(conf.get("uses", ())))
        def b(conf, ctx):
            got["built"] = sorted(ctx.built)
            return Named("b")
        reg = {"a": lambda c: Named("a"), "b": b}
        load_pipeline(cfg(("a", {}), ("b", {"uses": ["a"]})), reg)
        self.assertEqual(got["built"], ["a"])

    def test_a_missing_need_is_refused_and_present_it_loads(self):
        @requires("a")
        def b(conf, ctx):
            return Named("b")
        reg = {"a": lambda c: Named("a"), "b": b}
        with self.assertRaisesRegex(PluginError, "requires 'a', which is not in the config"):
            load_pipeline(cfg(("b", {})), reg)
        load_pipeline(cfg(("a", {}), ("b", {})), reg)                  # control

    def test_a_cycle_is_refused_and_a_chain_loads(self):
        @requires("b")
        def a(conf, ctx):
            return Named("a")

        @requires("a")
        def b(conf, ctx):
            return Named("b")
        with self.assertRaisesRegex(PluginError, "cycle: a -> b -> a"):
            load_pipeline(cfg(("a", {}), ("b", {})), {"a": a, "b": b})

        @requires("a")
        def c(conf, ctx):
            return Named("c")
        load_pipeline(cfg(("c", {}), ("a", {})), {"a": lambda k: Named("a"), "c": c})   # control

    def test_a_plugin_requiring_itself_is_a_cycle(self):
        @requires("a")
        def a(conf, ctx):
            return Named("a")
        with self.assertRaisesRegex(PluginError, "cycle"):
            load_pipeline(cfg(("a", {})), {"a": a})

    def test_a_v1_factory_declaring_a_need_is_refused_naming_the_fix(self):
        @requires("a")
        def b(conf):                            # takes no ctx: it could never receive "a"
            return Named("b")
        with self.assertRaisesRegex(PluginError, r"factory\(config, ctx\)"):
            load_pipeline(cfg(("a", {}), ("b", {})), {"a": lambda c: Named("a"), "b": b})

    def test_an_old_factory_with_an_optional_second_argument_never_receives_a_context(self):
        seen = {}

        def with_default(conf, strict=False):
            seen["a"] = strict
            return Named("a")

        class AsClass(Named):
            def __init__(self, conf, opt=None):
                seen["c"] = opt
                super().__init__("c")
        load_pipeline(cfg(("a", {}), ("c", {})), {"a": with_default, "c": AsClass})
        self.assertEqual(seen, {"a": False, "c": None})

    def test_requires_must_be_names(self):
        for bad in ("a", ["a", "a"], [""], [1]):
            @requires(lambda conf, bad=bad: bad)
            def b(conf, ctx):
                return Named("b")
            with self.subTest(bad), self.assertRaisesRegex(PluginError, "requires must be"):
                load_pipeline(cfg(("a", {}), ("b", {})), {"a": lambda c: Named("a"), "b": b})

    def test_a_class_can_be_a_factory(self):
        got = {}

        @requires("a")
        class B(Named):
            def __init__(self, conf, ctx):
                got["built"] = sorted(ctx.built)
                super().__init__("b")
        load_pipeline(cfg(("a", {}), ("b", {})), {"a": lambda c: Named("a"), "b": B})
        self.assertEqual(got["built"], ["a"])

    def test_a_factory_with_a_bad_signature_is_refused(self):
        def f(a, b, c):
            return Named("a")
        with self.assertRaisesRegex(PluginError, "must accept"):
            load_pipeline(cfg(("a", {})), {"a": f})

    def test_the_same_plugin_twice_in_a_config_is_refused(self):
        import dataclasses
        one = cfg(("a", {}))
        twice = dataclasses.replace(one, plugins=one.plugins * 2)        # the config parser refuses this itself
        with self.assertRaisesRegex(PluginError, "twice"):
            load_pipeline(twice, {"a": lambda c: Named("a")})


# --- the default registry ----------------------------------------------------

POLICY = {"type": "deny-unless-declared", "reversible": ["list_files", "fork"]}


def base(*extra, policies=None, services=None):
    return cfg(("ledger", {}), ("gate", {"policies": policies or [POLICY]}), *extra, services=services)


class TestRegistryConfigIsNeverIgnored(unittest.TestCase):
    def load(self, config):
        return load_pipeline(config, REGISTRY)

    def test_ledger_and_gate_load_and_share_one_ledger(self):
        p = self.load(base())
        ledger, gate = p._plugins   # noqa: SLF001
        self.assertIs(gate.gate.ledger, ledger.ledger)
        ledger.ledger.append(_entry("list_files"))
        self.assertEqual(tuple(gate.gate.facts.fact("executed_tools")), ("list_files",))   # LIVE

    def test_unknown_policy_type_is_an_error(self):
        with self.assertRaisesRegex(PluginError, "unknown policy type 'allow-everything'"):
            self.load(base(policies=[{"type": "allow-everything"}]))

    def test_known_policy_types_all_build(self):
        good = {"deny-unless-declared": {"reversible": ["a"]}, "forbid-tools": {"tools": ["a"]},
                "no-self-merge": {}, "require-precondition": {"tool": "a", "requires": "b"},
                "workspace-confined": {"root": os.getcwd(), "writable": ["**"]}}
        self.assertEqual(sorted(good), sorted(POLICY_TYPES))
        for kind, conf in good.items():
            self.load(base(policies=[{"type": kind, **conf}]))

    def test_unknown_key_in_a_policy_is_an_error(self):
        with self.assertRaisesRegex(PluginError, r"unknown keys \['revesible'\]|missing keys"):
            self.load(base(policies=[{"type": "deny-unless-declared", "revesible": ["a"]}]))
        with self.assertRaisesRegex(PluginError, "unknown keys"):
            self.load(base(policies=[{"type": "forbid-tools", "tools": ["a"], "reasn": "x"}]))

    def test_wrong_types_are_errors(self):
        with self.assertRaisesRegex(PluginError, "list of strings"):
            self.load(base(policies=[{"type": "deny-unless-declared", "reversible": "list_files"}]))
        with self.assertRaisesRegex(PluginError, "non-empty list"):
            self.load(cfg(("ledger", {}), ("gate", {"policies": []})))

    def test_unknown_keys_in_every_entry_are_errors(self):
        task = {"id": "t1", "repo": "r", "goal": "g", "check": CHECK, "writable": ["**"]}
        root = os.getcwd()
        cases = {
            "ledger": [("ledger", {"x": 1}), ("gate", {"policies": [POLICY]})],
            "gate": [("ledger", {}), ("gate", {"policies": [POLICY], "x": 1})],
            "fork": [("ledger", {}), ("gate", {"policies": [POLICY]}), ("fork", {"x": 1})],
            "mcp": [("mcp", {"service": "s", "x": 1})],
            "workspace-tools": [("workspace-tools", {"root": root, "task": task, "x": 1})],
        }
        for name, plugins in cases.items():
            with self.subTest(name), self.assertRaisesRegex(PluginError, "unknown keys"):
                self.load(cfg(*plugins))

    def test_workspace_confined_unknown_key_and_relative_roots_are_errors(self):
        with self.assertRaisesRegex(PluginError, "unknown keys"):
            self.load(base(policies=[{"type": "workspace-confined", "root": os.getcwd(),
                                      "writable": ["**"], "writeable": ["**"]}]))
        with self.assertRaisesRegex(PluginError, "absolute path"):
            self.load(base(policies=[{"type": "workspace-confined", "root": "rel", "writable": ["**"]}]))
        task = {"id": "t1", "repo": "r", "goal": "g", "check": CHECK, "writable": ["**"]}
        with self.assertRaisesRegex(PluginError, "absolute path"):
            self.load(cfg(("workspace-tools", {"root": "rel", "task": task})))

    def test_a_policy_that_covers_nothing_is_an_error(self):
        with self.assertRaisesRegex(PluginError, "must not be empty"):
            self.load(base(policies=[{"type": "forbid-tools", "tools": []}]))
        with self.assertRaisesRegex(PluginError, "must not be empty"):
            self.load(base(policies=[{"type": "no-self-merge", "merge_tools": []}]))

    def test_a_bool_is_not_a_step_count(self):
        with self.assertRaisesRegex(PluginError, "positive integer"):
            self.load(base(("fork", {"max_steps": True})))

    def test_the_ledger_must_come_before_the_fork_in_hook_order(self):
        with self.assertRaisesRegex(PluginError, "ledger plug-in must come BEFORE"):
            self.load(cfg(("gate", {"policies": [POLICY]}), ("fork", {}), ("ledger", {})))
        self.load(cfg(("ledger", {}), ("gate", {"policies": [POLICY]}), ("fork", {})))        # control

    def test_fork_needs_the_gate_and_the_ledger(self):
        with self.assertRaisesRegex(PluginError, "requires 'gate', which is not in the config"):
            self.load(cfg(("fork", {})))
        self.load(base(("fork", {})))                                          # control

    def test_fork_shared_must_be_in_the_config(self):
        with self.assertRaisesRegex(PluginError, "requires 'workspace-tools'"):
            self.load(base(("fork", {"shared": ["workspace-tools"]})))

    def test_fork_limits_are_validated(self):
        with self.assertRaisesRegex(PluginError, "positive integer"):
            self.load(base(("fork", {"max_steps": 0})))

    def test_mcp_needs_a_service_the_harness_opened(self):
        conf = cfg(("mcp", {"service": "srv"}), services=[{"name": "srv", "kind": "k", "options": {}}])
        with self.assertRaisesRegex(PluginError, "srv"):
            self.load(conf)

        class Svc:
            def schemas(self):
                return [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}]

            def call(self, tool, arguments):
                return "r"
        services.register_service("srv", Svc())
        self.addCleanup(services.unregister_service, "srv")
        self.assertEqual(self.load(conf).manifest().services, ("srv",))           # control


def _entry(name):
    from overmind.gate import Decision, LedgerEntry, ToolCall
    return LedgerEntry(0, ToolCall(name, {}, "t"), Decision.allow(), executed=True)


class TestForkThroughTheRegistry(unittest.TestCase):
    def test_a_fork_runs_through_a_pipeline_the_registry_built(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*a):
                subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
            git("init", "-q")
            with open(os.path.join(root, "a.txt"), "w") as f:
                f.write("one\n")
            git("add", "-A")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i")
            task = {"id": "t1", "repo": "r", "goal": "g", "check": CHECK, "writable": ["**"]}
            config = cfg(("ledger", {}),
                         ("gate", {"policies": [
                             {"type": "workspace-confined", "root": root, "writable": ["**"]},
                             {"type": "deny-unless-declared", "reversible": ["list_files", "fork"]}]}),
                         ("workspace-tools", {"root": root, "task": task}),
                         ("fork", {"shared": ["workspace-tools"]}))
            p = load_pipeline(config, REGISTRY)
            ledger, gate, _ws, fork = p._plugins   # noqa: SLF001
            script = [tool_turn(FORK_TOOL, '{"task": "look"}', call_id="p1"),
                      tool_turn("list_files", call_id="k1"),
                      {"role": "assistant", "content": "child done"},
                      {"role": "assistant", "content": "all done"}]
            tools = [{"type": "function", "function": {"name": "list_files",
                                                       "parameters": {"type": "object", "properties": {}}}}]
            run = run_agent(ScriptedClient(script), PipelineExecutor(p, gate.gate, fork), tools, "t",
                            fork=fork.config)
            self.assertEqual(run.final_text, "all done")
            rows = [(e.call.name, e.executed, e.lineage) for e in ledger.ledger.entries]
            self.assertEqual(rows, [(FORK_TOOL, True, ()), ("list_files", True, ("f1",))])
            self.assertEqual(len(run.forks), 1)


class TestSpawnedSession(unittest.TestCase):
    def test_a_real_session_loads_the_default_registry_with_a_fork(self):
        config = base(("fork", {}))
        h = spawn(config, registry="overmind.default_registry:REGISTRY")
        try:
            m = h.loaded(timeout=60)
            self.assertEqual([p.name for p in m.plugins], ["ledger", "gate", "fork"])
            self.assertIn("fork", m.tools)
        finally:
            h.stop(timeout=5)

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "the `mcp` package is not installed")
    def test_a_real_session_opens_a_configured_mcp_service_and_loads_its_plugin(self):
        """The child opens the stdio server itself (harness-owned, chokepoint armed),
        BEFORE the `mcp` plug-in is built from its tools; nothing is registered in
        this process."""
        stub = os.path.join(HERE, "stub_mcp_server.py")
        config = base(("mcp", {"service": "stub"}), services=[
            {"name": "stub", "kind": "mcp-stdio", "options": {"command": sys.executable, "args": ["-I", stub]}}])
        h = spawn(config, registry="overmind.default_registry:REGISTRY",
                  openers="overmind.default_registry:OPENERS")
        try:
            m = h.loaded(timeout=120)
            self.assertEqual(m.services, ("stub",))
            self.assertIn("shout", m.tools)
        finally:
            result = h.stop(timeout=30)
        self.assertEqual(result.violations, ())

    def test_the_mcp_stdio_opener_refuses_unknown_and_relative_options(self):
        from overmind.default_registry import OPENERS, RegistryConfigError
        from overmind.harness.config import ServiceRef
        opener = OPENERS["mcp-stdio"]
        for label, options in {"unknown key": {"command": "x", "shell": True},
                               "missing command": {},
                               "relative cwd": {"command": "x", "cwd": "rel"},
                               "env not strings": {"command": "x", "env": {"A": 1}},
                               "args not strings": {"command": "x", "args": [1]}}.items():
            with self.subTest(label), self.assertRaises(RegistryConfigError):
                opener(ServiceRef("s", "mcp-stdio", options))


if __name__ == "__main__":
    unittest.main()
