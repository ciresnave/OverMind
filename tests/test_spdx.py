"""Tests for the SPDX tool.

⚠️ The pass condition for the real sweep is a COUNT READ FROM THE TREE, never a
report of what happened. These tests pin the file transformations; the ratchet
pins the count.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import shutil
import subprocess
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import spdx  # noqa: E402

MIT = "MIT OR Apache-2.0"


class TestCommentSyntax(unittest.TestCase):
    """⚠️ 'One line each' is not one case. Writing `//` into a file whose comment
    syntax is `#` corrupts it silently, and 2,200 times is a lot of silence."""

    def test_line_comment_languages(self):
        self.assertEqual(spdx.header_line(".rs", MIT),
                         "// SPDX-License-Identifier: MIT OR Apache-2.0")

    def test_hash_comment_languages(self):
        self.assertEqual(spdx.header_line(".py", MIT),
                         "# SPDX-License-Identifier: MIT OR Apache-2.0")

    def test_block_comment_languages(self):
        self.assertEqual(spdx.header_line(".css", MIT),
                         "/* SPDX-License-Identifier: MIT OR Apache-2.0 */")

    def test_markup_languages(self):
        self.assertEqual(spdx.header_line(".html", MIT),
                         "<!-- SPDX-License-Identifier: MIT OR Apache-2.0 -->")

    def test_unknown_extension_is_refused_not_guessed(self):
        with self.assertRaises(spdx.Unsupported):
            spdx.header_line(".wat", MIT)


class TestPlacement(unittest.TestCase):
    def test_shebang_stays_on_line_one(self):
        """⚠️ Displace it and the file stops being executable."""
        out, _ = spdx.apply_to_text("#!/usr/bin/env python\nprint(1)\n", ".py", MIT)
        lines = out.split("\n")
        self.assertTrue(lines[0].startswith("#!"))
        self.assertIn(spdx.MARKER, lines[1])

    def test_no_shebang_goes_first(self):
        out, _ = spdx.apply_to_text("print(1)\n", ".py", MIT)
        self.assertIn(spdx.MARKER, out.split("\n")[0])

    def test_byte_order_mark_stays_at_byte_zero(self):
        out, _ = spdx.apply_to_text(spdx.BOM + "print(1)\n", ".py", MIT)
        self.assertTrue(out.startswith(spdx.BOM))
        self.assertIn(spdx.MARKER, out.split("\n")[0])

    def test_xml_declaration_stays_first(self):
        out, _ = spdx.apply_to_text('<?xml version="1.0"?>\n<a/>\n', ".xml", MIT)
        lines = out.split("\n")
        self.assertTrue(lines[0].startswith("<?xml"))
        self.assertIn(spdx.MARKER, lines[1])

    def test_astro_header_goes_inside_the_frontmatter(self):
        """⚠️ The trap the ThinkersJournal.com lane hit. Outside the `---` fence
        the comment is rendered OUTPUT, not code."""
        out, _ = spdx.apply_to_text("---\nconst a = 1;\n---\n<div/>\n", ".astro", MIT)
        lines = out.split("\n")
        self.assertEqual(lines[0], "---")
        self.assertIn(spdx.MARKER, lines[1])
        self.assertTrue(lines[1].startswith("//"))

    def test_astro_without_frontmatter_goes_first(self):
        out, _ = spdx.apply_to_text("<div/>\n", ".astro", MIT)
        self.assertIn(spdx.MARKER, out.split("\n")[0])

    def test_crlf_files_keep_crlf(self):
        out, _ = spdx.apply_to_text("print(1)\r\nprint(2)\r\n", ".py", MIT)
        self.assertIn("\r\n", out)
        self.assertNotIn("\n\n", out.replace("\r\n", ""))


class TestIdempotenceAndConflicts(unittest.TestCase):
    def test_running_twice_produces_one_header(self):
        once, _ = spdx.apply_to_text("print(1)\n", ".py", MIT)
        twice, skip = spdx.apply_to_text(once, ".py", MIT)
        self.assertEqual(once, twice)
        self.assertEqual(skip, "already-present")
        self.assertEqual(twice.count(spdx.MARKER), 1)

    def test_a_different_licence_is_reported_never_rewritten(self):
        """⚠️ Changing someone's declared licence is a legal change, not a
        formatting fix."""
        original = "# SPDX-License-Identifier: GPL-3.0-only\nprint(1)\n"
        out, skip = spdx.apply_to_text(original, ".py", MIT)
        self.assertEqual(out, original, "an existing licence was rewritten")
        self.assertEqual(skip, "different-licence")

    def test_existing_identifier_is_parsed_from_any_comment_style(self):
        self.assertEqual(
            spdx.existing_identifier("/* SPDX-License-Identifier: MIT */"), "MIT")
        self.assertEqual(
            spdx.existing_identifier("<!-- SPDX-License-Identifier: MIT OR Apache-2.0 -->"),
            "MIT OR Apache-2.0")


class TestScanAndExitCodes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.dir.name)
        (self.root / "a.py").write_text("print(1)\n", encoding="utf-8")
        (self.root / "b.py").write_text("# SPDX-License-Identifier: MIT OR Apache-2.0\nx=1\n",
                                        encoding="utf-8")
        skip = self.root / "node_modules"
        skip.mkdir()
        (skip / "c.py").write_text("print(2)\n", encoding="utf-8")

    def tearDown(self):
        self.dir.cleanup()

    def test_check_counts_from_the_tree(self):
        """⚠️ The pass condition is a count read from the FILES, never a report
        of what a run believes it did - the underclaiming finding (§19) says a
        model may do the work and deny it, and a file count is immune to both
        directions."""
        report = spdx.scan(self.root, {".py"}, None, apply=False,
                           exclude=("node_modules",))
        self.assertEqual(report.total, 2, "excluded directory was scanned")
        self.assertEqual(report.with_header, 1)
        self.assertEqual(len(report.missing), 1)

    def test_apply_then_check_is_clean(self):
        spdx.scan(self.root, {".py"}, MIT, apply=True, exclude=("node_modules",))
        after = spdx.scan(self.root, {".py"}, None, apply=False, exclude=("node_modules",))
        self.assertEqual(after.with_header, after.total)

    def test_apply_refuses_without_a_licence(self):
        """⚠️ No default. Inferring a licence from sibling projects is an
        observation, not a ruling."""
        code = spdx.main(["apply", str(self.root)])
        self.assertEqual(code, 2)

    def test_unknown_extension_refuses_the_whole_run(self):
        """⚠️ Refuse the run rather than silently skip a language."""
        self.assertEqual(spdx.main(["check", str(self.root), "--ext", ".wat"]), 2)

    def test_check_exit_code_is_one_when_files_are_missing(self):
        self.assertEqual(spdx.main(["check", str(self.root), "--ext", ".py"]), 1)

    def test_check_exit_code_is_zero_when_complete(self):
        spdx.scan(self.root, {".py"}, MIT, apply=True, exclude=("node_modules",))
        self.assertEqual(spdx.main(["check", str(self.root), "--ext", ".py",
                                    "--exclude", "node_modules"]), 0)

    def test_a_conflicting_licence_fails_distinctly(self):
        """⚠️ Exit 3, not 1. 'A file is missing a header' and 'a file declares a
        DIFFERENT licence' need different responses."""
        (self.root / "d.py").write_text("# SPDX-License-Identifier: GPL-3.0-only\n",
                                        encoding="utf-8")
        code = spdx.main(["apply", str(self.root), "--ext", ".py",
                          "--license", MIT, "--exclude", "node_modules"])
        self.assertEqual(code, 3)


