# SPDX-License-Identifier: MIT OR Apache-2.0
"""A durable, append-only record of every dispatch - collected now, not
routed on yet (PM/CireSnave, 2026-09-19, EXPECTATIONS §2.3a). Every test
here is about the RECORD reaching disk intact and never being lost, not
about any routing decision - there isn't one yet.
"""

import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind import ledger                                             # noqa: E402


def sample(**overrides) -> ledger.DispatchRecord:
    base = dict(
        task_id="t1", repo="C:/x", task_type="cargo test", docs_only=False,
        provider="google", model="gemini-3.6-flash", verdict="PASS", steps=6,
        tokens={"input": 100, "output": 50}, seconds=12.5,
        pr_url="https://github.com/x/y/pull/1", error=None,
    )
    base.update(overrides)
    return ledger.DispatchRecord(**base)


class TestAppend(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="ledger-test-"))
        self.path = self.tmp / "dispatch_ledger.jsonl"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_creates_the_file_and_parent_dir(self):
        nested = self.tmp / "nested" / "ledger.jsonl"
        ledger.append(sample(), path=nested)
        self.assertTrue(nested.exists())

    def test_one_record_is_one_json_line(self):
        ledger.append(sample(), path=self.path)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["task_id"], "t1")
        self.assertEqual(parsed["verdict"], "PASS")

    def test_every_field_the_pm_asked_for_is_recorded(self):
        """PM's own list, verbatim: "task type, provider/model, verdict,
        tokens and wall time"."""
        ledger.append(sample(), path=self.path)
        parsed = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        for field in ("task_type", "provider", "model", "verdict", "tokens", "seconds"):
            self.assertIn(field, parsed)

    def test_append_never_truncates_never_rewrites(self):
        """⚠️ THE WHOLE SAFETY PROPERTY. Three appends must leave three
        lines, each intact - never a whole-file rewrite that could lose an
        earlier record to a crash mid-write."""
        ledger.append(sample(task_id="a"), path=self.path)
        ledger.append(sample(task_id="b"), path=self.path)
        ledger.append(sample(task_id="c"), path=self.path)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        ids = [json.loads(line)["task_id"] for line in lines]
        self.assertEqual(ids, ["a", "b", "c"])

    def test_every_verdict_is_recorded_not_just_pass(self):
        """PM's own point: a CHECK_FAILED or ERROR run is exactly the data
        future cost-per-success routing needs, not just the PASS runs."""
        for verdict in ("PASS", "CHECK_FAILED", "NO_CHANGE", "INCOMPLETE", "ERROR"):
            ledger.append(sample(task_id=verdict, verdict=verdict), path=self.path)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        verdicts = {json.loads(line)["verdict"] for line in lines}
        self.assertEqual(verdicts, {"PASS", "CHECK_FAILED", "NO_CHANGE", "INCOMPLETE", "ERROR"})

    def test_a_write_failure_is_reported_not_raised(self):
        """⚠️ BEST-EFFORT: a ledger write must never take down the dispatch
        it was trying to record. A path that cannot be created (a file
        sitting where a directory needs to be) is the control."""
        blocker = self.tmp / "blocker"
        blocker.write_text("x", encoding="utf-8")
        bad_path = blocker / "ledger.jsonl"  # blocker is a FILE, not a dir
        try:
            ledger.append(sample(), path=bad_path)
        except Exception as exc:  # noqa: BLE001 - the property under test
            self.fail(f"append() must never raise; raised {exc!r}")

    def test_default_path_honours_the_env_var(self):
        old = os.environ.get("OVERMIND_LEDGER_FILE")
        os.environ["OVERMIND_LEDGER_FILE"] = str(self.path)
        try:
            self.assertEqual(ledger.default_path(), self.path)
        finally:
            if old is None:
                del os.environ["OVERMIND_LEDGER_FILE"]
            else:
                os.environ["OVERMIND_LEDGER_FILE"] = old

    def test_default_path_falls_back_to_home_dir(self):
        old = os.environ.pop("OVERMIND_LEDGER_FILE", None)
        try:
            path = ledger.default_path()
            self.assertEqual(path.name, "dispatch_ledger.jsonl")
            self.assertEqual(path.parent.name, ".overmind")
        finally:
            if old is not None:
                os.environ["OVERMIND_LEDGER_FILE"] = old


if __name__ == "__main__":
    unittest.main()
