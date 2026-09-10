"""Five pre-flight checks to run before an SPDX sweep touches a repo.

    python tools/preflight.py <repo> [<repo> ...] [--offline]

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

  4  A PUBLISHED MEMBER ON A SERVED VERSION -> a sequencing problem, and
     CHECKS 1-3 CANNOT SEE IT BECAUSE IT IS NOT ABOUT LICENCES AT ALL. A sweep
     is a content change to every file with NO version change: a consumer
     resolving `foo 0.1.1` gets the registry's tarball while the repo's own CI
     tests different bytes, and both are green about different objects.

     ⚠️ THE HAZARD IS NOT THAT A REPO HAS A GATE THAT NOTICES. The sweep moves
     content that is ALREADY PUBLISHED; a repo with no such gate takes the same
     collision and merely cannot see it. `vulkane` is not unusual - it is the
     only one on this list with eyes.

     🔴 RETRACTED, WITHIN THE HOUR, BY THE MEASUREMENT BELOW. This file said
     that "every publishing repo will hit this" was the plausible
     generalisation AND WAS WRONG, on the strength of vulkane's 1-of-4. Then:

         vulkane      1 of 4 members served    <- the accident
         Unpopped     4 of 4
         synapse      1 of 1
         baracuda    69 of 70
         mlmf, lightbulb   0 of 1 - both on unpublished versions

     vulkane's three clean rows were an ACCIDENT OF TIMING: that lane ran a
     post-publish bump pass the day before. ⚠️ I GENERALISED FROM THE ONE REPO
     I HAD MEASURED, WHICH HAPPENED TO BE THE LEAST AFFECTED, AND THE
     GENERALISATION POINTED THE COMFORTABLE WAY.

     ⚠️ NOR IS IT DEFERRABLE. The collision exists FROM THE MERGE: main's own
     armed gate finds the diverged member and MAIN GOES RED, blocking every
     subsequent PR in that repo. The bump belongs IN the sweep PR.

     ⚠️ CHECK 4 STILL DOES NOT BLOCK A SWEEP. The sweep is correct either way;
     what it changes is what must land ALONGSIDE.

  5  CI TYING A VERSION TO A CHANGELOG ENTRY -> check 4's remedy costs TWO
     edits, not one. On `vulkane` a bump alone turns the divergence gate green
     and trips the changelog gate. ⚠️ A REMEDY THAT TRADES ONE RED FOR ANOTHER
     IS WHAT YOU SHIP WHEN YOU STOP AT THE FIRST GREEN.

⚠️ A DISAGREEMENT IS NOT A FINDING OF DRIFT. A repo can be deliberately split -
CC0 for a standard's text, MIT/Apache for its implementation, which is normal
and correct for a standards project. Split and drift produce OPPOSITE actions,
and only the owner can say which this is. This script reports; it never rules.

⚠️ AND EVERY CHECK REPORTS UNKNOWN RATHER THAN CLEAN WHEN IT CANNOT SEE. A repo
with no LICENSE file has not passed check 3 - it has failed to be measured, and
"no conflict found" is what both look like from here. Check 4 does the same when
the registry is unreachable: offline and "nothing published" are the same empty
answer, and the empty answer is the one that says go ahead.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import spdx  # noqa: E402  - the SWEEPER's parser, deliberately the same one

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# ⚠️ AN EXTENSION THIS LIST OMITS IS A POPULATION THE REPORT NEVER COUNTED, and
# the omission shows up as a CLEANER result, not a smaller one. `Unpopped` holds
# 32 `.cu` files and the first version of this list had no `.cu`: check 1 said
# "clean" over a population that excluded a third of the repo's source. Re-run
# with `.cu` included it is still clean - but that is now a measurement rather
# than an artefact of what I happened to type.
SOURCE_GLOBS = ("*.rs", "*.py", "*.ts", "*.tsx", "*.js", "*.jsx", "*.go",
                "*.c", "*.h", "*.cpp", "*.hpp", "*.java", "*.rb", "*.sh",
                "*.cu", "*.cuh", "*.cl", "*.comp", "*.vert", "*.frag", "*.glsl",
                "*.wgsl", "*.hlsl", "*.metal", "*.zig", "*.kt", "*.swift", "*.cs")
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


def check_served_versions(repo: pathlib.Path, offline: bool = False) -> dict:
    """Check 4: does this repo publish a crate sitting on a SERVED version?

    ⚠️ A SWEEP IS A CONTENT CHANGE TO EVERY FILE WITH NO VERSION CHANGE, and
    that is precisely the condition a published-divergence gate exists to catch:
    a consumer resolving `foo 0.1.1` gets the registry's tarball while the
    repo's own CI tests different bytes, and both are green about different
    objects.

    ⚠️ I WROTE HERE THAT "EVERY PUBLISHING REPO WILL HIT THIS" WAS THE PLAUSIBLE
    GENERALISATION AND WAS WRONG. That was itself wrong, and measuring the rest
    of the portfolio falsified it within the hour:

        vulkane      1 of 4 members served
        Unpopped     4 of 4
        synapse      1 of 1
        baracuda    71 of 72
        mlmf, lightbulb   0 - both on unpublished versions

    vulkane's three clean rows were an ACCIDENT OF TIMING: that lane had run a
    post-publish bump pass the day before. ⚠️ I GENERALISED FROM THE ONE REPO I
    HAD MEASURED, WHICH HAPPENED TO BE THE LEAST AFFECTED ONE, AND THE
    GENERALISATION POINTED THE COMFORTABLE WAY.

    ⚠️ AND THE HAZARD IS NOT THAT A REPO HAS A GATE THAT NOTICES. The sweep moves
    content that is already published; a repo without such a gate takes the same
    collision and merely cannot see it. vulkane is not unusual - it is the only
    one with eyes.

    ⚠️ NOR IS IT DEFERRABLE. The collision exists FROM THE MERGE: main's own
    armed gate finds the diverged crate and main goes red, blocking every
    subsequent PR in that repo. The bump belongs in the sweep PR.

    ⚠️ OFFLINE REPORTS UNKNOWN, NEVER CLEAN. The registry being unreachable and
    the registry having nothing are the same empty answer here, and the empty
    answer is the one that says "go ahead".
    """
    # ⚠️ FROM GIT'S INDEX, NOT THE FILESYSTEM. `rglob` found 219 Cargo.toml
    # files under `baracuda/.claude` - OTHER LANES' WORKTREES of the same repo,
    # sitting at two different versions - and reported their crates as this
    # repo's, listing `baracuda-core` three times at alpha.78 and again at
    # alpha.79. A tracked-file listing cannot see another worktree's copy.
    code, out = _git(repo, "ls-files", "--", "Cargo.toml", "*/Cargo.toml", "**/Cargo.toml")
    if code != 0:
        return {"status": "UNKNOWN", "served": [], "unpublished": [],
                "detail": f"git ls-files failed: {out.strip()[:120]}"}
    crates: list[tuple[str, str]] = []
    for rel in out.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        path = repo / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r"^\s*publish\s*=\s*false", text, re.M):
            continue
        name = re.search(r'^\s*name\s*=\s*"([^"]+)"', text, re.M)
        version = re.search(r'^\s*version\s*=\s*"([^"]+)"', text, re.M)
        if name and version:
            crates.append((name.group(1), version.group(1)))
    crates = sorted(set(crates))

    if not crates:
        return {"status": "n/a", "served": [], "unpublished": [], "detail":
                "no publishable Cargo.toml found"}
    if offline:
        return {"status": "UNKNOWN", "served": [], "unpublished": [],
                "detail": "--offline: the registry was not asked"}

    served, unpublished, unknown = [], [], []
    for name, version in crates:
        url = f"https://crates.io/api/v1/crates/{name}/{version}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "ciresnave-spdx-preflight (github.com/ciresnave)"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                served.append((name, version)) if resp.status == 200 else None
        except urllib.error.HTTPError as exc:
            (unpublished if exc.code == 404 else unknown).append((name, version))
        except Exception:                                    # noqa: BLE001
            unknown.append((name, version))

    if unknown:
        # ⚠️ A crate we could not ask about is not a crate that is safe.
        return {"status": "UNKNOWN", "served": served, "unpublished": unpublished,
                "detail": f"could not reach the registry for {unknown}"}
    return {"status": "SERVED" if served else "clear", "served": served,
            "unpublished": unpublished, "detail": ""}


def check_changelog_coupling(repo: pathlib.Path) -> dict:
    """Check 5: does CI tie every declared version to a CHANGELOG entry?

    ⚠️ THIS EXISTS BECAUSE A REMEDY THAT TRADES ONE RED FOR ANOTHER IS WHAT YOU
    SHIP WHEN YOU STOP AT THE FIRST GREEN. Check 4's fix is a version bump. On
    `vulkane` the bump alone turns the divergence gate green and trips a SECOND
    gate - the one asserting that every version being shipped has a changelog
    heading - so the naive fix swaps one failure for another.

    Where this fires, EACH BUMP IS TWO EDITS: the manifest and the changelog.

    ⚠️ A REPO WITH NO CHANGELOG GATE IS NOT THEREBY SAFE, it is merely unwatched
    - the same relationship check 4 has to repos with no divergence gate.
    """
    workflows = sorted((repo / ".github" / "workflows").glob("*.y*ml"))
    if not workflows:
        return {"status": "UNKNOWN", "hits": [],
                "detail": "no .github/workflows - check 5 could not run"}
    hits = []
    for path in workflows:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "CHANGELOG" in line and not line.lstrip().startswith("#"):
                hits.append(f"{path.name}:{i}")
    has_file = (repo / "CHANGELOG.md").is_file()
    return {"status": "COUPLED" if hits else "none", "hits": hits[:6],
            "detail": "" if has_file else "no CHANGELOG.md at the root"}


def count_sources(repo: pathlib.Path) -> int:
    code, out = _git(repo, "ls-files", "--", *SOURCE_GLOBS)
    return len([line for line in out.splitlines() if line.strip()]) if code == 0 else -1


def report(repo: pathlib.Path, offline: bool = False) -> dict:
    one = check_copyright(repo)
    two = check_existing_spdx(repo)
    three = check_root_vs_manifests(repo)
    four = check_served_versions(repo, offline=offline)
    five = check_changelog_coupling(repo)
    total = count_sources(repo)
    # ⚠️ Check 4 does NOT block a sweep and is not in `sweepable`. It is a
    # SEQUENCING fact, not a licence fact: the sweep is correct either way, and
    # what it changes is whether the repo needs a version bump alongside it.
    # Folding a scheduling question into a correctness verdict would make the
    # tool refuse work that is perfectly safe to do.
    sweepable = (one["status"] == "clean" and three["status"] == "AGREE")
    return {"repo": repo.name, "sources": total, "1": one, "2": two, "3": three,
            "4": four, "5": five, "sweepable": sweepable}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    offline = "--offline" in args
    args = [a for a in args if a != "--offline"]
    results = [report(pathlib.Path(a), offline=offline) for a in args]

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
        four = r["4"]
        print(f"  4 served versions   : {four['status']}"
              + (f"  {four['detail']}" if four["detail"] else ""))
        for name, version in four["served"]:
            print(f"      🔴 {name} {version} IS SERVED - a content change here "
                  f"needs a version bump")
        for name, version in four["unpublished"]:
            print(f"      {name} {version}: unpublished, no collision possible")
        five = r["5"]
        print(f"  5 changelog coupling: {five['status']}"
              + (f"  ({len(five['hits'])}+ refs)" if five["hits"] else "")
              + (f"  {five['detail']}" if five["detail"] else ""))
        extra = ""
        if four["served"]:
            extra = f"  (+ BUMP {len(four['served'])} MEMBER(S) IN THE SWEEP PR"
            extra += ", EACH A TWO-EDIT BUMP)" if five["status"] == "COUPLED" else ")"
        print(f"  -> {'SWEEPABLE' if r['sweepable'] else 'NOT SWEEPABLE WITHOUT A RULING'}{extra}")

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
