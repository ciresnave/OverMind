# SPDX-License-Identifier: MIT OR Apache-2.0
"""OverMind#85: `infer_check` maps a marker to an argv `str` this module OWNS,
but nothing before this file ever RAN that argv against a real project - every
existing test in `test_repo_probe.py` proves the MAPPING (a marker file's
presence selects the right argv SHAPE), never that the argv actually works.
A resolver that returned `["true"]` for every marker would pass all of them.

Each scenario below builds a REAL, minimal project for one marker and
actually executes `infer_check`'s resolved argv against it - once for a
fixture that should PASS, once for one that should FAIL. ⚠️ THE FAILING CASE
IS NOT OPTIONAL. PM finding, 2026-09-23: a positive-only test on a command
resolver is close to worthless - without a negative control, "the resolver
produced an argv" and "the argv means what we think" are indistinguishable.
Both cases exist for every scenario in this file, on purpose, even where a
failing fixture takes more code than a passing one.

⚠️ SKIPPED IS NOT EXECUTED. Some markers need a toolchain (`go`, `make`,
`npm`, `pnpm`) this repo's own CI does not install and this module has no
business assuming exists - `unittest.skipUnless` turns a missing toolchain
into a SKIP, never a failure, so this file can never turn a red toolchain
into a red main. But a skip is not a pass: it is SILENCE, and #85's whole
point is that silence here is exactly the defect. Every skip below carries
an explicit reason naming the missing tool, so it reads as
"skipped: go not installed" in the log, never a bare dot - and closing #85
is only honest for the scenarios a real CI run actually EXECUTES, never for
ones that only ever skipped. See MEASUREMENTS.md's entry on this file for
the per-scenario executed/skipped accounting from a real run.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.repo_probe import infer_check  # noqa: E402

#: Every real subprocess this file runs gets a hard ceiling - a hung
#: toolchain must not hang the whole suite.
FIXTURE_TIMEOUT_S = 120


class TempFixture:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        return self.root

    def __exit__(self, *exc):
        self._tmp.cleanup()


def write(root: pathlib.Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def pytest_available() -> bool:
    """⚠️ NOT `importlib.util.find_spec` AGAINST THIS PROCESS. Sourcery
    finding on this file's own first PR revision: `run_resolved_check`
    resolves `python` via `shutil.which` - the SAME PATH-based resolution
    `lanework._run` uses - which can be a DIFFERENT interpreter from
    `sys.executable` (the one running this test suite). Checking
    `find_spec` here answers "does THIS process have pytest", not "will
    the interpreter that ACTUALLY RUNS have pytest" - the two can disagree
    in either direction. Ask the same interpreter `run_resolved_check`
    would actually invoke."""
    exe = shutil.which("python") or "python"
    proc = subprocess.run(  # noqa: S603 - fixed argv, shell=False
        [exe, "-c", "import pytest"], capture_output=True, timeout=30, check=False,
    )
    return proc.returncode == 0


class RealExecutionCase(unittest.TestCase):
    """Shared machinery: infer the check for a fixture, then actually run
    it - never assert on the argv's shape here, only on what running it did."""

    def run_resolved_check(self, root: pathlib.Path) -> subprocess.CompletedProcess:
        """⚠️ MIRRORS `lanework._run`'s OWN INVOCATION, NOT A BARE
        `subprocess.run`. `_run` resolves `argv[0]` through `shutil.which`
        before ever calling `CreateProcess` - required on Windows, where
        `npm`/`pnpm` are `.CMD` shims that `CreateProcess` can launch when
        given their full, extension-qualified path but NOT when given the
        bare command name directly (no shell, no PATHEXT search). Calling
        `subprocess.run(result.check, ...)` here without that resolution
        step would test a DIFFERENT (and on Windows, broken) invocation
        path than the one `lanework.run_task` actually uses - exactly the
        kind of gap this file exists to close, so the fixture's own
        machinery does not get to have one."""
        result = infer_check(root)
        self.assertIsNotNone(
            result.check,
            f"infer_check found nothing for the fixture at {root} "
            f"({result.refused_reason})",
        )
        exe = shutil.which(result.check[0]) or result.check[0]
        return subprocess.run(  # noqa: S603 - argv list, shell=False, fixture-only
            [exe, *result.check[1:]], cwd=root, capture_output=True, text=True,
            timeout=FIXTURE_TIMEOUT_S, shell=False, check=False,
        )


