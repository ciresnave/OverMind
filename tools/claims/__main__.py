# SPDX-License-Identifier: MIT OR Apache-2.0
"""Render STALE-CLAIMS.md from a claims.json file.

    python -m tools.claims <claims.json path> <output STALE-CLAIMS.md path>

The pipeline is three pure steps: `load_claims` reads and validates the file,
`classify_claim` decides each claim's status, `render_stale_claims` writes the
Markdown. This module only wires them together - it holds no judgement of its
own.

⚠️ `classify_claim` LIVES IN `tools.claims.checker`, WHICH IS A SEPARATE
MODULE. It is imported by name only; this file does not contain it and must
not grow a copy. A stub here would let a half-built pipeline report success
forever, which is the exact defect this project exists to remove.
"""

from __future__ import annotations

import argparse
import pathlib

from tools.claims.checker import classify_claim
from tools.claims.registry import load_claims
from tools.claims.render import render_stale_claims


def run(claims_path: str, output_path: str) -> str:
    """Load, classify and render; return the text written to `output_path`."""
    claims = load_claims(claims_path)

    results: dict[str, dict] = {}
    for claim in claims:
        results[claim["id"]] = classify_claim(claim, claim["repo"])

    text = render_stale_claims(claims, results)
    pathlib.Path(output_path).write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.claims",
        description="Render STALE-CLAIMS.md from a claims.json file.",
    )
    parser.add_argument("claims", help="path to the claims.json file")
    parser.add_argument("output", help="path of the STALE-CLAIMS.md to write")
    args = parser.parse_args(argv)
    run(args.claims, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