class TestHyphenatedIdentifiersParseWhole(unittest.TestCase):
    """⚠️ An earlier parser excluded `-` to stop at `-->` and truncated
    'MIT OR Apache-2.0' to 'MIT OR Apache'. Nearly every SPDX identifier is
    hyphenated, and idempotence is decided by comparing the PARSED value - so
    the truncation would have reported every file as carrying a DIFFERENT
    licence and inserted a second header on every re-run. 2,200 files, 2,200
    duplicates on the second pass, and the first pass looks perfect."""

    def test_hyphenated_identifiers_survive(self):
        for ident in ("Apache-2.0", "GPL-3.0-only", "MIT OR Apache-2.0",
                      "BSD-3-Clause", "LGPL-2.1-or-later", "MIT WITH Bison-exception-2.2"):
            with self.subTest(ident=ident):
                text, _ = spdx.apply_to_text("x=1\n", ".py", ident)
                self.assertEqual(spdx.existing_identifier(text), ident)

    def test_terminators_are_stripped_not_included(self):
        self.assertEqual(
            spdx.existing_identifier("<!-- SPDX-License-Identifier: Apache-2.0 -->"),
            "Apache-2.0")
        self.assertEqual(
            spdx.existing_identifier("/* SPDX-License-Identifier: GPL-3.0-only */"),
            "GPL-3.0-only")

    def test_round_trip_is_idempotent_for_every_comment_style(self):
        for ext in (".rs", ".py", ".css", ".html"):
            with self.subTest(ext=ext):
                once, _ = spdx.apply_to_text("body\n", ext, "MIT OR Apache-2.0")
                twice, skip = spdx.apply_to_text(once, ext, "MIT OR Apache-2.0")
                self.assertEqual(once, twice)
                self.assertEqual(skip, "already-present")
                self.assertEqual(twice.count(spdx.MARKER), 1)


