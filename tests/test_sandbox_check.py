# SPDX-License-Identifier: MIT OR Apache-2.0
"""P3.7: the sandbox seam. EVERYTHING HERE RUNS AGAINST A FAKE `spawn`: no VM was started
(live Windows Sandbox runs are held, board 321), so none of this measures isolation. It pins
the SEAM: which checks are sent to the sandbox, what the job spec asks for, how the
exit codes are read, and that a check which cannot be sandboxed is refused and never run
on the host."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from test_lanework import RepoCase, Scripted, say  # noqa: E402

from overmind import dispatch_mcp, lanework as lw, sandbox_check as sbx  # noqa: E402
from overmind.check_policy_plugin import CheckPolicyPlugin  # noqa: E402
from overmind.harness.plugin_api import Ok, Refuse, SessionContext  # noqa: E402

CTX = SessionContext("t")
DOCS = list(dispatch_mcp.DOCS_ONLY_CHECK)
PY = ["python", "-m", "unittest", "discover", "-s", "tests"]


class Done:
    def __init__(self, code=0, out=b"", err=b""):
        self.returncode, self.stdout, self.stderr = code, out, err


def fake_execute(providers_out="winsbx\tsatisfies all needs\n", run_code=0, run_err=b"",
                 providers_code=0, log=None):
    def execute(argv, cwd, timeout=120, env=None):
        if log is not None:
            log.append(list(argv))
        if argv[1] == "providers":
            return Done(providers_code, providers_out.encode())
        return Done(run_code, b"", run_err)
    return execute


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="sbx-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home = self.tmp / "py"
        self.home.mkdir()
        self.checkout = self.tmp / "co"
        self.checkout.mkdir()

    def runner(self, **kw):
        return sbx.SandboxRunner("spawn", self.home, fake_execute(**kw))


class TestRefusals(unittest.TestCase):
    def test_control_python_is_accepted(self):
        for argv in (PY, ["python3", "-c", "pass"], [r"C:\Python315\python.exe", "x.py"], ["py", "-3", "t.py"]):
            self.assertIsNone(sbx.refusal_for(argv), argv)

    def test_the_rest_is_refused_with_a_reason(self):
        for argv, word in ((["cargo", "test"], "network"), (["npm", "test"], "network"),
                           (["go", "test"], "network"), (["pnpm", "-r", "test"], "network"),
                           (["make", "check"], "only python"), (["bash", "-c", "x"], "only python"),
                           ([], "non-empty")):
            reason = sbx.refusal_for(argv)
            self.assertIsNotNone(reason, argv)
            self.assertIn(word, reason or "")

    def test_a_refused_argv_has_no_job_spec(self):
        with self.assertRaises(sbx.SandboxUnavailable):
            sbx.job_spec("n", ["cargo", "test"], pathlib.Path("."), pathlib.Path("."), 60)


class TestJobSpec(Base):
    def spec(self, argv=PY):
        return tomllib.loads(sbx.job_spec("n", argv, self.checkout, self.home, 321))

    def test_network_is_off_and_nothing_is_writable_on_the_host(self):
        s = self.spec()
        self.assertEqual(s["needs"]["network"], "off")
        self.assertNotIn("rw", [m["mode"] for m in s["mount"]])
        self.assertEqual(sorted(m["mode"] for m in s["mount"]), ["ro", "ro", "scratch"])
        self.assertEqual(s["limits"]["time_s"], 321)
        self.assertNotIn("output", s)

    def test_the_checkout_and_python_are_the_read_only_mounts(self):
        s = self.spec()
        ro = {pathlib.Path(m["host"]).name: m["guest"] for m in s["mount"] if m["mode"] == "ro"}
        self.assertEqual(ro, {"co": sbx.GUEST_WORK, "py": sbx.GUEST_PYTHON})

    def test_the_command_runs_the_guest_python_with_the_agents_arguments(self):
        s = self.spec(["python", "-m", "unittest", "x"])
        self.assertEqual(s["job"]["cmd"], [sbx.GUEST_PYTHON + "\\python.exe", "-m", "unittest", "x"])
        self.assertEqual(s["job"]["cwd"], sbx.GUEST_WORK)

    def test_unusual_characters_round_trip_through_the_toml(self):
        # an astral character (a surrogate pair under ensure_ascii) and quotes/backslashes
        arg = 'a"b\\c\U0001F600é'
        s = self.spec(["python", "-c", arg])
        self.assertEqual(s["job"]["cmd"][-1], arg)

    def test_a_non_positive_timeout_is_clamped_to_a_valid_limit(self):
        s = tomllib.loads(sbx.job_spec("n", PY, self.checkout, self.home, -5))
        self.assertEqual(s["limits"]["time_s"], 1)

    def test_the_capabilities_are_left_at_the_isolation_default(self):
        # a `require` or `prefer` here could narrow what spawn insists on
        self.assertNotIn("require", self.spec()["needs"])
        self.assertNotIn("prefer", self.spec()["needs"])


class TestAvailable(Base):
    def test_a_certified_launcher_is_enough(self):
        self.runner().available(PY, self.checkout)

    def test_only_launchers_that_lack_something_means_refusal(self):
        r = self.runner(providers_out="naive\tlacks: isolation.no-read-outside\nwinsbx\tlacks: network.off\n")
        with self.assertRaises(sbx.SandboxUnavailable):
            r.available(PY, self.checkout)

    def test_crlf_and_trailing_spaces_in_the_providers_output_are_read(self):
        self.runner(providers_out="winsbx\tsatisfies all needs  \r\n").available(PY, self.checkout)

    def test_empty_providers_output_is_a_refusal(self):
        with self.assertRaises(sbx.SandboxUnavailable):
            self.runner(providers_out="").available(PY, self.checkout)

    def test_a_failing_providers_command_is_a_refusal(self):
        with self.assertRaises(sbx.SandboxUnavailable):
            self.runner(providers_code=2).available(PY, self.checkout)

    def test_a_missing_spawn_is_a_refusal(self):
        def boom(*a, **k):
            raise FileNotFoundError("spawn")
        with self.assertRaises(sbx.SandboxUnavailable):
            sbx.SandboxRunner("spawn", self.home, boom).available(PY, self.checkout)

    def test_availability_starts_no_job(self):
        log = []
        self.runner(log=log).available(PY, self.checkout)
        self.assertEqual([a[1] for a in log], ["providers"])


class TestRunCheck(Base):
    def run_it(self, **kw):
        log = kw.setdefault("log", [])
        return self.runner(**kw).run_check(PY, self.checkout, 30), log

    def test_zero_is_the_only_pass(self):
        (code, tail), _ = self.run_it()
        self.assertEqual(code, 0)
        self.assertIn("no output captured", tail)

    def test_the_windows_sandbox_log_is_appended_when_spawn_collected_it(self):
        def execute(argv, cwd, timeout=120, env=None):
            results = pathlib.Path(argv[argv.index("--results") + 1])
            results.mkdir(parents=True)
            (results / "windows-sandbox.cmd.log").write_text("FAILED test_x")
            return Done(1)
        code, tail = sbx.SandboxRunner("spawn", self.home, execute).run_check(PY, self.checkout, 30)
        self.assertEqual(code, 1)
        self.assertIn("FAILED test_x", tail)

    def test_a_job_failure_keeps_its_code(self):
        (code, tail), _ = self.run_it(run_code=7)
        self.assertEqual(code, 7)

    def test_a_spawn_side_error_is_not_a_pass_and_says_it_did_not_run(self):
        (code, tail), _ = self.run_it(run_code=2, run_err=b"spawn: no launcher is certified for every need")
        self.assertEqual(code, 2)
        self.assertIn("NOT run", tail)

    def test_killed_is_not_a_pass(self):
        (code, _), _ = self.run_it(run_code=137)
        self.assertEqual(code, 137)

    def test_a_job_that_itself_exits_2_is_still_not_a_pass(self):
        (code, _), _ = self.run_it(run_code=2, run_err=b"")
        self.assertEqual(code, 2)

    def test_a_timeout_is_124(self):
        def slow(*a, **k):
            raise subprocess.TimeoutExpired("spawn", 1)
        code, _ = sbx.SandboxRunner("spawn", self.home, slow).run_check(PY, self.checkout, 30)
        self.assertEqual(code, 124)

    def test_a_spawn_that_cannot_start_is_not_a_pass(self):
        def boom(*a, **k):
            raise FileNotFoundError("spawn")
        code, _ = sbx.SandboxRunner("spawn", self.home, boom).run_check(PY, self.checkout, 30)
        self.assertNotEqual(code, 0)

    def test_it_never_names_a_launcher_and_never_asks_for_naive(self):
        _, log = self.run_it()
        flat = [a for argv in log for a in argv]
        self.assertNotIn("--launcher", flat)
        self.assertNotIn("naive", flat)
        self.assertEqual([a[1] for a in log], ["run"])

    def test_the_caller_supplied_runner_is_used(self):
        log = []
        sbx.SandboxRunner("spawn", self.home, fake_execute(run_code=5)).run_check(
            PY, self.checkout, 30, execute=fake_execute(run_code=0, log=log))
        self.assertEqual(len(log), 1)


class TestFromEnv(Base):
    def test_missing_spawn_or_python_is_unavailable(self):
        with self.assertRaises(sbx.SandboxUnavailable):
            sbx.SandboxRunner.from_env({sbx.ENV_PYTHON: str(self.home)})
        with self.assertRaises(sbx.SandboxUnavailable):
            sbx.SandboxRunner.from_env({sbx.ENV_SPAWN: "spawn"})
        with self.assertRaises(sbx.SandboxUnavailable):
            sbx.SandboxRunner.from_env({sbx.ENV_SPAWN: "spawn", sbx.ENV_PYTHON: str(self.tmp / "nope")})

    def test_control_both_set_works(self):
        r = sbx.SandboxRunner.from_env({sbx.ENV_SPAWN: "spawn", sbx.ENV_PYTHON: str(self.home)})
        self.assertEqual(r.spawn, "spawn")


class TestPolicy(Base):
    def plugin(self, check=PY, offer=True, **kw):
        factory = (lambda: self.runner(**kw)) if offer else None
        return CheckPolicyPlugin(self.checkout, check, DOCS, sandbox=factory)

    def test_an_agent_python_check_goes_to_the_sandbox(self):
        p = self.plugin()
        self.assertIsInstance(p.on_session_start(CTX), Ok)
        self.assertIsNotNone(p.check_runner)
        self.assertIn("sandboxed", p.isolation())
        self.assertIn("UNMEASURED", p.isolation())

    def test_no_certified_launcher_refuses_and_nothing_is_sent_to_the_host(self):
        p = self.plugin(providers_out="naive\tlacks: network.off\n")
        r = p.on_session_start(CTX)
        self.assertIsInstance(r, Refuse)
        self.assertIn("does not run on the host", r.reason)
        self.assertIsNone(p.check_runner)

    def test_a_non_python_agent_check_is_refused_with_the_reason(self):
        r = self.plugin(check=["cargo", "test"]).on_session_start(CTX)
        self.assertIsInstance(r, Refuse)
        self.assertIn("network", r.reason)

    def test_without_an_offer_the_sandbox_is_not_used(self):
        p = self.plugin(offer=False)
        self.assertIsInstance(p.on_session_start(CTX), Refuse)
        self.assertIsNone(p.check_runner)

    def test_a_trusted_author_still_runs_on_the_host(self):
        p = CheckPolicyPlugin(self.checkout, ["x"], DOCS, trusted_author=True, sandbox=lambda: self.runner())
        self.assertIsInstance(p.on_session_start(CTX), Ok)
        self.assertIsNone(p.check_runner)

    def test_the_docs_only_check_still_runs_on_the_host(self):
        p = CheckPolicyPlugin(self.checkout, DOCS, DOCS, sandbox=lambda: self.runner())
        self.assertIsInstance(p.on_session_start(CTX), Ok)
        self.assertIsNone(p.check_runner)

    def test_a_registry_check_still_runs_on_the_host(self):
        from overmind import repo_probe
        (self.checkout / "go.mod").write_text("module x\n\ngo 1.22\n")
        argv = repo_probe.infer_check(self.checkout, "go").check
        p = CheckPolicyPlugin(self.checkout, argv or [], DOCS, check_profile="go", sandbox=lambda: self.runner())
        self.assertIsInstance(p.on_session_start(CTX), Ok)
        self.assertIsNone(p.check_runner)
        self.assertIn("external, unsandboxed", p.isolation())


FAKE_SPAWN = r'''
import os, sys
log = os.environ["FAKE_SPAWN_LOG"]
open(log, "a").write(" ".join(sys.argv[1:]) + "\n")
if sys.argv[1] == "providers":
    print(os.environ.get("FAKE_PROVIDERS", "winsbx\tsatisfies all needs"))
    sys.exit(0)
sys.exit(int(os.environ.get("FAKE_RUN_EXIT", "0")))
'''


class TestEndToEnd(RepoCase):
    """`run_task` with a fake `spawn` on disk: the agent-supplied check writes a marker file
    in the worktree WHEN IT RUNS ON THE HOST. The marker is the known-bad control."""

    MARKER = "host_ran.txt"

    @property
    def marker(self):
        # ABSOLUTE and outside the worktree: the harness reads changed files before it runs the check
        return self.tmp / self.MARKER

    @property
    def CHECK(self):
        return [sys.executable, "-c", f"open({str(self.marker)!r}, 'w').write('x')"]

    def setUp(self):
        super().setUp()
        self.log = self.tmp / "spawn.log"
        script = self.tmp / "fake_spawn.py"
        script.write_text(FAKE_SPAWN)
        if os.name == "nt":
            self.spawn = self.tmp / "spawn.cmd"
            self.spawn.write_text(f'@"{sys.executable}" "{script}" %*\r\n')
        else:
            self.spawn = self.tmp / "spawn"
            self.spawn.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
            self.spawn.chmod(0o755)
        self.home = self.tmp / "pyhome"
        self.home.mkdir()
        env = {sbx.ENV_SPAWN: str(self.spawn), sbx.ENV_PYTHON: str(self.home),
               "FAKE_SPAWN_LOG": str(self.log)}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)

    def go(self, **kw):
        kw = {"trusted_author": False, "check_sandbox": True, "check": self.CHECK, **kw}
        return lw.run_task(self.task(**kw), Scripted(say("done")))

    def test_control_the_same_check_on_the_host_writes_the_marker(self):
        r = self.go(check_sandbox=False, trusted_author=True)
        self.assertTrue(self.marker.exists(), r.error)

    def test_the_agent_supplied_check_is_sent_to_spawn_and_not_run_here(self):
        r = self.go()
        self.assertFalse(self.marker.exists())
        self.assertEqual(r.check_exit, 0)
        self.assertTrue(r.check_isolation.startswith("sandboxed"), r.check_isolation)
        calls = self.log.read_text().splitlines()
        self.assertEqual([c.split()[0] for c in calls], ["providers", "run"])
        self.assertTrue(all("--launcher" not in c for c in calls))

    def test_the_models_run_check_tool_is_sandboxed_too(self):
        from test_lanework import call
        r = lw.run_task(self.task(trusted_author=False, check_sandbox=True, check=self.CHECK),
                        Scripted(call(("run_check", {})), say("done")))
        self.assertFalse(self.marker.exists())
        self.assertTrue(r.model_ran_check)
        runs = [c for c in self.log.read_text().splitlines() if c.startswith("run ")]
        self.assertEqual(len(runs), 2)      # the model's call, then the harness's own reading

    def test_a_failing_sandboxed_check_is_a_failing_check(self):
        with mock.patch.dict(os.environ, {"FAKE_RUN_EXIT": "3"}):
            r = self.go()
        self.assertEqual(r.check_exit, 3)
        self.assertNotEqual(r.verdict, "PASS")
        self.assertFalse(self.marker.exists())

    def test_no_certified_launcher_is_an_error_and_nothing_ran_anywhere(self):
        with mock.patch.dict(os.environ, {"FAKE_PROVIDERS": "naive\tlacks: network.off"}):
            r = self.go()
        self.assertEqual(r.verdict, "ERROR")
        self.assertIn("refused to start", r.error or "")
        self.assertEqual(r.steps, 0)
        self.assertEqual([c.split()[0] for c in self.log.read_text().splitlines()], ["providers"])
        self.assertFalse(self.marker.exists())

    def test_without_spawn_configured_it_is_an_error_not_a_host_run(self):
        env = {k: v for k, v in os.environ.items() if k != sbx.ENV_SPAWN}
        with mock.patch.dict(os.environ, env, clear=True):
            r = self.go()
        self.assertEqual(r.verdict, "ERROR")
        self.assertEqual(r.steps, 0)
        self.assertFalse(self.marker.exists())

    def test_a_non_python_check_is_refused_whole(self):
        r = self.go(check=["cargo", "test"])
        self.assertEqual(r.verdict, "ERROR")
        self.assertIn("network", r.error or "")


if __name__ == "__main__":
    unittest.main()
