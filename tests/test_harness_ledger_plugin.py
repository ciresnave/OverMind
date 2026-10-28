# SPDX-License-Identifier: MIT OR Apache-2.0
"""LedgerPlugin writes what GatedExecutor writes (plan P3.4).

The control is differential: the same calls through `GatedExecutor` and through
`Pipeline([GatePlugin, LedgerPlugin])` must leave equal ledgers.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.gate import (  # noqa: E402
    DenyUnlessDeclared, GatedExecutor, Gate, Ledger, RequirePrecondition, StaticFacts,
)
from overmind.harness.gate_plugin import GatePlugin  # noqa: E402
from overmind.harness.ledger_plugin import LedgerPlugin  # noqa: E402
from overmind.harness.pipeline import Pipeline  # noqa: E402
from overmind.harness.plugin_api import SessionContext, ToolSpec  # noqa: E402


def _boom():
    raise RuntimeError("bad")


FUNCS = {"read": lambda path="x": f"contents of {path}" * 100, "bad": _boom}


def _gate(ledger):
    return Gate([DenyUnlessDeclared(frozenset({"read", "bad", "ghost"}))], StaticFacts(), ledger)


CALLS = [("read", {"path": "a"}), ("write", {}), ("bad", {}), ("ghost", {}), ("read", {})]


def _rows(ledger):
    return [(e.seq, e.call, e.decision, e.executed, e.result_repr, e.error) for e in ledger]


class TestLedgerPlugin(unittest.TestCase):
    def _both(self):
        old = Ledger()
        ex = GatedExecutor(_gate(old), FUNCS)
        for n, a in CALLS:
            ex.execute(n, a)
        lp = LedgerPlugin()
        tools = [ToolSpec(n, {}, (lambda prim, f=f, **kw: f(**kw))) for n, f in FUNCS.items()]
        from overmind.harness.plugin_api import Declaration, API_VERSION, Plugin

        class Tools(Plugin):
            def declares(self):
                return Declaration("tools", "1", API_VERSION, tools=tuple(tools))

        p = Pipeline([GatePlugin(_gate(Ledger())), Tools(), lp], SessionContext("s"))
        for n, a in CALLS:
            p.execute(n, a)
        return old, lp

    def test_same_ledger_as_gated_executor(self):
        old, lp = self._both()
        self.assertEqual(len(old), 5)
        self.assertEqual(_rows(lp.ledger), _rows(old))

    def test_covers_denied_error_and_truncation(self):
        _, lp = self._both()
        e = lp.ledger.entries
        self.assertFalse(e[1].executed)                      # denied, not run
        self.assertEqual(e[2].error, "RuntimeError: bad")    # tool error recorded
        self.assertEqual(len(e[0].result_repr), 500)         # truncated like the executor
        self.assertEqual(lp.ledger.executed_tools(), ("read", "read"))   # an errored call did not succeed

    def test_facts_are_live(self):
        lp = LedgerPlugin()
        facts = lp.facts()
        self.assertEqual(facts.fact("executed_tools"), ())
        _, lp2 = self._both()
        self.assertIn("read", lp2.facts().fact("executed_tools"))

    def test_on_stop_summary(self):
        _, lp = self._both()
        self.assertEqual(lp.on_stop(SessionContext("s"), None), {"entries": 5, "executed": 3})


if __name__ == "__main__":
    unittest.main()