class TestTheCheckerDoesNotCountItself(unittest.TestCase):
    """A licence header is at the TOP of a file by definition.

    Searching the whole body finds any MENTION of the marker - and this tool's
    own source and tests contain it as a string constant, so a whole-file scan
    reported them compliant while they carried no header. That is the
    self-referential count the portfolio rule warns about, and in a 2,200-file
    sweep every file that merely DISCUSSES licensing would have counted itself
    done.
    """

    def test_a_mention_far_below_the_top_is_not_a_header(self):
        body = "\n".join(["code"] * 40 + ['MARKER = "SPDX-License-Identifier:"'])
        self.assertIsNone(spdx.existing_identifier(body))

    def test_a_real_header_at_the_top_is_still_found(self):
        """The control - bounding the search must not stop it working."""
        self.assertEqual(
            spdx.existing_identifier("# SPDX-License-Identifier: MIT\ncode\n"), "MIT")

    def test_a_header_after_a_shebang_is_found(self):
        text = "#!/usr/bin/env python\n# SPDX-License-Identifier: MIT\n"
        self.assertEqual(spdx.existing_identifier(text), "MIT")

    def test_this_tools_own_source_reports_as_missing(self):
        """⚠️ The end-to-end version of the same check, against the real file."""
        source = pathlib.Path(spdx.__file__).read_text(encoding="utf-8")
        self.assertIn(spdx.MARKER, source, "precondition: the marker IS in this file")
        self.assertIsNone(spdx.existing_identifier(source),
                          "the checker counted its own string constant as a header")


class TestOrderInsensitiveLicenceComparison(unittest.TestCase):
    """⚠️ MEASURED across 2,100 real files: 850 say `MIT OR Apache-2.0`, ONE says
    `Apache-2.0 OR MIT` (lightbulb's Hugging Face derivation), and ONE says
    `Apache-2.0` alone - a vendored third-party file whose author never granted
    an MIT option, which fuel's blanket sweep wrongly stamped and had to correct.

    A naive string compare calls BOTH of those conflicts. ⚠️ A FALSE POSITIVE
    STANDING NEXT TO A TRUE ONE TRAINS THE READER TO DISMISS BOTH - so removing
    the spurious one is what makes the real one visible.
    """

    def test_order_swapped_is_the_same_grant(self):
        self.assertTrue(spdx.same_licence("MIT OR Apache-2.0", "Apache-2.0 OR MIT"))

    def test_the_real_conflict_is_still_a_conflict(self):
        """The control, and the one that matters: Apache-2.0 ALONE is a
        different grant from MIT OR Apache-2.0."""
        self.assertFalse(spdx.same_licence("Apache-2.0", "MIT OR Apache-2.0"))

    def test_an_order_swapped_file_is_skipped_not_rewritten(self):
        original = "//! Copyright (c) 2023 Hugging Face\n//! SPDX-License-Identifier: Apache-2.0 OR MIT\n"
        out, skip = spdx.apply_to_text(original, ".rs", MIT)
        self.assertEqual(out, original)
        self.assertEqual(skip, "already-present")

    def test_an_apache_only_file_is_reported_and_left_alone(self):
        """🔴 The fuel hazard: a vendored Apache-2.0-only work. Stamping it
        asserts a licence grant nobody made."""
        original = "// SPDX-License-Identifier: Apache-2.0\nfn main() {}\n"
        out, skip = spdx.apply_to_text(original, ".rs", MIT)
        self.assertEqual(out, original, "a third-party licence was rewritten")
        self.assertEqual(skip, "different-licence")

    def test_unrelated_licences_are_not_conflated(self):
        self.assertFalse(spdx.same_licence("GPL-3.0-only", "MIT OR Apache-2.0"))
        self.assertFalse(spdx.same_licence("MIT", "MIT OR Apache-2.0"))


