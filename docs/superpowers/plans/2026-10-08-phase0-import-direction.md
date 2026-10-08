# Phase 0: Import-Direction Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status: a plan; the spec is approved (board item 143, 2026-10-08, rulings in spec §8a).** It is
> executed as its own PR after this docs PR merges. Written 2026-10-08, against `origin/main`
> `cbeaba6`. Order of the whole split, per the PM: (1) this docs PR; (2) this Phase 0 test PR;
> (3) the shared `lane-state` crate inside OverMind, with `relaunch` moved into lane-restart's library
> and split into `lane-stop` / `lane-start` entry points (spec §8b); one PR at a time, tests first.

**Goal:** A unit test that fails whenever an `overmind` module imports another `overmind` module
along an edge that is not on an explicit allow-list. The allow-list starts as **exactly today's
edges**, so the test freezes the current graph and documents it, and changes nothing else.

**Architecture:**
- One stdlib-only test module, `tests/test_import_direction.py`.
- It parses every `src/overmind/*.py` with `ast` and collects every import of another `overmind`
  module: module-level, function-level and `TYPE_CHECKING`-only, in relative and absolute forms.
- It compares the result with a frozen `ALLOWED` set.
- Positive controls stop it from passing vacuously: the module count, a non-empty edge set, and an
  extractor that is tested on known sources.
- Later phases edit `ALLOWED` toward the layer table in spec §1, one deliberate line at a time.

