"""Three pre-flight checks a repo must pass before an SPDX sweep touches it.

    python tools/preflight.py <repo> [<repo> ...]

⚠️ EACH CHECK CATCHES A DIFFERENT CLASS, AND EACH WAS LEARNED FROM A REAL MISS.

  1  COPYRIGHT NOTICES IN SOURCE  -> a vendored single-licence file.
     `fuel` swept 795 files and stamped `MIT OR Apache-2.0` onto
     `fuel-examples/src/bs1770.rs`, a verbatim Apache-2.0-ONLY third-party work.
     That is a FALSE LICENCE GRANT - an assertion nobody made - corrected in
     ef59fc23.

  2  AN EXISTING SPDX HEADER      -> skip, never append.
     A parser that truncates `MIT OR Apache-2.0` to `MIT OR Apache` sees every
     already-correct file as wrong, and pass two writes 2,200 duplicates while
     pass one looked flawless.

  3  ROOT LICENCE vs EVERY MANIFEST -> a repo-level conflict.
     ⚠️ THE ONE THAT WOULD PRODUCE THE LARGEST ERROR, BECAUSE IT IS SILENT.
     A repo can have a CC0-1.0 root LICENSE and a manifest declaring
     `MIT OR Apache-2.0`. Checks 1 and 2 both pass it CLEAN. Stamping its files
     asserts MIT-or-Apache over text in a repo whose LICENSE dedicates to the
     public domain. Nothing in any source file signals this.

⚠️ A DISAGREEMENT IS NOT A FINDING OF DRIFT. A repo can be deliberately split -
CC0 for a standard's text, MIT/Apache for its implementation, which is normal
and correct for a standards project. Split and drift produce OPPOSITE actions,
and only the owner can say which this is. This script reports; it never rules.

⚠️ AND EVERY CHECK REPORTS UNKNOWN RATHER THAN CLEAN WHEN IT CANNOT SEE. A repo
with no LICENSE file has not passed check 3 - it has failed to be measured, and
"no conflict found" is what both look like from here.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import spdx  # noqa: E402  - the SWEEPER's parser, deliberately the same one

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

SOURCE_GLOBS = ("*.rs", "*.py", "*.ts", "*.tsx", "*.js", "*.jsx", "*.go",
                "*.c", "*.h", "*.cpp", "*.hpp", "*.java", "*.rb", "*.sh")
MANIFESTS = ("Cargo.toml", "package.json", "pyproject.toml")

#: ⚠️ Recognised for REPORTING ONLY. A licence not on this list is UNRECOGNISED,
#: and unrecognised is a reason to ask, never a reason to proceed.
LICENCE_SIGNATURES = (
    ("CC0-1.0", ("creative commons legal code", "cc0 1.0 universal")),
    ("Apache-2.0", ("apache license", "version 2.0, january 2004")),
    ("MIT", ("mit license", "permission is hereby granted, free of charge")),
    ("BSD-3-Clause", ("redistribution and use in source and binary forms",)),
    ("GPL-3.0", ("gnu general public license", "version 3, 29 june 2007")),
    ("MPL-2.0", ("mozilla public license version 2.0",)),
    ("Unlicense", ("this is free and unencumbered software released into the public domain",)),
)


def _git(repo: pathlib.Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    # ⚠️ stderr is JOINED, never discarded. `git grep` exits 1 for "no matches"
    # and 128 for "not a repository", and counting lines cannot tell them apart.
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def check_copyright(repo: pathlib.Path) -> dict:
    """Check 1: source files carrying a copyright notice."""
    code, out = _git(repo, "grep", "-l", "-i", "copyright", "--", *SOURCE_GLOBS)
    if code not in (0, 1):
        return {"status": "UNKNOWN", "detail": out.strip()[:160], "hits": []}
    hits = [line for line in out.splitlines() if line.strip()]
    return {"status": "HITS" if hits else "clean", "hits": hits, "detail": ""}


def check_existing_spdx(repo: pathlib.Path) -> dict:
    """Check 2: files that already declare an identifier, and which spellings.

    ⚠️ USES THE SWEEPER'S OWN PARSER, ON THE SWEEPER'S OWN HEADER WINDOW. A
    `git grep` for the marker reads the whole file and matches any line that
    mentions it - so run against THIS repo it reported spellings like
    `'GPL-3.0-only\\nprint(1)\\n"'`, which is a Python test fixture, and
    `'MIT OR Apache-2.0")'`, which is the tool's own string constant.

    Same family as the checker that counted itself and reported 2/42 where the
    truth was 0/42. ⚠️ AND A CHECKER THAT SEES A DIFFERENT POPULATION FROM THE
    SWEEPER IS ITS OWN BUG CLASS, whichever of the two is right.
    """
    code, out = _git(repo, "ls-files", "--", *SOURCE_GLOBS)
    if code != 0:
        return {"status": "UNKNOWN", "detail": out.strip()[:160], "spellings": {}}
    spellings: dict[str, int] = {}
    for rel in out.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        try:
            text = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found = spdx.existing_identifier(text)
        if found:
            spellings[found] = spellings.get(found, 0) + 1
    return {"status": "PRESENT" if spellings else "none", "spellings": spellings,
            "detail": ""}


def identify_licence(text: str) -> str:
    low = text.lower()
    for name, needles in LICENCE_SIGNATURES:
        if any(n in low for n in needles):
            return name
    return "UNRECOGNISED"


def check_root_vs_manifests(repo: pathlib.Path) -> dict:
    """Check 3: the root LICENCE file(s) against every manifest's declaration."""
    root_files = sorted(p for p in repo.glob("LICEN[SC]E*") if p.is_file())
    roots = {}
    for path in root_files:
        try:
            roots[path.name] = identify_licence(
                path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            roots[path.name] = "UNREADABLE"

    declared: dict[str, str] = {}
    patterns = list(MANIFESTS) + [f"*/{m}" for m in MANIFESTS] + [f"**/{m}" for m in MANIFESTS]
    code, out = _git(repo, "grep", "-n", "-E",
                     r'^\s*"?license"?\s*[:=]', "--", *patterns)
    if code not in (0, 1):
        return {"status": "UNKNOWN", "roots": roots, "declared": {},
                "detail": out.strip()[:160]}
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        match = re.search(r'[:=]\s*"?([^",\n]+)"?', parts[2])
        if match:
            found = match.group(1).strip().strip('"').strip()
            # ⚠️ A table-valued `license = { file = "..." }` is a declaration we
            # cannot read as an identifier. Say so rather than guessing it away.
            if found.startswith("{"):
                found = "UNPARSED-TABLE"
            declared[parts[0]] = found

    if not root_files:
        # ⚠️ NOT "clean". No root licence means the question could not be asked.
        return {"status": "UNKNOWN", "roots": {}, "declared": declared,
                "detail": "no root LICENSE file - check 3 could not run"}

    # ⚠️ EQUALITY OF THE GRANT SET, NOT INTERSECTION. The first version asked
    # whether a manifest's licences OVERLAPPED the root's, and called
    # `auth-framework` AGREE: root is MIT + Apache-2.0, `Cargo.toml` says
    # `MIT OR Apache-2.0`, and `sdks/python/pyproject.toml` says `MIT` ALONE.
    # {MIT} intersects {MIT, Apache-2.0}, so it passed - and the sweep would
    # have stamped 10 SDK files with an APACHE GRANT THEIR OWN MANIFEST DOES
    # NOT MAKE. A NARROWER SUBTREE IS THE EXACT CLASS CHECK 3 EXISTS TO CATCH,
    # and an overlap test is blind to it in the permissive direction.
    root_names = set(roots.values())
    grants = {}
    conflict = False
    for where, value in declared.items():
        if value == "UNPARSED-TABLE":
            conflict = True
            continue
        parts = frozenset(p.strip() for p in re.split(r"\s+OR\s+|\s+or\s+", value))
        grants[where] = parts
        if parts != root_names:
            conflict = True
    # ⚠️ Manifests disagreeing with EACH OTHER is a conflict even where every
    # one of them overlaps the root.
    if len(set(grants.values())) > 1:
        conflict = True
    status = "CONFLICT" if conflict else ("AGREE" if declared else "UNKNOWN")
    detail = "" if declared else "no manifest declares a license field"
    return {"status": status, "roots": roots, "declared": declared,
            "grants": {k: sorted(v) for k, v in grants.items()}, "detail": detail}


def count_sources(repo: pathlib.Path) -> int:
    code, out = _git(repo, "ls-files", "--", *SOURCE_GLOBS)
    return len([line for line in out.splitlines() if line.strip()]) if code == 0 else -1


def report(repo: pathlib.Path) -> dict:
    one = check_copyright(repo)
    two = check_existing_spdx(repo)
    three = check_root_vs_manifests(repo)
    total = count_sources(repo)
    sweepable = (one["status"] == "clean" and three["status"] == "AGREE")
    return {"repo": repo.name, "sources": total, "1": one, "2": two, "3": three,
            "sweepable": sweepable}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    results = [report(pathlib.Path(a)) for a in args]

    for r in results:
        print(f"\n===== {r['repo']}  ({r['sources']} source files) =====")
        one, two, three = r["1"], r["2"], r["3"]
        print(f"  1 copyright notices : {one['status']}"
              + (f"  ({len(one['hits'])} files)" if one["hits"] else "")
              + (f"  {one['detail']}" if one["detail"] else ""))
        for hit in one["hits"][:12]:
            print(f"      {hit}")
        if len(one["hits"]) > 12:
            print(f"      ... and {len(one['hits']) - 12} more")
        print(f"  2 existing SPDX     : {two['status']}")
        for spelling, count in sorted(two["spellings"].items(), key=lambda kv: -kv[1]):
            print(f"      {count:>6}  {spelling!r}")
        print(f"  3 root vs manifests : {three['status']}"
              + (f"  {three['detail']}" if three["detail"] else ""))
        for name, kind in three["roots"].items():
            print(f"      root {name}: {kind}")
        for where, value in sorted(three["declared"].items()):
            print(f"      {where}: {value!r}")
        print(f"  -> {'SWEEPABLE' if r['sweepable'] else 'NOT SWEEPABLE WITHOUT A RULING'}")

    print("\n" + "=" * 70)
    ok = [r["repo"] for r in results if r["sweepable"]]
    held = [r["repo"] for r in results if not r["sweepable"]]
    print(f"sweepable now: {ok}")
    print(f"needs a human licence decision first: {held}")
    print()
    print("⚠️ SWEEPABLE means checks 1 and 3 found nothing to stop it. It is NOT")
    print("   a ruling that the licence is right - that is the owner's, and this")
    print("   script cannot make one. UNKNOWN anywhere means NOT MEASURED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
