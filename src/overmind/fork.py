# SPDX-License-Identifier: MIT OR Apache-2.0
"""Fork-and-curate: run a sub-task in a copy of the agent's own conversation.

Spec: SUBAGENT-FORK-AND-AUDITOR-DESIGN.md. The fork inherits the parent's
messages, runs through the parent's gate (plus any extra denials, never fewer),
and hands back a curated summary AND its ledger. The parent never ingests the
fork's raw transcript.

⚠️ THE SUMMARY IS TESTIMONY; THE LEDGER IS THE EVIDENCE (agent.py:3-9). A fork
is an actor like any other, and its account of its own work is not the work.
Every effect it caused is absorbed into the parent's ledger, and the parent
model is shown that ledger beside the summary.

⚠️ SEQUENTIAL. The parent blocks while its fork runs. Parallel forks need a
merge-semantics design that does not exist yet (spec §6) and are out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .agent import AgentRun, StopReason, _tool_result_message, run_agent
from .gate import (FactSource, ForbidTools, Gate, GatedExecutor, Ledger, LedgerFacts,
                   Policy, ToolOutcome)
from .providers import Usage

__all__ = ["CURATE_INSTRUCTION", "Curation", "FORK_TOOL", "ForkConfig", "ForkCost",
           "ForkFacts", "ForkResult", "curate_now", "execute_fork", "fork_schema",
           "run_fork"]

#: ⚠️ "State results, not steps": the harness hands the parent the fork's
#: ledger separately, so a narrative of what was run is redundant at best and
#: contradicts the evidence at worst.
CURATE_INSTRUCTION = (
    "Stop working now and call no tool. Reply in plain text with only what the "
    "agent that forked you needs from this work: what you found, what you "
    "changed, what is unfinished, and anything it must not assume. It already "
    "has everything that came before the fork, and the harness sends it your "
    "execution ledger separately, so state results rather than retelling steps.")


@dataclass
class Curation:
    text: str
    usage: Usage
    truncated: bool
    error: str | None = None


def curate_now(client: Any, messages: Sequence[Mapping[str, Any]], *,
               tools: Sequence[Mapping[str, Any]] | None = None,
               max_tokens: int | None = None) -> Curation:
    """Ask the agent whose transcript this is for a curated summary, now.

    Works at ANY step boundary, not only at completion - the auditor will
    call it on a fork it is about to stop, so that a relaunch does not start
    from zero (spec §3).

    ⚠️ `tools` should be the same schemas the transcript was produced with:
    they are part of the rendered prompt, and changing them changes the prefix
    a provider can reuse. The model is still told to call none, and a call it
    makes anyway is reported in `error` and NOT executed - this function has
    no executor, so it cannot cause an effect.
    """
    if messages:
        last = messages[-1]
        if last.get("role") == "assistant" and last.get("tool_calls"):
            raise ValueError("the transcript ends in tool calls with no results; "
                             "answer them before asking for a summary")
    asked = [dict(m) for m in messages]
    asked.append({"role": "user", "content": CURATE_INSTRUCTION})
    try:
        result = client.chat(asked, tools=tools, max_tokens=max_tokens)
    except Exception as exc:                       # noqa: BLE001 - reported, not hidden
        # ⚠️ Usage() is reported=False: the call failed and said nothing, which
        # is not the same fact as costing nothing.
        return Curation("", Usage(), False, error=f"{type(exc).__name__}: {exc}")
    error = None
    if result.tool_calls:
        error = "the agent called a tool instead of summarising; the call was not run"
    return Curation(result.content, result.usage, result.truncated, error)


@dataclass
class ForkCost:
    """What this fork cost, and what running the sub-task inline would have.

    PM ruling B1: the model decides to fork; OverMind MEASURES both arms on every
    fork so that the automatic fork-or-inline policy (CireSnave: "if it is
    cheaper to run one way than the other, do so") is fitted to data rather
    than guessed. ⚠️ SUBTRACTION, NEVER PREDICTION: nothing here decides.

    All figures are PROVIDER-REPORTED TOKENS - not dollars, and not net of
    provider-side prompt caching. With i = the parent call that emitted `fork`:

        work               = sum of the fork's own calls (curation excluded)
        growth             = last.prompt + last.completion - first.prompt
                             over the fork's calls: how much the sub-task's
                             transcript grew the context past the shared prefix
        returned_tokens    = parent[i+1].prompt - parent[i].prompt
                             - parent[i].completion: what the fork's result added
                             to the parent's context. ⚠️ AN UPPER BOUND - it also
                             counts any other tool result from the same turn.
        parent_calls_after = len(parent) - (i + 1)

        inline_tokens = work + growth * parent_calls_after
        fork_tokens   = work + curation + returned_tokens * parent_calls_after

    ⚠️ ANY UNREPORTED USAGE MAKES THE FIGURE None, NOT 0. Zero is a number;
    "the provider did not say" is not (providers.Usage).
    """
    fork_id: str
    time_sensitive: bool
    work_calls: tuple[Usage, ...]
    curation: Usage
    parent_call_index: int
    parent_model: str | None = None
    fork_model: str | None = None
    returned_tokens: int | None = None
    parent_calls_after: int | None = None

    def finalize(self, parent_calls: Sequence[Usage]) -> None:
        """Called when the PARENT run ends: only then is the number of parent
        calls that re-sent the fork's result known."""
        i = self.parent_call_index
        self.parent_calls_after = max(0, len(parent_calls) - (i + 1))
        self.returned_tokens = None
        if i + 1 < len(parent_calls):
            emit, nxt = parent_calls[i], parent_calls[i + 1]
            if emit.reported and nxt.reported:
                self.returned_tokens = max(
                    0, nxt.prompt_tokens - emit.prompt_tokens - emit.completion_tokens)

    def _work(self) -> int | None:
        if not self.work_calls or not all(u.reported for u in self.work_calls):
            return None
        return sum(u.total_tokens for u in self.work_calls)

    @property
    def growth(self) -> int | None:
        if self._work() is None:
            return None
        first, last = self.work_calls[0], self.work_calls[-1]
        return max(0, last.prompt_tokens + last.completion_tokens - first.prompt_tokens)

    @property
    def inline_tokens(self) -> int | None:
        work, growth = self._work(), self.growth
        if work is None or growth is None or self.parent_calls_after is None:
            return None
        return work + growth * self.parent_calls_after

    @property
    def fork_tokens(self) -> int | None:
        work = self._work()
        if (work is None or not self.curation.reported
                or self.parent_calls_after is None):
            return None
        if self.parent_calls_after == 0:
            return work + self.curation.total_tokens
        if self.returned_tokens is None:
            return None
        return work + self.curation.total_tokens + self.returned_tokens * self.parent_calls_after

    @property
    def cheaper(self) -> str | None:
        inline, fork = self.inline_tokens, self.fork_tokens
        if inline is None or fork is None:
            return None
        if fork < inline:
            return "fork"
        return "inline" if inline < fork else "equal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "fork_id": self.fork_id,
            "time_sensitive": self.time_sensitive,
            "parent_model": self.parent_model,
            "fork_model": self.fork_model,
            "parent_call_index": self.parent_call_index,
            "parent_calls_after": self.parent_calls_after,
            "work_calls": [[u.prompt_tokens, u.completion_tokens, u.reported]
                           for u in self.work_calls],
            "curation_tokens": self.curation.total_tokens if self.curation.reported else None,
            "growth": self.growth,
            "returned_tokens": self.returned_tokens,
            "inline_tokens": self.inline_tokens,
            "fork_tokens": self.fork_tokens,
            "cheaper": self.cheaper,
            "basis": "provider-reported tokens; not dollars; not net of provider-side "
                     "prompt caching; returned_tokens is an upper bound",
        }


