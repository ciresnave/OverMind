"""SPDX header insertion and verification.

    python tools/spdx.py check  <path> [--ext .py .rs]
    python tools/spdx.py apply  <path> --license "MIT OR Apache-2.0"

⚠️ THIS IS A SCRIPT, NOT AN AGENT TASK, AND THAT IS THE POINT.

Inserting a licence header is fully determined by the file's extension and its
first few bytes. There is no judgement in it. A language model doing this
2,200 times would cost tokens, take hours, and introduce a failure mode that a
40-line function does not have — and the whole reason this project exists is
that routine work should stop costing Claude tokens. **The cheapest non-Claude
executor is not a cheaper model. It is not needing a model.**

MEASUREMENTS.md §19 measured what a model is for: honesty about failure held
7/7, multi-turn state held 1 of 7. Neither number is relevant here, because this
task has no turns and no failure to report. **Reach for the model where
judgement is required; reach for a script where it is not.**

WHAT IS ACTUALLY HARD HERE, and it is not the header:

  · ⚠️ PLACEMENT IS PER-LANGUAGE AND SOMETIMES PER-FILE. A shebang must stay on
    line 1. A byte-order mark must stay at byte 0. An `.astro` file's header
    belongs INSIDE its `---` frontmatter, because outside it is rendered output.
    "One line each" is not one case.

  · ⚠️ AN UNKNOWN EXTENSION IS REFUSED, NOT GUESSED. Writing `//` into a file
    whose comment syntax is `#` corrupts it silently and 2,200 times is a lot of
    silence.

  · ⚠️ IDEMPOTENCE. Running twice must not produce two headers, and a file that
    already carries a DIFFERENT SPDX identifier is REPORTED, never rewritten —
    changing someone's declared licence is not a formatting fix.

⚠️ `--license` HAS NO DEFAULT. Stamping a licence into source files is a legal
declaration, and inferring it from what most sibling crates happen to contain is
an observation, not a ruling. The tool refuses to run without one.

⚠️ AND SOME FILES ARE NOT OURS TO STAMP AT ALL. `fuel` swept 795 files and had
to correct one: `fuel-examples/src/bs1770.rs` is a verbatim Apache-2.0-ONLY
third-party work, and the blanket sweep stamped `MIT OR Apache-2.0` on it —
asserting a grant nobody made. `--holdout <file>` names the paths a sweep must
leave alone. ⚠️ An unreadable, missing or empty holdout list REFUSES THE RUN,
and an entry matching no file exits 4, because a list that protects nothing
reads exactly like a list with nothing to protect.

Exit codes:
    0  every file in scope carries the identifier
    1  files are missing it
    2  refused — unsupported extension, no --license, or an unusable holdout
    3  a file declares a DIFFERENT licence (reported, never rewritten)
    4  a holdout entry matched no file — it protected NOTHING
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from dataclasses import dataclass, field

MARKER = "SPDX-License-Identifier:"

#: extension -> (prefix, suffix). A suffix means a block comment.
COMMENT_STYLES: dict[str, tuple[str, str]] = {}
for _ext in (".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".java",
             ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".kt", ".scala", ".php",
             # ⚠️ CUDA, and the GPU shading languages that share C's syntax.
             # `Unpopped` holds 32 `.cu` files and `baracuda` holds CUDA headers;
             # without these the tool REFUSES those files, which is the correct
             # failure but leaves a third of a repo unstamped and looking clean
             # if the caller only ever passes `--ext .rs`. ⚠️ AN EXTENSION THE
             # SWEEP NEVER NAMES IS A POPULATION THE REPORT NEVER COUNTED.
             ".cu", ".cuh", ".cl", ".comp", ".vert", ".frag", ".geom", ".glsl",
             ".wgsl", ".hlsl", ".metal", ".zig", ".dart", ".d", ".v"):
    COMMENT_STYLES[_ext] = ("// ", "")
for _ext in (".py", ".sh", ".bash", ".rb", ".pl", ".toml", ".yml", ".yaml",
             ".ps1", ".r", ".jl", ".nix", ".dockerfile", ".mk"):
    COMMENT_STYLES[_ext] = ("# ", "")
for _ext in (".css", ".scss", ".less"):
    COMMENT_STYLES[_ext] = ("/* ", " */")
for _ext in (".html", ".xml", ".svg", ".vue"):
    COMMENT_STYLES[_ext] = ("<!-- ", " -->")
COMMENT_STYLES[".sql"] = ("-- ", "")
COMMENT_STYLES[".lua"] = ("-- ", "")
#: ⚠️ `.astro` is the trap the ThinkersJournal.com lane hit: the header belongs
#: INSIDE the `---` frontmatter, where the language is JavaScript. Outside it,
#: the comment is rendered output.
COMMENT_STYLES[".astro"] = ("// ", "")

BOM = "﻿"
# ⚠️ READ TO END OF LINE, THEN STRIP THE COMMENT TERMINATOR. An earlier version
# used a character class that excluded `-` in order to stop at `-->`, and so
# parsed "MIT OR Apache-2.0" as "MIT OR Apache" - nearly every SPDX identifier
# is hyphenated. Idempotence is checked by comparing the PARSED identifier, so a
# truncating parser would have reported every file as carrying a DIFFERENT
# licence and inserted a second header on every re-run. Across 2,200 files that
# is 2,200 duplicates on the second pass, and the first pass looks perfect.
_TERMINATORS = ("-->", "*/", "#}", "}}", "--%>")


class Unsupported(ValueError):
    """An extension with no known comment syntax. ⚠️ Refused, never guessed."""


@dataclass
class FileReport:
    path: pathlib.Path
    has_header: bool
    existing: str | None = None
    changed: bool = False
    skipped: str | None = None


@dataclass
class Report:
    files: list[FileReport] = field(default_factory=list)
    #: Paths deliberately not touched, because their licence is CireSnave's
    #: decision. ⚠️ Counted apart from both compliant and missing: a file
    #: awaiting a human ruling is neither.
    held_out: list[str] = field(default_factory=list)
    #: Holdout entries that matched no file on disk. ⚠️ These protected NOTHING,
    #: and a stale list reads exactly like a working one until the file it names
    #: is renamed and then stamped.
    holdout_unmatched: set = field(default_factory=set)

    @property
    def total(self) -> int:
        """⚠️ EXCLUDES EMPTY FILES. See `empty` - counted in, the sweep could
        never reach 100% and a ratchet built on it could never go green."""
        return len(self.files) - len(self.empty)

    @property
    def empty(self) -> list[FileReport]:
        """Files with nothing in them. ⚠️ NOT missing a header - there is
        nothing here to license. Left out of the DENOMINATOR too: counted as
        missing they could never be satisfied, so the sweep would report
        "148/153" forever and a ratchet built on it could never go green."""
        return [f for f in self.files if f.skipped == "empty"]

    @property
    def with_header(self) -> int:
        return sum(1 for f in self.files if f.has_header)

    @property
    def missing(self) -> list[FileReport]:
        return [f for f in self.files
                if not f.has_header and f.skipped != "empty"]

    @property
    def conflicting(self) -> list[FileReport]:
        return [f for f in self.files if f.skipped == "different-licence"]

    @property
    def changed(self) -> list[FileReport]:
        return [f for f in self.files if f.changed]


#: A licence header is at the TOP of a file by definition. Searching the whole
#: body finds any MENTION of the marker - and this tool's own source, and its
#: tests, contain it as a string constant. Scanning everything reported them as
#: compliant while they carried no header: a self-referential count, the exact
#: shape the portfolio rule warns about ("exactly one occurrence, in the
#: document making the claim"). In a 2,200-file sweep every file that discusses
#: licensing would have counted itself done.
HEADER_WINDOW = 10


def existing_identifier(text: str) -> str | None:
    """Return the SPDX identifier a file already declares, or None.

    A line split rather than a regex, deliberately. The regex this replaced
    used a character class excluding the hyphen, to stop at the "-->" of an
    HTML comment - and so parsed "MIT OR Apache-2.0" as "MIT OR Apache".
    Nearly every SPDX identifier is hyphenated, and idempotence is decided by
    comparing this value, so the truncation would have reported every file as
    carrying a DIFFERENT licence and inserted a second header on every re-run.
    Across 2,200 files that is 2,200 duplicates on the second pass, and the
    first pass looks perfect.
    """
    for line in text.splitlines()[:HEADER_WINDOW]:
        if MARKER not in line:
            continue
        identifier = line.split(MARKER, 1)[1].strip()
        for terminator in _TERMINATORS:
            if identifier.endswith(terminator):
                identifier = identifier[: -len(terminator)].strip()
        return identifier or None
    return None


def _normalise(identifier: str) -> str:
    """Canonical form for COMPARISON only. Never for writing.

    ⚠️ "MIT OR Apache-2.0" and "Apache-2.0 OR MIT" are the same grant. Measured
    across 2,100 real files: 850 use the first spelling, ONE uses the second -
    lightbulb's Hugging Face derivation - and a naive string compare calls it a
    licence CONFLICT.

    That matters more than tidiness. The same scan finds ONE genuine conflict:
    `Apache-2.0` alone, on a vendored third-party file whose author never
    granted an MIT option. ⚠️ A FALSE POSITIVE STANDING NEXT TO A TRUE ONE
    TRAINS THE READER TO DISMISS BOTH - so removing the spurious conflict is
    what makes the real one visible.
    """
    text = identifier.strip()
    for joiner in (" OR ", " or "):
        if joiner in text:
            return " OR ".join(sorted(part.strip() for part in text.split(joiner)))
    return text


def same_licence(a: str, b: str) -> bool:
    return _normalise(a) == _normalise(b)


def header_line(ext: str, identifier: str) -> str:
    if ext not in COMMENT_STYLES:
        raise Unsupported(ext)
    prefix, suffix = COMMENT_STYLES[ext]
    return f"{prefix}{MARKER} {identifier}{suffix}"


def _insert_at(lines: list[str], ext: str) -> int:
    """Where the header may legally go.

    ⚠️ Not always line 0. A shebang must remain the first line or the file stops
    being executable; an XML declaration must remain first or the document stops
    parsing; an `.astro` header belongs inside the frontmatter fence.
    """
    index = 0
    if lines and lines[0].lstrip(BOM).startswith("#!"):
        index = 1
    if lines and lines[0].lstrip(BOM).lower().startswith("<?xml"):
        index = 1
    if ext == ".astro":
        # Insert just inside the opening `---` fence, if there is one.
        for i, line in enumerate(lines[:3]):
            if line.strip() == "---":
                return i + 1
        return 0
    if ext in (".php",) and lines and lines[0].strip().startswith("<?php"):
        index = max(index, 1)
    return index


def apply_to_text(text: str, ext: str, identifier: str) -> tuple[str, str | None]:
    """Return (new_text, skip_reason). ⚠️ Idempotent, and never rewrites a
    different licence — that is a legal change, not a formatting one."""
    found = existing_identifier(text)
    if found is not None:
        # ⚠️ AN EXISTING SPDX LINE MEANS SKIP, NEVER APPEND. That is the
        # truncating-parser failure mode, whose consequence was 2,200 duplicates
        # on pass two with pass one looking flawless.
        if same_licence(found, identifier):
            return text, "already-present"
        return text, "different-licence"

    had_bom = text.startswith(BOM)
    body = text[len(BOM):] if had_bom else text
    newline = "\r\n" if "\r\n" in body else "\n"
    lines = body.split(newline)
    at = _insert_at(lines, ext)
    lines.insert(at, header_line(ext, identifier))
    out = newline.join(lines)
    return (BOM + out if had_bom else out), None


class HoldoutError(Exception):
    """The holdout list could not be established. ⚠️ NOT the same as an empty one."""


def load_holdout(path: pathlib.Path) -> set[str]:
    """Paths a sweep must not touch, because they need a HUMAN LICENCE DECISION.

    ⚠️ THIS RAISES RATHER THAN RETURNING EMPTY. A holdout file that is missing,
    unreadable or misspelt would otherwise produce an empty set - and an empty
    holdout is indistinguishable, at the call site, from "nothing needs holding
    out". That is the fail-toward-NONE shape: the unreadable answer and the
    permissive answer are the same value, and the permissive one is the one that
    stamps a licence onto somebody else's copyright.

    Blank lines and `#` comments are ignored so the list can say WHY.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HoldoutError(f"cannot read holdout list {path}: {exc}") from exc
    entries = set()
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.add(line.replace("\\", "/"))
    if not entries:
        raise HoldoutError(
            f"holdout list {path} names no files. An EMPTY list and an "
            f"UNREAD one are the same value here; say so explicitly instead.")
    return entries


