# SPDX-License-Identifier: MIT OR Apache-2.0
"""Today's `Gate` as the first plug-in (plan P3.2).

An adapter and nothing more: `before_tool_call` asks the wrapped `Gate` and
passes its `Decision` through unchanged (same `reason`, same `policy`), so the
pipeline can never be more permissive or less informative than the gate it
wraps. `Gate.decide` already does the applies-only, first-denial-wins,
uncovered-is-denied and unvouched-sender work (gate.py:517-553); none of it is
re-implemented here.

Ledger writing stays with `GatedExecutor` until the ledger becomes a plug-in
(plan P3.4).
"""

from __future__ import annotations

from ..gate import Gate, ToolCall
from .plugin_api import API_VERSION, Allow, Declaration, Deny, HarnessCall, Plugin, SessionContext


class GatePlugin(Plugin):
    def __init__(self, gate: Gate, name: str = "gate", version: str = "1") -> None:
        self._gate = gate
        self._decl = Declaration(name=name, version=version, api_version=API_VERSION)

    def declares(self) -> Declaration:
        return self._decl

    def before_tool_call(self, ctx: SessionContext, call: HarnessCall) -> Allow | Deny:
        d = self._gate.decide(ToolCall(name=call.name, arguments=dict(call.arguments), actor=call.actor))
        if d.allowed:
            return Allow(d.reason, d.policy)
        return Deny(d.reason, d.policy)
