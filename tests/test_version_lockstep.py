# SPDX-License-Identifier: MIT OR Apache-2.0
"""One version number per project (CireSnave rule, CIRESNAVE-EXPECTATIONS §2.2).

pyproject.toml (the Python `overmind` package), `[workspace.package] version`
and the `[workspace.dependencies] lane-state` pin must all be the same string.
Every workspace member must inherit the workspace version rather than carry its
own. Runs in the CI test matrix, so a bump that touches one place fails here.
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

    def test_lane_state_pin_matches_workspace(self) -> None:
        pin = self.cargo["workspace"]["dependencies"]["lane-state"]["version"]
        self.assertEqual(pin, self.cargo["workspace"]["package"]["version"])

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
