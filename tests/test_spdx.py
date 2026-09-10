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


if __name__ == "__main__":
    unittest.main(verbosity=2)
