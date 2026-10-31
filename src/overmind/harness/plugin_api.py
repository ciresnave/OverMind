# SPDX-License-Identifier: MIT OR Apache-2.0
"""The plug-in interface (ARCHITECTURE-LAYERS-SPEC.md §2).

A plug-in is an object with a declaration and any subset of seven hooks. Every
hook receives a `SessionContext` and returns one of a small closed set of
result types the harness acts on.

⚠️ NO RESULT MEANS "PROCEED ANYWAY". That is gate.py's first constraint, kept:
`Allow` only says this plug-in does not object to a tool call, and any `Deny`
from any plug-in still wins. A hook that raises, or returns a result that is
not one of its own hook's types, is treated as the most restrictive answer for
that hook (refuse, drop, stop, deny); the pipeline never reads a failure as a
pass.

One addition to the spec's hook table, not a change of it: `NoOpinion` for
`before_tool_call`. `Gate.decide` asks only the policies that APPLY to a call
and denies a call that none applies to (gate.py:517-553); a plug-in that has
nothing to say about a tool needs a way to say so that is not `Allow`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

#: The plug-in API's major version. The harness refuses a plug-in whose
#: `api_version` differs and never runs a partial pipeline (spec §2).
API_VERSION = 1


# --- results ---------------------------------------------------------------

@dataclass(frozen=True)
class Ok:
    """on_session_start: the session may start."""


@dataclass(frozen=True)
class Refuse:
    """on_session_start: the session does not start."""
    reason: str


@dataclass(frozen=True)
class Pass:
    """No change (messages, model output, tool results)."""


@dataclass(frozen=True)
class Rewrite:
    """on_message_in: replace the inbound message."""
    message: str


@dataclass(frozen=True)
class Drop:
    """on_message_in / on_message_out: the message does not go on."""
    reason: str


@dataclass(frozen=True)
class Redact:
    """on_message_out / after_tool_call: replace what leaves or what the model sees."""
    value: Any


@dataclass(frozen=True)
class Correct:
    """on_model_output: ask the model for another turn, with this note."""
    note: str


@dataclass(frozen=True)
class Stop:
    """on_model_output: end the session."""
    reason: str


@dataclass(frozen=True)
class ModelStep:
    """What `on_model_step` is told: THAT a model step happened, never what was said
    (no text, no arguments), so an activity reporter cannot leak content."""
    step: int                                 # 0-based index in the run
    model: str | None
    provider: str | None
    tool_calls: int                           # how many the model asked for in this turn
    truncated: bool                           # the reply was cut off by the output budget


@dataclass(frozen=True)
class Allow:
    """before_tool_call: this plug-in does not object. Not a bypass."""
    reason: str = "no policy objected"
    policy: str | None = None


@dataclass(frozen=True)
class Deny:
    """before_tool_call: refuse. Any Deny wins."""
    reason: str
    policy: str | None = None


@dataclass(frozen=True)
class NoOpinion:
    """before_tool_call: nothing here covers this tool."""


# --- what a factory is handed ------------------------------------------------

@dataclass(frozen=True)
class BuildContext:
    """What a registry factory may ask for besides its own config: the plug-ins
    it NAMED in `requires` (see `harness.registry.requires`), already built, by
    name. Nothing else: a factory sees only what it declared it needs."""
    built: Mapping[str, "Plugin"]
    #: Names of the plug-ins that come BEFORE this one in the config, i.e. whose
    #: hooks run before its own. A factory whose plug-in must follow another in
    #: hook order (the fork follows the ledger) checks it here, at load.
    earlier: frozenset[str] = frozenset()


# --- what a hook is handed --------------------------------------------------

@dataclass(frozen=True)
class SessionContext:
    session_id: str


@dataclass(frozen=True)
class HarnessCall:
    """One attempted tool call, as the pipeline sees it. Like `gate.ToolCall`,
    deliberately NOT the model's message."""
    name: str
    arguments: Mapping[str, Any] = field(default_factory=lambda: dict[str, Any]())
    actor: str = "unknown"


@dataclass(frozen=True)
class PipelineOutcome:
    """What became of one submitted call, DENIED ones included. `result` is what
    the model is shown (after every `after_tool_call` redaction), so an observer
    never sees more than the model does."""
    call: HarnessCall
    decision: Allow | Deny
    executed: bool
    result: Any = None
    error: str | None = None


# --- the declaration --------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    """A tool a plug-in contributes. `effect(primitives, **arguments)` does its
    I/O only through the `primitives` it is handed, which refuse unless this
    very call was allowed (primitives.py)."""
    name: str
    schema: Mapping[str, Any]
    effect: Callable[..., Any]


@dataclass(frozen=True)
class Declaration:
    name: str
    version: str
    api_version: int
    tools: tuple[ToolSpec, ...] = ()
    #: Services the plug-in needs the HARNESS to open (spec §2). Recorded in the
    #: manifest; opening them is a later piece.
    services: tuple[str, ...] = ()


class Plugin:
    """Base class: every hook has a neutral default, so a plug-in overrides only
    what it uses. `declares` has none: a plug-in must say what it is."""

    def declares(self) -> Declaration:
        raise NotImplementedError

    def on_session_start(self, ctx: SessionContext) -> Ok | Refuse:
        return Ok()

    def on_message_in(self, ctx: SessionContext, msg: str) -> Pass | Rewrite | Drop:
        return Pass()

    def on_message_out(self, ctx: SessionContext, msg: str) -> Pass | Redact | Drop:
        return Pass()

    def on_model_output(self, ctx: SessionContext, turn: Any) -> Pass | Correct | Stop:
        return Pass()

    def before_tool_call(self, ctx: SessionContext, call: HarnessCall) -> Allow | Deny | NoOpinion:
        return NoOpinion()

    def after_tool_call(self, ctx: SessionContext, call: HarnessCall, result: Any) -> Pass | Redact:
        return Pass()

    def on_tool_outcome(self, ctx: SessionContext, outcome: "PipelineOutcome") -> None:
        """Observe-only (eighth hook, additive; API_VERSION unchanged). Called once
        per submitted call with a private copy of the outcome, denied calls
        included. The return value is ignored: an observer cannot change or veto
        anything. An exception is recorded in `Pipeline.observer_errors`."""
        return None

    def on_model_step(self, ctx: SessionContext, step: "ModelStep") -> None:
        """Observe-only (ninth hook, additive; API_VERSION unchanged). Called once per
        model call that returned, before any tool call in it runs. The return value
        is ignored and an exception is recorded in `Pipeline.observer_errors`: it
        can neither stop nor correct the step (that is `on_model_output`'s job, and
        that one fails closed). Meant for activity reporting."""
        return None

    def on_stop(self, ctx: SessionContext, run: Any) -> Any:
        return None