FORK_TOOL = "fork"

#: Appended to the fork's task. ⚠️ The fork's FINAL message is its summary, so
#: it is asked for up front; `curate_now` covers a fork that never gets there.
FORK_BRIEF_TAIL = (
    "When the sub-task is done, reply in plain text with no tool call. That reply "
    "is all the agent that forked you will read of your work besides your "
    "execution ledger: say what you found, what you changed and what is "
    "unfinished. Do not retell your steps.")

NOT_RUN = ("NOT RUN: the agent that forked you will make this call after you "
           "return. Do not make it yourself.")


def fork_schema() -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": FORK_TOOL,
        "description": (
            "Run a self-contained sub-task in a copy of yourself. The copy sees this "
            "whole conversation, does the work, and returns only a curated summary "
            "plus its execution ledger, so the sub-task's exploration does not fill "
            "your own context. Use it for work whose intermediate output you will "
            "not need."),
        "parameters": {"type": "object",
                       "properties": {"task": {"type": "string", "description":
                                               "What the copy must do and report back."}},
                       "required": ["task"]}}}


@dataclass(frozen=True)
class ForkConfig:
    max_steps: int = 8
    max_tokens: int | None = None
    #: ⚠️ ADDED to the parent's policies, never instead of them. Any denial wins
    #: (gate.Gate), so adding a policy can only take capability away.
    extra_policies: tuple[Policy, ...] = ()


