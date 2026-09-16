# SPDX-License-Identifier: MIT OR Apache-2.0
"""The figure checker in `probe/paper_summaries.py`.

⚠️ THE CHECKER IS THE ONLY PART OF THAT SCRIPT THAT IS NOT A CHEAP MODEL'S
WORD, so it is the part that gets tests. Every case below was a real verdict
on the 2026-09-16 run, or a control run before that run was trusted. The text
here is synthetic, not quoted from any paper.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "probe"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import paper_summaries as ps  # noqa: E402

PAPER = ps.normalise(
    "We ran the protocol on a net-\nwork of 100 agents. Out of 1000 queries,\n"
    "8 (thus 0.8% of the total) failed. Labelling was harder (M = .453, SD = .15).\n"
    "Across tasks the method gives an average boost of 5.4%.\n"
    "Method  70.5  8.85  46.5\n")


class TestCheckGain(unittest.TestCase):

    def test_a_verbatim_quote_with_its_figure_verifies(self):
        g = {"claim": "0.8% of queries failed",
             "quote": "Out of 1000 queries, 8 (thus 0.8% of the total) failed."}
        self.assertEqual(ps.check_gain(g, PAPER), "verified")

    def test_line_wrapping_and_hyphenation_do_not_matter(self):
        g = {"claim": "a network of 100 agents",
             "quote": "We ran the protocol on a network of\n  100 agents."}
        self.assertEqual(ps.check_gain(g, PAPER), "verified")

    def test_an_altered_figure_is_not_found(self):
        g = {"claim": "7.8% failed",
             "quote": "Out of 1000 queries, 8 (thus 7.8% of the total) failed."}
        self.assertEqual(ps.check_gain(g, PAPER), "quote not found in paper")

    def test_an_invented_sentence_is_not_found(self):
        g = {"claim": "5x cheaper", "quote": "the method is five times cheaper everywhere"}
        self.assertEqual(ps.check_gain(g, PAPER), "quote not found in paper")

    def test_a_figure_missing_from_a_real_quote_is_named(self):
        g = {"claim": "99.7% succeeded",
             "quote": "Out of 1000 queries, 8 (thus 0.8% of the total) failed."}
        self.assertIn("['99.7'] not in quote", ps.check_gain(g, PAPER))

    def test_100_is_not_found_inside_1000(self):
        """🔴 The first run's checker used a substring test and passed this."""
        g = {"claim": "1000 queries across 100 agents",
             "quote": "Out of 1000 queries, 8 (thus 0.8% of the total) failed."}
        self.assertIn("['100'] not in quote", ps.check_gain(g, PAPER))

    def test_a_leading_zero_is_the_same_figure(self):
        """🔴 The first run reported 0.453 as appearing NOWHERE in its paper,
        which wrote it `.453`."""
        g = {"claim": "labelling accuracy of 0.453",
             "quote": "Labelling was harder (M = .453, SD = .15)."}
        self.assertEqual(ps.check_gain(g, PAPER), "verified")

    def test_a_model_name_is_not_a_figure(self):
        """🔴 `GPT-3.5` put 3.5 on the list the quote had to contain."""
        g = {"claim": "a 5.4% boost for GPT-3.5 and Qwen2.5",
             "quote": "Across tasks the method gives an average boost of 5.4%."}
        self.assertEqual(ps.check_gain(g, PAPER), "verified")

    def test_a_claim_with_no_figure_is_not_simply_verified(self):
        """🔴 Vacuous: no numbers means the number check cannot fail."""
        g = {"claim": "the method beats the baseline", "quote": "Method 70.5 8.85 46.5"}
        self.assertEqual(ps.check_gain(g, PAPER), ps.NO_FIGURE)


class TestDiagnose(unittest.TestCase):

    def test_an_invented_figure_appears_nowhere(self):
        g = {"claim": "cuts cost by 4213.77%", "quote": "cuts cost by 4213.77%"}
        self.assertIn("NOWHERE", ps.diagnose(g, PAPER))

    def test_one_typo_reads_as_extraction_noise_not_paraphrase(self):
        """The first diagnostic used the longest shared run, and one typo in
        the middle halved it."""
        g = {"claim": "0.8%", "quote": "Out of 1000 queires, 8 (thus 0.8% of the total) failed."}
        self.assertIn("near-verbatim", ps.diagnose(g, PAPER))

    def test_a_paraphrase_with_a_real_figure_is_a_paraphrase(self):
        g = {"claim": "0.8%", "quote": "just 0.8% of everything we tried did not work out at all"}
        self.assertIn("paraphrased", ps.diagnose(g, PAPER))


class TestClaimNumbers(unittest.TestCase):

    def test_names_are_skipped_and_measurements_kept(self):
        self.assertEqual(ps.claim_numbers("GPT-3.5 gains 5.4%"), ["5.4"])
        self.assertEqual(ps.claim_numbers("o3 and GPT-4o at 15.4x"), ["15.4"])
        self.assertEqual(ps.claim_numbers("M = .453"), [".453"])
        self.assertEqual(ps.claim_numbers("1,000 queries, 0.8% failed"), ["1,000", "0.8"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
