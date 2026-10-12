# SPDX-License-Identifier: MIT OR Apache-2.0
"""One version number per project (CireSnave rule, CIRESNAVE-EXPECTATIONS §2.2).

pyproject.toml (the Python `overmind` package) and `[workspace.package] version`
must be the same string. Every workspace member must inherit the workspace
version rather than carry its own. `lane-state` lives in its own repo and is
taken from crates.io by version, so it carries ITS number, never this one. Runs in the CI test matrix, so a bump that touches one place fails here.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


class VersionLockstep(unittest.TestCase):
    def setUp(self) -> None:
        self.cargo = load(ROOT / "Cargo.toml")
        self.pyproject = load(ROOT / "pyproject.toml")

    def test_pyproject_matches_workspace(self) -> None:
        self.assertEqual(
            self.pyproject["project"]["version"],
            self.cargo["workspace"]["package"]["version"],
            "pyproject.toml and [workspace.package] version must be one number",
        )

    def test_lane_state_comes_from_crates_io(self) -> None:
        # CireSnave 2026-10-07: cross-repo dependencies are crates.io versions,
        # never a path on this laptop.
        deps = self.cargo["workspace"].get("dependencies", {})
        self.assertIn("lane-state", deps, "the lane-state pin is missing: the check is vacuous")
        self.assertNotIn("path", deps["lane-state"])
        for member in self.cargo["workspace"]["members"]:
            self.assertNotIn("lane-state", member, "lane-state is no longer a workspace member")
            dep = load(ROOT / member / "Cargo.toml").get("dependencies", {}).get("lane-state")
            if isinstance(dep, dict):
                self.assertNotIn("path", dep, f"{member} must not path-depend on lane-state")

    def test_members_inherit_workspace_version(self) -> None:
        members = self.cargo["workspace"]["members"]
        self.assertTrue(members, "no workspace members found: the check is vacuous")
        for member in members:
            version = load(ROOT / member / "Cargo.toml")["package"]["version"]
            self.assertEqual(
                version, {"workspace": True}, f"{member} must use version.workspace = true"
            )


if __name__ == "__main__":
    unittest.main()
