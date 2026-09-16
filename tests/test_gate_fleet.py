# SPDX-License-Identifier: MIT OR Apache-2.0
"""Tests for the fleet comparison of `.github/spdx_gate.py`.

⚠️ A TOOL THAT REPORTS AGREEMENT MUST BE SEEN REPORTING DISAGREEMENT. "All
deployments agree" is the comforting output, and a comparison that could never
say anything else would print it forever. Every null below has an arm that
must go red in the same test.

The fixtures are copies of THIS repository's real gate, not synthetic files:
the thing under comparison is the thing that ships.
"""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tools"))

import gate_fleet  # noqa: E402

GATE = ROOT / ".github" / "spdx_gate.py"


def _real_gate() -> str:
    """This repo's gate with LF endings, whatever the checkout did to it."""
    return GATE.read_bytes().decode("utf-8").replace("\r\n", "\n")


class Fleet:
    """N fake repositories, each holding a copy of a gate."""

    def __init__(self, tmp: pathlib.Path):
        self.tmp = tmp
        self.repos: list[str] = []

    def add(self, name: str, data: bytes) -> str:
        path = self.tmp / name / ".github" / "spdx_gate.py"
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        self.repos.append(str(self.tmp / name))
        return str(self.tmp / name)

    def run(self) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate_fleet.main(self.repos)
        return code, out.getvalue() + err.getvalue()


class TestFleet(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fleet = Fleet(pathlib.Path(self._tmp.name))
        self.source = _real_gate()

    def tearDown(self):
        self._tmp.cleanup()

    def test_identical_copies_agree_and_a_changed_operator_does_not(self):
        """Both arms in one test, so the null cannot come from a tool that
        never looks."""
        for name in ("a", "b"):
            self.fleet.add(name, self.source.encode("utf-8"))
        code, out = self.fleet.run()
        self.assertEqual(code, 0, out)
        self.assertIn("all deployments agree", out)

        old = "    return names if ok else None\n"
        self.assertEqual(self.source.count(old), 1, "mutation anchor moved")
        self.fleet.add("c", self.source.replace(
            old, "    return names if ok else []\n").encode("utf-8"))
        code, out = self.fleet.run()
        self.assertEqual(code, 1, out)
        self.assertIn("DIVERGED  tracked_sources()", out)

    def test_different_prose_is_not_a_divergence(self):
        """⚠️ THE REASON THIS COMPARES THE AST. Every deployment carries its
        own commentary; a text comparison would be nothing but noise."""
        self.fleet.add("a", self.source.encode("utf-8"))
        anchor = "def tracked_sources(root: pathlib.Path) -> list[str] | None:\n"
        self.assertEqual(self.source.count(anchor), 1)
        self.fleet.add("b", self.source.replace(
            anchor, "# a comment only this copy has\n" + anchor).encode("utf-8"))
        code, out = self.fleet.run()
        self.assertEqual(code, 0, out)

    def test_a_lone_cr_is_reported_and_crlf_is_not(self):
        """🔴 THE DEFECT THE AST COMPARISON COULD NOT SEE. All four deployments
        carried CR CR LF endings and every copy agreed with every other.

        ⚠️ THE CRLF ARM MATTERS AS MUCH AS THE RED ONE: a Windows checkout under
        `core.autocrlf=true` is CRLF on disk, and flagging it would teach the
        reader to ignore the finding."""
        self.fleet.add("lf", self.source.encode("utf-8"))
        self.fleet.add("crlf", self.source.replace("\n", "\r\n").encode("utf-8"))
        code, out = self.fleet.run()
        self.assertEqual(code, 0, out)
        self.assertNotIn("LONE CR", out)

        self.fleet.add("crcrlf",
                       self.source.replace("\n", "\r\r\n", 3).encode("utf-8"))
        code, out = self.fleet.run()
        self.assertEqual(code, 1, out)
        self.assertIn("LONE CR   crcrlf: 3 CRs", out)
        self.assertNotIn("LONE CR   crlf", out)

    def test_a_missing_deployment_is_refused_not_skipped(self):
        self.fleet.add("a", self.source.encode("utf-8"))
        self.fleet.repos.append(str(self.fleet.tmp / "nowhere"))
        code, out = self.fleet.run()
        self.assertEqual(code, 1)
        self.assertIn("does not exist", out)

    def test_a_fleet_with_no_controls_anywhere_does_not_crash(self):
        """⚠️ `max()` of an empty sequence raises. Every count being 0 or None
        is a legitimate input - copies with no self-test at all - and the
        answer must be a report, not a traceback."""
        for name in ("a", "b"):
            self.fleet.add(name, b"def f():\n    return 1\n")
        code, out = self.fleet.run()
        self.assertEqual(code, 0, out)
        self.assertIn("self-test case-tuples: a=None  b=None", out)

    def test_a_lagging_control_count_is_reported(self):
        self.fleet.add("a", self.source.encode("utf-8"))
        case = '        ("MIT", "MIT OR Apache-2.0", False)]\n'
        self.assertEqual(self.source.count(case), 1, "mutation anchor moved")
        self.fleet.add("b", self.source.replace(
            case, '        ("MIT", "MIT OR Apache-2.0", False),\n'
                  '        ("MIT", "MIT", True)]\n').encode("utf-8"))
        code, out = self.fleet.run()
        self.assertEqual(code, 1, out)
        self.assertIn("LAGGING", out)
        self.assertIn("['a'] behind", out)


class TestControlCount(unittest.TestCase):

    def test_the_count_matches_what_the_gate_prints(self):
        """⚠️ ONE SET, ONE NUMBER. The fleet counts controls by reading the
        source; the gate counts them by running. If those disagree, a lagging
        deployment can hide in the difference."""
        proc = subprocess.run(
            [sys.executable, str(GATE), "--self-test"],
            capture_output=True, text=True, encoding="utf-8", check=False,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        last = proc.stdout.strip().splitlines()[-1]
        printed = int(last.split(":")[1].split()[0])
        self.assertEqual(gate_fleet.control_count(GATE), printed, last)

    def test_the_helper_controls_are_counted(self):
        """The git controls live outside `self_test`. Without following the
        call, the count would silently drop them."""
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "g.py"
            path.write_text(
                "def _x_controls():\n    return [('a', True), ('b', True)]\n"
                "def _unrelated():\n    return [('c', True)]\n"
                "def self_test():\n    cases = [('d', 1)]\n    _x_controls()\n",
                encoding="utf-8")
            self.assertEqual(gate_fleet.control_count(path), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
