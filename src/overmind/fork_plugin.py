# SPDX-License-Identifier: MIT OR Apache-2.0
"""Fork-and-curate as a plug-in (plan P3.4; ARCHITECTURE-LAYERS-SPEC section 3).

The `fork` tool's effect runs `fork.run_child` over a CHILD PIPELINE built from
the parent's already-loaded plug-ins:

  * the gate and ledger plug-ins are rebuilt, as `fork.run_fork` rebuilds its
    gate: the parent's policies, the config's extras and `ForbidTools(fork)`
    (depth 1), facts that grow with the child's own ledger, and a ledger of its
    own so the child's effects can be absorbed with lineage;
  * every plug-in in `shared` (the workspace tools, say) is the SAME instance
    the parent runs;
  * nothing is loaded and no `declares()` runs inside the parent's allowed
    `fork` call: declarations are snapshotted here, at construction, and handed
    to the child `Pipeline`. The child's hooks run with the allowance cleared
    (pipeline.py), so a plug-in's hook that reaches for a primitive is refused
    there exactly as in the parent.

Order: this plug-in must come AFTER the ledger plug-in. Its `on_tool_outcome`
absorbs the child's ledger into the parent's once the `fork` call has its own
entry; if that entry is not there it raises, and the pipeline records it.

The loop has to `bind` the turn (messages, the calls pending after this one,
...) before it submits a `fork` call, as `fork.execute_fork` does per call.
`run_agent` still drives `GatedExecutor`; moving it to the pipeline is a later
piece.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .fork import (FORK_TOOL, ForkConfig, ForkFacts, ForkResult, fork_schema, run_child)
from .gate import ForbidTools, Gate, Ledger
from .harness.gate_plugin import GatePlugin
from .harness.ledger_plugin import LedgerPlugin
from .harness.pipeline import Pipeline
from .harness.pipeline_executor import PipelineExecutor
from .harness.plugin_api import (API_VERSION, Declaration, Plugin, PipelineOutcome,
                                 SessionContext, ToolSpec)
from .harness.primitives import Primitives, cleared

__all__ = ["ForkPlugin"]


class ForkPlugin(Plugin):
    def __init__(self, gate_plugin: GatePlugin, ledger_plugin: LedgerPlugin, gate: Gate,
                 shared: Sequence[Plugin] = (), config: ForkConfig = ForkConfig(),
                 name: str = "fork", version: str = "1") -> None:
        if type(gate) is not Gate:
            # ⚠️ Rebuilt for the child: a subclass's behaviour would not survive it.
            raise TypeError(f"cannot fork under {type(gate).__name__}: "
                            f"only a plain Gate can be rebuilt for the fork")
        if gate_plugin._gate is not gate:               # noqa: SLF001
            raise ValueError("the gate given is not the one the gate plug-in wraps; the "
                             "child would run under a different gate than its parent")
        if gate.ledger is not ledger_plugin.ledger:
            raise ValueError("the gate's ledger is not the ledger plug-in's ledger")
        self._gate = gate
        self._ledger = ledger_plugin.ledger
        self._config = config
        self._shared = tuple(shared)
        self._gate_plugin = gate_plugin
        self._ledger_plugin = ledger_plugin
        self._decl = Declaration(
            name=name, version=version, api_version=API_VERSION,
            tools=(ToolSpec(FORK_TOOL, fork_schema()["function"]["parameters"], self._effect),))
        # snapshots, taken now and never again (no declares() inside a fork)
        with cleared():
            self._snap = {id(p): p.declares() for p in (gate_plugin, ledger_plugin, *self._shared)}
        self._bound: dict[str, Any] | None = None
        self._result: ForkResult | None = None

    @property
    def gate(self) -> Gate:
        return self._gate

    @property
    def config(self) -> ForkConfig:
        return self._config

    def declares(self) -> Declaration:
        return self._decl

    def bind(self, client: Any, tools: Sequence[Mapping[str, Any]], *,
             messages: Sequence[Mapping[str, Any]], calls: Sequence[Mapping[str, Any]],
             index: int, time_sensitive: bool, parent_call_index: int,
             parent_model: str | None, fork_id: str) -> None:
        """The turn the NEXT `fork` call belongs to, snapshotted now (the loop
        appends results to `messages` afterwards). Consumed by that call."""
        call = calls[index]
        self._bound = dict(
            client=client, tools=list(tools), history=[dict(m) for m in messages],
            fork_call_id=call.get("id") or "",
            pending=tuple(((c.get("id") or ""), ((c.get("function") or {}).get("name") or ""))
                          for c in calls[index + 1:]),
            time_sensitive=time_sensitive, parent_call_index=parent_call_index,
            parent_model=parent_model, fork_id=fork_id)

    def _child(self, child_ledger: Ledger) -> Pipeline:
        gate = Gate([*self._gate.policies, *self._config.extra_policies,
                     ForbidTools(frozenset({FORK_TOOL}),
                                 reason="a fork cannot fork (depth limit 1)")],
                    facts=ForkFacts(self._gate.facts, child_ledger), ledger=child_ledger)
        gd, ld = self._snap[id(self._gate_plugin)], self._snap[id(self._ledger_plugin)]
        gp = GatePlugin(gate, gd.name, gd.version)
        lp = LedgerPlugin(child_ledger, ld.name, ld.version)
        plugins: list[Plugin] = [gp, lp, *self._shared]
        declared = {id(gp): gd, id(lp): ld, **{id(p): self._snap[id(p)] for p in self._shared}}
        self._child_gate = gate
        return Pipeline(plugins, SessionContext(session_id="fork"), declared=declared)

    def _effect(self, prims: Primitives, task: Any = None) -> ForkResult:
        bound, self._bound = self._bound, None
        if bound is None:
            raise RuntimeError("fork: the loop did not bind this turn first")
        if not isinstance(task, str) or not task.strip():
            raise TypeError(f"task must be a non-empty string, not {task!r}")
        child_ledger = Ledger()
        pipeline = self._child(child_ledger)
        executor = PipelineExecutor(pipeline, self._child_gate)
        result = run_child(bound.pop("client"), executor, child_ledger, bound.pop("tools"),
                           task=task, config=self._config, **bound)
        self._result = result
        return result

    def on_tool_outcome(self, ctx: SessionContext, outcome: PipelineOutcome) -> None:
        result, self._result = self._result, None
        self._bound = None                  # a binding serves ONE call, denied or not
        if outcome.call.name != FORK_TOOL or not outcome.executed or outcome.error is not None:
            return
        # absorbed only while the parent model still sees the ForkResult, as execute_fork did
        if result is None or not isinstance(outcome.result, ForkResult):
            return
        seq = len(self._ledger) - 1
        entries = self._ledger.entries
        if (seq < 0 or entries[seq].call.name != FORK_TOOL or not entries[seq].executed
                or entries[seq].result_repr != repr(result)[:500]):
            raise RuntimeError("fork: this fork call has no ledger entry yet; the fork "
                               "plug-in must come after the ledger plug-in")
        self._ledger.absorb(result.run.ledger, fork_id=result.fork_id, fork_seq=seq,
                            record=result.cost)
