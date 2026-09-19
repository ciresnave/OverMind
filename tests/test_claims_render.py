# SPDX-License-Identifier: MIT OR Apache-2.0
"""Tests for tools.claims registry and rendering.

⚠️ NO TEST HERE IMPORTS `tools.claims.checker` OR `classify_claim`. That
module is built separately and does not exist in this workspace; the render
contract is pinned against the `results` shape it is documented to produce.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.claims.registry import load_claims  # noqa: E402
from tools.claims.render import render_stale_claims  # noqa: E402

WELL_FORMED = [
    {
        "id": "claim-1",
        "file": "src/overmind/dispatch.py",
        "quote": "the gate refuses tool calls deterministically",
        "repo": "OverMind",
        "anchor": "dispatch.py:71",
        "depends_on": [],
    },
    {
        "id": "claim-2",
        "file": "tools/spdx.py",
        "quote": "every source file declares the licence",
        "repo": "OverMind",
        "anchor": "spdx.py:1",
        "depends_on": ["claim-1"],
    },
]


def _write_claims(tmpdir: str, claims: list[dict]) -> str:
    path = os.path.join(tmpdir, "claims.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(claims, handle)
    return path


class TestLoadClaims(unittest.TestCase):
    def test_accepts_a_well_formed_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_claims(tmpdir, WELL_FORMED)
            claims = load_claims(path)
        self.assertEqual(claims, WELL_FORMED)
        self.assertEqual([c["id"] for c in claims], ["claim-1", "claim-2"])

    def test_raises_on_a_missing_required_key(self):
        broken = [dict(WELL_FORMED[0])]
        del broken[0]["anchor"]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_claims(tmpdir, broken)
            with self.assertRaises(ValueError) as ctx:
                load_claims(path)
        self.assertIn("anchor", str(ctx.exception))

    def test_raises_on_an_unknown_extra_key(self):
        broken = [dict(WELL_FORMED[0], extra="surprise")]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_claims(tmpdir, broken)
            with self.assertRaises(ValueError) as ctx:
                load_claims(path)
        self.assertIn("extra", str(ctx.exception))


class TestRenderStaleClaims(unittest.TestCase):
    def test_excludes_an_all_current_claim_list(self):
        results = {
            "claim-1": {"status": "current", "invalidating_commit": None},
            "claim-2": {"status": "current", "invalidating_commit": None},
        }
        text = render_stale_claims(WELL_FORMED, results)
        self.assertEqual(
            text, "# Stale Claims\n\nNo stale, unverified, or orphaned claims.\n"
        )
        self.assertNotIn("claim-1", text)
        self.assertNotIn("claim-2", text)

    def test_includes_a_stale_claim_with_its_invalidating_commit(self):
        results = {
            "claim-1": {"status": "stale", "invalidating_commit": "abc1234"},
        }
        text = render_stale_claims(WELL_FORMED, results)
        self.assertIn(
            "- **claim-1** (OverMind: src/overmind/dispatch.py) - stale since abc1234",
            text,
        )
        self.assertNotIn("claim-2", text)

    def test_includes_an_unverified_claim(self):
        results = {
            "claim-2": {"status": "unverified", "invalidating_commit": None},
        }
        text = render_stale_claims(WELL_FORMED, results)
        self.assertIn(
            "- **claim-2** (OverMind: tools/spdx.py) - unverified: anchor unreachable",
            text,
        )
        self.assertNotIn("claim-1", text)

    def test_includes_an_orphaned_claim(self):
        results = {
            "claim-1": {"status": "orphaned", "invalidating_commit": None},
        }
        text = render_stale_claims(WELL_FORMED, results)
        self.assertIn(
            "- **claim-1** (OverMind: src/overmind/dispatch.py) - "
            "orphaned (no invalidating commit found)",
            text,
        )

    def test_includes_an_orphaned_claim_with_its_invalidating_commit(self):
        results = {
            "claim-1": {"status": "orphaned", "invalidating_commit": "deadbee"},
        }
        text = render_stale_claims(WELL_FORMED, results)
        self.assertIn(
            "- **claim-1** (OverMind: src/overmind/dispatch.py) - orphaned since deadbee",
            text,
        )

    def test_stale_without_commit_says_no_invalidating_commit_found(self):
        results = {
            "claim-1": {"status": "stale", "invalidating_commit": None},
        }
        text = render_stale_claims(WELL_FORMED, results)
        self.assertIn(
            "- **claim-1** (OverMind: src/overmind/dispatch.py) - "
            "stale (no invalidating commit found)",
            text,
        )


if __name__ == "__main__":
    unittest.main()
