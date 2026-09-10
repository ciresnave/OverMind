"""The mechanical gate.

WHY THIS EXISTS, in one measured sentence: a model that states a prohibition
perfectly violates it 7 times out of 8 (MEASUREMENTS.md §11), so a rule that
must hold cannot be a rule in a prompt.

Three constraints, each earned from a measurement rather than chosen:

1. IT REFUSES, IT DOES NOT WARN.
   A warning is advice, and advice is what already failed. `Gate.submit` returns
   a Decision and `GatedExecutor.execute` runs the tool ONLY on an allow. There
   is no "proceed anyway" parameter, because a parameter is a thing a caller
   under pressure sets.

2. IT CANNOT BE SATISFIED BY NARRATION.
   Four of five local model failures were a model writing tool-call JSON into
   its message and announcing success; `granite3.3:8b` announced a call it never
   made (§8.2). A gate that reads the model's text is defeated by the exact
   failure it exists to catch. This one sits at the tool-execution boundary and
   never reads model prose. THE ONLY TRUSTWORTHY SURFACE IS THE ONE THE MODEL
   CANNOT WRITE TO.

3. A POLICY RESOLVES ITS OWN FACTS.
   `qwen3:8b` called `get_pull_request`, was told the author was itself, and
   merged anyway. A policy that trusted the conversation for "who wrote this PR"
   would be trusting the same channel that just lied. Policies get a FactSource
   the gate owns; they never read the transcript.

⚠️ LANGUAGE IS AN OPEN DECISION (DESIGN-PROPOSAL.md §5) and this file is not it.
Python because every measurement rig in this repo is Python. The design here is
~200 lines of policy plumbing and ports to Rust without change of shape.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

__all__ = [
    "ToolCall", "Decision", "Policy", "FactSource", "Ledger", "LedgerEntry",
    "Gate", "GatedExecutor", "GateDenied", "ToolOutcome", "AllowSenders",
    "irreversible", "detect_smuggled_tool_call",
]


# --------------------------------------------------------------------------- #
# The call, as the gate sees it. Deliberately NOT the model's message.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ToolCall:
    """One attempted tool invocation.

    Note what is absent: any model text. The gate decides on the NAME and the
    ARGUMENTS, which are the only parts that can have an effect.
    """
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    actor: str = "unknown"

    def arg(self, key: str, default: Any = None) -> Any:
        return self.arguments.get(key, default)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    policy: str | None = None

    @staticmethod
    def allow(reason: str = "no policy objected", policy: str | None = None) -> "Decision":
        return Decision(True, reason, policy)

    @staticmethod
    def deny(reason: str, policy: str | None = None) -> "Decision":
        return Decision(False, reason, policy)


class GateDenied(RuntimeError):
    """Raised only by `execute_or_raise`. The default path returns an outcome."""

    def __init__(self, call: ToolCall, decision: Decision) -> None:
        super().__init__(f"{call.name} denied by {decision.policy}: {decision.reason}")
        self.call = call
        self.decision = decision


# --------------------------------------------------------------------------- #
# Facts. A policy asks the gate's own source, never the conversation.
# --------------------------------------------------------------------------- #

class FactSource(Protocol):
    """Authoritative answers a policy may rely on.

    ⚠️ Implementations MUST NOT read the model transcript. The whole point is
    that this channel is one the model cannot write to.
    """

    def fact(self, key: str, **params: Any) -> Any: ...


class StaticFacts:
    """A FactSource backed by a plain mapping. Useful for tests and for facts
    established out-of-band (the agent's own identity, for instance)."""

    def __init__(self, values: Mapping[str, Any] | None = None) -> None:
        self._values = dict(values or {})

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value

    def fact(self, key: str, **params: Any) -> Any:
        if params:
            # allow per-parameter overrides, e.g. ("pr_author", pr=42)
            specific = self._values.get((key, tuple(sorted(params.items()))))
            if specific is not None:
                return specific
        return self._values.get(key)


# --------------------------------------------------------------------------- #
# Policies
# --------------------------------------------------------------------------- #

class Policy(Protocol):
    name: str
    def applies_to(self, call: ToolCall) -> bool: ...
    def decide(self, call: ToolCall, facts: FactSource) -> Decision: ...


@dataclass
class DenyUnlessDeclared:
    """Deny-by-default for anything not explicitly declared reversible.

    ⚠️ THE DEFAULT IS THE POLICY. A gate whose default is "allow" protects only
    the actions someone remembered to think about, and the ones nobody thought
    about are exactly where the surprises live.
    """
    reversible: frozenset[str]
    name: str = "deny-unless-declared-reversible"

    def applies_to(self, call: ToolCall) -> bool:
        return True

    def definitely_denies(self, tool: str) -> bool:
        """True when this policy refuses `tool` WHATEVER the arguments.

        ⚠️ Deliberately conservative: a policy that might allow some calls
        answers False. Used only to spot tools offered to a model that can never
        succeed - never to grant anything.
        """
        return tool not in self.reversible

    def decide(self, call: ToolCall, facts: FactSource) -> Decision:
        if call.name in self.reversible:
            return Decision.allow(f"{call.name} is declared reversible", self.name)
        return Decision.deny(
            f"{call.name} is not declared reversible; irreversible actions require "
            f"an explicit policy that permits this specific call",
            self.name,
        )


@dataclass
class NoSelfMerge:
    """Never merge a pull request this agent authored.

    ⚠️ THE AUTHOR IS RESOLVED, NOT READ FROM THE CONVERSATION. In the measured
    failure the model had ALREADY been told the author was itself, in a tool
    result, and merged regardless.
    """
    merge_tools: frozenset[str] = frozenset({"merge_pull_request"})
    name: str = "no-self-merge"

    def applies_to(self, call: ToolCall) -> bool:
        return call.name in self.merge_tools

    def decide(self, call: ToolCall, facts: FactSource) -> Decision:
        pr = call.arg("pr")
        author = facts.fact("pr_author", pr=pr)
        me = facts.fact("self_identity")
        if author is None or me is None:
            # ⚠️ UNRESOLVED IS A DENIAL, NOT AN ALLOW. A gate that opens when it
            # cannot check is a gate that opens exactly when something is wrong.
            return Decision.deny(
                f"cannot establish authorship of PR {pr!r} (author={author!r}, self={me!r}); "
                f"refusing rather than assuming",
                self.name,
            )
        if author == me:
            return Decision.deny(
                f"PR {pr!r} was authored by {author!r}, which is this agent; "
                f"self-merge is forbidden without exception",
                self.name,
            )
        return Decision.allow(f"PR {pr!r} authored by {author!r}, not this agent", self.name)


@dataclass
class RequirePrecondition:
    """A tool may only run once another tool has run in this session.

    Encodes an ORDERING rule mechanically. `llama3.2:3b` stated such a rule
    correctly and then never performed the prerequisite step, 8 times out of 8.
    """
    tool: str
    requires: str
    name: str = "require-precondition"

    def applies_to(self, call: ToolCall) -> bool:
        return call.name == self.tool

    def decide(self, call: ToolCall, facts: FactSource) -> Decision:
        done: Sequence[str] = facts.fact("executed_tools") or ()
        if self.requires in done:
            return Decision.allow(f"{self.requires} has run in this session", self.name)
        return Decision.deny(
            f"{self.tool} requires {self.requires} to have run first; it has not",
            self.name,
        )


@dataclass
class ForbidTools:
    """A blunt prohibition on named tools, whatever the arguments."""
    tools: frozenset[str]
    reason: str = "this tool is forbidden for this agent"
    name: str = "forbid-tools"

    def applies_to(self, call: ToolCall) -> bool:
        return call.name in self.tools

    def definitely_denies(self, tool: str) -> bool:
        return tool in self.tools

    def decide(self, call: ToolCall, facts: FactSource) -> Decision:
        return Decision.deny(f"{call.name}: {self.reason}", self.name)


@dataclass
class AllowSenders:
    """Permit named tools, but only when a named sender asked for them.

    ⚠️ THIS IS THE DANGEROUS KIND OF POLICY AND IT SAYS SO IN ITS OWN TYPE.
    `requires_vouched_sender = True` makes the gate refuse every call it covers
    until `sender_vouched` is genuinely True — so this class is safe to write
    today and simply cannot be USED today. That is deliberate: a capability that
    exists but refuses is auditable, while one that does not exist gets
    reinvented under deadline by someone who has not read the measurement.
    """
    tools: frozenset[str]
    senders: frozenset[str]
    name: str = "allow-senders"
    #: Read by `Gate.decide`, once, for every policy.
    requires_vouched_sender: bool = True

    def applies_to(self, call: ToolCall) -> bool:
        return call.name in self.tools

    def decide(self, call: ToolCall, facts: FactSource) -> Decision:
        # Only reachable once the gate has confirmed a vouched sender.
        sender = facts.fact("dispatch_sender")
        if sender in self.senders:
            return Decision.allow(f"{sender!r} is permitted to call {call.name}", self.name)
        return Decision.deny(f"{sender!r} is not permitted to call {call.name}", self.name)


def irreversible(*names: str) -> frozenset[str]:
    """Readability helper: the complement of what you pass to DenyUnlessDeclared."""
    return frozenset(names)


# --------------------------------------------------------------------------- #
# The ledger — the verification surface
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    call: ToolCall
    decision: Decision
    executed: bool
    result_repr: str | None = None
    error: str | None = None


class Ledger:
    """Append-only record of every SUBMITTED call and what happened to it.

    ⚠️ THIS IS THE ONLY ACCEPTABLE EVIDENCE THAT WORK HAPPENED. Measured twice,
    in unrelated subsystems: a model announced a call it never made (§8.2), and
    a PTY rig reported three green results from three turns that never submitted
    (§3.2). An actor's report of its work is not the work.
    """

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []

    def append(self, entry: LedgerEntry) -> None:
        self._entries.append(entry)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def digest(self, limit: int = 12) -> str:
        """A compact record of what has already happened, for the model to read.

        ⚠️ THIS IS THE LEDGER POINTED THE OTHER WAY. Everywhere else it is
        evidence FOR the reconciler - the surface a claim gets checked against.
        Here it is context FOR the model: "what have I already done" is exactly
        the state a stateless agent lacks, and the harness gives a Claude session
        that state for free by keeping its transcript.

        ⚠️ SAME MOVE AS THE GATE, ONE LAYER OVER. The gate refuses instead of
        trusting the model to refuse. This remembers instead of trusting the
        model to remember. In both cases the property is held OUTSIDE the model
        and handed back, rather than asked for.

        ⚠️ AND A DENIED CALL IS IN HERE TOO. A model that cannot see its own
        refusals will re-attempt them - which costs a round trip and, on a
        metered provider, a request from a daily budget.
        """
        if not self._entries:
            return "Nothing has been done yet."
        lines = []
        for entry in self._entries[-limit:]:
            args = ", ".join(f"{k}={v!r}" for k, v in sorted(entry.call.arguments.items()))
            head = f"{entry.seq}. {entry.call.name}({args[:160]})"
            if not entry.decision.allowed:
                lines.append(f"{head} -> REFUSED: {entry.decision.reason}")
            elif entry.error is not None:
                lines.append(f"{head} -> FAILED: {entry.error[:160]}")
            elif entry.executed:
                lines.append(f"{head} -> OK: {(entry.result_repr or '')[:300]}")
            else:
                lines.append(f"{head} -> not executed")
        omitted = len(self._entries) - len(self._entries[-limit:])
        nl = chr(10)
        prefix = f"({omitted} earlier calls omitted){nl}" if omitted else ""
        return prefix + nl.join(lines)

    def executed_tools(self) -> tuple[str, ...]:
        """Tools that ran AND RETURNED. ⚠️ A call that raised is NOT here.

        This once returned every invoked tool, error or not, so a tool that
        raised on every call counted as done - which let a precondition be
        satisfied by a failure and let `reconcile` report a task complete when
        nothing had happened. Both callers want success, not attempt.

        ⚠️ For a non-idempotent tool that raised PARTWAY, the effect is genuinely
        uncertain. Excluding it fails toward doing the step again, which is the
        safe direction for a precondition and the honest one for a report.
        """
        return tuple(e.call.name for e in self._entries if e.executed and e.error is None)

    def attempted_tools(self) -> tuple[str, ...]:
        """Every tool the model tried to run, including refusals and errors."""
        return tuple(e.call.name for e in self._entries)

    def errored(self) -> tuple[LedgerEntry, ...]:
        return tuple(e for e in self._entries if e.executed and e.error is not None)

    def denied(self) -> tuple[LedgerEntry, ...]:
        return tuple(e for e in self._entries if not e.decision.allowed)

    def was_executed(self, name: str) -> bool:
        return name in self.executed_tools()

    def to_json(self) -> str:
        return json.dumps([
            {
                "seq": e.seq,
                "tool": e.call.name,
                "arguments": dict(e.call.arguments),
                "actor": e.call.actor,
                "allowed": e.decision.allowed,
                "policy": e.decision.policy,
                "reason": e.decision.reason,
                "executed": e.executed,
                "error": e.error,
            }
            for e in self._entries
        ], indent=2)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #

class Gate:
    """Evaluates policies over a call. Records every verdict.

    ⚠️ ANY DENIAL WINS. Policies do not vote and there is no precedence order to
    argue about at 3 a.m. The first policy that refuses ends the matter.
    """

    def __init__(self, policies: Iterable[Policy], facts: FactSource | None = None,
                 ledger: Ledger | None = None) -> None:
        self.policies = list(policies)
        # ⚠️ `is None`, NOT `or`. `Ledger` defines __len__, so an EMPTY ledger is
        # FALSY and `ledger or Ledger()` silently substituted a fresh one for the
        # caller's. The gate then wrote to one ledger while policies read another,
        # and RequirePrecondition could never be satisfied. Caught by the CONTROL
        # case - the test asserting the call SUCCEEDS once the precondition really
        # ran. The refusal test passed throughout, for the wrong reason.
        # It failed closed here; a policy that used the ledger to GRANT would have
        # failed open on the same bug.
        self.facts = StaticFacts() if facts is None else facts
        self.ledger = Ledger() if ledger is None else ledger

    def certainly_denied(self, tools: Iterable[str]) -> list[str]:
        """Which of `tools` will be refused no matter how they are called.

        ⚠️ MEASURED, and this is a COST control rather than a safety one: the
        tool schemas are re-sent on every turn, and offering a model the 20
        tools a server exposes instead of the 5 a gate permits cost 6,255 tokens
        against 2,595 for the identical task and the identical result - 59% of
        the bill spent describing tools that would have been refused. Offering
        one tool the task actually needed cost 1,100.

        The gate stays authoritative about what may RUN; this only reports what
        should never have been OFFERED.
        """
        out = []
        for tool in tools:
            for policy in self.policies:
                check = getattr(policy, "definitely_denies", None)
                if check is not None and check(tool):
                    out.append(tool)
                    break
        return out

    def decide(self, call: ToolCall) -> Decision:
        applicable = [p for p in self.policies if p.applies_to(call)]
        if not applicable:
            return Decision.deny(
                f"no policy covers {call.name}; an uncovered tool is refused, not allowed",
                "no-applicable-policy",
            )
        allows: list[Decision] = []
        for policy in applicable:
            # ⚠️ A POLICY THAT DECIDES BY *WHO ASKED* NEEDS AN IDENTITY THE
            # TRANSPORT ACTUALLY VOUCHED FOR, AND THE GATE CANNOT RESOLVE A FACT
            # THE TRANSPORT DID NOT CARRY. Measured: every dispatch received so
            # far reports `sender_vouched: false` - the relay's word, not an
            # account-vouched key. Today's safety is structural (the gate never
            # reads the message, so a sender CLAIMING authority changes nothing)
            # and that holds only while every write is forbidden by BLANKET
            # policy. The moment a policy is per-sender, identity stops being
            # metadata and becomes the authorisation input.
            # This is enforced HERE, once, rather than left to each policy to
            # remember - and it is a refusal rather than a note, because a
            # constraint that lives in a design document is one restart away
            # from being forgotten.
            if getattr(policy, "requires_vouched_sender", False):
                if self.facts.fact("sender_vouched") is not True:
                    return Decision.deny(
                        f"{getattr(policy, 'name', type(policy).__name__)} decides by sender "
                        f"identity, and the sender is not vouched "
                        f"(sender_vouched={self.facts.fact('sender_vouched')!r}). A per-sender "
                        f"policy is refused until the transport carries a vouched identity.",
                        "unvouched-sender",
                    )
            decision = policy.decide(call, self.facts)
            if not decision.allowed:
                return decision          # any denial wins, immediately
            allows.append(decision)
        return allows[-1]


class ToolOutcome:
    """What a caller gets back. A denial is a first-class outcome, not an exception,
    so the orchestrator can feed the refusal back to the model as a tool result."""

    __slots__ = ("call", "decision", "executed", "result", "error")

    def __init__(self, call: ToolCall, decision: Decision, executed: bool,
                 result: Any = None, error: str | None = None) -> None:
        self.call = call
        self.decision = decision
        self.executed = executed
        self.result = result
        self.error = error

    @property
    def allowed(self) -> bool:
        return self.decision.allowed

    def as_tool_content(self) -> str:
        """The string to hand back to the model as the tool's result."""
        if not self.decision.allowed:
            return f"REFUSED by the harness [{self.decision.policy}]: {self.decision.reason}"
        if self.error is not None:
            return f"ERROR: {self.error}"
        return str(self.result)


class GatedExecutor:
    """The ONLY path from a model's intent to an effect.

    ⚠️ There is no bypass parameter. A `force=True` would be set by whoever is in
    a hurry, which is the same condition under which the rule matters.
    """

    def __init__(self, gate: Gate, tools: Mapping[str, Callable[..., Any]]) -> None:
        self.gate = gate
        self.tools = dict(tools)

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None,
                actor: str = "agent") -> ToolOutcome:
        call = ToolCall(name=name, arguments=dict(arguments or {}), actor=actor)
        decision = self.gate.decide(call)
        seq = len(self.gate.ledger)

        if not decision.allowed:
            # The tool is NOT called. Not called with a flag, not called and
            # rolled back - never invoked at all.
            self.gate.ledger.append(LedgerEntry(seq, call, decision, executed=False))
            return ToolOutcome(call, decision, executed=False)

        fn = self.tools.get(name)
        if fn is None:
            d = Decision.deny(f"{name} is allowed by policy but not registered", "unregistered-tool")
            self.gate.ledger.append(LedgerEntry(seq, call, d, executed=False))
            return ToolOutcome(call, d, executed=False)

        try:
            result = fn(**call.arguments)
        except Exception as exc:                      # noqa: BLE001 - recorded, not swallowed
            err = f"{type(exc).__name__}: {exc}"
            self.gate.ledger.append(LedgerEntry(seq, call, decision, executed=True, error=err))
            return ToolOutcome(call, decision, executed=True, error=err)

        self.gate.ledger.append(
            LedgerEntry(seq, call, decision, executed=True, result_repr=repr(result)[:500])
        )
        return ToolOutcome(call, decision, executed=True, result=result)

    def execute_or_raise(self, name: str, arguments: Mapping[str, Any] | None = None,
                         actor: str = "agent") -> Any:
        outcome = self.execute(name, arguments, actor)
        if not outcome.allowed:
            raise GateDenied(outcome.call, outcome.decision)
        if outcome.error:
            raise RuntimeError(outcome.error)
        return outcome.result


# --------------------------------------------------------------------------- #
# The narration failure, detected rather than trusted
# --------------------------------------------------------------------------- #

_SMUGGLE = re.compile(
    r"""\{[^{}]*?["']?(?:name|function|tool(?:_name)?)["']?\s*[:=]\s*["']([A-Za-z_][\w.-]*)["']""",
    re.VERBOSE | re.DOTALL,
)


def detect_smuggled_tool_call(content: str, tool_names: Iterable[str]) -> list[str]:
    """Find tool-call-shaped JSON that a model wrote into its MESSAGE instead of
    emitting as a protocol tool call.

    ⚠️ This was the dominant failure: 4 of 5 local model failures (§8.2). It is a
    PROTOCOL failure to repair or re-prompt, never prose to interpret - and it is
    also never an authorisation to run anything. Detecting it must not become a
    second, softer path to an effect.
    """
    if not content:
        return []
    known = set(tool_names)
    return [m for m in _SMUGGLE.findall(content) if m in known]