class TestHoldout(unittest.TestCase):
    """🔴 THE FUEL HAZARD, made mechanical. fuel swept 795 files and had to
    correct one: `fuel-examples/src/bs1770.rs` is a verbatim Apache-2.0-ONLY
    third-party work, and the blanket sweep asserted a grant nobody made.

    ⚠️ The holdout list's OWN failure mode is the interesting one: unreadable,
    missing and empty all produce an empty set, which is indistinguishable at
    the call site from "nothing needs holding out" - and the permissive reading
    is the one that stamps a licence onto somebody else's copyright.
    """

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_a_held_out_file_is_not_stamped(self):
        third_party = self._write("vendor_ex/bs1770.rs", "// Copyright (c) 2020 Someone\nfn main() {}\n")
        ours = self._write("src/lib.rs", "fn main() {}\n")
        holdout = self._write("HOLDOUT.txt", "vendor_ex/bs1770.rs  # Apache-2.0 only\n")
        report = spdx.scan(self.root, {".rs"}, MIT, apply=True, exclude=(),
                           holdout=spdx.load_holdout(holdout))
        self.assertNotIn("SPDX", third_party.read_text(encoding="utf-8"),
                         "a third-party file was stamped despite being held out")
        self.assertIn("SPDX", ours.read_text(encoding="utf-8"),
                      "the holdout suppressed a file it did not name")
        self.assertEqual(report.held_out, ["vendor_ex/bs1770.rs"])

    def test_a_missing_holdout_file_raises_rather_than_returning_empty(self):
        """⚠️ THE WHOLE POINT. Returning an empty set here would sweep every
        file the list existed to protect, and look like a clean run."""
        with self.assertRaises(spdx.HoldoutError):
            spdx.load_holdout(self.root / "does-not-exist.txt")

    def test_an_empty_holdout_file_raises(self):
        empty = self._write("HOLDOUT.txt", "# only a comment\n\n")
        with self.assertRaises(spdx.HoldoutError):
            spdx.load_holdout(empty)

    def test_an_unusable_holdout_refuses_the_whole_run(self):
        self._write("src/lib.rs", "fn main() {}\n")
        code = spdx.main(["apply", str(self.root), "--license", MIT,
                          "--ext", ".rs", "--holdout", str(self.root / "nope.txt")])
        self.assertEqual(code, 2, "the run continued without its holdout list")
        self.assertNotIn("SPDX", (self.root / "src/lib.rs").read_text(encoding="utf-8"))

    def test_an_entry_matching_no_file_is_reported_not_silent(self):
        """⚠️ THE POSITIVE CONTROL FOR THE HOLDOUT ITSELF. Rename the protected
        file and the list still parses, still loads, and protects nothing."""
        self._write("src/lib.rs", "fn main() {}\n")
        holdout = self._write("HOLDOUT.txt", "vendor_ex/renamed-away.rs\n")
        report = spdx.scan(self.root, {".rs"}, MIT, apply=False, exclude=(),
                           holdout=spdx.load_holdout(holdout))
        self.assertEqual(report.holdout_unmatched, {"vendor_ex/renamed-away.rs"})

    def test_unmatched_holdout_outranks_a_conflict_in_the_exit_code(self):
        """A conflict announces itself in the report; an unmatched holdout entry
        announces nothing, so it takes the exit code."""
        self._write("src/other.rs", "// SPDX-License-Identifier: GPL-3.0-only\n")
        holdout = self._write("HOLDOUT.txt", "gone.rs\n")
        code = spdx.main(["check", str(self.root), "--license", MIT,
                          "--ext", ".rs", "--holdout", str(holdout)])
        self.assertEqual(code, 4)

    def test_holdout_entries_accept_backslashes_because_windows(self):
        self._write("vendor_ex/bs1770.rs", "fn main() {}\n")
        holdout = self._write("HOLDOUT.txt", "vendor_ex" + chr(92) + "bs1770.rs" + chr(10))
        report = spdx.scan(self.root, {".rs"}, MIT, apply=False, exclude=(),
                           holdout=spdx.load_holdout(holdout))
        self.assertEqual(report.held_out, ["vendor_ex/bs1770.rs"])
        self.assertEqual(report.holdout_unmatched, set())

