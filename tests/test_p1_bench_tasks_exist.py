# SPDX-License-Identifier: MIT OR Apache-2.0
"""`probe/p1_bench.py` plants a one-line bug in a file of THIS tree and asks a
model to fix it. A task whose file left the tree (the licence tooling moved to
`licence-tools` at 0.13.6) would crash the default run with "anchor matched 0
times" instead of reporting a result - found by reading, so it is pinned here.

The probe is read as source, not imported: importing it pulls in the dispatch
stack and a live provider table.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _tasks() -> list[tuple]:
    tree = ast.parse((ROOT / "probe" / "p1_bench.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "TASKS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("TASKS not found in probe/p1_bench.py")


class TestBenchTasksExist(unittest.TestCase):

    def test_every_task_file_is_here_and_holds_its_anchor_once(self):
        tasks = _tasks()
        self.assertGreaterEqual(len(tasks), 3)
        for name, path, good, _bad, test_module, _why in tasks:
            with self.subTest(task=name):
                f = ROOT / path
                self.assertTrue(f.is_file(), f"{name}: {path} is not in this tree")
                text = f.read_bytes().decode("utf-8").replace("\r\n", "\n")
                self.assertEqual(text.count(good), 1, f"{name}: anchor in {path}")
                module = test_module.split(".")[1]
                self.assertTrue((ROOT / "tests" / f"{module}.py").is_file(),
                                f"{name}: tests/{module}.py is not in this tree")


if __name__ == "__main__":
    unittest.main(verbosity=2)
