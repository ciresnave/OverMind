# SPDX-License-Identifier: MIT OR Apache-2.0
"""The six workspace tools as a plug-in (plan P3.4).

`lanework.Workspace` already holds the tools; this carries them into the
pipeline. Each tool's effect builds a `Workspace` over the primitives it is
handed, so the processes (`git`, the task's check) and the writes run through
`run_process` / `write_file` and are attributed to the call the pipeline
allowed, which is what the chokepoint checks.

The plug-in has no opinion on whether a call is allowed (`before_tool_call` is
the default `NoOpinion`): the policy is `lanework.WorkspaceConfined`, carried by
the gate plug-in. A pipeline holding this plug-in and no gate refuses every
call as uncovered.

A flat module, not part of `harness/`: it imports `lanework`, and the harness
package must not.
"""

from __future__ import annotations

import pathlib
from typing import Any

from .harness.plugin_api import API_VERSION, Declaration, Plugin, ToolSpec
from .harness.primitives import Primitives
from .lanework import TOOL_NAMES, Task, Workspace, schemas


class WorkspacePlugin(Plugin):
    def __init__(self, root: pathlib.Path, task: Task, name: str = "workspace-tools",
                 version: str = "1") -> None:
        self._root = pathlib.Path(root)
        self._task = task
        by_name = {s["function"]["name"]: s["function"]["parameters"] for s in schemas()}
        tools = tuple(ToolSpec(n, by_name[n], self._effect(n)) for n in TOOL_NAMES)
        self._decl = Declaration(name=name, version=version, api_version=API_VERSION, tools=tools)

    def _effect(self, tool: str):
        def effect(prims: Primitives, **arguments: Any) -> Any:
            return getattr(Workspace(self._root, self._task, prims), tool)(**arguments)
        return effect

    def declares(self) -> Declaration:
        return self._decl
