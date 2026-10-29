# SPDX-License-Identifier: MIT OR Apache-2.0
"""The workspace tools as a plug-in (plan P3.4).

Three claims, each with a control:
  * the plug-in's tools return what `lanework.Workspace` returns (differential);
  * their I/O goes through the primitives: refused outside an allowed call, and
    a child process with the chokepoint ARMED sees no violation through the
    plug-in, but does see one from the same write done directly (known-bad);
  * the primitives changes this piece needed (parents, non-UTF-8 output).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, SRC)

from overmind.harness.pipeline import Pipeline  # noqa: E402
from overmind.harness.plugin_api import (  # noqa: E402
    API_VERSION, Allow, Declaration, HarnessCall, Plugin, SessionContext, ToolSpec,
)
from overmind.harness.primitives import PrimitiveRefused, Primitives, allowing  # noqa: E402
from overmind.lanework import TOOL_NAMES, Task, Workspace  # noqa: E402
from overmind.workspace_plugin import WorkspacePlugin  # noqa: E402

CTX = SessionContext(session_id="s-1")
CHECK = [sys.executable, "-c", "print('check ran')"]


class AllowAll(Plugin):
    def declares(self):
        return Declaration("allow-all", "1", API_VERSION)

    def before_tool_call(self, ctx, call):
        return Allow()


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def make_repo(root):
    git(root, "init", "-q")
    with open(os.path.join(root, "a.txt"), "wb") as f:
        f.write(b"one\r\ntwo\r\nthree\r\n")                     # CRLF with a final newline
    os.makedirs(os.path.join(root, "src"))
    with open(os.path.join(root, "src", "m.py"), "wb") as f:
        f.write(b"def f():\n    return 1\n")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")


def task():
    return Task(id="t1", repo="r", goal="g", check=CHECK, writable=["**"])


class TestWorkspacePlugin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        make_repo(self.root)
        self.addCleanup(self._tmp.cleanup)

    def pipeline(self):
        return Pipeline([AllowAll(), WorkspacePlugin(self.root, task())], CTX)

    def test_declares_the_six_tools_with_the_lanework_schemas(self):
        decl = WorkspacePlugin(self.root, task()).declares()
        self.assertEqual([t.name for t in decl.tools], list(TOOL_NAMES))
        self.assertEqual(len(TOOL_NAMES), 6)
        self.assertEqual(decl.tools[1].schema["required"], ["path"])        # read_file

    def test_reads_and_check_match_workspace(self):
        direct = Workspace(pathlib.Path(self.root), task())
        p = self.pipeline()
        cases = [
            ("list_files", {"pattern": "*.py"}),
            ("read_file", {"path": "a.txt", "start": 2, "count": 1}),
            ("search", {"pattern": "return"}),
            ("search", {"pattern": "nothing-matches-this"}),
            ("run_check", {}),
        ]
        for name, args in cases:
            out = p.execute(name, args)
            self.assertTrue(out.executed, name)
            self.assertIsNone(out.error, (name, out.error))
            self.assertEqual(out.result, getattr(direct, name)(**args), name)
        self.assertIn("check ran", p.execute("run_check").result)
        self.assertIn("2 tracked files", p.execute("list_files").result)

    def test_writes_keep_line_endings_and_make_parents(self):
        p = self.pipeline()
        out = p.execute("replace_in_file", {"path": "a.txt", "old": "two", "new": "2"})
        self.assertIsNone(out.error, out.error)
        with open(os.path.join(self.root, "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"one\r\n2\r\nthree\r\n")             # CRLF kept, no CR CR LF
        out = p.execute("write_file", {"path": "deep/er/new.txt", "content": "hi\n"})
        self.assertIsNone(out.error, out.error)
        with open(os.path.join(self.root, "deep", "er", "new.txt"), "rb") as f:
            self.assertEqual(f.read(), b"hi\n")

    def test_a_failing_tool_is_an_error_not_a_crash(self):
        out = self.pipeline().execute("replace_in_file", {"path": "a.txt", "old": "absent", "new": "x"})
        self.assertTrue(out.executed)
        self.assertIn("exactly once", out.error)

    def test_without_a_gate_every_call_is_refused(self):
        out = Pipeline([WorkspacePlugin(self.root, task())], CTX).execute("read_file", {"path": "a.txt"})
        self.assertFalse(out.executed)
        self.assertEqual(out.decision.policy, "no-applicable-policy")

    def test_primitives_refuse_outside_an_allowed_call(self):
        ws = Workspace(pathlib.Path(self.root), task(), Primitives())
        with self.assertRaises(PrimitiveRefused):
            ws.write_file("blocked.txt", "x")
        self.assertFalse(os.path.exists(os.path.join(self.root, "blocked.txt")))
        with self.assertRaises(PrimitiveRefused):
            ws.search("one")
        # control: the same calls work inside an allowed call
        with allowing(HarnessCall("write_file", {}, "t")):
            ws.write_file("ok.txt", "x")
            self.assertIn("one", ws.search("one"))
        self.assertTrue(os.path.exists(os.path.join(self.root, "ok.txt")))


class TestPrimitiveAdditions(unittest.TestCase):
    def test_run_process_survives_non_utf8_output(self):
        with allowing(HarnessCall("t", {}, "t")):
            done = Primitives().run_process(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\xffb')"],
                cwd=os.getcwd(), timeout=30, env=dict(os.environ))
        self.assertEqual(done.stdout, "a�b")

    def test_run_process_does_not_translate_newlines(self):
        with allowing(HarnessCall("t", {}, "t")):
            done = Primitives().run_process(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\r\\nb')"],
                cwd=os.getcwd(), timeout=30, env=dict(os.environ))
        self.assertEqual(done.stdout, "a\r\nb")

    def test_write_file_parents_is_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "x", "y.txt")
            with allowing(HarnessCall("t", {}, "t")):
                with self.assertRaises(FileNotFoundError):
                    Primitives().write_file(target, "a")
                Primitives().write_file(target, "a", parents=True)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"a")


CHILD = textwrap.dedent('''
    import os, sys
    sys.path.insert(0, sys.argv[1])
    root, mode = sys.argv[2], sys.argv[3]
    from overmind.harness import chokepoint
    seen = []
    chokepoint.arm(seen.append, lambda: None)           # a stop that returns: the I/O raises instead
    from overmind.harness.pipeline import Pipeline
    from overmind.harness.plugin_api import API_VERSION, Allow, Declaration, Plugin, SessionContext
    from overmind.lanework import Task, Workspace
    from overmind.workspace_plugin import WorkspacePlugin
    import pathlib

    class AllowAll(Plugin):
        def declares(self):
            return Declaration("allow-all", "1", API_VERSION)
        def before_tool_call(self, ctx, call):
            return Allow()

    task = Task(id="t1", repo="r", goal="g", check=[sys.executable, "-c", "print(1)"], writable=["**"])
    if mode == "plugin":
        p = Pipeline([AllowAll(), WorkspacePlugin(root, task)], SessionContext("s"))
        for name, args in [("list_files", {}), ("read_file", {"path": "a.txt"}), ("search", {"pattern": "one"}),
                           ("write_file", {"path": "n/new.txt", "content": "x"}),
                           ("replace_in_file", {"path": "a.txt", "old": "two", "new": "2"}), ("run_check", {})]:
            out = p.execute(name, args)
            assert out.executed and out.error is None, (name, out.error)
    else:
        try:
            Workspace(pathlib.Path(root), task).write_file("direct/new.txt", "x")
        except Exception as exc:
            print("raised", type(exc).__name__)
    print("violations", len(seen), sorted({v["category"] for v in seen}))
''')


class TestChokepointArmed(unittest.TestCase):
    def run_child(self, mode):
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp)
            script = os.path.join(tmp, "..", "child_ws_%d.py" % os.getpid())
            script = os.path.abspath(script)
            with open(script, "w", encoding="utf-8") as f:
                f.write(CHILD)
            try:
                p = subprocess.run([sys.executable, "-B", "-I", script, SRC, tmp, mode],
                                   capture_output=True, text=True, timeout=120)
            finally:
                os.remove(script)
        return p

    def test_plugin_tools_make_no_violation(self):
        p = self.run_child("plugin")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("violations 0 []", p.stdout)

    def test_known_bad_the_same_write_done_directly_is_a_violation(self):
        p = self.run_child("direct")
        self.assertIn("raised ChokepointViolation", p.stdout, p.stderr)
        self.assertIn("file-write", p.stdout)


if __name__ == "__main__":
    unittest.main()
