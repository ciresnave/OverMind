# SPDX-License-Identifier: MIT OR Apache-2.0
"""The plug-in pipeline: runs plug-ins in config order (spec §2 "Ordering").

`before_tool_call` keeps `Gate.decide`'s semantics (gate.py:517-553): only
plug-ins with an opinion are asked, the first `Deny` returns at once, and a call
nobody covers is denied. Every other hook composes as its docstring in
plugin_api.py says. A hook that raises, or returns the wrong kind of result,
fails closed.

`load_pipeline` is where "the harness never runs a partial pipeline" lives: a
plug-in that is not registered, declares itself differently from the config, or
has another API major version refuses the WHOLE pipeline.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from .config import SessionConfig
from .manifest import LoadedManifest, PluginRecord, source_sha256
from .plugin_api import (
    API_VERSION, Allow, Correct, Deny, Drop, HarnessCall, NoOpinion, Ok, Pass, Plugin,
    Redact, Refuse, Rewrite, SessionContext, Stop, ToolSpec,
)
from .primitives import Primitives, allowing, cleared


class PluginError(ValueError):
    """The set of plug-ins is refused whole."""


@dataclass(frozen=True)
class PipelineOutcome:
    call: HarnessCall
    decision: Allow | Deny
    executed: bool
    result: Any = None
    error: str | None = None


class Pipeline:
    def __init__(self, plugins: Iterable[Plugin], ctx: SessionContext) -> None:
        self._plugins = list(plugins)
        self._ctx = ctx
        self._tools: dict[str, ToolSpec] = {}
        self._names: list[str] = []
        owners: dict[str, str] = {}
        for plugin in self._plugins:
            decl = plugin.declares()
            if type(decl.api_version) is not int or decl.api_version != API_VERSION:
                raise PluginError(
                    f"plug-in {decl.name!r}: api_version {decl.api_version!r}, this harness is {API_VERSION}")
            if decl.name in self._names:
                raise PluginError(f"plug-in {decl.name!r} is loaded twice")
            self._names.append(decl.name)
            for spec in decl.tools:
                if spec.name in owners:
                    raise PluginError(
                        f"tool {spec.name!r} is declared by both {owners[spec.name]!r} and {decl.name!r}")
                owners[spec.name] = decl.name
                self._tools[spec.name] = spec

    # -- one guarded hook call ------------------------------------------------

    def _call(self, plugin: Plugin, hook: str, args: tuple[Any, ...],
              valid: tuple[type, ...], fail: Callable[[str], Any]) -> Any:
        name = plugin.declares().name
        with cleared():
            try:
                result = getattr(plugin, hook)(self._ctx, *args)
            except Exception as exc:                  # noqa: BLE001 - fails closed, says why
                return fail(f"plug-in {name!r} {hook} raised {type(exc).__name__}: {exc}")
        if type(result) not in valid:
            return fail(f"plug-in {name!r} {hook} returned {type(result).__name__}, "
                        f"which is not a result of that hook")
        return result

    # -- the hooks -------------------------------------------------------------

    def start(self) -> Ok | Refuse:
        for plugin in self._plugins:
            r = self._call(plugin, "on_session_start", (), (Ok, Refuse), Refuse)
            if isinstance(r, Refuse):
                return r
        return Ok()

    def message_in(self, msg: str) -> Pass | Rewrite | Drop:
        current, changed = msg, False
        for plugin in self._plugins:
            r = self._call(plugin, "on_message_in", (current,), (Pass, Rewrite, Drop), Drop)
            if isinstance(r, Drop):
                return r
            if isinstance(r, Rewrite):
                current, changed = r.message, True
        return Rewrite(current) if changed else Pass()

    def message_out(self, msg: str) -> Pass | Redact | Drop:
        current, changed = msg, False
        for plugin in self._plugins:
            r = self._call(plugin, "on_message_out", (current,), (Pass, Redact, Drop), Drop)
            if isinstance(r, Drop):
                return r
            if isinstance(r, Redact):
                current, changed = r.value, True
        return Redact(current) if changed else Pass()

    def model_output(self, turn: Any) -> Pass | Correct | Stop:
        notes: list[str] = []
        for plugin in self._plugins:
            r = self._call(plugin, "on_model_output", (turn,), (Pass, Correct, Stop), Stop)
            if isinstance(r, Stop):
                return r
            if isinstance(r, Correct):
                notes.append(r.note)
        return Correct("; ".join(notes)) if notes else Pass()

    def decide(self, call: HarnessCall) -> Allow | Deny:
        last: Allow | None = None
        for plugin in self._plugins:
            r = self._call(plugin, "before_tool_call", (call,), (Allow, Deny, NoOpinion), Deny)
            if isinstance(r, Deny):
                return r                               # any denial wins, immediately
            if isinstance(r, Allow):
                last = r
        if last is None:
            return Deny(f"no plug-in covers {call.name}; an uncovered tool is refused, not allowed",
                        "no-applicable-policy")
        return last

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None,
                actor: str = "agent") -> PipelineOutcome:
        call = HarnessCall(name=name, arguments=dict(arguments or {}), actor=actor)
        decision = self.decide(call)
        if isinstance(decision, Deny):
            return PipelineOutcome(call, decision, executed=False)
        spec = self._tools.get(name)
        if spec is None:
            return PipelineOutcome(
                call, Deny(f"{name} is allowed by policy but not registered", "unregistered-tool"),
                executed=False)

        result: Any = None
        error: str | None = None
        with allowing(call):
            try:
                result = spec.effect(Primitives(), **call.arguments)
            except Exception as exc:                  # noqa: BLE001 - recorded, not swallowed
                error = f"{type(exc).__name__}: {exc}"

        for plugin in self._plugins:
            r = self._call(plugin, "after_tool_call", (call, result), (Pass, Redact),
                           lambda why: Redact(f"[result withheld: {why}]"))
            if isinstance(r, Redact):
                result = r.value
        return PipelineOutcome(call, decision, executed=True, result=result, error=error)

    def stop(self, run: Any) -> dict[str, Any]:
        """Each plug-in's record, by name. A plug-in that raises records the error."""
        out: dict[str, Any] = {}
        for plugin in self._plugins:
            name = plugin.declares().name
            with cleared():
                try:
                    out[name] = plugin.on_stop(self._ctx, run)
                except Exception as exc:              # noqa: BLE001
                    out[name] = {"error": f"{type(exc).__name__}: {exc}"}
        return out

    # -- what loaded -------------------------------------------------------------

    def manifest(self) -> LoadedManifest:
        records: list[PluginRecord] = []
        services: list[str] = []
        for plugin in self._plugins:
            decl = plugin.declares()
            path = inspect.getsourcefile(type(plugin))
            if path is None:
                raise PluginError(f"plug-in {decl.name!r}: no source file to hash")
            records.append(PluginRecord(decl.name, decl.version, decl.api_version, source_sha256(path)))
            services.extend(s for s in decl.services if s not in services)
        return LoadedManifest(plugins=tuple(records), tools=tuple(self._tools), services=tuple(services))


def load_pipeline(config: SessionConfig,
                  registry: Mapping[str, Callable[[Mapping[str, Any]], Plugin]]) -> Pipeline:
    """Build the pipeline a config asks for, or refuse all of it."""
    plugins: list[Plugin] = []
    for ref in config.plugins:
        factory = registry.get(ref.name)
        if factory is None:
            raise PluginError(f"plug-in {ref.name!r} is in the config but not registered")
        plugin = factory(ref.config)
        decl = plugin.declares()
        if decl.name != ref.name:
            raise PluginError(f"plug-in {ref.name!r}: the registered plug-in declares itself {decl.name!r}")
        if decl.version != ref.version:
            raise PluginError(
                f"plug-in {ref.name!r}: config version {ref.version!r}, declared version {decl.version!r}")
        plugins.append(plugin)
    return Pipeline(plugins, SessionContext(session_id=config.session_id))
