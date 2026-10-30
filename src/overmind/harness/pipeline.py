# SPDX-License-Identifier: MIT OR Apache-2.0
"""The plug-in pipeline: runs plug-ins in config order (spec §2 "Ordering").

`before_tool_call` keeps `Gate.decide`'s semantics (gate.py:517-553): only
plug-ins with an opinion are asked, the first `Deny` returns at once, and a call
nobody covers is denied. Every other hook composes as its docstring in
plugin_api.py says. A hook that raises, or returns the wrong kind of result (or
a right kind with a malformed payload), fails closed.

⚠️ Three things this file does on purpose, each found by review:
  * `declares()` is called ONCE per plug-in, in `__init__`, with the allowance
    cleared, and the answer is snapshotted. A plug-in cannot answer differently
    later, and a `declares()` that runs inside a fork's allowed call cannot reach
    a primitive (spec §3: no `declares()` ever runs inside the parent's call).
  * Every hook gets its OWN copy of the call, and the effect runs on a pristine
    copy taken before any hook ran. The arguments a policy allowed are the
    arguments that execute; a hook that mutates its copy changes nothing.
  * `load_pipeline` is where "the harness never runs a partial pipeline" lives:
    a plug-in that is not registered, declares itself differently from the
    config, or has another API major version refuses the WHOLE pipeline.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping

from .config import ConfigError, SessionConfig, check_text
from .manifest import LoadedManifest, PluginRecord, source_sha256
from .plugin_api import (
    API_VERSION, Allow, BuildContext, Correct, Declaration, Deny, Drop, HarnessCall, NoOpinion, Ok, Pass,
    Plugin, PipelineOutcome, Redact, Refuse, Rewrite, SessionContext, Stop, ToolSpec,
)
from .primitives import Primitives, allowing, cleared


#: `Pipeline.stop` key for observer failures; no plug-in may take this name.
OBSERVER_ERRORS_KEY = "<observer-errors>"
#: Observer failures kept in full; the rest are counted.
MAX_OBSERVER_ERRORS = 100


class PluginError(ValueError):
    """The set of plug-ins is refused whole."""


def _str(v: Any) -> bool:
    return type(v) is str


#: A result of the right TYPE is still refused if its payload is malformed
#: (`Correct(None)` would otherwise crash the join that composes corrections).
_PAYLOAD: dict[type, Callable[[Any], bool]] = {
    Ok: lambda r: True,
    Pass: lambda r: True,
    NoOpinion: lambda r: True,
    Refuse: lambda r: _str(r.reason),
    Drop: lambda r: _str(r.reason),
    Stop: lambda r: _str(r.reason),
    Rewrite: lambda r: _str(r.message),
    Correct: lambda r: _str(r.note),
    Redact: lambda r: True,
    Allow: lambda r: _str(r.reason) and (r.policy is None or _str(r.policy)),
    Deny: lambda r: _str(r.reason) and (r.policy is None or _str(r.policy)),
}


def _check_declaration(decl: Any) -> Declaration:
    if type(decl) is not Declaration:
        raise PluginError(f"declares() returned {type(decl).__name__}, not a Declaration")
    try:
        check_text(decl.name, "declaration name")
        check_text(decl.version, f"plug-in {decl.name!r} version")
    except ConfigError as exc:
        raise PluginError(str(exc)) from None
    if decl.name == OBSERVER_ERRORS_KEY:
        raise PluginError(f"plug-in name {decl.name!r} is reserved")
    if type(decl.api_version) is not int:
        raise PluginError(f"plug-in {decl.name!r}: api_version must be an integer")
    if type(decl.tools) is not tuple or not all(type(t) is ToolSpec for t in decl.tools):
        raise PluginError(f"plug-in {decl.name!r}: tools must be a tuple of ToolSpec")
    if type(decl.services) is not tuple or not all(type(s) is str and s for s in decl.services):
        raise PluginError(f"plug-in {decl.name!r}: services must be a tuple of non-empty strings")
    for t in decl.tools:
        try:
            check_text(t.name, f"plug-in {decl.name!r} tool name")
        except ConfigError as exc:
            raise PluginError(str(exc)) from None
        if not callable(t.effect):
            raise PluginError(f"plug-in {decl.name!r}: tool {t.name!r} has no callable effect")
    return decl


def plugin_sha256(plugin: Plugin) -> str:
    """SHA-256 of the plug-in's source, byte for byte.

    ⚠️ Scope: the source file of EVERY class in the plug-in's MRO below `Plugin`
    (so behaviour inherited from a base in another file is covered). One file
    gives that file's own digest, which is what the spec records; several give
    the SHA-256 of their sorted digests. NOT covered: tool effect functions
    defined elsewhere and any helper module the plug-in imports, and two plug-ins
    defined in one file share a digest. The orchestrator pins per checkout
    (line endings are part of the bytes)."""
    files: list[str] = []
    for cls in type(plugin).__mro__:
        if cls is object or cls is Plugin:
            continue
        try:
            path = inspect.getsourcefile(cls)
        except (TypeError, OSError):
            path = None
        if path is None:
            raise PluginError(f"plug-in class {cls.__name__}: no source file to hash")
        if path not in files:
            files.append(path)
    if not files:
        raise PluginError("plug-in has no source file to hash")
    digests = sorted(source_sha256(p) for p in files)
    if len(digests) == 1:
        return digests[0]
    return hashlib.sha256("\n".join(digests).encode("ascii")).hexdigest()


class Pipeline:
    def __init__(self, plugins: Iterable[Plugin], ctx: SessionContext,
                 declared: Mapping[int, Declaration] | None = None) -> None:
        """`declared` maps `id(plugin)` to a declaration the CALLER already took
        (checked here like any other), so `declares()` is not called again. A
        fork's child pipeline uses it: no `declares()` runs inside the parent's
        allowed `fork` call (spec section 3)."""
        self._plugins = list(plugins)
        self._ctx = ctx
        self._decl: dict[int, Declaration] = {}
        self._tools: dict[str, ToolSpec] = {}
        self._observer_errors: list[str] = []
        self._observer_errors_dropped = 0
        names: list[str] = []
        owners: dict[str, str] = {}
        self._tool_services: dict[str, frozenset[str]] = {}
        for plugin in self._plugins:
            if declared is not None and id(plugin) in declared:
                raw = declared[id(plugin)]
            else:
                with cleared():
                    try:
                        raw = plugin.declares()
                    except Exception as exc:          # noqa: BLE001
                        raise PluginError(
                            f"plug-in {type(plugin).__name__}: declares() raised "
                            f"{type(exc).__name__}: {exc}") from exc
            decl = _check_declaration(raw)
            if decl.api_version != API_VERSION:
                raise PluginError(
                    f"plug-in {decl.name!r}: api_version {decl.api_version!r}, this harness is {API_VERSION}")
            if decl.name in names:
                raise PluginError(f"plug-in {decl.name!r} is loaded twice")
            names.append(decl.name)
            self._decl[id(plugin)] = decl
            for spec in decl.tools:
                if spec.name in owners:
                    raise PluginError(
                        f"tool {spec.name!r} is declared by both {owners[spec.name]!r} and {decl.name!r}")
                owners[spec.name] = decl.name
                self._tools[spec.name] = spec
                self._tool_services[spec.name] = frozenset(decl.services)

    @property
    def declarations(self) -> tuple[Declaration, ...]:
        """The snapshot taken at construction, in config order."""
        return tuple(self._decl[id(p)] for p in self._plugins)

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    # -- one guarded hook call ------------------------------------------------

    def _call(self, plugin: Plugin, hook: str, args: tuple[Any, ...],
              valid: tuple[type, ...], fail: Callable[[str], Any]) -> Any:
        name = self._decl[id(plugin)].name
        # each hook gets its own copy of the call: a mutation stays in that copy
        fresh = tuple(copy.deepcopy(a) if isinstance(a, HarnessCall) else a for a in args)
        with cleared():
            try:
                result = getattr(plugin, hook)(self._ctx, *fresh)
            except Exception as exc:                  # noqa: BLE001 - fails closed, says why
                return fail(f"plug-in {name!r} {hook} raised {type(exc).__name__}: {exc}")
        if type(result) not in valid:
            return fail(f"plug-in {name!r} {hook} returned {type(result).__name__}, "
                        f"which is not a result of that hook")
        if not _PAYLOAD[type(result)](result):
            return fail(f"plug-in {name!r} {hook} returned a malformed {type(result).__name__}, "
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

    @property
    def observer_errors(self) -> tuple[str, ...]:
        """Every `on_tool_outcome` that raised. Observers cannot alter a decision,
        but a broken one is never silent."""
        return tuple(self._observer_errors)

    @property
    def observer_errors_dropped(self) -> int:
        """Errors past `MAX_OBSERVER_ERRORS`, counted not kept."""
        return self._observer_errors_dropped

    def _note_observer_error(self, message: str) -> None:
        if len(self._observer_errors) < MAX_OBSERVER_ERRORS:
            self._observer_errors.append(message)
        else:
            self._observer_errors_dropped += 1

    def _observe(self, outcome: PipelineOutcome) -> None:
        # ⚠️ An uncopyable result (a lock, a handle, a generator) must not hide an
        # executed call from the ledger: observers get its repr instead.
        try:
            copy.deepcopy(outcome)
            safe = outcome
        except Exception as exc:                      # noqa: BLE001
            self._note_observer_error(
                f"outcome of {outcome.call.name!r} is not copyable ({type(exc).__name__}); "
                f"observers were given repr(result)")
            safe = replace(outcome, result=repr(outcome.result))
        for plugin in self._plugins:
            name = self._decl[id(plugin)].name
            with cleared():
                try:
                    plugin.on_tool_outcome(self._ctx, copy.deepcopy(safe))
                except Exception as exc:              # noqa: BLE001 - recorded, decision unchanged
                    self._note_observer_error(
                        f"plug-in {name!r} on_tool_outcome raised {type(exc).__name__}: {exc}")

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None,
                actor: str = "agent") -> PipelineOutcome:
        outcome = self._execute(name, arguments, actor)
        self._observe(outcome)
        return outcome

    def _execute(self, name: str, arguments: Mapping[str, Any] | None,
                 actor: str) -> PipelineOutcome:
        try:
            pristine = copy.deepcopy(dict(arguments or {}))
        except Exception as exc:                      # noqa: BLE001
            call = HarnessCall(name=name, arguments={}, actor=actor)
            return PipelineOutcome(call, Deny(f"arguments cannot be copied ({type(exc).__name__}); refused",
                                              "uncopyable-arguments"), executed=False)
        call = HarnessCall(name=name, arguments=copy.deepcopy(pristine), actor=actor)
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
        # the effect runs on the pristine copy: what was decided is what executes
        with allowing(HarnessCall(name=name, arguments=copy.deepcopy(pristine), actor=actor),
                      self._tool_services[name]):
            try:
                result = spec.effect(Primitives(), **copy.deepcopy(pristine))
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
            name = self._decl[id(plugin)].name
            with cleared():
                try:
                    out[name] = plugin.on_stop(self._ctx, run)
                except Exception as exc:              # noqa: BLE001
                    out[name] = {"error": f"{type(exc).__name__}: {exc}"}
        if self._observer_errors:
            out[OBSERVER_ERRORS_KEY] = list(self._observer_errors)
            if self._observer_errors_dropped:
                out[OBSERVER_ERRORS_KEY].append(f"... and {self._observer_errors_dropped} more")
        return out

    # -- what loaded -------------------------------------------------------------

    def manifest(self) -> LoadedManifest:
        records: list[PluginRecord] = []
        services: list[str] = []
        for plugin in self._plugins:
            decl = self._decl[id(plugin)]
            records.append(PluginRecord(decl.name, decl.version, decl.api_version, plugin_sha256(plugin)))
            services.extend(s for s in decl.services if s not in services)
        return LoadedManifest(plugins=tuple(records), tools=tuple(self._tools), services=tuple(services))


def _takes_context(factory: Callable[..., Plugin], name: str) -> bool:
    """`factory(config)` (the original form) or `factory(config, ctx)`.

    ⚠️ A v1 factory must be called EXACTLY as before, so the context is sent only
    when the factory asks for it: its second positional argument is REQUIRED, or it
    carries a `requires` mark (`harness.registry.requires`). An old `def f(config,
    strict=False)` therefore never has `strict` replaced by a context. Anything
    that accepts neither form is refused naming the fix. An uninspectable callable
    is the original form."""
    try:
        sig = inspect.signature(factory)
    except (TypeError, ValueError):
        return False
    one = two = True
    try:
        sig.bind({})
    except TypeError:
        one = False
    try:
        sig.bind({}, BuildContext({}))
    except TypeError:
        two = False
    if two and (not one or hasattr(factory, "requires")):
        return True
    if one:
        return False
    raise PluginError(f"plug-in {name!r}: its factory must accept (config) or (config, ctx)")


def _needs(factory: Callable[..., Plugin], ref_config: Mapping[str, Any], name: str,
           takes_ctx: bool) -> tuple[str, ...]:
    declared: Any = getattr(factory, "requires", ())
    needs: Any
    try:
        needs = declared(ref_config) if callable(declared) else declared
    except Exception as exc:                          # noqa: BLE001
        raise PluginError(f"plug-in {name!r}: its requires raised {type(exc).__name__}: {exc}") from exc
    if (type(needs) not in (tuple, list) or not all(type(n) is str and n for n in needs)
            or len(set(needs)) != len(needs)):
        raise PluginError(f"plug-in {name!r}: requires must be distinct non-empty names, not {needs!r}")
    if needs and not takes_ctx:
        raise PluginError(f"plug-in {name!r} declares requires {tuple(needs)} but its factory takes "
                          f"only (config); make it factory(config, ctx) to receive them")
    return tuple(needs)


def _build_order(names: list[str], needs: Mapping[str, tuple[str, ...]]) -> list[str]:
    """Dependencies first; config order otherwise. A missing need or a cycle is refused."""
    for name in names:
        for need in needs[name]:
            if need not in needs:
                raise PluginError(f"plug-in {name!r} requires {need!r}, which is not in the config")
    order: list[str] = []
    state: dict[str, int] = {}                        # 1 = in progress, 2 = done

    def visit(name: str, path: tuple[str, ...]) -> None:
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            cycle = path[path.index(name):] + (name,)
            raise PluginError("plug-in requirements form a cycle: " + " -> ".join(cycle))
        state[name] = 1
        for need in needs[name]:
            visit(need, path + (name,))
        state[name] = 2
        order.append(name)

    for name in names:
        visit(name, ())
    return order


def load_pipeline(config: SessionConfig,
                  registry: Mapping[str, Callable[..., Plugin]]) -> Pipeline:
    """Build the pipeline a config asks for, or refuse all of it.

    Factories run in dependency order (`requires`); the pipeline runs its hooks in
    CONFIG order. API_VERSION stays 1: the original `factory(config)` is unchanged."""
    names = [ref.name for ref in config.plugins]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise PluginError(f"plug-in {dupes[0]!r} is in the config twice")
    by_name = {ref.name: ref for ref in config.plugins}
    factories: dict[str, Callable[..., Plugin]] = {}
    takes: dict[str, bool] = {}
    needs: dict[str, tuple[str, ...]] = {}
    for ref in config.plugins:
        factory = registry.get(ref.name)
        if factory is None:
            raise PluginError(f"plug-in {ref.name!r} is in the config but not registered")
        factories[ref.name] = factory
        takes[ref.name] = _takes_context(factory, ref.name)
        needs[ref.name] = _needs(factory, ref.config, ref.name, takes[ref.name])
    built: dict[str, Plugin] = {}
    for name in _build_order(names, needs):
        ref = by_name[name]
        try:
            if takes[name]:
                built[name] = factories[name](ref.config, BuildContext(
                    {n: built[n] for n in needs[name]}, frozenset(names[:names.index(name)])))
            else:
                built[name] = factories[name](ref.config)
        except PluginError:
            raise
        except Exception as exc:                      # noqa: BLE001
            raise PluginError(f"plug-in {name!r}: its factory raised {type(exc).__name__}: {exc}") from exc
    plugins = [built[n] for n in names]
    pipeline = Pipeline(plugins, SessionContext(session_id=config.session_id))
    configured = {s.name for s in config.services}
    declared_services = {s for decl in pipeline.declarations for s in decl.services}
    for decl in pipeline.declarations:
        for s in decl.services:
            if s not in configured:
                raise PluginError(f"plug-in {decl.name!r} declares service {s!r}, which the config does not open")
    for s in sorted(configured - declared_services):
        raise PluginError(f"service {s!r} is opened by the config but no plug-in declares it")
    for ref, decl in zip(config.plugins, pipeline.declarations):
        if decl.name != ref.name:
            raise PluginError(f"plug-in {ref.name!r}: the registered plug-in declares itself {decl.name!r}")
        if decl.version != ref.version:
            raise PluginError(
                f"plug-in {ref.name!r}: config version {ref.version!r}, declared version {decl.version!r}")
    return pipeline
