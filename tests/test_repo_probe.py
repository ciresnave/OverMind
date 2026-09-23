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


def write_json(root: Path, rel: str, data) -> None:
    import json
    write(root, rel, json.dumps(data))


class TestInferCheck(unittest.TestCase):
    """⚠️ The check is a subprocess argv. Every case here must be one of the
    fixed table's own entries, never anything built from repo content."""

    def test_no_marker_returns_no_check_and_no_refusal_reason(self):
        with TempRepo() as root:
            result = infer_check(root)
            self.assertIsNone(result.check)
            self.assertIsNone(result.refused_reason)

    def test_cargo_toml_selects_cargo_test(self):
        with TempRepo() as root:
            write(root, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
            result = infer_check(root)
            self.assertEqual(result.check, ["cargo", "test"])
            self.assertEqual(result.check_name, "cargo test")

    def test_pyproject_selects_unittest_when_only_a_tests_dir_exists(self):
        """PM finding, 2026-09-19: a bare `tests/` dir is evidence a Python
        test runner has something to run, not evidence of `pytest`
        specifically - OverMind's own repo has no pytest dependency at all.
        Auto-inference now defaults to stdlib `unittest`."""
        with TempRepo() as root:
            write(root, "pyproject.toml", "[project]\nname = \"x\"\n")
            write(root, "tests/test_x.py")
            result = infer_check(root)
            self.assertEqual(result.check, ["python", "-m", "unittest", "discover", "-s", "tests"])

    def test_pyproject_selects_pytest_via_config_section_with_no_tests_dir(self):
        with TempRepo() as root:
            write(root, "pyproject.toml",
                 "[tool.pytest.ini_options]\ntestpaths = [\"src\"]\n")
            result = infer_check(root)
            self.assertEqual(result.check, ["python", "-m", "pytest"])

    def test_package_json_selects_npm_test_when_root_has_a_test_script(self):
        with TempRepo() as root:
            write_json(root, "package.json", {"scripts": {"test": "jest"}})
            result = infer_check(root)
            self.assertEqual(result.check, ["npm", "test"])

    def test_go_mod_selects_go_test(self):
        with TempRepo() as root:
            write(root, "go.mod", "module example.com/x\n\ngo 1.22\n")
            result = infer_check(root)
            self.assertEqual(result.check, ["go", "test", "./..."])

    def test_makefile_selects_make_test(self):
        with TempRepo() as root:
            write(root, "Makefile", "test:\n\tpytest\n")
            result = infer_check(root)
            self.assertEqual(result.check, ["make", "test"])
            self.assertEqual(result.check_name, "make test")

    def test_makefile_wins_over_pyproject_by_table_order(self):
        """A Python project that still runs its suite through `make test`
        (a Makefile wrapping pytest/tox/whatever) gets `make test`, not
        `pytest` directly - the table is authored most to least likely to
        be the project's own entry point."""
        with TempRepo() as root:
            write(root, "Makefile", "test:\n\tpytest\n")
            write(root, "pyproject.toml", "[project]\nname = \"x\"\n")
            write(root, "tests/test_x.py")
            result = infer_check(root)
            self.assertEqual(result.check, ["make", "test"])

    def test_cargo_wins_over_package_json_by_table_order(self):
        """A Rust project with a JS-based doc site still gets `cargo test`."""
        with TempRepo() as root:
            write(root, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
            write_json(root, "package.json", {"scripts": {"test": "jest"}})
            result = infer_check(root)
            self.assertEqual(result.check, ["cargo", "test"])

    def test_a_ci_config_is_never_read_for_this(self):
        """⚠️ The exact failure mode this module exists to prevent: a repo
        whose ONLY signal is its own CI file must not get a check at all,
        let alone one built from that file's content."""
        with TempRepo() as root:
            write(root, ".github/workflows/ci.yml",
                 "jobs:\n  build:\n    steps:\n      - run: rm -rf /\n")
            result = infer_check(root)
            self.assertIsNone(result.check)

    # -- validation: a marker's bare presence is no longer enough ---------- #
    # PM finding, 2026-09-19, OverMind's first real dispatch job
    # (ThinkersJournal-Community#67): `npm test` was inferred from
    # `package.json`'s bare presence, but the repo is a pnpm workspace whose
    # ROOT package.json has no "test" script at all - the check could never
    # have passed regardless of what the model did.

    def test_package_json_with_no_test_script_and_no_workspace_refuses(self):
        with TempRepo() as root:
            write_json(root, "package.json", {"scripts": {"build": "tsc"}})
            result = infer_check(root)
            self.assertIsNone(result.check)
            self.assertIn("test", result.refused_reason.lower())

    def test_community_shaped_fixture_prefers_pnpm_recursive_test(self):
        """The REAL shape (ThinkersJournal-Community, 2026-09-19): root
        package.json has `workspaces`/`scripts` but no "test" script;
        `pnpm-workspace.yaml` lists `apps/*`/`packages/*`; some workspace
        packages (not all) declare their own "test" script. This is exactly
        the case that produced the real incomplete-verdict run - it must now
        infer `pnpm -r --if-present test`, never `npm test`."""
        with TempRepo() as root:
            write_json(root, "package.json", {
                "name": "thinkersjournal-community",
                "private": True,
                "workspaces": ["apps/*", "packages/*"],
                "scripts": {
                    "typecheck": "pnpm -r run typecheck",
                    "test:e2e": "playwright test",
                },
            })
            write(root, "pnpm-workspace.yaml", 'packages:\n  - "apps/*"\n  - "packages/*"\n')
            write_json(root, "apps/web/package.json", {"scripts": {"build": "astro build"}})
            write_json(root, "apps/api/package.json", {"scripts": {"test": "vitest run"}})
            write_json(root, "packages/shared/package.json", {"scripts": {"test": "vitest run"}})
            result = infer_check(root)
            self.assertEqual(result.check, ["pnpm", "-r", "--if-present", "test"])
            self.assertIsNone(result.refused_reason)

    def test_pnpm_workspace_with_no_package_having_a_test_script_refuses(self):
        with TempRepo() as root:
            write_json(root, "package.json", {"workspaces": ["apps/*"]})
            write(root, "pnpm-workspace.yaml", 'packages:\n  - "apps/*"\n')
            write_json(root, "apps/web/package.json", {"scripts": {"build": "astro build"}})
            result = infer_check(root)
            self.assertIsNone(result.check)
            self.assertIn("test", result.refused_reason.lower())

    def test_malformed_package_json_refuses_with_a_reason(self):
        with TempRepo() as root:
            write(root, "package.json", "{not json")
            result = infer_check(root)
            self.assertIsNone(result.check)
            self.assertIsNotNone(result.refused_reason)

    def test_cargo_toml_with_no_package_or_workspace_table_refuses(self):
        with TempRepo() as root:
            write(root, "Cargo.toml", 'version = "0.1.0"\n')
            result = infer_check(root)
            self.assertIsNone(result.check)
            self.assertIsNotNone(result.refused_reason)

    def test_pyproject_with_neither_tests_dir_nor_pytest_config_refuses(self):
        with TempRepo() as root:
            write(root, "pyproject.toml", "[project]\nname = \"x\"\n")
            result = infer_check(root)
            self.assertIsNone(result.check)
            self.assertIsNotNone(result.refused_reason)

    def test_setup_py_with_no_tests_dir_refuses(self):
        with TempRepo() as root:
            write(root, "setup.py", "from setuptools import setup\nsetup()\n")
            result = infer_check(root)
            self.assertIsNone(result.check)
            self.assertIsNotNone(result.refused_reason)

    def test_setup_py_with_a_tests_dir_selects_unittest(self):
        with TempRepo() as root:
            write(root, "setup.py", "from setuptools import setup\nsetup()\n")
            write(root, "tests/test_x.py")
            result = infer_check(root)
            self.assertEqual(result.check, ["python", "-m", "unittest", "discover", "-s", "tests"])


class TestCheckProfile(unittest.TestCase):
    """`check_profile` bypasses `CHECK_TABLE`'s table-order tie-break, but
    keeps every safety property: the argv still comes only from this
    module's own table, and the chosen profile's own marker must still be
    present and still validates."""

    def test_python_unittest_profile_wins_over_cargo_table_order(self):
        """The exact blind spot this exists to fix: OverMind's own repo root
        has both Cargo.toml and pyproject.toml/tests/, so auto-inference
        always picks cargo - a caller who knows the dispatch is Python-only
        can override that."""
        with TempRepo() as root:
            write(root, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
            write(root, "tests/test_x.py")
            auto = infer_check(root)
            self.assertEqual(auto.check, ["cargo", "test"])
            overridden = infer_check(root, check_profile="python-unittest")
            self.assertEqual(overridden.check,
                             ["python", "-m", "unittest", "discover", "-s", "tests"])

    def test_python_pytest_profile_selects_pytest_even_with_only_a_tests_dir(self):
        with TempRepo() as root:
            write(root, "pyproject.toml", "[project]\nname = \"x\"\n")
            write(root, "tests/test_x.py")
            result = infer_check(root, check_profile="python-pytest")
            self.assertEqual(result.check, ["python", "-m", "pytest"])

    def test_cargo_profile_still_requires_its_own_marker(self):
        """⚠️ THE SAFETY PROPERTY: a profile name is not a bypass of marker
        presence - only of the table-order tie-break. Asserts the SPECIFIC
        "not found in the repo" wording, not just a loose substring match -
        a weaker `assertIn("Cargo.toml", ...)` would still pass even if the
        marker-existence check were deleted outright, because the resolver's
        OWN "could not be parsed" refusal also happens to mention the
        filename (caught live by a manual mutation test that disabled the
        `.exists()` check and found every prior assertion here still green)."""
        with TempRepo() as root:
            write(root, "pyproject.toml", "[project]\nname = \"x\"\n")
            write(root, "tests/test_x.py")
            result = infer_check(root, check_profile="cargo")
            self.assertIsNone(result.check)
            self.assertIn("was not found in the repo", result.refused_reason)
            self.assertIn("Cargo.toml", result.refused_reason)

    def test_python_unittest_profile_with_empty_tests_dir_refuses(self):
        with TempRepo() as root:
            (root / "tests").mkdir()
            result = infer_check(root, check_profile="python-unittest")
            self.assertIsNone(result.check)
            self.assertIsNotNone(result.refused_reason)

    def test_unknown_profile_name_refuses_with_a_reason(self):
        with TempRepo() as root:
            write(root, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
            result = infer_check(root, check_profile="rust-nightly-fuzz")
            self.assertIsNone(result.check)
            self.assertIn("rust-nightly-fuzz", result.refused_reason)

    def test_repo_probe_run_accepts_check_profile(self):
        with TempRepo() as root:
            write(root, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
            write(root, "tests/test_x.py")
            probe = RepoProbe.run(root, check_profile="python-unittest")
            self.assertEqual(probe.check, ["python", "-m", "unittest", "discover", "-s", "tests"])


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
            write(root, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
            write(root, ".overmind/STANDARDS.md", "must: keep MSRV at 1.75")
            probe = RepoProbe.run(root)
            self.assertEqual(probe.check, ["cargo", "test"])
            self.assertEqual(probe.check_name, "cargo test")
            self.assertIsNone(probe.refused_reason)
            self.assertIn("MSRV", probe.context)

    def test_no_marker_still_returns_context(self):
        with TempRepo() as root:
            write(root, ".overmind/STANDARDS.md", "must: something")
            probe = RepoProbe.run(root)
            self.assertIsNone(probe.check)
            self.assertIsNone(probe.check_name)
            self.assertIn("something", probe.context)

    def test_a_marker_that_fails_validation_carries_the_reason(self):
        """PM finding, 2026-09-19: the refusal reason must reach `RepoProbe`,
        not just `infer_check` internally - it's what `NoCheckInferred`
        quotes so a caller can see WHY, not just THAT nothing was inferred."""
        with TempRepo() as root:
            write_json(root, "package.json", {"scripts": {"build": "tsc"}})
            probe = RepoProbe.run(root)
            self.assertIsNone(probe.check)
            self.assertIsNotNone(probe.refused_reason)


if __name__ == "__main__":
    unittest.main()
