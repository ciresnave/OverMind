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


# (importer, imported) at origin/main cbeaba6, measured 2026-10-08 with this
# file's own extractor. Phase 0 changes nothing: a new edge fails until it is
# added here on purpose, in review. Later phases move this toward spec §1.
ALLOWED: frozenset[tuple[str, str]] = frozenset({
    ("agent", "fork"),          # agent.py:50 (TYPE_CHECKING), :280 (lazy, inside run_agent)
    ("agent", "gate"),          # agent.py:46
    ("agent", "providers"),     # agent.py:47
    ("dispatch", "agent"),      # dispatch.py:37
    ("dispatch", "gate"),       # dispatch.py:38
    ("dispatch_mcp", "lanework"),    # dispatch_mcp.py:57
    ("dispatch_mcp", "ledger"),      # dispatch_mcp.py:56 (`from . import ledger`)
    ("dispatch_mcp", "quota"),       # dispatch_mcp.py:58
    ("dispatch_mcp", "repo_probe"),  # dispatch_mcp.py:59
    ("fork", "agent"),          # fork.py:23 (with agent->fork: today's one cycle)
    ("fork", "gate"),           # fork.py:24
    ("fork", "providers"),      # fork.py:26
    ("lanework", "agent"),      # lanework.py:63
    ("lanework", "gate"),       # lanework.py:64
    ("lanework", "outcome"),    # lanework.py:68
    ("lanework", "providers"),  # lanework.py:69
    ("lanework", "quota"),      # lanework.py:70
    ("providers", "quota"),     # providers.py:61
})

# The modules this test knows. A new module must be added here AND its edges
# reviewed into ALLOWED; otherwise the control below fails, naming it.
MODULES: frozenset[str] = frozenset({
    "agent", "dispatch", "dispatch_mcp", "fork", "gate", "lanework",
    "ledger", "mcp_tools", "outcome", "providers", "quota", "repo_probe",
    # the harness subpackage (plan P3.1): "pkg/mod" names, scanned below
    "harness/__init__", "harness/config", "harness/manifest",
    "harness/plugin_api", "harness/primitives", "harness/pipeline", "harness/gate_plugin",
    "harness/session", "harness/session_main", "harness/registry", "harness/proctree",
})

# Edges INSIDE the harness package, plus edges from it to the flat modules.
# Only `gate_plugin` (the Gate adapter) reaches a flat module, and only `gate`:
# the pipeline and the primitives import nothing outside the package.
HARNESS_ALLOWED: frozenset[tuple[str, str]] = frozenset({
    ("harness/manifest", "harness/config"),   # manifest.py: `from .config import ...`
    ("harness/primitives", "harness/plugin_api"),
    ("harness/pipeline", "harness/config"),
    ("harness/pipeline", "harness/manifest"),
    ("harness/pipeline", "harness/plugin_api"),
    ("harness/pipeline", "harness/primitives"),
    ("harness/gate_plugin", "harness/plugin_api"),
    ("harness/gate_plugin", "gate"),          # the Gate adapter: the one place harness meets a flat module
    ("harness/session", "harness/config"),
    ("harness/session", "harness/manifest"),
    ("harness/session", "harness/proctree"),
    ("harness/primitives", "harness/proctree"),
    ("harness/session_main", "harness/config"),
    ("harness/session_main", "harness/pipeline"),
    ("harness/session_main", "harness/plugin_api"),
    ("harness/registry", "harness/plugin_api"),
})


