"""The agent loop — a model, its tools, and the gate between them.

⚠️ THE LOOP NEVER DERIVES STATE FROM THE MODEL'S TEXT. Every effect goes through
`GatedExecutor`, and the ledger is the only record consulted afterwards. Measured
twice in unrelated subsystems (MEASUREMENTS.md §8.2, §3.2): `granite3.3:8b`
announced a call it never made, and a PTY rig reported three green results from
three turns that never submitted. AN ACTOR'S REPORT OF ITS WORK IS NOT THE WORK,
and in both cases the wrong answer matched expectation exactly.

TWO FAILURE MODES THE LOOP HANDLES BECAUSE THEY WERE MEASURED, NOT IMAGINED:

  · SMUGGLED TOOL CALLS. Four of five local model failures were a model writing
    tool-call JSON into `content` instead of emitting a protocol tool call, then
    narrating success. That is a PROTOCOL failure to repair - never prose to
    interpret, and never a second path to an effect. The loop re-prompts once
    and then stops, because a model that cannot emit a tool call will not be
    argued into it.

  · A REFUSAL IS A RESULT, NOT AN ERROR. When the gate denies a call, the denial
    is fed back as the tool's result so the model can take the permitted route.
    A loop that raised here would turn every policy into an outage.

  · REPEATING THE SAME CALL CONSECUTIVELY IS NOT PROGRESS. The identical call is
    nudged ONCE with what it already returned, and stopped on the second repeat,
    because a model that has not moved after being told will not move.

    ⚠️ TWO CORRECTIONS TO THIS GUARD'S OWN HISTORY, both worth keeping.
    It was built because models called the first tool four and seven times on a
    two-step task - and §21 later showed THAT repetition was caused by a defect
    of mine making the tool raise on every call. The model was responding
    rationally to a broken tool. The guard is retained on its own merits, not
    that evidence.
    And it originally counted ANY repeat in a run, which blocked a legitimate
    RE-READ: `list -> send -> list` suppressed the second list and handed the
    model a STALE result immediately after it had changed the world. It now
    counts CONSECUTIVE repeats only. A, B, A is progress; A, A, A is stuck.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .gate import GatedExecutor, Ledger, detect_smuggled_tool_call
from .providers import ChatResult, ProviderClient, Usage, normalise_for_echo

__all__ = ["AgentRun", "StopReason", "run_agent"]


class StopReason:
    COMPLETED = "completed"                 # the model answered with no tool call
    MAX_STEPS = "max-steps"
    PROTOCOL_FAILURE = "protocol-failure"   # smuggled tool calls, twice
    PROVIDER_ERROR = "provider-error"
    NO_PROGRESS = "no-progress"             # same call repeated, twice over
    #: 🔴 The reply was CUT OFF by the output budget, not finished. Measured
    #: three times on three models, each time read as the model being unable or
    #: unwilling. It is neither - it is a setting.
    TRUNCATED = "truncated"


@dataclass
class AgentRun:
    """What happened. ⚠️ `ledger` is the evidence; `final_text` is testimony."""
    final_text: str
    stop_reason: str
    steps: int
    ledger: Ledger
    messages: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    provider: str | None = None
    error: str | None = None
    #: ⚠️ What this run actually COST, summed across every call the loop made -
    #: not the last one. The whole project exists to reduce this number, and it
    #: had never been recorded.
    usage: Usage = field(default_factory=Usage)
    #: Tools offered to the model that the gate would always refuse. ⚠️ Paid for
    #: on every turn and never usable.
    offered_but_refused: list[str] = field(default_factory=list)

    @property
    def executed_tools(self) -> tuple[str, ...]:
        return self.ledger.executed_tools()

    @property
    def denied_count(self) -> int:
        return len(self.ledger.denied())

    def did(self, tool: str) -> bool:
        """Did this tool ACTUALLY run? ⚠️ The only honest way to ask."""
        return self.ledger.was_executed(tool)


def _tool_result_message(call_id: str, name: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id or "call_1",
            "name": name, "content": content}


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def run_agent(client: ProviderClient, executor: GatedExecutor,
              tools: Sequence[Mapping[str, Any]], task: str, *,
              system: str = "", max_steps: int = 8,
              max_tokens: int | None = None) -> AgentRun:
    """Drive one task to completion through the gate.

    `tools` are OpenAI-shaped schemas; `executor` holds the callables. The two
    are deliberately separate: a schema the model can see is not permission to
    run anything, and the gate is what decides.
    """
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": task})

    offered_but_refused: list[str] = []
    tool_names = [t.get("function", {}).get("name") for t in tools]
    tool_names = [n for n in tool_names if n]

    # ⚠️ COST, MEASURED: schemas are re-sent on EVERY turn, so a tool the gate
    # will always refuse is paid for repeatedly and never usable. Offering 20
    # instead of the permitted 5 cost 6,255 tokens against 2,595 for the same
    # task and the same result. Reported rather than silently dropped - the
    # caller chose the offer, and a harness that quietly edits the tool list is
    # a harness whose behaviour cannot be predicted from its inputs.
    wasted = executor.gate.certainly_denied(tool_names)
    if wasted:
        offered_but_refused.extend(wasted)
    smuggle_strikes = 0
    result: ChatResult | None = None
    spent = Usage.zero()
    # ⚠️ CONSECUTIVE repetition, not session-wide. The first version counted any
    # repeat in the run, which blocked a legitimate RE-READ: `list -> send ->
    # list` suppressed the second list and handed the model a STALE cached
    # result immediately after it had changed the world. A, B, A is progress;
    # A, A, A is stuck, and only the second is worth stopping.
    repeat_signature: tuple[str, str] | None = None
    repeat_count = 0
    last_results: dict[tuple[str, str], str] = {}

    for step in range(max_steps):
        try:
            result = client.chat(messages, tools=tools, max_tokens=max_tokens)
        except Exception as exc:                       # noqa: BLE001 - reported, not hidden
            return AgentRun(final_text="", stop_reason=StopReason.PROVIDER_ERROR,
                            steps=step, ledger=executor.gate.ledger, messages=messages,
                            usage=spent, offered_but_refused=offered_but_refused,
                            error=f"{type(exc).__name__}: {exc}")

        spent = spent + result.usage
        messages.append(normalise_for_echo(result.message))
        calls = result.tool_calls

        if not calls and result.truncated:
            # ⚠️ CHECKED BEFORE "it is finished" AND BEFORE the smuggle check.
            # A truncated reply has no tool calls and often no text, which is
            # indistinguishable from a model that had nothing to say - and I
            # misread exactly that three times before the harness could tell me.
            return AgentRun(
                final_text=result.content, stop_reason=StopReason.TRUNCATED,
                steps=step + 1, ledger=executor.gate.ledger, messages=messages,
                model=result.model, provider=result.provider, usage=spent,
                offered_but_refused=offered_but_refused,
                error=(f"the reply was cut off by the output budget "
                       f"(finish_reason={result.finish_reason!r}); raise max_tokens. "
                       f"A thinking model spends this budget BEFORE it answers."))

        if not calls:
            # ⚠️ Before believing "it is finished", check whether it TRIED to call
            # a tool and merely wrote it in prose. That is the dominant failure.
            smuggled = detect_smuggled_tool_call(result.content, tool_names)
            if smuggled:
                smuggle_strikes += 1
                if smuggle_strikes > 1:
                    return AgentRun(
                        final_text=result.content, stop_reason=StopReason.PROTOCOL_FAILURE,
                        steps=step + 1, ledger=executor.gate.ledger, messages=messages,
                        model=result.model, provider=result.provider, usage=spent,
                        offered_but_refused=offered_but_refused,
                        error=f"model wrote tool-call JSON into its message twice "
                              f"({', '.join(smuggled)}) instead of emitting a tool call",
                    )
                messages.append({
                    "role": "user",
                    "content": (
                        f"You wrote what looks like a call to {smuggled[0]} inside your "
                        f"message. That does nothing - it was not executed. Emit it as a "
                        f"real tool call, or answer without one."
                    ),
                })
                continue
            return AgentRun(final_text=result.content, stop_reason=StopReason.COMPLETED,
                            steps=step + 1, ledger=executor.gate.ledger, messages=messages,
                            model=result.model, provider=result.provider, usage=spent,
                            offered_but_refused=offered_but_refused)

        repeated_twice = False
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            args = _parse_arguments(fn.get("arguments"))
            signature = (name, json.dumps(args, sort_keys=True, default=str))
            if signature != repeat_signature:
                repeat_signature, repeat_count = signature, 0
            count = repeat_count

            if count >= 2:
                # ⚠️ Told once and repeated anyway. Stop rather than spend.
                repeated_twice = True
                messages.append(_tool_result_message(
                    call.get("id", ""), name,
                    "STOPPED: this identical call has already been made twice. "
                    "Nothing new will come of it."))
                continue

            if count == 1:
                # ⚠️ NOT re-executed. Re-running it would be an EFFECT the model
                # did not earn - and for a non-idempotent tool that is a second
                # channel, a second message, a second merge.
                repeat_count = count + 1
                messages.append(_tool_result_message(
                    call.get("id", ""), name,
                    f"ALREADY CALLED with these exact arguments. It was not run again. "
                    f"Its previous result was: {last_results.get(signature, '(unknown)')[:400]} "
                    f"Move on to the next step, or say what is blocking you."))
                continue

            repeat_count = 1
            # THE ONLY PATH TO AN EFFECT.
            outcome = executor.execute(name, args, actor=f"{result.provider}:{result.model}")
            content = outcome.as_tool_content()
            last_results[signature] = content
            messages.append(_tool_result_message(call.get("id", ""), name, content))

        if repeated_twice:
            return AgentRun(final_text=result.content, stop_reason=StopReason.NO_PROGRESS,
                            steps=step + 1, ledger=executor.gate.ledger, messages=messages,
                            model=result.model, provider=result.provider, usage=spent,
                            offered_but_refused=offered_but_refused,
                            error="the model repeated an identical call after being told it had "
                                  "already been made")

    return AgentRun(final_text=result.content if result else "",
                    stop_reason=StopReason.MAX_STEPS, steps=max_steps,
                    ledger=executor.gate.ledger, messages=messages,
                    model=result.model if result else None,
                    provider=result.provider if result else None, usage=spent,
                    offered_but_refused=offered_but_refused)
