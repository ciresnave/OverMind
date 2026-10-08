# SPDX-License-Identifier: MIT OR Apache-2.0
"""Which overmind module may import which (ARCHITECTURE-LAYERS-SPEC.md §1, §9 Phase 0).

Phase 0 freezes TODAY's graph: ALLOWED is exactly the edges at origin/main cbeaba6.
A new edge fails this test until someone adds it here on purpose, in review.

Limit: `ast` sees import statements, not `importlib.import_module("overmind.x")`
or `__import__`. Those are the static scan's job (spec §3), not this test's.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "overmind"


def overmind_imports(source: str, own: str) -> set[str]:
    """Names of the overmind modules `source` imports, anywhere in the file
    (module level, inside functions, under TYPE_CHECKING), excluding `own`."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1:
                if node.module:                      # from .gate import X
                    found.add(node.module.split(".")[0])
                else:                                # from . import ledger
                    found.update(a.name.split(".")[0] for a in node.names)
            elif node.level == 0 and node.module:
                parts = node.module.split(".")
                if parts[0] == "overmind":
                    if len(parts) > 1:               # from overmind.gate import X
                        found.add(parts[1])
                    else:                            # from overmind import ledger
                        found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:                     # import overmind.gate [as g]
                parts = a.name.split(".")
                if parts[0] == "overmind" and len(parts) > 1:
                    found.add(parts[1])
    found.discard(own)
    return found


class TestExtractor(unittest.TestCase):
    """The extractor, on sources whose answer is known: a test of the test."""

    def test_relative_from_import(self):
        self.assertEqual(overmind_imports("from .gate import Gate\n", "agent"), {"gate"})

    def test_relative_name_import(self):
        # dispatch_mcp.py:56 is `from . import ledger`
        self.assertEqual(overmind_imports("from . import ledger, quota\n", "x"),
                         {"ledger", "quota"})

    def test_absolute_forms(self):
        src = ("import overmind.gate\n"
               "import overmind.quota as q\n"
               "from overmind import ledger\n"
               "from overmind.providers import X\n")
        self.assertEqual(overmind_imports(src, "x"), {"gate", "quota", "ledger", "providers"})

    def test_type_checking_and_function_level_imports_count(self):
        # agent.py:50 (TYPE_CHECKING) and agent.py:280 (inside run_agent)
        src = ("from typing import TYPE_CHECKING\n"
               "if TYPE_CHECKING:\n"
               "    from .fork import ForkConfig\n"
               "def f():\n"
               "    from .outcome import claims_success\n")
        self.assertEqual(overmind_imports(src, "agent"), {"fork", "outcome"})

    def test_other_packages_and_self_are_ignored(self):
        src = ("import json\nfrom typing import Any\nimport overmindish\n"
               "from ..elsewhere import y\nfrom .agent import z\n")
        self.assertEqual(overmind_imports(src, "agent"), set())


if __name__ == "__main__":
    unittest.main()
