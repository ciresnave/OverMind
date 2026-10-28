# SPDX-License-Identifier: MIT OR Apache-2.0
"""`gate.Ledger` as a plug-in (plan P3.4).

Records every submitted call through `on_tool_outcome`, the one hook that also
sees denied calls and a tool's error. The entry has the shape `GatedExecutor`
writes (same `seq`, decision, `executed`, `repr(result)[:500]`, error), so
everything that reads a `Ledger` reads this one unchanged.

The ledger is evidence, and `result` here is what the model was shown (after any
redaction), so the record never holds more than the model saw. `facts()` is the
live `executed_tools` source a `Gate` needs for `RequirePrecondition`; wiring it
into `GatePlugin` is the `run_agent` move, a later piece.
"""

from __future__ import annotations

from ..gate import Decision, FactSource, Ledger, LedgerEntry, LedgerFacts, ToolCall
from .plugin_api import (
    API_VERSION, Allow, Declaration, PipelineOutcome, Plugin, SessionContext,
)


class LedgerPlugin(Plugin):
    def __init__(self, ledger: Ledger | None = None, name: str = "ledger", version: str = "1") -> None:
        self.ledger = ledger if ledger is not None else Ledger()
        self._decl = Declaration(name=name, version=version, api_version=API_VERSION)

    def declares(self) -> Declaration:
        return self._decl

    def facts(self, base: FactSource | None = None) -> LedgerFacts:
        return LedgerFacts(self.ledger, base)

    def on_tool_outcome(self, ctx: SessionContext, outcome: PipelineOutcome) -> None:
        call = ToolCall(name=outcome.call.name, arguments=dict(outcome.call.arguments),
                        actor=outcome.call.actor)
        d = outcome.decision
        decision = Decision(isinstance(d, Allow), d.reason, d.policy)
        self.ledger.append(LedgerEntry(
            seq=len(self.ledger), call=call, decision=decision, executed=outcome.executed,
            result_repr=repr(outcome.result)[:500] if outcome.executed and outcome.error is None else None,
            error=outcome.error))

    def on_stop(self, ctx: SessionContext, run: object) -> dict[str, int]:
        return {"entries": len(self.ledger), "executed": sum(e.executed for e in self.ledger)}
