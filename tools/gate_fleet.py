# SPDX-License-Identifier: MIT OR Apache-2.0
"""Do the copies of `spdx_gate.py` still agree about their own logic?

    python tools/gate_fleet.py <repo> <repo> ...

⚠️ BUILT FROM A DETECTION THAT HAD NO RED TO CHASE. `spdx_gate.py` is deployed
in four repositories. On 2026-09-11 three of them reported 29 self-test controls
and one reported 18 - and ALL FOUR PRINTED "PASS". There was no failure
anywhere. The signal was entirely in the comparison BETWEEN deployments, which
is a query nobody runs because each copy looks fine on its own.

The cause was a squash-merge sequencing gap: a fix went to a branch after the
PR had merged from it, so one repo kept a `rsplit(".")` extension derivation
that FABRICATES an extension from a dot in a directory name, feeding the
population check a member of the population it is checking.

> ⚠️ WHEREVER ONE INSTRUMENT IS COPIED INTO N PLACES, THE FLEET'S OWN
> DISAGREEMENT IS A DETECTOR THAT NO SINGLE DEPLOYMENT CAN PROVIDE.

WHAT THIS COMPARES, AND WHY IT IS THE AST AND NOT THE TEXT:

Comparing file text would be all noise - every copy legitimately carries
different prose. `vulkane`'s holdout comment discusses Khronos's `vk.xml`;
`synapse`'s discusses five empty `.rs` files. Those SHOULD differ.

⚠️ SO IT COMPARES THE PARSED STRUCTURE, WHICH HAS NO COMMENTS IN IT AT ALL.
Two functions with identical logic and different commentary are equal here; two
with the same commentary and one changed operator are not. That is exactly the
axis the defect travelled on.

WHAT IS ALLOWED TO DIFFER, DECLARED BY NAME:

Module-level constants are per-repo by design - `EXTENSIONS`, `MINIMUM_FILES`,
`HOLDOUT`, `NOT_STAMPED`, `COPYRIGHT_ACKNOWLEDGED`. They are not compared.
⚠️ AND A FUNCTION THAT IS ALLOWED TO DIFFER MUST BE NAMED IN `DIVERGENT` WITH A
REASON, so an unexplained divergence is a finding rather than a shrug.

⚠️ THIS IS A COMPARISON, NOT A RULING. It cannot say WHICH copy is right - only
that they disagree. Deciding is the reader's, and the disagreement is the part
that was invisible.

🔴 AND IT CANNOT RUN IN ANY ONE REPOSITORY'S CI, WHICH IS THE SAME REASON THE
DEFECT WAS INVISIBLE. A checkout of `synapse` contains synapse's gate and
nothing else; the comparison needs all four at once. So this is a tool somebody
RUNS, not a gate that fires - and a tool somebody runs is one that gets
forgotten, which is the failure mode this whole session has been cataloguing.

⚠️ STATED RATHER THAN SOLVED. Closing it properly means one of: a scheduled job
with checkouts of every deployment, or publishing the gate as a package each
repo pins by version - at which point the fleet cannot diverge silently because
the version string says so. Both are larger than tonight, and NAMING THE
TRIGGER is the part that stops this becoming a promise with no state: when a
FIFTH repo gets this gate, hand-copying stops being defensible.

VERIFIED AGAINST THE REAL DEFECT, not a synthetic one. `synapse`'s gate at
`ebcad61c`, the commit before the fix landed, against the three that were
correct:

    MISSING   _git_z() absent from ['synapse_old']
    DIVERGED  _git_z_all() · tracked_sources() · uncovered_extensions()
    LAGGING   self-test counts differ; ['synapse_old'] behind. Every copy PASSes.

`uncovered_extensions()` is exactly where the `rsplit` bug lived.
"""

from __future__ import annotations

import ast
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

GATE = ".github/spdx_gate.py"

