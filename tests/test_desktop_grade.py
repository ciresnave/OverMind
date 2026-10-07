# SPDX-License-Identifier: MIT OR Apache-2.0
"""The graders of probe/desktop_grade.py: computed scores, never a model's
opinion. No network and no model here."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "probe"))
import desktop_grade as g  # noqa: E402


class TestFacts(unittest.TestCase):
    def test_any_listed_spelling_counts_and_case_is_ignored(self):
        score, missing = g.facts_score("It TIMED OUT after 120 s", [["timeout", "timed out"], ["120"]])
        self.assertEqual((score, missing), (1.0, []))

    def test_a_missing_fact_is_named(self):
        score, missing = g.facts_score("h2 is vulnerable", [["h2"], ["rsa"]])
        self.assertEqual((score, missing), (0.5, ["rsa"]))


class TestFabrication(unittest.TestCase):
    SRC = "error[E0308] in crates/a/tests/x.rs:75 ... RUSTSEC-2026-0258 ... 15750944"

    def test_identifiers_from_the_input_are_not_fabricated(self):
        self.assertEqual(g.fabricated("E0308 at x.rs:75, RUSTSEC-2026-0258, 15750944", self.SRC), [])

    def test_a_path_with_a_different_prefix_or_separator_still_matches(self):
        self.assertEqual(g.fabricated(r"see crates\a\tests\x.rs", self.SRC), [])

    def test_invented_identifiers_are_flagged(self):
        self.assertEqual(
            g.fabricated("E0599 in y.rs, RUSTSEC-2026-9999, 123456", self.SRC),
            sorted(["E0599", "y.rs", "RUSTSEC-2026-9999", "123456"]),
        )


class TestExcerpt(unittest.TestCase):
    def test_it_stops_at_the_first_error_and_strips_prefixes_and_colour(self):
        raw = ("job\tstep\t2026-10-01T00:00:00.0Z \x1b[31mfirst\x1b[0m\n"
               "job\tstep\t2026-10-01T00:00:01.0Z ##[error]Process completed\n"
               "job\tstep\t2026-10-01T00:00:02.0Z later job\n")
        self.assertEqual(g.ci_excerpt(raw), "first\n##[error]Process completed")


class TestSweep(unittest.TestCase):
    def test_recall_and_misses(self):
        g.UNIVERSE["sweep"] = {"a.md", "b.md", "c.py"}
        r = g.sweep_score("a.md\nc.py", {"a.md", "b.md"})
        self.assertEqual((r["recall"], r["missed"], r["wrongly_listed"]), (0.5, ["b.md"], ["c.py"]))

    def test_a_name_inside_a_longer_one_is_not_a_hit(self):
        g.UNIVERSE["sweep"] = {"12", "123"}
        self.assertEqual(g.sweep_score("123", {"12"})["recall"], 0.0)


class TestControls(unittest.TestCase):
    TASK = {"id": "t", "input": "go test timed out after 120 s",
            "facts": [["timed out"], ["120"]], "oracle": "It timed out after 120 s."}

    def test_a_good_task_passes_its_controls(self):
        self.assertEqual(g.controls(self.TASK), [])

    def test_an_input_that_lost_a_fact_is_reported(self):
        t = {**self.TASK, "input": "go test failed"}
        self.assertTrue(any("input lacks facts" in p for p in g.controls(t)))

    def test_a_wrong_oracle_is_reported(self):
        t = {**self.TASK, "oracle": "It failed."}
        self.assertTrue(any("oracle facts" in p for p in g.controls(t)))


if __name__ == "__main__":
    unittest.main()