class ForkFacts:
    """The parent's facts, with the fork's own executed tools appended.

    ⚠️ ONLY WHERE THE PARENT'S `executed_tools` IS LIVE (gate.LedgerFacts). A
    parent that reports none, or a static tuple, can never newly satisfy a
    RequirePrecondition; extending its answer from the fork's ledger would make
    the fork stronger than its parent. Checking `is not None` alone let a
    static `()` through - found in the final review.
    """

    def __init__(self, parent: FactSource, child: Ledger) -> None:
        self._parent = parent
        self._child = child

    def fact(self, key: str, **params: Any) -> Any:
        value = self._parent.fact(key, **params)
        if key == "executed_tools" and isinstance(self._parent, LedgerFacts):
            return tuple(value) + self._child.executed_tools()
        return value


@dataclass
class ForkResult:
    fork_id: str
    run: AgentRun
    #: ⚠️ TESTIMONY. `run.ledger` is the evidence.
    summary: str
    curated_by: str            # "completion" | "curate_now" | "none"
    cost: ForkCost

    def __str__(self) -> str:
        """What the PARENT MODEL reads as the fork call's result: the summary
        AND the ledger, each labelled for what it is."""
        nl = chr(10)
        return nl.join([
            f"[fork {self.fork_id} ended: {self.run.stop_reason}; "
            f"summary from {self.curated_by}]",
            "Summary (the fork's own account - testimony):",
            self.summary or "(no summary)",
            "",
            "Execution ledger (what actually ran - evidence):",
            self.run.ledger.digest(),
        ])

    def __repr__(self) -> str:
        # ⚠️ The parent ledger stores repr(result)[:500]; keep the evidence in
        # the absorbed entries, not in a truncated blob.
        return (f"ForkResult({self.fork_id!r}, stop={self.run.stop_reason!r}, "
                f"curated_by={self.curated_by!r}, "
                f"executed={list(self.run.executed_tools)!r})")