def _relative(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def scan(root: pathlib.Path, extensions: set[str], identifier: str | None,
         apply: bool, exclude: tuple[str, ...],
         holdout: set[str] | None = None) -> Report:
    report = Report()
    holdout = set(holdout or ())
    report.holdout_unmatched = set(holdout)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        if any(part in exclude for part in path.parts):
            continue
        rel = _relative(path, root)
        if rel in holdout:
            # ⚠️ Held out, NOT skipped-as-done. Counted apart from both, because
            # a file awaiting a human ruling is neither compliant nor a failure.
            report.holdout_unmatched.discard(rel)
            report.held_out.append(rel)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            report.files.append(FileReport(path, False, skipped="unreadable"))
            continue
        if not text.strip():
            # ⚠️ AN EMPTY FILE HAS NO CODE TO LICENSE, and stamping one is not
            # merely pointless - it BREAKS THE SHAPE INVARIANT the whole sweep
            # is verified by. The synapse lane simulated this before I ran it:
            # five of their `.rs` files are a single newline, and prepending a
            # header yields a header line followed by a BLANK line, which
            # `cargo fmt --check` wants trimmed.
            # trimmed. Trimming it makes those files +1/-1 in a change whose
            # entire reviewability rests on EVERY file being +1/-0.
            #
            # ⚠️ SO THE SKIP PROTECTS THE AUDIT, NOT JUST THE FORMATTER. Four
            # legitimate files would have failed a reviewer's shape check, and
            # a reviewer who learns the invariant has exceptions stops using it.
            report.files.append(FileReport(path, False, skipped="empty"))
            continue
        found = existing_identifier(text)
        entry = FileReport(path, has_header=found is not None, existing=found)
        if apply and identifier:
            new_text, skip = apply_to_text(text, path.suffix.lower(), identifier)
            entry.skipped = skip
            if new_text != text:
                path.write_text(new_text, encoding="utf-8", newline="")
                entry.changed = True
                entry.has_header = True
        report.files.append(entry)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("check", "apply"))
    parser.add_argument("path", type=pathlib.Path)
    parser.add_argument("--license", dest="identifier",
                        help="SPDX identifier, e.g. 'MIT OR Apache-2.0'. "
                             "REQUIRED for apply; there is deliberately no default.")
    parser.add_argument("--ext", nargs="*", default=[".py"],
                        help="extensions to consider (default: .py)")
    parser.add_argument("--holdout", type=pathlib.Path,
                        help="file listing repo-relative paths to LEAVE ALONE - "
                             "files whose licence is a human decision, not a stamp. "
                             "An unreadable or empty list REFUSES the run.")
    parser.add_argument("--exclude", nargs="*",
                        default=[".git", "node_modules", "__pycache__", "build", "dist",
                                 ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
                                 "site-packages", "target", "vendor", ".next", "coverage"])
    args = parser.parse_args(argv)

    extensions = {e.lower() if e.startswith(".") else "." + e.lower() for e in args.ext}
    unsupported = sorted(e for e in extensions if e not in COMMENT_STYLES)
    if unsupported:
        # ⚠️ Refuse the whole run rather than silently skipping a language.
        print(f"error: no comment syntax known for {unsupported}. "
              f"Add it to COMMENT_STYLES rather than guessing.", file=sys.stderr)
        return 2

    if args.mode == "apply" and not args.identifier:
        print("error: apply requires --license. Stamping a licence into source files is a "
              "legal declaration, and inferring it from sibling projects is an observation, "
              "not a ruling.", file=sys.stderr)
        return 2

    holdout = None
    if args.holdout:
        try:
            holdout = load_holdout(args.holdout)
        except HoldoutError as exc:
            # ⚠️ Refuse the RUN. Continuing without the list is the one outcome
            # the list exists to prevent.
            print(f"error: {exc}", file=sys.stderr)
            return 2

    report = scan(args.path, extensions, args.identifier,
                  apply=args.mode == "apply", exclude=tuple(args.exclude),
                  holdout=holdout)

    pct = (100.0 * report.with_header / report.total) if report.total else 0.0
    print(f"{report.path_label if hasattr(report, 'path_label') else args.path}: "
          f"{report.with_header}/{report.total} files carry {MARKER} ({pct:.0f}%)")
    for entry in report.conflicting:
        # ⚠️ Reported, never rewritten.
        print(f"  CONFLICT {entry.path}: declares {entry.existing!r}, not {args.identifier!r}")
    if report.held_out:
        print(f"  held out (awaiting a human licence decision): {len(report.held_out)}")
        for rel in report.held_out:
            print(f"    HELD {rel}")
    if report.holdout_unmatched:
        # ⚠️ THE POSITIVE CONTROL FOR THE HOLDOUT ITSELF. An entry matching no
        # file protects nothing, and a stale list reads exactly like a working
        # one - silently, and only until the file it names gets stamped.
        print(f"  🔴 HOLDOUT ENTRIES THAT MATCHED NO FILE: "
              f"{len(report.holdout_unmatched)} - these protected NOTHING",
              file=sys.stderr)
        for rel in sorted(report.holdout_unmatched):
            print(f"    UNMATCHED {rel}", file=sys.stderr)
    if report.empty:
        print(f"  empty, nothing to license: {len(report.empty)}")
    if args.mode == "apply":
        print(f"  changed: {len(report.changed)}")
    else:
        for entry in report.missing[:20]:
            print(f"  MISSING {entry.path}")
        if len(report.missing) > 20:
            print(f"  ... and {len(report.missing) - 20} more")

    if report.holdout_unmatched:
        # ⚠️ Ranked ABOVE a conflict, because it is the failure that is silent.
        # A conflict announces itself in the report; an unmatched holdout entry
        # announces nothing and leaves a file it was written to protect exposed.
        return 4
    if report.conflicting:
        return 3
    return 0 if not report.missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