class TestGpuAndCudaExtensions(unittest.TestCase):
    """⚠️ AN EXTENSION THE SWEEP NEVER NAMES IS A POPULATION THE REPORT NEVER
    COUNTED. `Unpopped` holds 87 .rs files and 32 .cu files; a sweep invoked as
    `--ext .rs` reports 87/87 and 100%, and the 32 CUDA kernels are not in the
    denominator at all. The refusal was correct - the tool will not guess a
    comment syntax - but the REPORT read as complete."""

    def test_cuda_and_shader_sources_use_c_style_comments(self):
        for ext in (".cu", ".cuh", ".comp", ".vert", ".wgsl", ".hlsl", ".metal", ".cl"):
            with self.subTest(ext=ext):
                self.assertEqual(spdx.header_line(ext, MIT),
                                 "// SPDX-License-Identifier: " + MIT)

    def test_a_cuda_file_is_stamped_above_its_leading_comment(self):
        text = "// a kernel" + chr(10) + "__global__ void k() {}" + chr(10)
        out, skip = spdx.apply_to_text(text, ".cu", MIT)
        self.assertTrue(out.startswith("// SPDX-License-Identifier: " + MIT))
        self.assertIsNone(skip)

    def test_an_extension_with_no_known_syntax_is_still_refused(self):
        """The control. Adding extensions must not turn the refusal into a guess."""
        with self.assertRaises(spdx.Unsupported):
            spdx.header_line(".sbatch", MIT)

class TestEmptyFilesAreSkipped(unittest.TestCase):
    """⚠️ FOUND BY THE SYNAPSE LANE SIMULATING MY SWEEP BEFORE I RAN IT. Five of
    their 153 `.rs` files are a single newline. Prepending a header yields
    `// SPDX...` followed by a blank line, which `cargo fmt --check` wants
    trimmed - and trimming makes those files +1/-1.

    ⚠️ THE FORMATTER IS THE SMALLER HALF. The whole change is reviewable
    BECAUSE every file is exactly +1/-0: `git diff --numstat | awk '$1!=1'`
    either prints nothing or prints the files worth looking at. Four legitimate
    exceptions destroy that, and a reviewer who learns the invariant has
    exceptions stops using it.
    """

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_an_empty_file_is_not_stamped(self):
        blank = self._write("tests/empty_test.rs", chr(10))
        spdx.scan(self.root, {".rs"}, MIT, apply=True, exclude=())
        self.assertEqual(blank.read_text(encoding="utf-8"), chr(10),
                         "an empty file was stamped")

    def test_an_empty_file_is_not_counted_as_missing(self):
        """⚠️ Counted as missing it could never be satisfied - the sweep would
        report 1/2 forever and a ratchet built on it could never go green."""
        self._write("tests/empty_test.rs", chr(10))
        self._write("src/real.rs", "fn main() {}" + chr(10))
        report = spdx.scan(self.root, {".rs"}, MIT, apply=True, exclude=())
        self.assertEqual(report.missing, [])
        self.assertEqual(report.total, 1, "the empty file stayed in the denominator")
        self.assertEqual(len(report.empty), 1)

    def test_whitespace_only_counts_as_empty(self):
        blank = self._write("src/ws.rs", chr(10) + "   " + chr(10) + chr(9) + chr(10))
        before = blank.read_text(encoding="utf-8")
        spdx.scan(self.root, {".rs"}, MIT, apply=True, exclude=())
        self.assertEqual(blank.read_text(encoding="utf-8"), before)

    def test_a_file_with_one_real_line_is_still_stamped(self):
        """The control. 'Empty' must mean empty, not 'short'."""
        small = self._write("src/tiny.rs", "fn x() {}" + chr(10))
        spdx.scan(self.root, {".rs"}, MIT, apply=True, exclude=())
        self.assertTrue(small.read_text(encoding="utf-8").startswith("// SPDX"))

