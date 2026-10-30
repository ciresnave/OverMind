# SPDX-License-Identifier: MIT OR Apache-2.0
"""The executor `lanework.run_task` hands the agent: a plug-in pipeline (plan P3.4).

It used to be `GatedExecutor(Gate([WorkspaceConfined, DenyUnlessDeclared]), ws.tools())`.
It is now the pipeline `default_registry.REGISTRY` builds from the same two
policies: `ledger`, `gate` (workspace-confined, then deny-unless-declared over the
six workspace tools) and `workspace-tools`, driven through `PipelineExecutor`.

The harness's OWN readings after the run (`Workspace.changed_files`, `.check`) stay
with `run_task`'s plain `Workspace`; only the agent's tool calls go through the
pipeline. A flat module: it imports `lanework`, `default_registry` and the
harness, and `lanework` imports it lazily inside `run_task` (like `agent` ->
`fork`, a cycle that is only a name at call time).
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Collection

from .check_policy_plugin import CheckPolicyPlugin
from .default_registry import REGISTRY
from .dispatch_mcp import DOCS_ONLY_CHECK
from .harness.config import SessionConfig
from .harness.gate_plugin import GatePlugin
from .harness.pipeline import load_pipeline
from .harness.pipeline_executor import PipelineExecutor
from .harness.plugin_api import Refuse
from .lanework import TOOL_NAMES, Task


def check_isolation(executor: PipelineExecutor) -> str:
    """The `check-policy` plug-in's statement of where the check ran (see its `isolation`)."""
    for p in executor.pipeline.plugins:
        if isinstance(p, CheckPolicyPlugin):
            return p.isolation()
    return "undeclared, unsandboxed"


def build_task_executor(root: pathlib.Path, task: Task, tracked: Collection[str]) -> PipelineExecutor:
    """`tracked`: the repo's tracked file names, as `run_task` reads them (the
    policy casefolds them itself). Raises if the pipeline cannot be built or
    refuses to start; `run_task` reports that as an ERROR verdict."""
    config = SessionConfig.from_dict({
        "schema_version": 1, "session_id": "lanework-task",
        "handler": {"kind": "lanework", "version": 1},
        "route": [{"provider": "-", "model": "-"}],
        "limits": {"max_steps": max(1, task.max_steps), "max_tokens_total": 1, "wall_seconds": 1},
        "plugins": [
            {"name": "check-policy", "version": "1", "config": {
                "root": str(root), "check": list(task.check), "check_profile": task.check_profile,
                "trusted_author": task.trusted_author, "docs_only_check": list(DOCS_ONLY_CHECK)}},
            {"name": "ledger", "version": "1", "config": {}},
            {"name": "gate", "version": "1", "config": {"policies": [
                {"type": "workspace-confined", "root": str(root), "writable": list(task.writable),
                 "tracked": sorted(tracked), "extra_denied_globs": list(task.protected_globs)},
                {"type": "deny-unless-declared", "reversible": sorted(TOOL_NAMES)}]}},
            {"name": "workspace-tools", "version": "1",
             "config": {"root": str(root), "task": {k: v for k, v in dataclasses.asdict(task).items()
                                                    if k != "trusted_author"}}},
        ],
        "workspace": {"repo": task.repo, "base": task.base},
    })
    pipeline = load_pipeline(config, REGISTRY)
    started = pipeline.start()
    if isinstance(started, Refuse):
        raise RuntimeError(f"the task pipeline refused to start: {started.reason}")
    gate = next(p for p in pipeline.plugins if isinstance(p, GatePlugin))
    return PipelineExecutor(pipeline, gate.gate)
