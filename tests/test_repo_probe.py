# SPDX-License-Identifier: MIT OR Apache-2.0
"""§7's own split, tested: what verifies a task must come from a fixed table
OverMind owns; what tells the model what "good" looks like may come from the
repo's own content - and the two must never trade places.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.repo_probe import RepoProbe, context_text, infer_check  # noqa: E402


class TempRepo:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        return self.root

    def __exit__(self, *exc):
        self._tmp.cleanup()


def write(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class TestInferCheck(unittest.TestCase):
    """⚠️ The check is a subprocess argv. Every case here must be one of the
    fixed table's own entries, never anything built from repo content."""

    def test_no_marker_returns_none(self):
        with TempRepo() as root:
            self.assertIsNone(infer_check(root))

    def test_cargo_toml_selects_cargo_test(self):
        with TempRepo() as root:
            write(root, "Cargo.toml")
            check, name = infer_check(root)
            self.assertEqual(check, ["cargo", "test"])
            self.assertEqual(name, "cargo test")

    def test_pyproject_selects_pytest(self):
        with TempRepo() as root:
            write(root, "pyproject.toml")
            check, _ = infer_check(root)
            self.assertEqual(check, ["python", "-m", "pytest"])

    def test_package_json_selects_npm_test(self):
        with TempRepo() as root:
            write(root, "package.json")
            check, _ = infer_check(root)
            self.assertEqual(check, ["npm", "test"])

    def test_go_mod_selects_go_test(self):
        with TempRepo() as root:
            write(root, "go.mod")
            check, _ = infer_check(root)
            self.assertEqual(check, ["go", "test", "./..."])

    def test_cargo_wins_over_package_json_by_table_order(self):
        """A Rust project with a JS-based doc site still gets `cargo test`."""
        with TempRepo() as root:
            write(root, "Cargo.toml")
            write(root, "package.json")
            check, _ = infer_check(root)
            self.assertEqual(check, ["cargo", "test"])

    def test_a_ci_config_is_never_read_for_this(self):
        """⚠️ The exact failure mode this module exists to prevent: a repo
        whose ONLY signal is its own CI file must not get a check at all,
        let alone one built from that file's content."""
        with TempRepo() as root:
            write(root, ".github/workflows/ci.yml",
                 "jobs:\n  build:\n    steps:\n      - run: rm -rf /\n")
            self.assertIsNone(infer_check(root))


class TestContextText(unittest.TestCase):
    """Informational only - every assertion here is about TEXT, never argv."""

    def test_empty_repo_yields_empty_context(self):
        with TempRepo() as root:
            self.assertEqual(context_text(root), "")

    def test_standards_file_is_quoted(self):
        with TempRepo() as root:
            write(root, ".overmind/STANDARDS.md", "must: no unwrap() in library code")
            text = context_text(root)
            self.assertIn("no unwrap() in library code", text)
            self.assertIn(".overmind/STANDARDS.md", text)

    def test_ci_workflow_is_quoted_labelled_not_executed(self):
        with TempRepo() as root:
            write(root, ".github/workflows/ci.yml", "name: CI\non: [push]\n")
            text = context_text(root)
            self.assertIn("name: CI", text)
            self.assertIn("informational only, not executed", text)

    def test_gitlab_ci_file_is_quoted(self):
        with TempRepo() as root:
            write(root, ".gitlab-ci.yml", "stages:\n  - test\n")
            self.assertIn("stages:", context_text(root))

    def test_total_context_is_capped(self):
        """⚠️ Bound is a FIXED number, not `MAX_HINT_TOTAL_CHARS` - a mutation
        that quietly raises that constant must still fail this test, not pass
        it by definition. Six files, each individually under the PER-FILE cap,
        so only the TOTAL cap can be the thing stopping them all going in."""
        with TempRepo() as root:
            for i in range(6):
                write(root, f".github/workflows/w{i}.yml", "x" * 2100)
            written_total = 6 * 2100
            text = context_text(root)
            self.assertLess(len(text), written_total,
                            "real truncation must have happened")
            self.assertLessEqual(len(text), 8_000,
                                 "sanity bound independent of the module's own constant")

    def test_a_missing_standards_file_is_silently_absent(self):
        with TempRepo() as root:
            write(root, ".github/workflows/ci.yml", "name: CI\n")
            text = context_text(root)
            self.assertNotIn("STANDARDS.md", text)


class TestRepoProbe(unittest.TestCase):
    def test_bundles_both_results(self):
        with TempRepo() as root:
            write(root, "Cargo.toml")
            write(root, ".overmind/STANDARDS.md", "must: keep MSRV at 1.75")
            probe = RepoProbe.run(root)
            self.assertEqual(probe.check, ["cargo", "test"])
            self.assertEqual(probe.check_name, "cargo test")
            self.assertIn("MSRV", probe.context)

    def test_no_marker_still_returns_context(self):
        with TempRepo() as root:
            write(root, ".overmind/STANDARDS.md", "must: something")
            probe = RepoProbe.run(root)
            self.assertIsNone(probe.check)
            self.assertIsNone(probe.check_name)
            self.assertIn("something", probe.context)


if __name__ == "__main__":
    unittest.main()
