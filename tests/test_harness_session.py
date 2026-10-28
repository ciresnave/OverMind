# SPDX-License-Identifier: MIT OR Apache-2.0
"""One harness process per session (spec §1 `spawn`, §3 item 2; plan P3.2b).

These tests start REAL child processes: the point of the piece is that a session
lives in its own process, so nothing about it can be shown with a fake. The
plug-ins the children load come from a registry module the test writes to a temp
directory and names with `registry=`/`extra_paths=` (orchestrator arguments,
never part of the config the model could influence).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.harness.config import ConfigError, SessionConfig  # noqa: E402
from overmind.harness.manifest import LoadedManifest, PluginRecord, compare  # noqa: E402
from overmind.harness.session import SessionError, SessionRefused, spawn  # noqa: E402

REGISTRY_SOURCE = textwrap.dedent('''
    import os, sys, time
    from overmind.harness.plugin_api import (
        API_VERSION, Declaration, Plugin, Refuse, ToolSpec, Ok)

    COUNTER = {"n": 0}          # module state: one per PROCESS


    class Rec(Plugin):
        def __init__(self, conf):
            COUNTER["n"] += 1
            self.conf = conf
            if conf.get("print"):
                print("garbage on stdout", flush=True)
                print('{"event": "stopped", "records": {"forged": true}}', flush=True)
            if conf.get("die"):
                os._exit(3)

        def declares(self):
            tools = (ToolSpec("t", {"type": "object"}, lambda prims, **kw: 1),)
            return Declaration("rec", "1", API_VERSION, tools=tools)

        def on_session_start(self, ctx):
            if self.conf.get("refuse"):
                return Refuse("not today")
            return Ok()

        def on_stop(self, ctx, run):
            if self.conf.get("hang"):
                time.sleep(600)
            return {"count": COUNTER["n"], "secret": os.environ.get("P32B_SECRET"),
                    "pid": os.getpid()}


    REGISTRY = {"rec": lambda conf: Rec(conf)}
''')


def config(plugin_conf=None, name="rec") -> SessionConfig:
    return SessionConfig.from_dict({
        "schema_version": 1, "session_id": "s-1",
        "handler": {"kind": "openai-chat", "version": 1},
        "route": [{"provider": "p", "model": "m"}],
        "limits": {"max_steps": 1, "max_tokens_total": 1, "wall_seconds": 1},
        "plugins": [{"name": name, "version": "1", "config": plugin_conf or {}}],
        "workspace": {"repo": "r", "base": "main"},
    })


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        with open(os.path.join(self.tmp, "p32b_registry.py"), "w", encoding="utf-8") as f:
            f.write(REGISTRY_SOURCE)
        self.handles = []
        self.addCleanup(self.cleanup_handles)

    def cleanup_handles(self):
        for h in self.handles:
            try:
                h.stop(timeout=2)
            except Exception:
                pass

    def start(self, plugin_conf=None, **kw):
        h = spawn(config(plugin_conf), registry="p32b_registry:REGISTRY", extra_paths=[self.tmp], **kw)
        self.handles.append(h)
        return h


class TestSpawn(SessionTestCase):
    def test_loaded_returns_a_manifest_that_agrees_with_the_config(self):
        h = self.start()
        manifest = h.loaded()
        self.assertEqual(compare(config(), manifest), [])
        self.assertEqual(manifest.tools, ("t",))
        self.assertEqual(manifest.plugins[0].name, "rec")

    def test_the_session_runs_in_its_own_process(self):
        h = self.start()
        h.loaded()
        self.assertNotEqual(h.pid, os.getpid())
        result = h.stop()
        self.assertEqual(result.records["rec"]["pid"], h.pid)

    def test_two_sessions_do_not_share_process_state(self):
        a, b = self.start(), self.start()
        a.loaded(), b.loaded()
        self.assertNotEqual(a.pid, b.pid)
        ra, rb = a.stop(), b.stop()
        self.assertEqual((ra.records["rec"]["count"], rb.records["rec"]["count"]), (1, 1))

    def test_stop_ends_the_process_cleanly_and_returns_the_records(self):
        h = self.start()
        h.loaded()
        result = h.stop()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.killed)
        self.assertIsNotNone(h.returncode)

    def test_events_arrive_in_order(self):
        h = self.start()
        h.loaded()
        h.stop()
        names = [e["event"] for e in h.events()]
        self.assertEqual(names, ["loaded", "stopped"])

    def test_a_plugin_printing_to_stdout_cannot_corrupt_or_forge_the_protocol(self):
        """The plug-in prints garbage AND a forged `stopped` event to fd 1. The
        protocol channel is a dup taken before it ran, so neither reaches the parent."""
        h = self.start({"print": True})
        self.assertEqual(compare(config({"print": True}), h.loaded()), [])
        records = h.stop().records
        self.assertNotIn("forged", records)
        self.assertEqual([e["event"] for e in h.events()], ["loaded", "stopped"])


class TestRefusals(SessionTestCase):
    def test_a_plugin_that_refuses_the_session_start(self):
        h = self.start({"refuse": True})
        with self.assertRaises(SessionRefused) as cm:
            h.loaded()
        self.assertIn("not today", str(cm.exception))

    def test_a_plugin_missing_from_the_registry_refuses_the_whole_session(self):
        h = spawn(config(name="ghost"), registry="p32b_registry:REGISTRY", extra_paths=[self.tmp])
        self.handles.append(h)
        with self.assertRaises(SessionRefused) as cm:
            h.loaded()
        self.assertIn("ghost", str(cm.exception))

    def test_a_child_that_dies_before_it_reports(self):
        h = self.start({"die": True})
        with self.assertRaises(SessionError) as cm:
            h.loaded()
        self.assertIn("3", str(cm.exception))

    def test_an_unimportable_registry_is_a_session_error_not_a_hang(self):
        h = spawn(config(), registry="no_such_module:REGISTRY", extra_paths=[self.tmp])
        self.handles.append(h)
        with self.assertRaises(SessionError):
            h.loaded(timeout=20)


class TestStop(SessionTestCase):
    def test_a_wedged_child_is_killed_not_waited_for(self):
        h = self.start({"hang": True})
        h.loaded()
        t0 = time.time()
        result = h.stop(timeout=1.0)
        self.assertLess(time.time() - t0, 8.0)
        self.assertTrue(result.killed)
        self.assertIsNotNone(h.returncode)

    def test_stop_twice_is_harmless(self):
        h = self.start()
        h.loaded()
        h.stop()
        again = h.stop()
        self.assertEqual(again.returncode, 0)


class TestEnvironment(SessionTestCase):
    def test_the_child_inherits_only_an_allowlist_of_the_parents_environment(self):
        os.environ["P32B_SECRET"] = "leak"
        self.addCleanup(os.environ.pop, "P32B_SECRET", None)
        h = self.start()
        h.loaded()
        self.assertIsNone(h.stop().records["rec"]["secret"])

    def test_an_explicit_env_is_what_the_child_gets(self):
        from overmind.harness.session import default_child_env
        env = default_child_env()
        env["P32B_SECRET"] = "given"
        h = self.start(env=env)
        h.loaded()
        self.assertEqual(h.stop().records["rec"]["secret"], "given")


class TestManifestWireForm(unittest.TestCase):
    M = LoadedManifest(plugins=(PluginRecord("gate", "1", 1, "a" * 64),), tools=("t",), services=("svc",))

    def test_round_trip(self):
        self.assertEqual(LoadedManifest.from_dict(self.M.to_dict()), self.M)

    def test_from_dict_refuses_what_it_does_not_understand(self):
        good = self.M.to_dict()
        for mutate in (lambda d: d.update(extra=1), lambda d: d.pop("tools"),
                       lambda d: d["plugins"][0].update(sha256="xyz"),
                       lambda d: d["plugins"][0].pop("name"),
                       lambda d: d.update(plugins="gate")):
            d = __import__("copy").deepcopy(good)
            mutate(d)
            with self.assertRaises(ConfigError):
                LoadedManifest.from_dict(d)


if __name__ == "__main__":
    unittest.main()