class TestCargoRealExecution(RealExecutionCase):
    @unittest.skipUnless(shutil.which("cargo"), "cargo not installed")
    def test_a_real_passing_cargo_project_actually_passes(self):
        with TempFixture() as root:
            write(root, "Cargo.toml",
                 '[package]\nname = "fixture"\nversion = "0.1.0"\nedition = "2021"\n')
            write(root, "src/lib.rs",
                 "#[test]\nfn it_passes() { assert_eq!(1 + 1, 2); }\n")
            proc = self.run_resolved_check(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    @unittest.skipUnless(shutil.which("cargo"), "cargo not installed")
    def test_a_real_failing_cargo_project_actually_fails(self):
        """⚠️ THE NEGATIVE CONTROL. Without this, `_resolve_cargo_toml`
        could return `["true"]` and the test above would still pass."""
        with TempFixture() as root:
            write(root, "Cargo.toml",
                 '[package]\nname = "fixture"\nversion = "0.1.0"\nedition = "2021"\n')
            write(root, "src/lib.rs",
                 "#[test]\nfn it_fails() { assert_eq!(1 + 1, 3); }\n")
            proc = self.run_resolved_check(root)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestGoRealExecution(RealExecutionCase):
    @unittest.skipUnless(shutil.which("go"), "go not installed")
    def test_a_real_passing_go_project_actually_passes(self):
        with TempFixture() as root:
            # ⚠️ A LOW VERSION DIRECTIVE, DELIBERATELY. Sourcery finding on
            # this file's own first PR revision: `go 1.22` refuses to build
            # on any installed toolchain OLDER than 1.22, unconditionally -
            # this fixture uses no feature newer than early Go, so pin the
            # floor low rather than to whatever's locally installed.
            write(root, "go.mod", "module fixture\n\ngo 1.16\n")
            write(root, "fixture_test.go",
                 "package fixture\n\nimport \"testing\"\n\n"
                 "func TestPasses(t *testing.T) {\n"
                 "\tif 1+1 != 2 {\n\t\tt.Fatal(\"math is broken\")\n\t}\n}\n")
            proc = self.run_resolved_check(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    @unittest.skipUnless(shutil.which("go"), "go not installed")
    def test_a_real_failing_go_project_actually_fails(self):
        with TempFixture() as root:
            # ⚠️ A LOW VERSION DIRECTIVE, DELIBERATELY. Sourcery finding on
            # this file's own first PR revision: `go 1.22` refuses to build
            # on any installed toolchain OLDER than 1.22, unconditionally -
            # this fixture uses no feature newer than early Go, so pin the
            # floor low rather than to whatever's locally installed.
            write(root, "go.mod", "module fixture\n\ngo 1.16\n")
            write(root, "fixture_test.go",
                 "package fixture\n\nimport \"testing\"\n\n"
                 "func TestFails(t *testing.T) {\n"
                 "\tif 1+1 != 3 {\n\t\tt.Fatal(\"deliberately wrong\")\n\t}\n}\n")
            proc = self.run_resolved_check(root)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestMakeRealExecution(RealExecutionCase):
    @unittest.skipUnless(shutil.which("make"), "make not installed")
    def test_a_real_passing_makefile_actually_passes(self):
        with TempFixture() as root:
            write(root, "Makefile", "test:\n\t@exit 0\n")
            proc = self.run_resolved_check(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    @unittest.skipUnless(shutil.which("make"), "make not installed")
    def test_a_real_failing_makefile_actually_fails(self):
        with TempFixture() as root:
            write(root, "Makefile", "test:\n\t@exit 1\n")
            proc = self.run_resolved_check(root)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestPythonUnittestRealExecution(RealExecutionCase):
    """The `unittest` branch of `_resolve_pyproject_toml`/`_resolve_setup_py`
    - stdlib, so this never skips: `python` is this process's own
    interpreter, and `unittest` ships with it."""

    def test_a_real_passing_unittest_project_actually_passes(self):
        with TempFixture() as root:
            write(root, "pyproject.toml", "[project]\nname = \"fixture\"\n")
            write(root, "tests/test_fixture.py",
                 "import unittest\n\n"
                 "class TestFixture(unittest.TestCase):\n"
                 "    def test_it_passes(self):\n"
                 "        self.assertEqual(1 + 1, 2)\n")
            proc = self.run_resolved_check(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_real_failing_unittest_project_actually_fails(self):
        with TempFixture() as root:
            write(root, "pyproject.toml", "[project]\nname = \"fixture\"\n")
            write(root, "tests/test_fixture.py",
                 "import unittest\n\n"
                 "class TestFixture(unittest.TestCase):\n"
                 "    def test_it_fails(self):\n"
                 "        self.assertEqual(1 + 1, 3)\n")
            proc = self.run_resolved_check(root)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestPythonPytestRealExecution(RealExecutionCase):
    """The `pytest` branch, reached via a `[tool.pytest...]` config section
    (auto-inference) - only ever executed where the `pytest` MODULE is
    actually importable, which is not guaranteed anywhere this repo's own
    CI runs today (OverMind is deliberately stdlib-only)."""

    @unittest.skipUnless(pytest_available(), "pytest module not installed")
    def test_a_real_passing_pytest_project_actually_passes(self):
        with TempFixture() as root:
            write(root, "pyproject.toml",
                 "[project]\nname = \"fixture\"\n\n"
                 "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n")
            write(root, "tests/test_fixture.py",
                 "def test_it_passes():\n    assert 1 + 1 == 2\n")
            proc = self.run_resolved_check(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    @unittest.skipUnless(pytest_available(), "pytest module not installed")
    def test_a_real_failing_pytest_project_actually_fails(self):
        with TempFixture() as root:
            write(root, "pyproject.toml",
                 "[project]\nname = \"fixture\"\n\n"
                 "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n")
            write(root, "tests/test_fixture.py",
                 "def test_it_fails():\n    assert 1 + 1 == 3\n")
            proc = self.run_resolved_check(root)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestNpmRealExecution(RealExecutionCase):
    @unittest.skipUnless(shutil.which("npm"), "npm not installed")
    def test_a_real_passing_npm_project_actually_passes(self):
        with TempFixture() as root:
            write(root, "package.json",
                 '{"name": "fixture", "scripts": {"test": "node -e \\"process.exit(0)\\""}}')
            proc = self.run_resolved_check(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    @unittest.skipUnless(shutil.which("npm"), "npm not installed")
    def test_a_real_failing_npm_project_actually_fails(self):
        with TempFixture() as root:
            write(root, "package.json",
                 '{"name": "fixture", "scripts": {"test": "node -e \\"process.exit(1)\\""}}')
            proc = self.run_resolved_check(root)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class TestPnpmWorkspaceRealExecution(RealExecutionCase):
    """The `pnpm -r --if-present test` branch: a root `package.json` with no
    `scripts.test` of its own, a `pnpm-workspace.yaml`, and one workspace
    package that has a real `test` script - the same shape
    `_resolve_package_json`'s own docstring describes."""

    @unittest.skipUnless(shutil.which("pnpm"), "pnpm not installed")
    def test_a_real_passing_pnpm_workspace_actually_passes(self):
        with TempFixture() as root:
            write(root, "package.json", '{"name": "fixture-root", "private": true}')
            write(root, "pnpm-workspace.yaml", 'packages:\n  - "packages/*"\n')
            write(root, "packages/pkg/package.json",
                 '{"name": "pkg", "scripts": {"test": "node -e \\"process.exit(0)\\""}}')
            proc = self.run_resolved_check(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    @unittest.skipUnless(shutil.which("pnpm"), "pnpm not installed")
    def test_a_real_failing_pnpm_workspace_actually_fails(self):
        with TempFixture() as root:
            write(root, "package.json", '{"name": "fixture-root", "private": true}')
            write(root, "pnpm-workspace.yaml", 'packages:\n  - "packages/*"\n')
            write(root, "packages/pkg/package.json",
                 '{"name": "pkg", "scripts": {"test": "node -e \\"process.exit(1)\\""}}')
            proc = self.run_resolved_check(root)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
