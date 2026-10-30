# SPDX-License-Identifier: MIT OR Apache-2.0
"""MCP as a service and a plug-in (plan P3.4).

`McpService` is what the HARNESS holds: it opens an `McpToolSource` inside
`harness_owned()` and has the source's loop thread mark itself harness-owned
first (MEASUREMENTS s38), so the SDK's start-up I/O is the harness's own and not
a chokepoint violation. It is registered under a name with
`harness.services.register_service`.

`McpPlugin` holds only that NAME and the server's tool schemas. Each tool's
effect is `prims.call_service(name, tool, arguments)`, so a call reaches the
connection only after the pipeline allowed it; a hook that tries it directly is
refused (`PrimitiveRefused`). Policy is not here: the gate plug-in covers the
tool names, and a pipeline with no gate refuses them as uncovered.

A flat module: it imports `mcp_tools`, which the harness package must not.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .harness.plugin_api import API_VERSION, Declaration, Plugin, ToolSpec
from .harness.primitives import Primitives, harness_owned, mark_thread_harness_owned
from .mcp_tools import McpToolSource

__all__ = ["McpPlugin", "McpService"]


class McpService:
    def __init__(self, source: Any) -> None:
        self._source = source

    @classmethod
    def stdio(cls, command: str, args: Sequence[str] = (), **kw: Any) -> "McpService":
        """`kw`: `McpToolSource.stdio`'s (env, cwd, timeout, expect_channels)."""
        source = McpToolSource.stdio(command, args, **kw)
        source.on_thread_start = mark_thread_harness_owned
        return cls(source)

    def open(self) -> "McpService":
        with harness_owned():
            self._source.open()
        return self

    def close(self) -> None:
        self._source.close()          # measured: needs no mark (MEASUREMENTS s38 notes open() only)

    def __enter__(self) -> "McpService":
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def schemas(self) -> list[dict[str, Any]]:
        return self._source.schemas()

    def call(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        return self._source.call(tool, **arguments)


class McpPlugin(Plugin):
    def __init__(self, service_name: str, service: McpService, name: str = "mcp",
                 version: str = "1") -> None:
        tools = tuple(ToolSpec(s["function"]["name"], s["function"]["parameters"],
                               self._effect(service_name, s["function"]["name"]))
                      for s in service.schemas())
        self._decl = Declaration(name=name, version=version, api_version=API_VERSION,
                                 tools=tools, services=(service_name,))

    @staticmethod
    def _effect(service_name: str, tool: str):
        def effect(prims: Primitives, **arguments: Any) -> Any:
            return prims.call_service(service_name, tool, arguments)
        return effect

    def declares(self) -> Declaration:
        return self._decl