#: Functions permitted to differ between deployments, each with the reason.
#: ⚠️ AN ENTRY HERE IS A DECISION ON THE RECORD. An unexplained divergence is a
#: finding; a declared one is a design choice somebody wrote down.
DIVERGENT: dict[str, str] = {
    "main": "vulkane's carries the COPYRIGHT_ACKNOWLEDGED staleness check, and "
            "synapse's carries the empty-file column. Both are repo-specific "
            "features rather than drift.",
    "audit": "synapse skips empty files - five of its .rs files are a single "
             "newline, and requiring a header on them means the gate can never "
             "go green.",
    "report": "synapse prints the empty-file count.",
    "self_test": "the control lists grow per repo as each finds new cases; the "
                 "COUNT is compared separately below, which is the part that "
                 "caught the original defect.",
    "survey_copyright": "vulkane subtracts COPYRIGHT_ACKNOWLEDGED, which only "
                        "it declares.",
}


def functions(path: pathlib.Path) -> dict[str, str]:
    """name -> normalised AST dump, for every top-level function.

    ⚠️ `ast.dump` CARRIES NO COMMENTS AND NO FORMATTING, which is the whole
    point: prose is allowed to differ between deployments and logic is not.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # docstrings are prose too - strip before comparing
            body = list(node.body)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]
            stripped = ast.Module(body=body, type_ignores=[])
            out[node.name] = ast.dump(stripped)
    return out


def control_count(path: pathlib.Path) -> int | None:
    """How many case-tuples `self_test` declares.

    ⚠️ THE NUMBER THAT CAUGHT THE ORIGINAL DEFECT. 18 against 29, with every
    copy printing PASS. It is compared as a NUMBER rather than as logic because
    the lists legitimately grow - what is suspicious is one lagging the others.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "self_test":
            total = 0
            for sub in ast.walk(node):
                if isinstance(sub, ast.List):
                    total += sum(1 for e in sub.elts
                                 if isinstance(e, ast.Tuple))
            return total
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    repos = {}
    for arg in argv:
        path = pathlib.Path(arg) / GATE
        if not path.is_file():
            # ⚠️ Refused, not skipped. A repo silently dropped from the fleet
            # is a deployment nobody is comparing, which is the condition this
            # tool exists to detect.
            print(f"FAIL: {path} does not exist. A deployment that cannot be "
                  f"read cannot be compared, and skipping it would hide "
                  f"exactly what this checks.", file=sys.stderr)
            return 1
        repos[pathlib.Path(arg).name] = path

    parsed = {name: functions(p) for name, p in repos.items()}
    counts = {name: control_count(p) for name, p in repos.items()}

    every = sorted(set().union(*(set(f) for f in parsed.values())))
    findings = []

    for fn in every:
        present = [r for r in parsed if fn in parsed[r]]
        missing = [r for r in parsed if fn not in parsed[r]]
        if missing:
            findings.append(f"  MISSING  {fn}() absent from {missing}, "
                            f"present in {present}")
            continue
        shapes = {parsed[r][fn] for r in present}
        if len(shapes) > 1 and fn not in DIVERGENT:
            groups = {}
            for r in present:
                groups.setdefault(parsed[r][fn], []).append(r)
            split = " vs ".join(str(sorted(v)) for v in groups.values())
            findings.append(f"  DIVERGED  {fn}() differs: {split}")

    print(f"comparing {len(repos)} deployments of {GATE}: {sorted(repos)}")
    print(f"  functions compared: {len(every)}  "
          f"({len(DIVERGENT)} declared divergent and skipped)")
    print(f"  self-test case-tuples: "
          + "  ".join(f"{r}={counts[r]}" for r in sorted(counts)))

    lagging = [r for r, c in counts.items()
               if c is not None and c < max(x for x in counts.values() if x)]
    if lagging:
        # ⚠️ NOT AN ERROR BY ITSELF - a repo can legitimately have fewer cases.
        # It is reported because it is the signal that worked, and because
        # every copy printing PASS is what made it invisible.
        findings.append(f"  LAGGING   self-test counts differ; {lagging} "
                        f"behind the highest. Every copy still prints PASS.")

    for line in findings:
        print(line)
    if not findings:
        print("  all deployments agree on every compared function.")
    print()
    print("⚠️ THIS IS A COMPARISON, NOT A RULING. It says the copies disagree,")
    print("   never which one is right - and the disagreement is the part that")
    print("   no single deployment can report.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