**Tech Stack:**
- Python ≥3.11, stdlib only: `ast`, `pathlib`, `unittest`. The suite is stdlib-only (ci.yml "Run
  tests" comment).
- The runner is `python -m unittest discover -s tests -v` (ci.yml).

**Spec:** `ARCHITECTURE-LAYERS-SPEC.md` (PR #121). Its §1 is "Enforced import direction", and its §9
Phase 0 is "the import-direction test … with today's actual edges as its allow-list. It documents the
graph and freezes it."

## Global Constraints

- **Nothing moves in Phase 0.** No file under `src/` changes. Only `tests/` gains a file.
- **Stdlib only**, no new dependency (pyproject `dependencies = []`).
- Every source file starts with `# SPDX-License-Identifier: MIT OR Apache-2.0`. The `spdx` CI job
  enforces this.
- CI's test-count floor stays at least 70 (ci.yml "Assert the suite is not empty"). This plan adds
  tests and removes none.
- **Version:** this is the Phase 0 code PR. Per the PM (2026-10-08), its bump also covers #121, the
  architecture spec. The PM allocates the number at gate time; do not edit version files in the PR.
- **Red first:** each test is seen failing for the expected reason before it passes. Then a mutation
  check shows the test catches what it claims to.

## Review Focus

1. **`from . import ledger`** (name form). `dispatch_mcp.py:56` uses it. An extractor that only
   reads `node.module` would miss the edge `dispatch_mcp → ledger`. *Pinned in Task 1, Step 1.*
2. **`TYPE_CHECKING`-only and function-local imports** must count: `agent.py:50` and `agent.py:280`
   both import `fork`. Today's graph has a cycle (`agent ↔ fork`), and dropping either import from the
   count would hide it. *Pinned in Task 1.*
3. **Absolute forms**, `import overmind.gate`, `from overmind import gate` and
   `from overmind.gate import X`, must count the same as relative ones, or a later edit could get
   past the test by changing style. *Pinned in Task 1.*
4. **A new module** added to `src/overmind/` without updating the test must fail loudly, naming the
   module, rather than pass with an edge nobody reviewed. *Pinned in Task 2 (the module-set control).*
5. **Dynamic imports** (`importlib.import_module("overmind.x")`, `__import__`) are not seen by `ast`.
   This is a documented limit, not a test. The test's docstring says so, and the spec's §3 static scan
   is where dynamic routes are dealt with later.

---

## File Structure

| File | Responsibility |
|---|---|
| `tests/test_import_direction.py` (create) | `overmind_imports(source, own)`: the edge extractor, a pure function. `ALLOWED`: the frozen edges. Tests: the extractor against known sources; the module-set control; the graph equals `ALLOWED`. |

There are no other files. A separate helper module would be a second file for one function.

---

### Task 1: The edge extractor, tested on known sources

**Files:**
- Create: `tests/test_import_direction.py`

**Interfaces:**
- Produces: `overmind_imports(source: str, own: str) -> set[str]`. It returns the names of the
  `overmind` modules that `source` imports, excluding `own` (the module's own name). It is pure: no
  file I/O.

- [ ] **Step 1: Write the failing extractor tests**

```python
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
    raise NotImplementedError


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
```

- [ ] **Step 2: Run the tests and see them fail for the expected reason**

Run: `python -m unittest tests.test_import_direction -v`
Expected: 5 tests ERROR with `NotImplementedError`. (If any test passes, the fixture is wrong. Stop
and fix it.)

- [ ] **Step 3: Implement the extractor**

Replace the `overmind_imports` stub with:

```python
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
```

`from ..elsewhere` (level 2) is outside the package and is ignored on purpose. `import overmindish`
fails `parts[0] == "overmind"`, so it is ignored too.

- [ ] **Step 4: Run the tests and see them pass**

Run: `python -m unittest tests.test_import_direction -v`
Expected: 5 tests OK.

- [ ] **Step 5: Mutation check (do not commit)**

Each of these edits, made alone, must turn the named test red. Undo each before the next.

| Edit in `overmind_imports` | Must fail |
|---|---|
| delete the `else:  # from . import ledger` branch | `test_relative_name_import` |
| replace `ast.walk(ast.parse(source))` with `ast.parse(source).body` | `test_type_checking_and_function_level_imports_count` |
| delete the whole `elif isinstance(node, ast.Import):` block | `test_absolute_forms` |
| delete `found.discard(own)` | `test_other_packages_and_self_are_ignored` |

Then run `git diff --stat` and expect only the new test file. A mutation that never applied looks
like one that was not caught, so assert each edit changed the file before running the test.

- [ ] **Step 6: Commit**

```bash
git add tests/test_import_direction.py
git commit -m "tests: an import-direction extractor, tested on known sources (spec §9 Phase 0)"
```

---

### Task 2: Freeze today's graph

**Files:**
- Modify: `tests/test_import_direction.py` (append below `TestExtractor`)

**Interfaces:**
- Consumes: `overmind_imports(source: str, own: str) -> set[str]` (Task 1), and `SRC` (Task 1).
- Produces: `ALLOWED: frozenset[tuple[str, str]]`, the `(importer, imported)` pairs, and
  `MODULES: frozenset[str]`.

- [ ] **Step 1: Write the failing graph tests, with an EMPTY allow-list**

```python
# (importer, imported) at origin/main cbeaba6, measured 2026-10-08 with this
# file's own extractor. Phase 0 changes nothing: a new edge fails until it is
# added here on purpose, in review. Later phases move this toward spec §1.
ALLOWED: frozenset[tuple[str, str]] = frozenset()

# The modules this test knows. A new module must be added here AND its edges
# reviewed into ALLOWED; otherwise the control below fails, naming it.
MODULES: frozenset[str] = frozenset()


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
        found = {p.stem for p in SRC.glob("*.py")}
        self.assertEqual(found, set(MODULES),
                         f"new: {sorted(found - MODULES)}, gone: {sorted(MODULES - found)}")

    def test_no_import_edge_outside_the_allow_list(self):
        actual = actual_edges()
        self.assertTrue(actual, "no edges found at all: the extractor or SRC is wrong")
        new = sorted(actual - ALLOWED)
        gone = sorted(ALLOWED - actual)
        self.assertEqual(new, [], f"import edges not on the allow-list: {new}")
        self.assertEqual(gone, [], f"allowed edges that no longer exist (remove them): {gone}")
```

The test fails on edges that disappeared, too. An allow-list that keeps dead edges would quietly
permit them to come back.

- [ ] **Step 2: Run, and read the actual graph from the failure**

Run: `python -m unittest tests.test_import_direction -v`
Expected: `test_the_module_set_is_the_known_one` FAILS with `new: [...]`, listing 12 modules, and
`test_no_import_edge_outside_the_allow_list` FAILS, listing every edge. Compare both lists with the
expected values below. If they differ, **stop**: the code changed since `cbeaba6`, or the extractor
is wrong. Report it; do not paste in whatever came out.

- [ ] **Step 3: Fill in the measured values**

```python
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

MODULES: frozenset[str] = frozenset({
    "agent", "dispatch", "dispatch_mcp", "fork", "gate", "lanework",
    "ledger", "mcp_tools", "outcome", "providers", "quota", "repo_probe",
})
```

Eighteen distinct `(importer, imported)` pairs come from 19 import statements: `agent → fork` is
two statements. They were measured at `cbeaba6` on 2026-10-08 by an `ast` walk over the git blobs,
which is the same logic as `overmind_imports`. `gate`, `ledger`, `mcp_tools`, `outcome`, `quota`
and `repo_probe` import no other `overmind` module.

> Note for the executor: count the set literal above. If it does not have exactly 18 entries, one is
> missing, a duplicate or a typo. `frozenset` would hide a duplicate, so count the source lines too.

- [ ] **Step 4: Run, and see both pass**

Run: `python -m unittest tests.test_import_direction -v`
Expected: 7 tests OK (5 extractor, 2 graph).

- [ ] **Step 5: Mutation check (do not commit)**

Each change, made alone, must turn the named test red. Undo each before the next.

| Change | Must fail, naming |
|---|---|
| remove `("providers", "quota")` from `ALLOWED` | `test_no_import_edge_outside_the_allow_list`: new `[('providers', 'quota')]` |
| add `("gate", "lanework")` to `ALLOWED` | the same test: gone `[('gate', 'lanework')]` |
| add `from .lanework import Task` as the last line of `src/overmind/gate.py` (**do not commit**) | the same test: new `[('gate', 'lanework')]` |
| create an empty `src/overmind/newmod.py` (delete it after) | `test_the_module_set_is_the_known_one`: new `['newmod']` |
| set `SRC` to a directory that does not exist | `test_the_module_set_is_the_known_one` **and** "no edges found at all" |

Then check `git status --porcelain`: only `tests/test_import_direction.py` may appear.

- [ ] **Step 6: Run the whole suite**

Run: `python -m unittest discover -s tests -v`
Expected: every test passes. "Ran N tests" equals the count on `main` plus 7. Take the `main` count
from a run at the PR's merge-base, not from memory.

- [ ] **Step 7: Commit**

```bash
git add tests/test_import_direction.py
git commit -m "tests: freeze today's overmind import graph as an allow-list (spec §9 Phase 0)"
```

---

### Task 3: The PR

**Files:** none changed. This task is the gate.

- [ ] **Step 1: Local gates**

Run, and expect each to pass:
- `python -m unittest discover -s tests -v`
- `python3 .github/spdx_gate.py` (licence headers)
- `git diff origin/main --stat`: only `tests/test_import_direction.py`

- [ ] **Step 2: Open the PR**

The title is `tests: import-direction allow-list, frozen at today's graph (architecture Phase 0)`.

The body states:
- that it implements spec §9 Phase 0 and changes nothing under `src/`;
- the 18 edges and the one cycle (`agent ↔ fork`);
- the mutation table results from Tasks 1 and 2;
- that its version bump (PM-allocated) also covers #121, the architecture spec, per the PM's ruling
  of 2026-10-08.

- [ ] **Step 3: [READY] to the PM** in the compact format (CLAUDE.md §10), once CI is green. Read the
  checks and the review threads first.

---

## Self-review (done when writing this plan)

- **Spec coverage:** §9 Phase 0 asks for one thing, the import-direction test "with today's actual
  edges as its allow-list" that "documents the graph and freezes it". That is Task 2. The rogue
  plug-in tests are explicitly **not** Phase 0 (spec §9), so they are not here. §1's target table is
  for later phases to move `ALLOWED` toward. It is not enforced now, because enforcing it would fail
  on today's code.
- **Placeholders:** none. Every code step has its code, and every run step has its command and
  expected result.
- **Names:** `overmind_imports`, `SRC`, `ALLOWED`, `MODULES` and `actual_edges` are used the same way
  in Tasks 1 to 3.
- **Review Focus:** items 1-4 are pinned (Task 1, Step 1; Task 2, Steps 1 and 5). Item 5 is a stated
  limit, in the module docstring.
