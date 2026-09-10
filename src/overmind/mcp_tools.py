"""MCP as a tool source for the agent loop.

⚠️ THE GATE DOES NOT MOVE. `GatedExecutor` still takes a mapping of callables and
still decides at the moment of execution. What changes is only what those
callables DO: instead of running local Python, they issue an MCP `tools/call`.
`gate.py` is untouched by this module, and that is deliberate - the property that
makes the harness safe is WHERE the gate sits, and a refactor that quietly moved
it to somewhere a model can write to would lose exactly that.

Two jobs:

  1. TRANSLATE. MCP publishes `{name, description, inputSchema}`; the providers
     want `{type: "function", function: {name, description, parameters}}`.
     ⚠️ These schemas are written by SOMEONE ELSE'S SERVER. MEASUREMENTS.md
     §14.1 applies directly - the permissive majority hides the strict minority -
     so `convert_tool` REPORTS what it had to repair rather than silently
     normalising, and `audit_tools` exists to find the bad ones BEFORE a model
     is pointed at them.

  2. INVOKE. A thread-hosted event loop so the synchronous agent loop can drive
     an inherently async MCP session without every caller becoming async.

⚠️ The `mcp` package is imported LAZILY. gate/providers/agent are stdlib-only and
should stay runnable without it.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "convert_tool", "convert_tools", "audit_tools", "SchemaIssue",
    "McpToolSource", "McpUnavailable",
]


class McpUnavailable(RuntimeError):
    """The `mcp` package is not installed, or the server would not start."""


# --------------------------------------------------------------------------- #
# Schema translation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SchemaIssue:
    tool: str
    problem: str
    repaired: bool

    def __str__(self) -> str:
        mark = "repaired" if self.repaired else "NOT REPAIRABLE"
        return f"{self.tool}: {self.problem} [{mark}]"


#: Providers vary in what they accept. These are the constraints that actually
#: bit, or that are documented limits rather than guesses.
MAX_DESCRIPTION = 1024


def _raw_schema(tool: Any) -> dict[str, Any]:
    """MCP tool schemas arrive under different attribute names by SDK version.

    ⚠️ MEASURED: the Python SDK 2.2.0 exposes `input_schema`, while the wire
    field and the TS SDK use `inputSchema`. Reading only one produces
    `AttributeError` on the other, which reads as a broken tool.
    """
    for attr in ("input_schema", "inputSchema"):
        value = getattr(tool, attr, None)
        if value is not None:
            return dict(value)
    if isinstance(tool, Mapping):
        for key in ("inputSchema", "input_schema", "parameters"):
            if tool.get(key) is not None:
                return dict(tool[key])
    return {}


def _name_of(tool: Any) -> str:
    return str(getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, Mapping) else "") or "")


def _description_of(tool: Any) -> str:
    value = getattr(tool, "description", None)
    if value is None and isinstance(tool, Mapping):
        value = tool.get("description")
    return str(value or "")


def convert_tool(tool: Any) -> tuple[dict[str, Any], list[SchemaIssue]]:
    """One MCP tool -> one OpenAI function schema, plus what had to be repaired.

    ⚠️ REPAIRS ARE REPORTED, NOT HIDDEN. A silently normalised schema is a
    difference between what the server offers and what the model was told, and
    that difference surfaces later as a model "getting the arguments wrong".
    """
    name = _name_of(tool)
    issues: list[SchemaIssue] = []
    if not name:
        issues.append(SchemaIssue("<unnamed>", "tool has no name", repaired=False))
        return {}, issues

    schema = _raw_schema(tool)
    if not schema:
        schema = {"type": "object", "properties": {}}
        issues.append(SchemaIssue(name, "no input schema; substituted an empty object", True))

    # A parameters block must be an object schema; several servers omit `type`.
    if schema.get("type") != "object":
        if "type" not in schema:
            schema = {**schema, "type": "object"}
            issues.append(SchemaIssue(name, "schema omitted type; set to object", True))
        else:
            issues.append(SchemaIssue(
                name, f"schema type is {schema.get('type')!r}, not object", repaired=False))

    schema.setdefault("properties", {})

    # ⚠️ `required` naming a property that does not exist is accepted by lenient
    # providers and rejected by strict ones - §14.1's shape exactly.
    required = schema.get("required")
    if isinstance(required, list):
        missing = [r for r in required if r not in schema["properties"]]
        if missing:
            schema = {**schema, "required": [r for r in required if r not in missing]}
            issues.append(SchemaIssue(
                name, f"required named absent properties {missing}; dropped them", True))

    description = _description_of(tool)
    if len(description) > MAX_DESCRIPTION:
        description = description[:MAX_DESCRIPTION]
        issues.append(SchemaIssue(name, f"description over {MAX_DESCRIPTION} chars; truncated", True))
    if not description:
        issues.append(SchemaIssue(name, "no description; the model must guess its purpose", True))

    return ({"type": "function",
             "function": {"name": name, "description": description, "parameters": schema}},
            issues)


def convert_tools(tools: Sequence[Any]) -> tuple[list[dict[str, Any]], list[SchemaIssue]]:
    out: list[dict[str, Any]] = []
    issues: list[SchemaIssue] = []
    for tool in tools:
        schema, tool_issues = convert_tool(tool)
        issues.extend(tool_issues)
        if schema:
            out.append(schema)
    return out, issues


def audit_tools(tools: Sequence[Any]) -> dict[str, Any]:
    """Convert everything and summarise what needed repair.

    ⚠️ Run this against a server's real tool set BEFORE pointing a model at it.
    These schemas are authored elsewhere and change without notice.
    """
    schemas, issues = convert_tools(tools)
    unrepaired = [i for i in issues if not i.repaired]
    return {
        "converted": len(schemas),
        "offered": len(tools),
        "issues": issues,
        "unrepairable": unrepaired,
        "clean": len(issues) == 0,
    }


# --------------------------------------------------------------------------- #
# Invocation
# --------------------------------------------------------------------------- #

@dataclass
class McpToolSource:
    """A live MCP stdio session presented as schemas + callables.

    Usage keeps the gate exactly where it was::

        with McpToolSource.stdio("bun", ["run", "server.ts"]) as source:
            executor = GatedExecutor(gate, source.callables())
            run_agent(client, executor, source.schemas(), task)
    """

    command: str
    args: Sequence[str] = ()
    env: Mapping[str, str] | None = None
    cwd: str | None = None
    timeout: float = 120.0

    _loop: Any = field(default=None, repr=False)
    _thread: Any = field(default=None, repr=False)
    _session: Any = field(default=None, repr=False)
    _stack: Any = field(default=None, repr=False)
    _tools: list[Any] = field(default_factory=list, repr=False)
    _issues: list[SchemaIssue] = field(default_factory=list, repr=False)
    _experimental: dict[str, Any] = field(default_factory=dict, repr=False)

    # -- lifecycle ---------------------------------------------------------- #

    @classmethod
    def stdio(cls, command: str, args: Sequence[str] = (), *,
              env: Mapping[str, str] | None = None, cwd: str | None = None,
              timeout: float = 120.0) -> "McpToolSource":
        return cls(command=command, args=list(args), env=env, cwd=cwd, timeout=timeout)

    def __enter__(self) -> "McpToolSource":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(self.timeout)

    def open(self) -> "McpToolSource":
        try:
            from contextlib import AsyncExitStack
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:                       # pragma: no cover
            raise McpUnavailable(
                "the `mcp` package is required for MCP tool sources: pip install mcp") from exc

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True,
                                        name="overmind-mcp")
        self._thread.start()

        params = StdioServerParameters(command=self.command, args=list(self.args),
                                       env=dict(self.env) if self.env else None,
                                       cwd=self.cwd)

        async def _connect():
            stack = AsyncExitStack()
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            info = await session.initialize()
            caps = getattr(info, "capabilities", None)
            experimental = dict(getattr(caps, "experimental", None) or {})
            listing = await session.list_tools()
            return stack, session, list(listing.tools), experimental

        try:
            self._stack, self._session, self._tools, self._experimental = self._run(_connect())
        except Exception as exc:
            self.close()
            raise McpUnavailable(f"could not start {self.command}: {exc}") from exc

        _, self._issues = convert_tools(self._tools)
        return self

    def close(self) -> None:
        if self._stack is not None and self._loop is not None:
            try:
                self._run(self._stack.aclose())
            except Exception:                            # pragma: no cover - shutdown is best effort
                pass
            self._stack = None
        if self._loop is not None:
            loop = self._loop
            loop.call_soon_threadsafe(loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
            # Stopping a loop does not release it; an unclosed loop leaks its
            # selector and warns at collection.
            if not loop.is_running():
                loop.close()
            self._loop = None
            self._thread = None

    # -- what the loop needs ------------------------------------------------ #

    @property
    def experimental_capabilities(self) -> dict[str, Any]:
        """⚠️ What the server ADVERTISES. The channel binding is derived from
        this rather than hardcoded (MEASUREMENTS.md §7)."""
        return dict(self._experimental)

    @property
    def schema_issues(self) -> list[SchemaIssue]:
        return list(self._issues)

    def tool_names(self) -> list[str]:
        return [_name_of(t) for t in self._tools]

    def schemas(self, only: Sequence[str] | None = None) -> list[dict[str, Any]]:
        tools = self._tools
        if only is not None:
            wanted = set(only)
            tools = [t for t in tools if _name_of(t) in wanted]
        schemas, _ = convert_tools(tools)
        return schemas

    def call(self, name: str, **arguments: Any) -> str:
        """Invoke one MCP tool and flatten the result to text.

        ⚠️ An `isError` result is returned as TEXT, not raised. The gate already
        distinguishes refusal from failure, and the model needs to read either.
        """
        async def _call():
            return await self._session.call_tool(name, dict(arguments))

        result = self._run(_call())
        parts = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
        body = "\n".join(parts) or "(no content)"
        is_error = getattr(result, "is_error", None)
        if is_error is None:
            is_error = getattr(result, "isError", False)
        return f"ERROR: {body}" if is_error else body

    def callables(self, only: Sequence[str] | None = None) -> dict[str, Callable[..., str]]:
        """The mapping `GatedExecutor` takes.

        ⚠️ THE GATE IS UNCHANGED BY THIS. It still decides at execution, and
        these callables are simply what execution now means.
        """
        names = self.tool_names() if only is None else list(only)
        return {name: self._bind(name) for name in names if name}

    def _bind(self, name: str) -> Callable[..., str]:
        def invoke(**arguments: Any) -> str:
            return self.call(name, **arguments)
        invoke.__name__ = name
        return invoke
