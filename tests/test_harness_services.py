# SPDX-License-Identifier: MIT OR Apache-2.0
"""Services the harness opens (plan P3.4, slice 3). Claims, each with a control:
  * `services` is optional in a config: an old config without it loads and
    round-trips as itself;
  * the child opens the configured services BEFORE the plug-ins are built, closes
    them on every exit path, and refuses a config whose services and plug-ins
    disagree (a declared service not configured; a configured one nobody declares);
  * `call_service` refuses a service the plug-in owning the allowed call did not
    declare.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from overmind.harness import services  # noqa: E402
from overmind.harness.config import ConfigError, ServiceRef, SessionConfig  # noqa: E402
from overmind.harness.pipeline import Pipeline, PluginError, load_pipeline  # noqa: E402
from overmind.harness.plugin_api import (  # noqa: E402
    API_VERSION, Allow, Declaration, Pass, Plugin, SessionContext, ToolSpec,
)
from overmind.harness.session import SessionRefused, spawn  # noqa: E402

CTX = SessionContext(session_id="s-1")


def raw(plugins=(), **extra):
    return {"schema_version": 1, "session_id": "s-1",
            "handler": {"kind": "openai-chat", "version": 1},
            "route": [{"provider": "p", "model": "m"}],
            "limits": {"max_steps": 1, "max_tokens_total": 1, "wall_seconds": 1},
            "plugins": [{"name": n, "version": "1", "config": {}} for n in plugins],
            "workspace": {"repo": "r", "base": "main"}, **extra}


def svc(name="srv", kind="fake", **options):
    return {"name": name, "kind": kind, "options": options}


class TestConfig(unittest.TestCase):
    def test_an_old_config_without_services_still_loads_and_round_trips(self):
        data = raw()
        config = SessionConfig.from_dict(data)
        self.assertEqual(config.services, ())
        self.assertEqual(config.to_dict(), data)          # no `services` key invented
        self.assertEqual(SessionConfig.from_json(config.to_json()), config)

    def test_services_round_trip(self):
        config = SessionConfig.from_dict(raw(services=[svc(command="x", args=["a"])]))
        self.assertEqual(config.services, (ServiceRef("srv", "fake", {"command": "x", "args": ["a"]}),))
        self.assertEqual(SessionConfig.from_json(config.to_json()), config)

    def test_malformed_services_are_refused_whole(self):
        bad = {
            "not a list": {"srv": 1},
            "unknown field": [{**svc(), "extra": 1}],
            "missing options": [{"name": "s", "kind": "k"}],
            "options not an object": [{"name": "s", "kind": "k", "options": []}],
            "blank name": [svc(name=" ")],
            "blank kind": [svc(kind="")],
            "duplicate": [svc(), svc()],
            "non-json options": [svc(x=float("nan"))],
        }
        for label, value in bad.items():
            with self.subTest(label), self.assertRaises(ConfigError):
                SessionConfig.from_dict(raw(services=value))


class Closing:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def call(self, tool, arguments):
        return None

    def close(self):
        self.log.append(("closed", self.name))


class TestOpenServices(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.addCleanup(lambda: services.close_services(["a", "b"]))

    def opener(self, ref):
        if ref.options.get("fail"):
            raise RuntimeError("boom")
        self.log.append(("opened", ref.name))
        return Closing(self.log, ref.name)

    def test_opens_and_registers_in_order_and_closes_in_reverse(self):
        refs = [ServiceRef("a", "k", {}), ServiceRef("b", "k", {})]
        names = services.open_services(refs, {"k": self.opener})
        self.assertEqual(names, ["a", "b"])
        self.assertIsNotNone(services.get_service("a"))
        services.close_services(names)
        self.assertEqual(self.log, [("opened", "a"), ("opened", "b"), ("closed", "b"), ("closed", "a")])
        with self.assertRaises(services.UnknownService):
            services.get_service("a")

    def test_one_failure_opens_none(self):
        refs = [ServiceRef("a", "k", {}), ServiceRef("b", "k", {"fail": True})]
        with self.assertRaisesRegex(services.ServiceOpenError, "boom"):
            services.open_services(refs, {"k": self.opener})
        self.assertIn(("closed", "a"), self.log)
        with self.assertRaises(services.UnknownService):
            services.get_service("a")                    # control: it WAS registered first

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaisesRegex(services.ServiceOpenError, "no opener for kind 'nope'"):
            services.open_services([ServiceRef("a", "nope", {})], {"k": self.opener})

    def test_a_name_already_registered_is_refused_and_not_closed_twice(self):
        services.register_service("a", Closing(self.log, "pre"))
        with self.assertRaisesRegex(services.ServiceOpenError, "already registered"):
            services.open_services([ServiceRef("a", "k", {})], {"k": self.opener})
        self.assertEqual(self.log.count(("closed", "a")), 1)       # the new one, once
        self.assertNotIn(("closed", "pre"), self.log)              # the pre-existing one is untouched


class UsesService(Plugin):
    def __init__(self, service="srv", name="uses"):
        self._service, self._name = service, name

    def declares(self):
        return Declaration(self._name, "1", API_VERSION,
                           tools=(ToolSpec("t", {"type": "object"}, self._effect),),
                           services=(self._service,))

    def _effect(self, prims, **kw):
        return prims.call_service("srv", "t", {})


class AllowAll(Plugin):
    def declares(self):
        return Declaration("allow", "1", API_VERSION)

    def before_tool_call(self, ctx, call):
        return Allow()


class Echo:
    def __init__(self):
        self.calls = 0

    def call(self, tool, arguments):
        self.calls += 1
        return "ok"


class TestLoadChecks(unittest.TestCase):
    def registry(self):
        return {"uses": lambda c: UsesService(), "plain": lambda c: AllowAll()}

    def test_a_declared_service_the_config_does_not_open_is_refused(self):
        config = SessionConfig.from_dict(raw(["uses"]))
        with self.assertRaisesRegex(PluginError, "declares service 'srv', which the config does not open"):
            load_pipeline(config, self.registry())

    def test_a_configured_service_nobody_declares_is_refused(self):
        config = SessionConfig.from_dict(raw(["plain"], services=[svc()]))
        with self.assertRaisesRegex(PluginError, "no plug-in declares it"):
            load_pipeline(config, self.registry())

    def test_control_matching_services_and_plugins_load(self):
        config = SessionConfig.from_dict(raw(["uses"], services=[svc()]))
        self.assertEqual(load_pipeline(config, self.registry()).manifest().services, ("srv",))


class TestCallServiceNeedsTheDeclaration(unittest.TestCase):
    def setUp(self):
        self.echo = Echo()
        services.register_service("srv", self.echo)
        self.addCleanup(services.unregister_service, "srv")

    def test_a_plugin_that_declared_the_service_reaches_it(self):
        out = Pipeline([AllowAll(), UsesService()], CTX).execute("t", {})
        self.assertIsNone(out.error)
        self.assertEqual(out.result, "ok")

    def test_a_plugin_that_did_not_declare_it_is_refused_even_though_it_is_open(self):
        out = Pipeline([AllowAll(), UsesService(service="other")], CTX).execute("t", {})
        self.assertIn("PrimitiveRefused", out.error)
        self.assertIn("not declared", out.error)
        self.assertEqual(self.echo.calls, 0)

    def test_another_plugins_declaration_does_not_cover_it(self):
        class Declarer(Plugin):                       # declares srv; its tool is not the one called
            def declares(self):
                return Declaration("declarer", "1", API_VERSION,
                                   tools=(ToolSpec("d", {"type": "object"}, lambda prims, **kw: 1),),
                                   services=("srv",))
        out = Pipeline([AllowAll(), Declarer(), UsesService(service="other", name="uses2")], CTX).execute("t", {})
        self.assertIn("not declared", out.error)
        self.assertEqual(self.echo.calls, 0)

    def test_a_hook_gets_no_services_even_inside_an_allowed_call(self):
        from overmind.harness.primitives import PrimitiveRefused, Primitives, allowed_services, allowing, cleared
        from overmind.harness.plugin_api import HarnessCall
        with allowing(HarnessCall("t", {}, "a"), frozenset({"srv"})):
            self.assertEqual(Primitives().call_service("srv", "t", {}), "ok")      # control
            with cleared():
                self.assertEqual(allowed_services(), frozenset())
                with self.assertRaises(PrimitiveRefused):
                    Primitives().call_service("srv", "t", {})
            self.assertEqual(allowed_services(), frozenset({"srv"}))               # restored
        self.assertEqual(allowed_services(), frozenset())

    def test_the_services_are_restored_when_the_effect_raises(self):
        from overmind.harness.primitives import allowed_services, allowing
        from overmind.harness.plugin_api import HarnessCall
        with self.assertRaises(ValueError):
            with allowing(HarnessCall("t", {}, "a"), frozenset({"srv"})):
                raise ValueError
        self.assertEqual(allowed_services(), frozenset())

    def test_a_hook_cannot_use_the_declaration_of_a_tool_it_does_not_run(self):
        out = Pipeline([AllowAll(), UsesService()], CTX).execute("t", {})     # control: allowed call works
        self.assertEqual(out.result, "ok")
        from overmind.harness.primitives import PrimitiveRefused, Primitives
        with self.assertRaises(PrimitiveRefused):
            Primitives().call_service("srv", "t", {})                # outside any allowed call


REGISTRY_SOURCE = textwrap.dedent('''
    from overmind.harness.plugin_api import API_VERSION, Declaration, Plugin


    class Needs(Plugin):
        def __init__(self, conf):
            from overmind.harness.services import get_service
            self.svc = get_service("srv")              # the child opened it BEFORE this factory ran

        def declares(self):
            return Declaration("needs", "1", API_VERSION, services=("srv",))


    class Plain(Plugin):
        def __init__(self, conf):
            pass

        def declares(self):
            return Declaration("plain", "1", API_VERSION)


    class Boom(Plugin):
        def __init__(self, conf):
            raise RuntimeError("factory blew up")

        def declares(self):
            return Declaration("boom", "1", API_VERSION)


    REGISTRY = {"needs": Needs, "plain": Plain, "boom": Boom}
''')

OPENERS_SOURCE = textwrap.dedent('''
    class Svc:
        def __init__(self, marker):
            self.marker = marker

        def call(self, tool, arguments):
            return None

        def close(self):
            with open(self.marker, "a", encoding="utf-8") as f:     # a harness-owned write
                f.write("closed\\n")


    def _fake(ref):
        return Svc(ref.options["marker"])


    OPENERS = {"fake": _fake}
''')


class TestRealSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        for name, src in (("p3s_registry.py", REGISTRY_SOURCE), ("p3s_openers.py", OPENERS_SOURCE)):
            with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as f:
                f.write(src)
        self.marker = os.path.join(self.tmp, "closed.txt")
        self.handles = []
        self.addCleanup(self.stop_all)

    def stop_all(self):
        for h in self.handles:
            try:
                h.stop(timeout=2)
            except Exception:
                pass

    def start(self, plugins, services_):
        config = SessionConfig.from_dict(raw(plugins, **({} if services_ is None else {"services": services_})))
        h = spawn(config, registry="p3s_registry:REGISTRY", openers="p3s_openers:OPENERS",
                  extra_paths=[self.tmp])
        self.handles.append(h)
        return h

    def closed(self):
        try:
            with open(self.marker, encoding="utf-8") as f:
                return f.read().count("closed")
        except OSError:
            return 0

    def test_the_child_opens_the_service_before_the_plugin_and_closes_it_at_stop(self):
        h = self.start(["needs"], [svc(marker=self.marker)])
        self.assertEqual(h.loaded().services, ("srv",))
        self.assertEqual(self.closed(), 0)               # open while the session lives
        h.stop()
        self.assertEqual(self.closed(), 1)

    def test_the_service_is_closed_when_the_load_is_refused(self):
        h = self.start(["plain"], [svc(marker=self.marker)])    # configured, nobody declares it
        with self.assertRaises(SessionRefused) as cm:
            h.loaded()
        self.assertIn("no plug-in declares it", str(cm.exception))
        h.stop()
        self.assertEqual(self.closed(), 1)

    def test_the_service_is_closed_when_a_factory_raises(self):
        h = self.start(["boom"], [svc(marker=self.marker)])
        with self.assertRaises(SessionRefused) as cm:
            h.loaded()
        self.assertIn("factory blew up", str(cm.exception))
        h.stop()
        self.assertEqual(self.closed(), 1)

    def test_an_unknown_kind_refuses_the_session(self):
        h = self.start(["needs"], [svc(kind="nope", marker=self.marker)])
        with self.assertRaises(SessionRefused) as cm:
            h.loaded()
        self.assertIn("no opener for kind 'nope'", str(cm.exception))

    def test_a_config_without_services_still_starts(self):
        h = self.start(["plain"], None)
        self.assertEqual(h.loaded().services, ())


if __name__ == "__main__":
    unittest.main()
