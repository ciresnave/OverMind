# SPDX-License-Identifier: MIT OR Apache-2.0
"""A `GatedExecutor`-shaped view of a `Pipeline`, so `run_agent` can drive one.

`run_agent` wants an executor with `.execute`, `.tools` and `.gate` (for its
ledger and `certainly_denied`). This keeps that shape and sends every call
through the pipeline instead; the `gate` it carries is only the ledger and the
cost-control query, never asked to decide (the pipeline's gate plug-in does).

⚠️ `tools` holds stubs that refuse: a caller that reached for `tools[name](...)`
would otherwise skip the pipeline.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..gate import Decision, Gate, GatedExecutor, ToolCall, ToolOutcome
from .pipeline import Pipeline
from .plugin_api import Allow, ModelStep


def _refuse(name: str):
    def stub(**_: Any) -> Any:
        raise RuntimeError(f"{name}: call it through PipelineExecutor.execute, not directly")
    return stub


class PipelineExecutor(GatedExecutor):
    def __init__(self, pipeline: Pipeline, gate: Gate, fork_plugin: Any = None) -> None:
        """`fork_plugin`: the `ForkPlugin` in `pipeline`, if the run may fork;
        `run_agent` then binds each turn to it before submitting a `fork` call."""
        if fork_plugin is not None and fork_plugin.gate is not gate:
            # the run reads this gate's ledger and policies; the fork rebuilds ITS gate
            raise ValueError("the executor's gate is not the gate the fork plug-in was built on")
        super().__init__(gate, {n: _refuse(n) for n in pipeline.tool_names})
        self.pipeline = pipeline
        self.fork_plugin = fork_plugin

    def observe_model_step(self, *, step: int, model: str | None, provider: str | None,
                           tool_calls: int, truncated: bool) -> None:
        """`run_agent` calls this after every model call that returned (if present)."""
        self.pipeline.observe_model_step(ModelStep(step, model, provider, tool_calls, truncated))

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None,
                actor: str = "agent") -> ToolOutcome:
        out = self.pipeline.execute(name, arguments, actor)
        d = out.decision
        return ToolOutcome(ToolCall(name=out.call.name, arguments=dict(out.call.arguments),
                                    actor=out.call.actor),
                           Decision(isinstance(d, Allow), d.reason, d.policy),
                           executed=out.executed, result=out.result, error=out.error)
