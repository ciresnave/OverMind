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

__all__ = ["CURATE_INSTRUCTION", "Curation", "curate_now"]

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