def run_fork(client: Any, parent_executor: GatedExecutor,
             tools: Sequence[Mapping[str, Any]], *,
             history: Sequence[Mapping[str, Any]], fork_call_id: str,
             pending: Sequence[tuple[str, str]], task: str, fork_id: str,
             config: ForkConfig, time_sensitive: bool, parent_call_index: int,
             parent_model: str | None) -> ForkResult:
    """Run one fork to its end and curate it. Writes nothing to the parent's
    ledger - the caller absorbs `result.run.ledger` once the fork call has its
    own seq there.

    ⚠️ THE FORK IS OFFERED THE PARENT'S TOOL LIST UNCHANGED, `fork` INCLUDED.
    The schemas are part of the rendered prompt; removing one would change the
    prefix the fork shares with its parent and defeat prefix caching, which is
    the efficiency case for forking at all. Depth 1 is enforced by the gate
    refusing `fork`, not by hiding it.
    """
    child_ledger = Ledger()
    gate = Gate([*parent_executor.gate.policies, *config.extra_policies,
                 ForbidTools(frozenset({FORK_TOOL}),
                             reason="a fork cannot fork (depth limit 1)")],
                facts=ForkFacts(parent_executor.gate.facts, child_ledger),
                ledger=child_ledger)
    executor = GatedExecutor(gate, {k: v for k, v in parent_executor.tools.items()
                                    if k != FORK_TOOL})

    seed = [dict(m) for m in history]
    seed.append(_tool_result_message(fork_call_id, FORK_TOOL,
                                     f"Forked as {fork_id}. You are now the fork; "
                                     f"your instructions follow."))
    for call_id, name in pending:
        seed.append(_tool_result_message(call_id, name, NOT_RUN))

    try:
        run = run_agent(client, executor, tools, f"{task}\n\n{FORK_BRIEF_TAIL}",
                        history=seed, max_steps=config.max_steps,
                        max_tokens=config.max_tokens)
    except Exception as exc:                       # noqa: BLE001 - reported, not hidden
        # ⚠️ THE EVIDENCE OUTLIVES THE LOOP. Effects already in `child_ledger`
        # happened; letting this propagate would leave the parent recording
        # only "fork -> ERROR" and understating what was done. The transcript
        # and per-call usage died with the loop, so there is nothing to curate
        # and the spend is unknown (Usage() is reported=False), not zero.
        run = AgentRun(final_text="", stop_reason=StopReason.HARNESS_ERROR, steps=0,
                       ledger=child_ledger, messages=seed, usage=Usage(),
                       error=f"{type(exc).__name__}: {exc}")

    if run.stop_reason == StopReason.COMPLETED:
        summary, curated_by, curation = run.final_text, "completion", Usage.zero()
    elif run.stop_reason == StopReason.HARNESS_ERROR:
        summary, curated_by, curation = "", "none", Usage.zero()
    else:
        cur = curate_now(client, run.messages, tools=tools, max_tokens=config.max_tokens)
        summary, curation = cur.text, cur.usage
        curated_by = "curate_now" if cur.error is None and cur.text else "none"

    cost = ForkCost(fork_id=fork_id, time_sensitive=time_sensitive,
                    work_calls=tuple(run.call_usage), curation=curation,
                    parent_call_index=parent_call_index,
                    parent_model=parent_model, fork_model=run.model)
    return ForkResult(fork_id, run, summary, curated_by, cost)


def execute_fork(client: Any, executor: GatedExecutor,
                 tools: Sequence[Mapping[str, Any]], *,
                 messages: Sequence[Mapping[str, Any]], calls: Sequence[Mapping[str, Any]],
                 index: int, args: Mapping[str, Any], actor: str, config: ForkConfig,
                 time_sensitive: bool, parent_call_index: int,
                 parent_model: str | None, fork_id: str
                 ) -> tuple[ToolOutcome, "ForkResult | None"]:
    """Run `calls[index]` (a fork call) through the PARENT'S gate, then absorb
    the fork's ledger into the parent's.

    ⚠️ THE FORK CALL GOES THROUGH THE GATE LIKE ANY OTHER. A caller whose
    policies do not cover `fork` gets it refused, and no child run starts. The
    callable is bound per call (it needs this turn's messages) on a view that
    shares the parent's gate and ledger, so nothing about the gate changes.
    """
    call = calls[index]
    history = [dict(m) for m in messages]       # NOW: results are appended after
    pending = tuple(((c.get("id") or ""), ((c.get("function") or {}).get("name") or ""))
                    for c in calls[index + 1:])

    def bound(task: str) -> ForkResult:
        return run_fork(client, executor, tools, history=history,
                        fork_call_id=call.get("id") or "", pending=pending, task=task,
                        fork_id=fork_id, config=config, time_sensitive=time_sensitive,
                        parent_call_index=parent_call_index, parent_model=parent_model)

    view = GatedExecutor(executor.gate, {**executor.tools, FORK_TOOL: bound})
    fork_seq = len(executor.gate.ledger)        # the seq execute() will give it
    outcome = view.execute(FORK_TOOL, args, actor=actor)
    result = outcome.result if isinstance(outcome.result, ForkResult) else None
    if outcome.executed and outcome.error is None and result is not None:
        executor.gate.ledger.absorb(result.run.ledger, fork_id=fork_id,
                                    fork_seq=fork_seq, record=result.cost)
        return outcome, result
    return outcome, None
