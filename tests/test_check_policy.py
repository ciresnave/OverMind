# SPDX-License-Identifier: MIT OR Apache-2.0
"""P3.5: the `check-policy` plug-in (DESIGN-PROPOSAL 7.5). A task's check runs only if
the host's registry derives it (and the entry declares its effects), it is the docs-only
no-op, or the author is trusted (the CLI's hand-run task.json). Refusal is at
`on_session_start`, before any process: `run_task` returns an ERROR verdict."""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from test_lanework import RepoCase, Scripted, say  # noqa: E402

from overmind import dispatch_mcp, lanework as lw, repo_probe  # noqa: E402
from overmind.check_policy_plugin import CheckPolicyPlugin  # noqa: E402
from overmind.harness.plugin_api import Ok, Refuse, SessionContext  # noqa: E402

CTX = SessionContext("t")
DOCS = list(dispatch_mcp.DOCS_ONLY_CHECK)


def policy(root, check, **kw):
    return CheckPolicyPlugin(pathlib.Path(root), check, DOCS, **kw).on_session_start(CTX)


class TestPolicy(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="cp-"))
        (self.root / "Cargo.toml").write_text('[package]\nname = "x"\n')
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\n')
        self.cargo = repo_probe.infer_check(self.root, "cargo").check
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_control_the_registry_check_exists(self):
        self.assertEqual(self.cargo[:2], ["cargo", "test"])

    def test_registry_check_with_its_profile_is_accepted(self):
        self.assertIsInstance(policy(self.root, self.cargo, check_profile="cargo"), Ok)

    def test_registry_check_by_table_order_is_accepted(self):
        auto = repo_probe.infer_check(self.root).check
        assert auto is not None
        self.assertIsInstance(policy(self.root, auto), Ok)

    def test_caller_supplied_argv_is_refused(self):
        r = policy(self.root, ["python", "-c", "print(1)"], check_profile="cargo")
        self.assertIsInstance(r, Refuse)
        self.assertIn("sandbox", r.reason)

    def test_registry_argv_for_a_different_profile_is_refused(self):
        # the argv is a real registry argv, but not the one THIS profile derives
        self.assertIsInstance(policy(self.root, self.cargo, check_profile="python-pytest"), Refuse)

    def test_registry_argv_for_a_different_repo_is_refused(self):
        other = pathlib.Path(tempfile.mkdtemp(prefix="cp-other-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(other, ignore_errors=True))
        (other / "go.mod").write_text("module x\n")
        self.assertIsInstance(policy(other, self.cargo), Refuse)

    def test_unknown_profile_is_refused(self):
        self.assertIsInstance(policy(self.root, self.cargo, check_profile="nope"), Refuse)

    def test_refused_inference_with_a_nonempty_check_is_refused(self):
        empty = pathlib.Path(tempfile.mkdtemp(prefix="cp-empty-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(empty, ignore_errors=True))
        self.assertIsInstance(policy(empty, self.cargo), Refuse)

    def test_docs_only_check_is_accepted(self):
        self.assertIsInstance(policy(self.root, DOCS), Ok)

    def test_trusted_author_is_accepted(self):
        self.assertIsInstance(policy(self.root, ["anything", "at", "all"], trusted_author=True), Ok)

    def test_an_entry_with_no_declared_effects_is_refused(self):
        with mock.patch.dict(repo_probe.NAMED_CHECK_PROFILE_EFFECTS, clear=True):
            self.assertIsInstance(policy(self.root, self.cargo, check_profile="cargo"), Refuse)


class TestEveryEntryDeclaresItsEffects(unittest.TestCase):
    def test_check_table(self):
        self.assertEqual({m for m, _ in repo_probe.CHECK_TABLE}, set(repo_probe.CHECK_TABLE_EFFECTS))

    def test_named_profiles(self):
        self.assertEqual(set(repo_probe.NAMED_CHECK_PROFILES), set(repo_probe.NAMED_CHECK_PROFILE_EFFECTS))

    def test_values_are_the_two_allowed(self):
        for d in (repo_probe.CHECK_TABLE_EFFECTS, repo_probe.NAMED_CHECK_PROFILE_EFFECTS):
            self.assertTrue(set(d.values()) <= repo_probe.EFFECTS)


class TestTaskProvenance(unittest.TestCase):
    BASE = {"id": "t", "repo": "/r", "goal": "g", "check": ["x"], "writable": []}

    def test_a_task_file_cannot_claim_trusted_author(self):
        with self.assertRaises(ValueError):
            lw.Task.from_json({**self.BASE, "trusted_author": True})

    def test_default_is_untrusted(self):
        self.assertFalse(lw.Task.from_json(self.BASE).trusted_author)

    def test_check_profile_is_type_checked(self):
        with self.assertRaises(ValueError):
            lw.Task.from_json({**self.BASE, "check_profile": 3})

    def test_cli_sets_trusted_author(self):
        seen = []
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="cp-cli-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        f = tmp / "task.json"
        f.write_text(json.dumps({**self.BASE, "provider": "google"}))

        def fake(task, **kw):
            seen.append(task.trusted_author)
            return mock.Mock(verdict="PASS", to_json=lambda: "{}")
        with mock.patch.object(lw, "run_task", fake):
            lw.main([str(f)])
        self.assertEqual(seen, [True])

    def test_dispatch_built_task_is_untrusted_and_carries_its_profile(self):
        req = dispatch_mcp.DispatchRequest(repo="r", prompt="p", check_profile="cargo")
        probe = repo_probe.RepoProbe(check=["cargo", "test"], check_name="cargo test", context="")
        task = dispatch_mcp.build_task("d1", pathlib.Path("/c"), probe, req,
                                       fetch_requirements=lambda u: "")
        self.assertFalse(task.trusted_author)
        self.assertEqual(task.check_profile, "cargo")


class TestIsolationIsRecorded(RepoCase):
    def test_trusted_task_says_unsandboxed(self):
        r = lw.run_task(self.task(), Scripted(say("done")))
        self.assertEqual(r.check_isolation, "trusted author's check, unsandboxed")
        self.assertIn("unsandboxed", json.loads(r.to_json())["check_isolation"])

    def test_registry_check_says_external_unsandboxed(self):
        (self.repo / "go.mod").write_text("module x\n\ngo 1.22\n")
        lw._git(self.repo, "add", "-A")
        lw._git(self.repo, "-c", "user.name=t", "-c", "user.email=t@e.x", "commit", "-q", "-m", "go")
        argv = repo_probe.infer_check(self.repo, "go").check
        r = lw.run_task(self.task(trusted_author=False, check_profile="go", check=argv),
                        Scripted(say("done")))
        self.assertEqual(r.check_isolation, "external, unsandboxed")

    def test_docs_only_says_no_check_ran(self):
        r = lw.run_task(self.task(trusted_author=False, check=DOCS), Scripted(say("done")))
        self.assertEqual(r.check_isolation, "docs-only: no check ran")


class TestRunTaskRefuses(RepoCase):
    def test_a_caller_supplied_check_is_an_error_verdict_and_nothing_runs(self):
        class Counting(Scripted):
            n = 0

            def chat(self, *a, **k):
                Counting.n += 1
                return super().chat(*a, **k)
        client = Counting(say("never reached"))
        r = lw.run_task(self.task(trusted_author=False, check_profile="cargo"), client)
        self.assertEqual(r.verdict, "ERROR")
        self.assertIn("refused to start", r.error or "")
        self.assertEqual(Counting.n, 0)
        self.assertEqual(r.steps, 0)

    def test_the_registry_check_runs(self):
        (self.repo / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
        lw._git(self.repo, "add", "-A")
        lw._git(self.repo, "-c", "user.name=t", "-c", "user.email=t@e.x", "commit", "-q", "-m", "c")
        argv = repo_probe.infer_check(self.repo, "cargo").check
        self.assertIsNotNone(argv)
        r = lw.run_task(self.task(trusted_author=False, check_profile="cargo", check=argv),
                        Scripted(say("done")))
        self.assertNotIn("refused to start", r.error or "")
        self.assertGreater(r.steps, 0)

    def test_the_profile_reaches_the_policy(self):
        # Cargo.toml wins table order, so only the profile selects `go`
        (self.repo / "go.mod").write_text("module x\n\ngo 1.22\n")
        lw._git(self.repo, "add", "-A")
        lw._git(self.repo, "-c", "user.name=t", "-c", "user.email=t@e.x", "commit", "-q", "-m", "go")
        argv = repo_probe.infer_check(self.repo, "go").check
        self.assertIsNotNone(argv)
        self.assertNotEqual(argv, repo_probe.infer_check(self.repo).check)
        r = lw.run_task(self.task(trusted_author=False, check_profile="go", check=argv),
                        Scripted(say("done")))
        self.assertNotIn("refused to start", r.error or "")
        self.assertGreater(r.steps, 0)

    def test_the_trusted_task_runs(self):
        r = lw.run_task(self.task(), Scripted(say("done")))
        self.assertNotIn("refused to start", r.error or "")


if __name__ == "__main__":
    unittest.main()
