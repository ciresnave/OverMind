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

from .providers import Usage

__all__ = ["CURATE_INSTRUCTION", "Curation", "ForkCost", "curate_now"]

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