class TestNestedWorktreesAreNotSwept(unittest.TestCase):
    """🔴 THE MOST DANGEROUS BUG IN THIS TOOL, because unlike every other one it
    would have WRITTEN.

    `baracuda/.claude/` holds 3,588 `.rs` files: OTHER LANES' WORKTREES of the
    same repository, checked out at other commits, with other agents' work in
    progress in them. Measured:

        git ls-files     1,207 files
        filesystem walk  4,870 files
        under .claude    3,588        <- would have been stamped

    ⚠️ AND THE FIX IS NOT TO ADD `.claude` TO THE EXCLUSION LIST. A hand-written
    set of names can only exclude what its author thought of, and what it misses
    is SILENT. `git ls-files` cannot see another worktree by construction: a
    nested checkout is a different repository with a different index.
    """

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def _write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_a_nested_repository_is_not_swept(self):
        ours = self._write("src/ours.rs", "fn a() {}" + chr(10))
        subprocess.run(["git", "-C", str(self.root), "add", "src/ours.rs"], check=True)

        # a SEPARATE repository living inside ours, as a worktree does
        nested = self.root / ".claude" / "worktrees" / "other-lane"
        nested.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(nested)], check=True)
        theirs = nested / "src.rs"
        theirs.write_text("fn theirs() {}" + chr(10), encoding="utf-8")

        report = spdx.scan(self.root, {".rs"}, MIT, apply=True, exclude=())

        self.assertEqual(report.enumerated_by, "git ls-files")
        self.assertTrue(ours.read_text(encoding="utf-8").startswith("// SPDX"))
        self.assertEqual(theirs.read_text(encoding="utf-8"), "fn theirs() {}" + chr(10),
                         "ANOTHER LANE'S WORKTREE WAS MODIFIED")

    def test_an_untracked_file_in_our_own_repo_is_not_swept_either(self):
        """⚠️ The cost of the fix, stated rather than discovered. An untracked
        file is invisible to `git ls-files` - and that is the RIGHT call, because
        the CI ratchet reads the same list, so sweeper and checker agree. A file
        nobody has added is not yet part of the repo."""
        self._write("src/tracked.rs", "fn a() {}" + chr(10))
        subprocess.run(["git", "-C", str(self.root), "add", "src/tracked.rs"], check=True)
        loose = self._write("src/untracked.rs", "fn b() {}" + chr(10))
        spdx.scan(self.root, {".rs"}, MIT, apply=True, exclude=())
        self.assertEqual(loose.read_text(encoding="utf-8"), "fn b() {}" + chr(10))

    def test_a_non_repository_falls_back_and_SAYS_SO(self):
        """⚠️ Reported, not silent. The two enumerations answer different
        questions and the difference was 3,588 files."""
        plain = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        (plain / "a.rs").write_text("fn a() {}" + chr(10), encoding="utf-8")
        report = spdx.scan(plain, {".rs"}, MIT, apply=False, exclude=())
        self.assertEqual(report.enumerated_by, "filesystem walk")
        self.assertEqual(report.total, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