def harness_edges() -> set[tuple[str, str]]:
    """Every overmind import made by a module under src/overmind/harness/,
    resolved to a flat module name or a "harness/x" name. Relative levels are
    resolved against the file's own package: `.x` is a sibling, `..x` is flat.
    A form this cannot resolve (level 3 or more) raises rather than being skipped."""
    def name_of(dotted: str, imported: tuple[str, ...] = ()) -> list[str]:
        """`dotted` is a path under `overmind` ("" is the package itself)."""
        parts = [x for x in dotted.split(".") if x]
        if not parts:
            return [("harness/__init__" if n == "harness" else n) for n in imported]
        if parts[0] != "harness":
            return [parts[0]]
        if len(parts) > 1:
            return ["harness/" + parts[1]]
        return ["harness/" + n if (SRC / "harness" / (n + ".py")).exists() else "harness/__init__"
                for n in imported] or ["harness/__init__"]

    edges: set[tuple[str, str]] = set()
    for path in sorted((SRC / "harness").glob("*.py")):
        own = "harness/" + path.stem
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names = tuple(a.name for a in node.names)
                mod = node.module or ""
                if node.level == 0:
                    parts = mod.split(".")
                    if parts[0] == "overmind":
                        targets = name_of(".".join(parts[1:]), names)
                elif node.level == 1:                      # sibling in harness/
                    targets = name_of("harness." + mod if mod else "harness", names)
                elif node.level == 2:                      # a flat overmind module
                    targets = name_of(mod, names)
                else:
                    raise AssertionError(f"{own}: unsupported relative import level {node.level}")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    parts = a.name.split(".")
                    if parts[0] == "overmind" and len(parts) > 1:
                        targets += name_of(".".join(parts[1:]))
            for t in targets:
                if t != own:
                    edges.add((own, t))
    return edges


def actual_edges() -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for path in sorted(SRC.glob("*.py")):
        own = path.stem
        for imported in overmind_imports(path.read_text(encoding="utf-8"), own):
            edges.add((own, imported))
    return edges


class TestImportDirection(unittest.TestCase):
    def test_the_module_set_is_the_known_one(self):
        """Positive control: an empty or moved src/overmind would give zero
        edges and an empty set equal to an empty ALLOWED. This fails instead."""
        # rglob: a module in a subpackage must fail here ("sub/x"), because the
        # edge scan below is flat and would otherwise never see its imports.
        found = {p.relative_to(SRC).with_suffix("").as_posix() for p in SRC.rglob("*.py")}
        self.assertEqual(found, set(MODULES),
                         f"new: {sorted(found - MODULES)}, gone: {sorted(MODULES - found)}")

    def test_harness_edges_are_the_known_ones(self):
        """The flat scan is `SRC.glob("*.py")` and never sees the subpackage, so
        it needs its own scan (and the allow-list can be empty only on purpose)."""
        actual = harness_edges()
        self.assertEqual(sorted(actual - HARNESS_ALLOWED), [], "new harness import edge")
        self.assertEqual(sorted(HARNESS_ALLOWED - actual), [], "allowed harness edge that no longer exists")

    def test_harness_extractor_on_known_sources(self):
        """Test of the test: the resolver on sources whose answer is known."""
        import tempfile
        cases = {
            "from .config import X\n": {("harness/manifest", "harness/config")},
            "from ..gate import Gate\n": {("harness/manifest", "gate")},
            "from overmind.quota import Q\n": {("harness/manifest", "quota")},
            "import overmind.harness.config\n": {("harness/manifest", "harness/config")},
            "from . import config\n": {("harness/manifest", "harness/config")},
            "from overmind.harness import config\n": {("harness/manifest", "harness/config")},
            "from overmind import harness\n": {("harness/manifest", "harness/__init__")},
            "import overmind.harness\n": {("harness/manifest", "harness/__init__")},
            "from ..harness.config import X\n": {("harness/manifest", "harness/config")},
            "from .. import gate, quota\n": {("harness/manifest", "gate"), ("harness/manifest", "quota")},
            "from overmind import gate\n": {("harness/manifest", "gate")},
            "import json\nfrom typing import Any\n": set(),
        }
        for src, want in cases.items():
            with tempfile.TemporaryDirectory() as tmp:
                global SRC
                saved = SRC
                try:
                    (Path(tmp) / "harness").mkdir()
                    (Path(tmp) / "harness" / "config.py").write_text("", encoding="utf-8")
                    (Path(tmp) / "harness" / "manifest.py").write_text(src, encoding="utf-8")
                    SRC = Path(tmp)
                    self.assertEqual(harness_edges(), want, src)
                finally:
                    SRC = saved

    def test_no_import_edge_outside_the_allow_list(self):
        actual = actual_edges()
        self.assertTrue(actual, "no edges found at all: the extractor or SRC is wrong")
        new = sorted(actual - ALLOWED)
        gone = sorted(ALLOWED - actual)
        self.assertEqual(new, [], f"import edges not on the allow-list: {new}")
        self.assertEqual(gone, [], f"allowed edges that no longer exist (remove them): {gone}")


if __name__ == "__main__":
    unittest.main()
