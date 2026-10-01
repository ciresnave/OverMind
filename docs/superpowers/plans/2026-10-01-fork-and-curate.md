# Fork-and-Curate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a model running under `run_agent` call a `fork` tool. The tool runs a sub-task in a copy of the model's own conversation. The copy goes through the same gate as the parent, plus any extra denials. It returns a curated summary (testimony) together with its execution ledger (evidence). The sub-task's raw transcript never enters the parent's context.

**Architecture:** A new module `src/overmind/fork.py` holds the fork mechanics:
- `curate_now`;
- `ForkCost`;
- `ForkFacts`;
- `run_fork`;
- `execute_fork`.

`run_agent` (`src/overmind/agent.py`) gains three opt-in parameters: `history=` (start from an existing message list), `fork=` (offer the fork tool) and `time_sensitive=`.

`Ledger` (`src/overmind/gate.py`) gains `LedgerEntry.lineage` and `Ledger.absorb()`. With these, a fork's effects land on the parent's ledger, tagged with where they came from, and the fork's cost record travels with them.

Everything is sequential. The parent blocks while its fork runs.

**Tech Stack:** Python ≥3.11, stdlib only. Tests use `unittest` and run under `python -m unittest discover -s tests -v`, which is what CI runs. pytest also works.

**Spec:** `SUBAGENT-FORK-AND-AUDITOR-DESIGN.md`, committed alongside this plan. Read §2, §3's corrections 3–5 and §4 before starting. The PM's scope rulings of 2026-10-01 are A1 and B1, quoted under Global Constraints.

## Global Constraints

- **Stdlib only in the core.** `pyproject.toml`: *"The core (gate, providers, agent) is STDLIB-ONLY and stays that way."* `fork.py` is core. Add no dependency.
- **Sequential only.** PM ruling A1: *"Sequential fork-and-curate (single writer, parent pauses) only in this plan. Parallel forks wait for their own plan."* So: no `asyncio`, no `threading`, no `concurrent.futures`.
- **Fork is model-requested, and both cost arms are measured.** PM ruling B1: *"Model requests a fork via a `fork` tool; OverMind logs both the real fork cost and the counterfactual inline cost (re-sent transcript tokens) on the parent's ledger, plus the time_sensitive flag."* Do not build an automatic fork-or-inline decision.
- **OpenAI-compatible providers only.** CireSnave: *"Stick with OpenAI-compatible providers at first."* No PTY work.
- **The ledger is the evidence. A summary is testimony** (`agent.py:3-9`, spec §2 correction 3). Every effect a fork causes must reach the parent's `Ledger`. The parent model must be shown the fork's ledger as well as its summary.
- **Subtractive only.** A fork's gate is the parent gate's policies, plus `ForkConfig.extra_policies`, plus a ban on `fork` itself. A fork may never be able to do something its parent could not.
- **Depth 1.** A fork cannot fork.
- **The gate is the only path to an effect.** The fork call itself goes through `GatedExecutor.execute`. A caller whose policies do not cover `fork` gets it refused.
- **Do not bump any version.** CireSnave via the PM, 2026-09-23: the PM allocates version numbers at gate time.
- **Every new `.py` file starts with** `# SPDX-License-Identifier: MIT OR Apache-2.0`. CI's `spdx_gate.py` fails otherwise.
- **Match the house comment style.** Comments explain *why*, and measured hazards are flagged with `⚠️`. Do not add comments that narrate *what* the code does.

## Review Focus

1. **The model emits `fork` alongside other tool calls in one turn.** OpenAI-style providers reject a history in which an assistant `tool_calls` entry has no matching `tool` result. Calls listed *after* `fork` in the same turn have not run when the fork starts. The fork's seed history must answer each of them with a "not run" placeholder. The parent then runs them normally after the fork returns. Pinned in Task 6: `test_calls_after_fork_in_the_same_turn`.
2. **The parent's facts are not wired to its ledger** (`StaticFacts` with no `executed_tools`). In that case the parent can never satisfy a `RequirePrecondition`. A fork must not gain that ability from its own ledger, because that would be escalation. Pinned in Task 5: `test_no_escalation_when_parent_does_not_wire_executed_tools`.
3. **The provider reports no `usage`.** Cost figures must be `None` ("unknown"), never `0`. Pinned in Task 4: `test_unreported_usage_gives_none_not_zero`.
4. **The fork's provider fails mid-fork, and the curation call fails too.** The parent must still get a `ForkResult` whose ledger holds every effect that ran before the failure, plus a `curated_by == "none"` marker. It must not get an exception or a lost ledger. Pinned in Task 5: `test_provider_failure_mid_fork_keeps_the_evidence`.
5. **The model calls `fork` with bad arguments** (no `task`, or an extra key). This must be recorded as an ERROR on the parent's ledger and must start no child run. Pinned in Task 6: `test_fork_with_bad_arguments_starts_nothing`.

---

## File structure

| File | Change | Responsibility |
|---|---|---|
| `src/overmind/gate.py` | modify | `LedgerEntry.lineage`; `Ledger.absorb`, `Ledger.fork_record`; lineage in `digest`/`to_json` |
| `src/overmind/agent.py` | modify | `run_agent(history=, fork=, time_sensitive=)`; `AgentRun.call_usage`, `AgentRun.forks` |
| `src/overmind/fork.py` | create | `FORK_TOOL`, `fork_schema`, `ForkConfig`, `curate_now`/`Curation`, `ForkCost`, `ForkFacts`, `ForkResult`, `run_fork`, `execute_fork` |
| `tests/test_gate.py` | modify | ledger lineage and absorb tests |
| `tests/test_agent.py` | modify | `history=` and `call_usage` tests |
| `tests/test_fork.py` | create | curate, cost, run_fork and end-to-end fork tests |
| `SUBAGENT-FORK-AND-AUDITOR-DESIGN.md` | add, then modify in Task 7 | spec; status line updated when built |

`fork.py` imports `agent.py` (to call `run_agent`). `agent.py` must therefore import `fork.py` **inside** `run_agent`, only when `fork is not None`, to avoid an import cycle. For type hints use `TYPE_CHECKING`.

---

### Task 1: Ledger lineage and absorb

**Files:**
- Modify: `src/overmind/gate.py` (`LedgerEntry` at ~281, `Ledger` at ~291-398)
- Test: `tests/test_gate.py` (append a new class)

**Interfaces:**
- Produces:
  - `LedgerEntry.lineage: tuple[str, ...] = ()`, as the last field with a default.
  - `Ledger.absorb(child: Ledger, *, fork_id: str, fork_seq: int, record: Any = None) -> None`.
  - `Ledger.fork_record(seq: int) -> Any | None`.
  - `to_json()` rows gain `"lineage": list[str]`, plus `"fork": record.to_dict()` on the row whose seq is a fork's seq.
  - Each `digest()` line for an entry with lineage gains a `[fork f1]`-style marker after the seq.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_gate.py`:

```python
class TestLedgerAbsorbsAForksEvidence(unittest.TestCase):
    """⚠️ A fork's summary is testimony; its ledger is the evidence. Absorbing
    puts every effect a fork caused on the parent's own ledger, tagged with
    where it came from, so `did()` and `executed_tools()` see it."""

    def entry(self, seq, name, executed=True, lineage=()):
        from overmind.gate import LedgerEntry, ToolCall, Decision
        return LedgerEntry(seq, ToolCall(name=name), Decision.allow("ok", "p"),
                           executed=executed, result_repr="ok", lineage=lineage)

    def parent_with_fork_call(self):
        parent = Ledger()
        parent.append(self.entry(0, "list_entities"))
        parent.append(self.entry(1, "fork"))
        return parent

    def test_child_entries_follow_the_fork_entry_renumbered_and_tagged(self):
        parent = self.parent_with_fork_call()
        child = Ledger()
        child.append(self.entry(0, "write_file"))
        child.append(self.entry(1, "run_check"))
        parent.absorb(child, fork_id="f1", fork_seq=1)
        rows = parent.entries
        self.assertEqual([e.seq for e in rows], [0, 1, 2, 3])
        self.assertEqual([e.call.name for e in rows],
                         ["list_entities", "fork", "write_file", "run_check"])
        self.assertEqual([e.lineage for e in rows], [(), (), ("f1",), ("f1",)])

    def test_absorbed_effects_count_as_done(self):
        parent = self.parent_with_fork_call()
        child = Ledger()
        child.append(self.entry(0, "write_file"))
        parent.absorb(child, fork_id="f1", fork_seq=1)
        self.assertTrue(parent.was_executed("write_file"))

    def test_nested_lineage_is_kept_outermost_first(self):
        parent = self.parent_with_fork_call()
        child = Ledger()
        child.append(self.entry(0, "write_file", lineage=("g7",)))
        parent.absorb(child, fork_id="f1", fork_seq=1)
        self.assertEqual(parent.entries[-1].lineage, ("f1", "g7"))

    def test_the_child_ledger_is_not_modified(self):
        parent = self.parent_with_fork_call()
        child = Ledger()
        child.append(self.entry(0, "write_file"))
        parent.absorb(child, fork_id="f1", fork_seq=1)
        self.assertEqual(child.entries[0].seq, 0)
        self.assertEqual(child.entries[0].lineage, ())

    def test_absorbing_itself_is_refused(self):
        parent = self.parent_with_fork_call()
        with self.assertRaises(ValueError):
            parent.absorb(parent, fork_id="f1", fork_seq=1)

    def test_an_unknown_fork_seq_is_refused(self):
        parent = self.parent_with_fork_call()
        with self.assertRaises(ValueError):
            parent.absorb(Ledger(), fork_id="f1", fork_seq=9)

    def test_absorbing_twice_at_one_seq_is_refused(self):
        """⚠️ Twice would duplicate evidence: every effect would count double."""
        parent = self.parent_with_fork_call()
        parent.absorb(Ledger(), fork_id="f1", fork_seq=1)
        with self.assertRaises(ValueError):
            parent.absorb(Ledger(), fork_id="f1", fork_seq=1)

    def test_an_empty_fork_id_is_refused(self):
        parent = self.parent_with_fork_call()
        with self.assertRaises(ValueError):
            parent.absorb(Ledger(), fork_id="", fork_seq=1)

    def test_to_json_carries_lineage_and_the_fork_record(self):
        import json

        class Record:
            def to_dict(self):
                return {"fork_id": "f1", "inline_tokens": 10}
        parent = self.parent_with_fork_call()
        child = Ledger()
        child.append(self.entry(0, "write_file"))
        record = Record()
        parent.absorb(child, fork_id="f1", fork_seq=1, record=record)
        rows = json.loads(parent.to_json())
        self.assertEqual(rows[2]["lineage"], ["f1"])
        self.assertEqual(rows[0]["lineage"], [])
        self.assertEqual(rows[1]["fork"], {"fork_id": "f1", "inline_tokens": 10})
        self.assertNotIn("fork", rows[0])
        self.assertIs(parent.fork_record(1), record)
        self.assertIsNone(parent.fork_record(0))

    def test_digest_marks_forked_entries(self):
        parent = self.parent_with_fork_call()
        child = Ledger()
        child.append(self.entry(0, "write_file"))
        parent.absorb(child, fork_id="f1", fork_seq=1)
        last = parent.digest().splitlines()[-1]
        self.assertTrue(last.startswith("2. [fork f1] write_file("), last)
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `python -m unittest tests.test_gate.TestLedgerAbsorbsAForksEvidence -v`
Expected: every test errors with `TypeError: LedgerEntry.__init__() got an unexpected keyword argument 'lineage'`, or with `AttributeError: 'Ledger' object has no attribute 'absorb'`.

- [ ] **Step 3: Implement.** In `src/overmind/gate.py`, change the `dataclasses` import to `from dataclasses import dataclass, field, replace`. Add to `LedgerEntry`, after `error`:

```python
    #: Which fork(s) this entry came from, outermost first; `()` for the run
    #: that owns the ledger. ⚠️ LINEAGE IS DATA, NOT NARRATIVE: a parent that
    #: learned what its fork did only from the fork's summary would be trusting
    #: testimony, which is exactly what this ledger exists to replace.
    lineage: tuple[str, ...] = ()
```

In `Ledger.__init__`, add `self._forks: dict[int, Any] = {}`. Then add these methods after `entries`:

```python
    def absorb(self, child: "Ledger", *, fork_id: str, fork_seq: int,
               record: Any = None) -> None:
        """Append a fork's ledger to this one, renumbered and tagged.

        `fork_seq` is the seq of THIS ledger's entry for the fork call itself;
        `record` (anything with `to_dict()`) is kept against it and serialised
        with that row. ⚠️ HELD BY REFERENCE: the fork's cost is finalised only
        when the parent run ends, after this call.
        """
        if child is self:
            raise ValueError("a ledger cannot absorb itself")
        if not fork_id:
            raise ValueError("fork_id must be non-empty: it is the lineage tag")
        if not any(e.seq == fork_seq for e in self._entries):
            raise ValueError(f"no entry with seq {fork_seq} to attach the fork to")
        if fork_seq in self._forks:
            # ⚠️ A second absorb would count every effect of the fork twice.
            raise ValueError(f"fork {fork_id!r} at seq {fork_seq} was already absorbed")
        for e in child.entries:
            self._entries.append(replace(e, seq=len(self._entries),
                                         lineage=(fork_id,) + e.lineage))
        self._forks[fork_seq] = record

    def fork_record(self, seq: int) -> Any | None:
        return self._forks.get(seq)
```

`test_absorbing_twice_at_one_seq_is_refused` absorbs with `record=None`, so the guard must not depend on `record` being truthy. That is why `_forks[fork_seq]` is set even when `record` is `None`, and why the check is `fork_seq in self._forks`.

In `digest()`, change the `head = ...` line to:

```python
            mark = f"[fork {'/'.join(entry.lineage)}] " if entry.lineage else ""
            head = f"{entry.seq}. {mark}{entry.call.name}({args[:160]})"
```

Replace `to_json()` with:

```python
    def to_json(self) -> str:
        rows = []
        for e in self._entries:
            row = {
                "seq": e.seq,
                "tool": e.call.name,
                "arguments": dict(e.call.arguments),
                "actor": e.call.actor,
                "allowed": e.decision.allowed,
                "policy": e.decision.policy,
                "reason": e.decision.reason,
                "executed": e.executed,
                "error": e.error,
                "lineage": list(e.lineage),
            }
            record = self._forks.get(e.seq)
            if record is not None:
                row["fork"] = record.to_dict()
            rows.append(row)
        return json.dumps(rows, indent=2)
```

- [ ] **Step 4: Run the tests to verify they pass, and that nothing else broke.**

Run: `python -m unittest tests.test_gate -v`
Expected: all PASS, including the 10 new tests.

Run: `python -m unittest discover -s tests`
Expected: `OK`. Read the *names* of any failures. Do not compare counts alone.

- [ ] **Step 5: Commit.**

```bash
git add src/overmind/gate.py tests/test_gate.py
git commit -m "gate: ledger lineage and absorb, so a fork's effects land on the parent's ledger"
```

---

### Task 2: `run_agent(history=)` and per-call usage

**Files:**
- Modify: `src/overmind/agent.py` (`AgentRun` ~65-99, `run_agent` ~158-410)
- Test: `tests/test_agent.py` (append two classes)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `run_agent(..., history: Sequence[Mapping[str, Any]] | None = None)`. When given, the request is a *copy* of `history` plus `{"role": "user", "content": task}`.
  - `history` is refused (`ValueError`) together with `system=`, with an empty list, or with any `context_mode` except `"transcript"`.
  - `AgentRun.call_usage: list[Usage]`: one entry per **successful** `client.chat` call, in order.
  - A `finish(run)` closure inside `run_agent` that every `return` goes through. Task 6 extends it.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_agent.py`:

```python
class TestRunFromHistory(unittest.TestCase):
    """A fork starts from its parent's messages. ⚠️ It must COPY them: a fork
    that appended to its parent's list would put its whole transcript into
    the parent's context, which is the one thing forking exists to prevent."""

    HISTORY = [{"role": "system", "content": "rules"},
               {"role": "user", "content": "the parent's task"},
               {"role": "assistant", "content": "working on it"}]

    def test_first_request_is_history_then_the_task(self):
        client = ScriptedClient([{"role": "assistant", "content": "done"}])
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset())])
        run_agent(client, ex, TOOLS, "the sub-task", history=self.HISTORY)
        self.assertEqual(client.seen[0],
                         self.HISTORY + [{"role": "user", "content": "the sub-task"}])

    def test_history_is_not_mutated(self):
        history = [dict(m) for m in self.HISTORY]
        client = ScriptedClient([{"role": "assistant", "content": "done"}])
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset())])
        run_agent(client, ex, TOOLS, "the sub-task", history=history)
        self.assertEqual(history, self.HISTORY)

    def test_history_with_system_is_refused(self):
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset())])
        with self.assertRaises(ValueError):
            run_agent(ScriptedClient([]), ex, TOOLS, "t", history=self.HISTORY, system="x")

    def test_empty_history_is_refused(self):
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset())])
        with self.assertRaises(ValueError):
            run_agent(ScriptedClient([]), ex, TOOLS, "t", history=[])

    def test_history_in_ledger_mode_is_refused(self):
        """⚠️ Ledger mode rebuilds the prompt every step, so it would silently
        discard the history it was given."""
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset())])
        for mode in ("ledger", "ledger+closure"):
            with self.assertRaises(ValueError):
                run_agent(ScriptedClient([]), ex, TOOLS, "t", history=self.HISTORY,
                          context_mode=mode)


class TestCallUsageIsRecordedPerCall(unittest.TestCase):
    """The fork's cost model needs each call's usage, not only the sum."""

    def client(self, turns):
        from overmind.providers import Usage

        class UsageClient:
            def __init__(self, turns): self.turns = list(turns)
            def chat(self, messages, tools=None, max_tokens=None, **kw):
                item = self.turns.pop(0)
                if isinstance(item, Exception):
                    raise item
                msg, (p, c) = item
                return ChatResult(message=dict(msg), model="m", provider="p", latency_s=0.0,
                                  usage=Usage(p, c, p + c, reported=True))
        return UsageClient(turns)

    def test_one_entry_per_call_in_order(self):
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset({"list_entities"}))],
                              tools={"list_entities": Spy()})
        run = run_agent(self.client([(tool_turn("list_entities"), (100, 10)),
                                     ({"role": "assistant", "content": "done"}, (150, 20))]),
                        ex, TOOLS, "go")
        self.assertEqual([(u.prompt_tokens, u.completion_tokens) for u in run.call_usage],
                         [(100, 10), (150, 20)])

    def test_a_failed_call_adds_no_entry(self):
        gate, ex = build_gate([DenyUnlessDeclared(reversible=frozenset({"list_entities"}))],
                              tools={"list_entities": Spy()})
        run = run_agent(self.client([(tool_turn("list_entities"), (100, 10)),
                                     RuntimeError("boom")]),
                        ex, TOOLS, "go")
        self.assertEqual(run.stop_reason, StopReason.PROVIDER_ERROR)
        self.assertEqual(len(run.call_usage), 1)
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `python -m unittest tests.test_agent.TestRunFromHistory tests.test_agent.TestCallUsageIsRecordedPerCall -v`
Expected: errors with `TypeError: run_agent() got an unexpected keyword argument 'history'` and `AttributeError: 'AgentRun' object has no attribute 'call_usage'`.

- [ ] **Step 3: Implement.**

(a) Add this field to `AgentRun`, after `max_tokens`:

```python
    #: Each successful model call's usage, in order. ⚠️ `usage` is the SUM and
    #: cannot say how the context grew; the fork cost model needs the shape.
    call_usage: list[Usage] = field(default_factory=list)
```

(b) Add `history: Sequence[Mapping[str, Any]] | None = None,` to the `run_agent` signature, after `offer`. Add a paragraph to the docstring:

```
    `history`, when given, is the conversation to CONTINUE: the first request
    is a copy of it plus `task` as a user turn. Used by forks. ⚠️ Copied, never
    appended to - the caller's list is the parent's context.
```

(c) Replace the block that builds the initial `messages`:

```python
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": task})
```

with:

```python
    if history is not None:
        if context_mode != "transcript":
            raise ValueError("history requires context_mode='transcript': the ledger "
                             "modes rebuild the prompt every step and would discard it")
        if system:
            raise ValueError("pass the system message inside history, not as system=; "
                             "a second system message would break the shared prefix")
        if not history:
            raise ValueError("history must not be empty; omit it to start fresh")
        messages: list[dict[str, Any]] = [dict(m) for m in history]
    else:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": task})
```

(d) Just before `for step in range(max_steps):`, add:

```python
    call_usage: list[Usage] = []

    def finish(run: AgentRun) -> AgentRun:
        run.call_usage = list(call_usage)
        return run
```

(e) Directly after `spent = spent + result.usage`, add `call_usage.append(result.usage)`.

(f) Wrap **every** `return AgentRun(...)` in `run_agent` as `return finish(AgentRun(...))`. At `b6ed69a` there are six, one each for `PROVIDER_ERROR`, `TRUNCATED`, `PROTOCOL_FAILURE`, `COMPLETED`, `NO_PROGRESS` and the final `MAX_STEPS`.

Check the count before and after:
- before the edit, `grep -c "return AgentRun(" src/overmind/agent.py` prints `6`;
- after it, the same command prints `0`, and `grep -c "return finish(AgentRun(" src/overmind/agent.py` prints `6`.

A different "before" count means `agent.py` has changed since this plan was written. Stop and re-read it.

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `python -m unittest tests.test_agent -v`
Expected: all PASS.

Run: `python -m unittest discover -s tests`
Expected: `OK`.

- [ ] **Step 5: Commit.**

```bash
git add src/overmind/agent.py tests/test_agent.py
git commit -m "agent: run_agent(history=) to continue a conversation, and per-call usage"
```

---

### Task 3: `curate_now` — a summary on demand, at any step boundary

**Files:**
- Create: `src/overmind/fork.py`
- Test: `tests/test_fork.py` (create)

**Interfaces:**
- Consumes: `ChatResult`, `Usage` from `overmind.providers`.
- Produces:
  - `CURATE_INSTRUCTION: str`.
  - `@dataclass Curation(text: str, usage: Usage, truncated: bool, error: str | None = None)`.
  - `curate_now(client, messages: Sequence[Mapping[str, Any]], *, tools: Sequence[Mapping[str, Any]] | None = None, max_tokens: int | None = None) -> Curation`.
  - It raises `ValueError` when the last message is an assistant turn with unanswered `tool_calls`.
  - It never executes a tool. If the model answers with a tool call instead of a summary, `error` is set and the call is not run.

The auditor plan will call `curate_now` on a fork's `messages` at a step boundary. That is why it is a pure function over a message list, not a method on a running loop.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_fork.py`:

```python
# SPDX-License-Identifier: MIT OR Apache-2.0
"""Tests for forking. As in test_agent.py, the load-bearing assertions are about
the LEDGER: a fork's summary is testimony, and only its ledger is evidence."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.providers import ChatResult, Usage  # noqa: E402


def U(prompt, completion):
    return Usage(prompt, completion, prompt + completion, reported=True)


class ScriptedClient:
    """Every turn written in advance. A turn is a message dict, a
    (message, Usage) pair, or an Exception to raise. Records what it was sent,
    tools included, so a test can check the shared prefix."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen: list[list[dict]] = []
        self.seen_tools: list = []

    def chat(self, messages, tools=None, max_tokens=None, **kw):
        self.seen.append([dict(m) for m in messages])
        self.seen_tools.append(tools)
        item = self.turns.pop(0) if self.turns else {"role": "assistant",
                                                     "content": "nothing further"}
        if isinstance(item, Exception):
            raise item
        msg, usage = item if isinstance(item, tuple) else (item, Usage())
        return ChatResult(message=dict(msg), model="scripted", provider="test",
                          latency_s=0.0, usage=usage)


def tool_turn(name, args="{}", call_id="c1", content=None):
    return {"role": "assistant", "content": content,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name, "arguments": args}}]}


class TestCurateNow(unittest.TestCase):
    MESSAGES = [{"role": "user", "content": "task"},
                {"role": "assistant", "content": "found three things"}]

    def test_asks_for_a_summary_after_the_messages(self):
        from overmind.fork import CURATE_INSTRUCTION, curate_now
        client = ScriptedClient([({"role": "assistant", "content": "SUMMARY"}, U(50, 5))])
        cur = curate_now(client, self.MESSAGES)
        self.assertEqual(client.seen[0], self.MESSAGES +
                         [{"role": "user", "content": CURATE_INSTRUCTION}])
        self.assertEqual(cur.text, "SUMMARY")
        self.assertEqual(cur.usage.total_tokens, 55)
        self.assertIsNone(cur.error)
        self.assertFalse(cur.truncated)

    def test_does_not_mutate_the_messages(self):
        from overmind.fork import curate_now
        messages = [dict(m) for m in self.MESSAGES]
        curate_now(ScriptedClient([{"role": "assistant", "content": "S"}]), messages)
        self.assertEqual(messages, self.MESSAGES)

    def test_passes_the_same_tools_so_the_prefix_still_matches(self):
        """⚠️ Tool schemas are part of the rendered prompt. Dropping them here
        would change the prefix and defeat provider-side prefix caching."""
        from overmind.fork import curate_now
        tools = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
        client = ScriptedClient([{"role": "assistant", "content": "S"}])
        curate_now(client, self.MESSAGES, tools=tools)
        self.assertEqual(client.seen_tools[0], tools)

    def test_a_tool_call_instead_of_a_summary_is_flagged_not_run(self):
        from overmind.fork import curate_now
        client = ScriptedClient([tool_turn("write_file", content="partial")])
        cur = curate_now(client, self.MESSAGES)
        self.assertEqual(cur.text, "partial")
        self.assertIn("tool", cur.error)

    def test_a_provider_error_is_returned_not_raised(self):
        from overmind.fork import curate_now
        cur = curate_now(ScriptedClient([RuntimeError("down")]), self.MESSAGES)
        self.assertEqual(cur.text, "")
        self.assertIn("RuntimeError: down", cur.error)
        self.assertFalse(cur.usage.reported)

    def test_truncation_is_flagged(self):
        from overmind.fork import curate_now

        class Cut(ScriptedClient):
            def chat(self, messages, tools=None, max_tokens=None, **kw):
                r = super().chat(messages, tools, max_tokens)
                r.finish_reason = "length"
                return r
        cur = curate_now(Cut([{"role": "assistant", "content": "half a sum"}]), self.MESSAGES)
        self.assertTrue(cur.truncated)

    def test_unanswered_tool_calls_are_refused(self):
        """A history ending in tool calls with no results is rejected by
        OpenAI-style providers, so refuse it here with a clear reason."""
        from overmind.fork import curate_now
        with self.assertRaises(ValueError):
            curate_now(ScriptedClient([]), self.MESSAGES + [tool_turn("x")])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `python -m unittest tests.test_fork -v`
Expected: every test errors with `ModuleNotFoundError: No module named 'overmind.fork'`.

- [ ] **Step 3: Implement.** Create `src/overmind/fork.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `python -m unittest tests.test_fork -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/overmind/fork.py tests/test_fork.py
git commit -m "fork: curate_now - a curated summary on demand at any step boundary"
```

---

### Task 4: `ForkCost` — both arms, measured from provider-reported usage

**Files:**
- Modify: `src/overmind/fork.py`
- Test: `tests/test_fork.py` (append a class)

**Interfaces:**
- Consumes: `Usage`.
- Produces a `@dataclass ForkCost` with:
  - fields `fork_id: str`, `time_sensitive: bool`, `work_calls: tuple[Usage, ...]`, `curation: Usage`, `parent_call_index: int`, `parent_model: str | None = None`, `fork_model: str | None = None`, `returned_tokens: int | None = None` and `parent_calls_after: int | None = None`;
  - the method `finalize(parent_calls: Sequence[Usage]) -> None`;
  - the properties `growth`, `inline_tokens`, `fork_tokens` and `cheaper` (each `int | None` or `str | None`);
  - `to_dict() -> dict`.

**The formula** (put it in the docstring exactly like this). All figures are provider-reported tokens. They are not dollars, and they ignore provider-side prompt caching.

- `work` is the sum of the fork's own calls, with curation excluded. Inline, the same sub-task would make about the same calls over the same shared prefix.
- `growth` is `last.prompt + last.completion - first.prompt` over `work_calls`. It is how much the sub-task's transcript grew the context beyond the shared prefix.
- `returned_tokens` is `parent[i+1].prompt - parent[i].prompt - parent[i].completion`, where `i` is the parent call that emitted `fork`. It is the parent's context growth on that turn beyond its own reply. It is an **upper bound** on the summary's size, because it also counts any other tool results from that turn.
- `parent_calls_after` is `len(parent_calls) - (i + 1)`.
- `inline_tokens = work + growth × parent_calls_after`.
- `fork_tokens = work + curation + returned_tokens × parent_calls_after`.
- Any unreported `Usage` among the inputs makes the affected figure `None`, never `0`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_fork.py`, above the `if __name__` block:

```python
class TestForkCost(unittest.TestCase):
    """Worked numbers, so the formula is pinned rather than re-derived.
    work: 1000+100, 1300+150, 1700+200 -> work 4450, growth 1700+200-1000 = 900.
    parent: call 0 emitted the fork (1000+50); call 1 prompt 1200.
    returned = 1200 - 1000 - 50 = 150."""

    def cost(self, **kw):
        from overmind.fork import ForkCost
        base = dict(fork_id="f1", time_sensitive=False,
                    work_calls=(U(1000, 100), U(1300, 150), U(1700, 200)),
                    curation=U(1900, 80), parent_call_index=0)
        base.update(kw)
        return ForkCost(**base)

    def test_few_parent_turns_after_makes_inline_cheaper(self):
        c = self.cost()
        c.finalize([U(1000, 50), U(1200, 40), U(1300, 30)])
        self.assertEqual(c.growth, 900)
        self.assertEqual(c.returned_tokens, 150)
        self.assertEqual(c.parent_calls_after, 2)
        self.assertEqual(c.inline_tokens, 4450 + 900 * 2)            # 6250
        self.assertEqual(c.fork_tokens, 4450 + 1980 + 150 * 2)       # 6730
        self.assertEqual(c.cheaper, "inline")

    def test_many_parent_turns_after_makes_the_fork_cheaper(self):
        c = self.cost()
        c.finalize([U(1000, 50), U(1200, 40)] + [U(1300, 30)] * 9)
        self.assertEqual(c.parent_calls_after, 10)
        self.assertEqual(c.inline_tokens, 4450 + 900 * 10)           # 13450
        self.assertEqual(c.fork_tokens, 4450 + 1980 + 150 * 10)      # 7930
        self.assertEqual(c.cheaper, "fork")

    def test_a_fork_that_completed_on_its_own_has_zero_curation(self):
        c = self.cost(curation=Usage.zero())
        c.finalize([U(1000, 50), U(1200, 40)])
        self.assertEqual(c.fork_tokens, 4450 + 0 + 150 * 1)

    def test_unreported_usage_gives_none_not_zero(self):
        c = self.cost(work_calls=(U(1000, 100), Usage()))
        c.finalize([U(1000, 50), U(1200, 40)])
        self.assertIsNone(c.growth)
        self.assertIsNone(c.inline_tokens)
        self.assertIsNone(c.fork_tokens)
        self.assertIsNone(c.cheaper)

    def test_unreported_parent_usage_gives_no_returned_tokens(self):
        c = self.cost()
        c.finalize([U(1000, 50), Usage()])
        self.assertIsNone(c.returned_tokens)
        self.assertIsNone(c.fork_tokens)
        self.assertEqual(c.inline_tokens, 4450 + 900 * 1)

    def test_no_parent_call_after_the_fork(self):
        c = self.cost()
        c.finalize([U(1000, 50)])
        self.assertEqual(c.parent_calls_after, 0)
        self.assertIsNone(c.returned_tokens)
        self.assertEqual(c.inline_tokens, 4450)

    def test_a_fork_that_never_reached_a_model(self):
        c = self.cost(work_calls=())
        c.finalize([U(1000, 50), U(1200, 40)])
        self.assertIsNone(c.growth)
        self.assertIsNone(c.inline_tokens)

    def test_not_finalised_means_unknown(self):
        c = self.cost()
        self.assertIsNone(c.parent_calls_after)
        self.assertIsNone(c.inline_tokens)

    def test_to_dict_carries_the_flag_both_arms_and_the_basis(self):
        c = self.cost(time_sensitive=True, parent_model="m1", fork_model="m1")
        c.finalize([U(1000, 50), U(1200, 40), U(1300, 30)])
        d = c.to_dict()
        self.assertEqual(d["fork_id"], "f1")
        self.assertIs(d["time_sensitive"], True)
        self.assertEqual(d["inline_tokens"], 6250)
        self.assertEqual(d["fork_tokens"], 6730)
        self.assertEqual(d["cheaper"], "inline")
        self.assertEqual(d["parent_model"], "m1")
        self.assertIn("provider-reported", d["basis"])
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `python -m unittest tests.test_fork.TestForkCost -v`
Expected: errors with `ImportError: cannot import name 'ForkCost'`.

- [ ] **Step 3: Implement.** Add `"ForkCost"` to `__all__` and add:

```python
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
```

Check `test_no_parent_call_after_the_fork`: with `parent_calls_after == 0`, `fork_tokens` is `work + curation` (nothing re-sent). That branch is why `fork_tokens` handles `0` before `returned_tokens`. `test_unreported_parent_usage_gives_no_returned_tokens` expects `fork_tokens is None`; there `parent_calls_after == 1` and `returned_tokens is None`, so it does.

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `python -m unittest tests.test_fork -v`
Expected: all PASS (7 + 9).

- [ ] **Step 5: Commit.**

```bash
git add src/overmind/fork.py tests/test_fork.py
git commit -m "fork: ForkCost - measure the fork arm and the inline counterfactual from reported usage"
```

---

### Task 5: `run_fork` — the fork itself

**Files:**
- Modify: `src/overmind/fork.py`
- Test: `tests/test_fork.py` (append classes)

**Interfaces:**
- Consumes:
  - `run_agent(..., history=)` and `AgentRun.call_usage` (Task 2);
  - `curate_now` (Task 3) and `ForkCost` (Task 4);
  - `Gate`, `GatedExecutor`, `Ledger`, `ForbidTools`, `Policy` and `FactSource` from `overmind.gate`;
  - `StopReason` and `_tool_result_message` from `overmind.agent`.
- Produces:
  - `FORK_TOOL = "fork"`.
  - `fork_schema() -> dict`.
  - `@dataclass(frozen=True) ForkConfig(max_steps: int = 8, max_tokens: int | None = None, extra_policies: tuple = ())`.
  - `ForkFacts(parent: FactSource, child: Ledger)`.
  - `@dataclass ForkResult(fork_id: str, run: AgentRun, summary: str, curated_by: str, cost: ForkCost)`. `curated_by` is one of `"completion"`, `"curate_now"` or `"none"`. Its `__str__` is what the parent model reads; its `__repr__` is short.
  - `run_fork(client, parent_executor: GatedExecutor, tools, *, history, fork_call_id: str, pending: Sequence[tuple[str, str]], task: str, fork_id: str, config: ForkConfig, time_sensitive: bool, parent_call_index: int, parent_model: str | None) -> ForkResult`. It never writes to the parent's ledger.

Two design points the implementer must keep:

1. **The fork is offered the same tool list as the parent, `fork` included, and the gate refuses `fork`.** Hiding the schema would change the rendered prompt prefix and defeat provider-side prefix caching, which is the whole efficiency case. So depth 1 is enforced by refusal, not by concealment, and `fork` shows up in the child's `offered_but_refused`. That is the honest record of the trade-off.
2. **The seed history.** It is the parent's messages (ending in the assistant turn that called `fork`), then:
   - the tool result for the fork call;
   - one "NOT RUN" tool result per call later in that turn;
   - the sub-task as a user turn, added by `run_agent`.

   Everything up to and including the assistant turn is byte-identical to the start of the parent's next request.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_fork.py`, above `if __name__`:

```python
def gate_and_executor(reversible, tools=None, facts=None, extra=()):
    from overmind.gate import DenyUnlessDeclared, Gate, GatedExecutor, Ledger
    gate = Gate([*extra, DenyUnlessDeclared(reversible=frozenset(reversible))],
                facts=facts, ledger=Ledger())
    return GatedExecutor(gate, tools or {})


class Spy:
    def __init__(self, result="ok"):
        self.calls = []
        self.result = result

    def __call__(self, **kw):
        self.calls.append(kw)
        return self.result


TOOLS = [
    {"type": "function", "function": {"name": "write_file", "description": "w",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "list_entities", "description": "l",
     "parameters": {"type": "object", "properties": {}}}},
]

HISTORY = [{"role": "system", "content": "rules"},
           {"role": "user", "content": "the parent's task"},
           tool_turn("fork", '{"task": "investigate"}', call_id="cF")]


def fork(client, executor, *, tools=None, pending=(), config=None, task="investigate"):
    from overmind.fork import ForkConfig, fork_schema, run_fork
    return run_fork(client, executor, tools if tools is not None else TOOLS + [fork_schema()],
                    history=HISTORY, fork_call_id="cF", pending=pending, task=task,
                    fork_id="f1", config=config or ForkConfig(), time_sensitive=False,
                    parent_call_index=0, parent_model="scripted")


class TestRunFork(unittest.TestCase):
    def setUp(self):
        self.write = Spy("written")
        self.ex = gate_and_executor({"write_file", "list_entities", "fork"},
                                    tools={"write_file": self.write})

    def test_seed_is_parent_history_then_fork_result_then_the_task(self):
        client = ScriptedClient([{"role": "assistant", "content": "S"}])
        fork(client, self.ex, task="investigate X")
        first = client.seen[0]
        self.assertEqual(first[:3], HISTORY)
        self.assertEqual(first[3]["role"], "tool")
        self.assertEqual(first[3]["tool_call_id"], "cF")
        self.assertEqual(first[-1]["role"], "user")
        self.assertTrue(first[-1]["content"].startswith("investigate X"))

    def test_fork_sees_the_same_tools_as_the_parent(self):
        from overmind.fork import fork_schema
        client = ScriptedClient([{"role": "assistant", "content": "S"}])
        offered = TOOLS + [fork_schema()]
        fork(client, self.ex, tools=offered)
        self.assertEqual(client.seen_tools[0], offered)

    def test_a_completed_fork_curates_itself(self):
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 {"role": "assistant", "content": "wrote a"}])
        res = fork(client, self.ex)
        self.assertEqual(res.summary, "wrote a")
        self.assertEqual(res.curated_by, "completion")
        self.assertEqual(self.write.calls, [{"path": "a"}])
        self.assertTrue(res.run.did("write_file"))

    def test_the_parent_ledger_is_untouched(self):
        """Absorbing is the caller's job (execute_fork), after the fork call
        itself has its own seq on the parent's ledger."""
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 {"role": "assistant", "content": "wrote a"}])
        fork(client, self.ex)
        self.assertEqual(len(self.ex.gate.ledger), 0)

    def test_a_fork_that_runs_out_of_steps_is_asked_to_curate(self):
        from overmind.fork import CURATE_INSTRUCTION, ForkConfig
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}', "c1"),
                                 tool_turn("write_file", '{"path": "b"}', "c2"),
                                 {"role": "assistant", "content": "interim: a and b"}])
        res = fork(client, self.ex, config=ForkConfig(max_steps=2))
        self.assertEqual(res.run.stop_reason, "max-steps")
        self.assertEqual(client.seen[-1][-1], {"role": "user", "content": CURATE_INSTRUCTION})
        self.assertEqual(res.summary, "interim: a and b")
        self.assertEqual(res.curated_by, "curate_now")

    def test_provider_failure_mid_fork_keeps_the_evidence(self):
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 RuntimeError("down"), RuntimeError("still down")])
        res = fork(client, self.ex)
        self.assertEqual(res.run.stop_reason, "provider-error")
        self.assertEqual(res.curated_by, "none")
        self.assertEqual(res.summary, "")
        self.assertTrue(res.run.did("write_file"), "the effect that DID run was lost")

    def test_a_fork_cannot_fork(self):
        client = ScriptedClient([tool_turn("fork", '{"task": "deeper"}'),
                                 {"role": "assistant", "content": "S"}])
        res = fork(client, self.ex)
        denied = res.run.ledger.denied()
        self.assertEqual([e.call.name for e in denied], ["fork"])
        self.assertEqual(denied[0].decision.policy, "forbid-tools")
        self.assertEqual(len(client.seen), 2, "a nested fork reached a model")

    def test_extra_policies_only_take_away(self):
        from overmind.fork import ForkConfig
        from overmind.gate import ForbidTools
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 {"role": "assistant", "content": "S"}])
        res = fork(client, self.ex, config=ForkConfig(
            extra_policies=(ForbidTools(frozenset({"write_file"})),)))
        self.assertEqual(self.write.calls, [])
        self.assertFalse(res.run.did("write_file"))

    def test_the_parents_policies_still_bind_the_fork(self):
        from overmind.gate import ForbidTools
        ex = gate_and_executor({"write_file", "fork"}, tools={"write_file": self.write},
                               extra=(ForbidTools(frozenset({"write_file"})),))
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 {"role": "assistant", "content": "S"}])
        fork(client, ex)
        self.assertEqual(self.write.calls, [])

    def test_calls_after_fork_in_the_same_turn_get_not_run_placeholders(self):
        client = ScriptedClient([{"role": "assistant", "content": "S"}])
        fork(client, self.ex, pending=[("c9", "list_entities")])
        first = client.seen[0]
        placeholder = first[4]
        self.assertEqual(placeholder["tool_call_id"], "c9")
        self.assertIn("NOT RUN", placeholder["content"])

    def test_cost_is_recorded_unfinalised(self):
        client = ScriptedClient([({"role": "assistant", "content": "S"}, U(500, 20))])
        res = fork(client, self.ex)
        self.assertEqual(res.cost.fork_id, "f1")
        self.assertEqual([u.total_tokens for u in res.cost.work_calls], [520])
        self.assertTrue(res.cost.curation.reported)
        self.assertEqual(res.cost.curation.total_tokens, 0)
        self.assertIsNone(res.cost.parent_calls_after)
        self.assertEqual(res.cost.fork_model, "scripted")

    def test_str_shows_summary_and_ledger_labelled(self):
        client = ScriptedClient([tool_turn("write_file", '{"path": "a"}'),
                                 {"role": "assistant", "content": "wrote a"}])
        text = str(fork(client, self.ex))
        self.assertIn("testimony", text)
        self.assertIn("evidence", text)
        self.assertIn("wrote a", text)
        self.assertIn("write_file(path='a') -> OK", text)


class TestForkFacts(unittest.TestCase):
    def ledger_with(self, *names):
        from overmind.gate import Decision, Ledger, LedgerEntry, ToolCall
        ledger = Ledger()
        for i, n in enumerate(names):
            ledger.append(LedgerEntry(i, ToolCall(name=n), Decision.allow(), executed=True))
        return ledger

    def test_executed_tools_is_parent_then_child(self):
        from overmind.fork import ForkFacts
        from overmind.gate import StaticFacts
        parent = StaticFacts({"executed_tools": ("list_entities",)})
        facts = ForkFacts(parent, self.ledger_with("write_file"))
        self.assertEqual(facts.fact("executed_tools"), ("list_entities", "write_file"))

    def test_no_escalation_when_parent_does_not_wire_executed_tools(self):
        """⚠️ A parent whose facts never report executed_tools can never
        satisfy RequirePrecondition. Its fork must not gain that power from
        its own ledger - that would make the fork stronger than its parent."""
        from overmind.fork import ForkFacts
        from overmind.gate import StaticFacts
        facts = ForkFacts(StaticFacts(), self.ledger_with("list_entities"))
        self.assertIsNone(facts.fact("executed_tools"))

    def test_other_facts_pass_through(self):
        from overmind.fork import ForkFacts
        from overmind.gate import StaticFacts
        facts = ForkFacts(StaticFacts({"sender_vouched": True}), self.ledger_with())
        self.assertIs(facts.fact("sender_vouched"), True)

    def test_a_precondition_met_in_the_parent_holds_in_the_fork(self):
        from overmind.fork import ForkFacts
        from overmind.gate import (DenyUnlessDeclared, Gate, GatedExecutor, Ledger,
                                   RequirePrecondition, StaticFacts)
        child = Ledger()
        gate = Gate([RequirePrecondition(tool="write_file", requires="list_entities"),
                     DenyUnlessDeclared(reversible=frozenset({"write_file"}))],
                    facts=ForkFacts(StaticFacts({"executed_tools": ("list_entities",)}), child),
                    ledger=child)
        out = GatedExecutor(gate, {"write_file": Spy()}).execute("write_file", {"path": "a"})
        self.assertTrue(out.executed)
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `python -m unittest tests.test_fork.TestRunFork tests.test_fork.TestForkFacts -v`
Expected: errors with `ImportError: cannot import name 'ForkConfig'` (and `ForkFacts`).

- [ ] **Step 3: Implement.** Add to `__all__`: `"FORK_TOOL"`, `"fork_schema"`, `"ForkConfig"`, `"ForkFacts"`, `"ForkResult"`, `"run_fork"`. Add these imports:

```python
from .agent import AgentRun, StopReason, _tool_result_message, run_agent
from .gate import FactSource, ForbidTools, Gate, GatedExecutor, Ledger, Policy
```

Then add:

```python
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

    ⚠️ ONLY WHERE THE PARENT ALREADY REPORTS `executed_tools`. A parent that
    does not wire its ledger into its facts can never satisfy a
    RequirePrecondition; filling the gap from the fork's ledger would make the
    fork stronger than its parent.
    """

    def __init__(self, parent: FactSource, child: Ledger) -> None:
        self._parent = parent
        self._child = child

    def fact(self, key: str, **params: Any) -> Any:
        value = self._parent.fact(key, **params)
        if key == "executed_tools" and value is not None:
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

    run = run_agent(client, executor, tools, f"{task}\n\n{FORK_BRIEF_TAIL}",
                    history=seed, max_steps=config.max_steps,
                    max_tokens=config.max_tokens)

    if run.stop_reason == StopReason.COMPLETED:
        summary, curated_by, curation = run.final_text, "completion", Usage.zero()
    else:
        cur = curate_now(client, run.messages, tools=tools, max_tokens=config.max_tokens)
        summary, curation = cur.text, cur.usage
        curated_by = "curate_now" if cur.error is None and cur.text else "none"

    cost = ForkCost(fork_id=fork_id, time_sensitive=time_sensitive,
                    work_calls=tuple(run.call_usage), curation=curation,
                    parent_call_index=parent_call_index,
                    parent_model=parent_model, fork_model=run.model)
    return ForkResult(fork_id, run, summary, curated_by, cost)
```

Check `test_provider_failure_mid_fork_keeps_the_evidence`. The fork's second call raises, giving `PROVIDER_ERROR`; at that point `run.messages` ends with the tool result for `write_file`. `curate_now` then calls the client, which raises again, so `error` is set and `curated_by == "none"`.

Check `test_a_fork_that_runs_out_of_steps_is_asked_to_curate`. With `max_steps=2`, two tool turns end in `MAX_STEPS`. The third scripted turn is the curation reply, and the last request ends with `CURATE_INSTRUCTION`.

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `python -m unittest tests.test_fork -v`
Expected: all PASS.

Run: `python -m unittest discover -s tests`
Expected: `OK`. `fork.py` imports `agent.py`, but `agent.py` does not yet import `fork.py`, so there is no cycle yet.

- [ ] **Step 5: Commit.**

```bash
git add src/overmind/fork.py tests/test_fork.py
git commit -m "fork: run_fork - a gated, depth-1 copy of the agent that returns summary and ledger"
```

---

### Task 6: Wire `fork` into `run_agent`

**Files:**
- Modify: `src/overmind/fork.py` (add `execute_fork`)
- Modify: `src/overmind/agent.py` (`AgentRun`, `run_agent` signature, validation, the call loop and `finish`)
- Test: `tests/test_fork.py` (append a class)

**Interfaces:**
- Consumes: everything above, plus `Ledger.absorb` from Task 1.
- Produces:
  - `run_agent(..., fork: ForkConfig | None = None, time_sensitive: bool = False)`;
  - `AgentRun.forks: list[ForkResult]`;
  - `execute_fork(client, executor, tools, *, messages, calls, index, args, actor, config, time_sensitive, parent_call_index, parent_model, fork_id) -> tuple[ToolOutcome, ForkResult | None]`.

`run_agent` refuses `fork=` (`ValueError`) when:
- `context_mode != "transcript"`;
- the caller's `tools` already name `fork`;
- the executor already registers `fork`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_fork.py`, above `if __name__`:

```python
class TestForkInTheAgentLoop(unittest.TestCase):
    """End to end. ONE client serves parent and fork - sequential, so the
    scripted turns are consumed parent, fork..., parent."""

    def setUp(self):
        self.write = Spy("written")
        self.listing = Spy("alice")
        self.ex = gate_and_executor({"write_file", "list_entities", "fork"},
                                    tools={"write_file": self.write,
                                           "list_entities": self.listing})

    def run_parent(self, turns, **kw):
        from overmind.agent import run_agent
        from overmind.fork import ForkConfig
        self.client = ScriptedClient(turns)
        return run_agent(self.client, self.ex, TOOLS, "parent task",
                         fork=kw.pop("fork", ForkConfig()), **kw)

    def happy(self):
        return [
            tool_turn("fork", '{"task": "write a"}', "cF"),             # parent
            tool_turn("write_file", '{"path": "a"}', "k1",
                      content="SCRATCH-THOUGHT-XYZ"),                     # fork
            {"role": "assistant", "content": "wrote a"},                 # fork summary
            {"role": "assistant", "content": "parent done"},             # parent
        ]

    def test_the_forks_effects_land_on_the_parents_ledger_with_lineage(self):
        run = self.run_parent(self.happy())
        self.assertEqual(run.stop_reason, "completed")
        self.assertEqual(self.write.calls, [{"path": "a"}])
        names = [(e.call.name, e.lineage) for e in run.ledger.entries]
        self.assertEqual(names, [("fork", ()), ("write_file", ("f1",))])
        self.assertTrue(run.did("write_file"))
        self.assertEqual(len(run.forks), 1)

    def test_the_parent_never_sees_the_forks_transcript(self):
        run = self.run_parent(self.happy())
        parent_next = self.client.seen[-1]
        flat = chr(10).join(str(m.get("content")) for m in parent_next)
        self.assertNotIn("SCRATCH-THOUGHT-XYZ", flat)
        self.assertIn("wrote a", flat)                       # testimony
        self.assertIn("write_file(path='a') -> OK", flat)     # evidence

    def test_fork_and_parent_share_the_prefix(self):
        """The fork's first request and the parent's next request both begin
        with the parent's first request plus the assistant turn calling fork."""
        self.run_parent(self.happy())
        parent_first, fork_first, parent_next = (self.client.seen[0], self.client.seen[1],
                                                 self.client.seen[3])
        n = len(parent_first) + 1           # + the assistant turn that called fork
        self.assertEqual(fork_first[:n], parent_next[:n])
        self.assertEqual(fork_first[:len(parent_first)], parent_first)
        self.assertEqual(fork_first[n - 1]["tool_calls"][0]["id"], "cF")
        self.assertEqual(self.client.seen_tools[1], self.client.seen_tools[0])

    def test_cost_is_finalised_and_on_the_ledger(self):
        import json
        turns = [
            (tool_turn("fork", '{"task": "write a"}', "cF"), U(1000, 50)),
            (tool_turn("write_file", '{"path": "a"}', "k1"), U(1100, 60)),
            ({"role": "assistant", "content": "wrote a"}, U(1300, 40)),
            ({"role": "assistant", "content": "parent done"}, U(1200, 10)),
        ]
        run = self.run_parent(turns, time_sensitive=True)
        cost = run.forks[0].cost
        self.assertEqual(cost.parent_calls_after, 1)
        self.assertEqual(cost.returned_tokens, 1200 - 1000 - 50)
        rows = json.loads(run.ledger.to_json())
        self.assertIs(rows[0]["fork"]["time_sensitive"], True)
        self.assertEqual(rows[0]["fork"]["inline_tokens"], cost.inline_tokens)
        self.assertEqual(rows[0]["fork"]["fork_tokens"], cost.fork_tokens)

    def test_calls_after_fork_in_the_same_turn(self):
        both = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "cF", "type": "function",
             "function": {"name": "fork", "arguments": '{"task": "write a"}'}},
            {"id": "cL", "type": "function",
             "function": {"name": "list_entities", "arguments": "{}"}}]}
        run = self.run_parent([both,
                               {"role": "assistant", "content": "wrote nothing"},
                               {"role": "assistant", "content": "parent done"}])
        self.assertEqual(len(self.listing.calls), 1, "the later call was not run exactly once")
        names = [(e.call.name, e.lineage) for e in run.ledger.entries]
        self.assertEqual(names, [("fork", ()), ("list_entities", ())])
        fork_first = self.client.seen[1]
        self.assertTrue(any(m.get("tool_call_id") == "cL" and "NOT RUN" in m["content"]
                            for m in fork_first))

    def test_fork_with_bad_arguments_starts_nothing(self):
        run = self.run_parent([tool_turn("fork", '{"oops": 1}', "cF"),
                               {"role": "assistant", "content": "parent done"}])
        entry = run.ledger.entries[0]
        self.assertEqual(entry.call.name, "fork")
        self.assertIsNotNone(entry.error)
        self.assertEqual(len(self.client.seen), 2, "a child run was started")
        self.assertEqual(run.forks, [])

    def test_a_gate_that_does_not_cover_fork_refuses_it(self):
        from overmind.agent import run_agent
        from overmind.fork import ForkConfig
        ex = gate_and_executor({"write_file"}, tools={"write_file": self.write})
        client = ScriptedClient([tool_turn("fork", '{"task": "x"}', "cF"),
                                 {"role": "assistant", "content": "parent done"}])
        run = run_agent(client, ex, TOOLS, "t", fork=ForkConfig())
        self.assertEqual(len(client.seen), 2)
        self.assertFalse(run.ledger.entries[0].decision.allowed)
        self.assertEqual(run.forks, [])

    def test_without_fork_config_no_fork_tool_is_offered(self):
        from overmind.agent import run_agent
        client = ScriptedClient([{"role": "assistant", "content": "done"}])
        run_agent(client, self.ex, TOOLS, "t")
        self.assertEqual(client.seen_tools[0], TOOLS)

    def test_refused_configurations(self):
        from overmind.agent import run_agent
        from overmind.fork import ForkConfig, fork_schema
        with self.assertRaises(ValueError):
            run_agent(ScriptedClient([]), self.ex, TOOLS, "t", fork=ForkConfig(),
                      context_mode="ledger")
        with self.assertRaises(ValueError):
            run_agent(ScriptedClient([]), self.ex, TOOLS + [fork_schema()], "t",
                      fork=ForkConfig())
        ex = gate_and_executor({"fork"}, tools={"fork": Spy()})
        with self.assertRaises(ValueError):
            run_agent(ScriptedClient([]), ex, TOOLS, "t", fork=ForkConfig())

    def test_two_forks_get_distinct_ids(self):
        run = self.run_parent([
            tool_turn("fork", '{"task": "one"}', "cF1"),
            {"role": "assistant", "content": "did one"},
            tool_turn("fork", '{"task": "two"}', "cF2"),
            {"role": "assistant", "content": "did two"},
            {"role": "assistant", "content": "parent done"}])
        self.assertEqual([f.fork_id for f in run.forks], ["f1", "f2"])
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `python -m unittest tests.test_fork.TestForkInTheAgentLoop -v`
Expected: errors with `TypeError: run_agent() got an unexpected keyword argument 'fork'`. The one exception is `test_without_fork_config_no_fork_tool_is_offered`, which passes already. That test is a control: it shows the default path is unchanged.

- [ ] **Step 3a: Add `execute_fork` to `fork.py`.** Add `"execute_fork"` to `__all__` and `ToolOutcome` to the gate import, then add:

```python
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
```

- [ ] **Step 3b: Wire it into `agent.py`.**

(a) Add `from typing import TYPE_CHECKING` to the `typing` import. Below the imports add:

```python
if TYPE_CHECKING:
    from .fork import ForkConfig
```

(b) Add this field to `AgentRun`, after `call_usage`:

```python
    #: Every fork this run completed, in order (fork.ForkResult). Their effects
    #: are ALSO on `ledger`, tagged with lineage - that copy is the evidence.
    forks: list[Any] = field(default_factory=list)
```

(c) Add these parameters to the signature, after `history`:

```python
              fork: "ForkConfig | None" = None,
              time_sensitive: bool = False) -> AgentRun:
```

Add this to the docstring:

```
    `fork`, when given, offers the model a `fork` tool (fork.fork_schema) that
    runs a sub-task in a copy of this conversation and returns a curated
    summary plus the copy's ledger (fork.py). The caller's policies must cover
    `fork` or it is refused like any uncovered tool. `time_sensitive` is
    recorded on every fork's cost record (CireSnave's ruling: latency governs
    only for a task deemed time-sensitive; otherwise cost does).
```

(d) Directly after the existing `offer`/`context_mode` validation, and **before** `messages` is built, add:

```python
    if fork is not None:
        from .fork import FORK_TOOL, execute_fork, fork_schema   # fork.py imports this module
        if context_mode != "transcript":
            raise ValueError("fork requires context_mode='transcript': a fork continues "
                             "its parent's transcript, and the ledger modes have none")
        if any((t.get("function") or {}).get("name") == FORK_TOOL for t in tools):
            raise ValueError(f"the caller's tools already name {FORK_TOOL!r}")
        if FORK_TOOL in executor.tools:
            raise ValueError(f"the executor already registers {FORK_TOOL!r}")
        tools = [*tools, fork_schema()]
```

Because `tool_names` is computed from `tools` after this block, `fork` is included in the `certainly_denied` check. Leave that check's code unchanged.

(e) Before the step loop, next to `call_usage`, add `forks: list[Any] = []`. Then extend `finish`:

```python
    def finish(run: AgentRun) -> AgentRun:
        run.call_usage = list(call_usage)
        for f in forks:
            f.cost.finalize(call_usage)
        run.forks = list(forks)
        return run
```

(f) In the call loop:
- change `for call in calls:` to `for index, call in enumerate(calls):`;
- replace the two lines under `# THE ONLY PATH TO AN EFFECT.` with the code below.

```python
            # THE ONLY PATH TO AN EFFECT - a fork call included (execute_fork
            # runs it through this same gate).
            actor = f"{result.provider}:{result.model}"
            if fork is not None and name == FORK_TOOL:
                outcome, forked = execute_fork(
                    client, executor, tools, messages=messages, calls=calls, index=index,
                    args=args, actor=actor, config=fork, time_sensitive=time_sensitive,
                    parent_call_index=len(call_usage) - 1, parent_model=result.model,
                    fork_id=f"f{len(forks) + 1}")
                if forked is not None:
                    forks.append(forked)
            else:
                outcome = executor.execute(name, args, actor=actor)
```

`parent_call_index` is `len(call_usage) - 1` because `call_usage` was appended for *this* step's call before the call loop runs.

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `python -m unittest tests.test_fork -v`
Expected: all PASS.

Run: `python -m unittest discover -s tests`
Expected: `OK`. Pay particular attention to `test_agent.py`, `test_dispatch.py` and `test_lanework.py`: they call `run_agent` without `fork=`, and their results must be unchanged.

- [ ] **Step 5: Mutation check (the house rule: a guard nobody saw fail is unproven).** Make each mutation below one at a time, run `python -m unittest tests.test_fork`, confirm the named test FAILS, then revert. Before each run, assert the mutation applied: `git diff --stat` shows exactly 1 file changed.
  - In `execute_fork`, delete the `executor.gate.ledger.absorb(...)` call. `test_the_forks_effects_land_on_the_parents_ledger_with_lineage` must fail.
  - In `ForkFacts.fact`, drop `and value is not None`. `test_no_escalation_when_parent_does_not_wire_executed_tools` must fail.
  - In `run_fork`, drop the `ForbidTools(...)` from the child gate. `test_a_fork_cannot_fork` must fail.
  - In `execute_fork`, drop the `for c in calls[index + 1:]` placeholders (set `pending = ()`). `test_calls_after_fork_in_the_same_turn` must fail.

  Record the four results (test name and FAIL) in the commit message body.

- [ ] **Step 6: Commit.**

```bash
git add src/overmind/agent.py src/overmind/fork.py tests/test_fork.py
git commit -m "agent: offer a gated fork tool; a fork's ledger and cost land on the parent's ledger"
```

---

### Task 7: Spec status and README

**Files:**
- Modify: `SUBAGENT-FORK-AND-AUDITOR-DESIGN.md` (status line, top of file)
- Modify: `README.md` (one short entry wherever the README lists modules; check `git show origin/main:README.md` for the layout first)

- [ ] **Step 1: Update the spec's status line.** Replace `**Status: PROPOSAL. Nothing in this file is built.**` with:

```
**Status: fork-and-curate (§2 item 1, sequential) BUILT in `src/overmind/fork.py` -
see `docs/superpowers/plans/2026-10-01-fork-and-curate.md`. Parallel forks (§4.5, §6
merge semantics), the automatic fork-or-inline policy (§4.1 - data now collected on
every fork's ledger entry), and the auditor (§3) are NOT built.**
```

- [ ] **Step 2: README.** If the README has a module list, add one line in its existing style:
  `fork.py — fork-and-curate: a gated, depth-1 copy of the agent that returns a summary (testimony) and its ledger (evidence).`
  If it has no module list, do not add a section. Skip this step and say so in the PR.

- [ ] **Step 3: Run the full suite and the CI gates locally.**

Run: `python -m unittest discover -s tests -v`
Expected: `OK`.

Run: `python3 .github/spdx_gate.py --self-test && python3 .github/spdx_gate.py`
Expected: both exit 0.

- [ ] **Step 4: Commit.**

```bash
git add SUBAGENT-FORK-AND-AUDITOR-DESIGN.md README.md
git commit -m "docs: mark fork-and-curate built; parallel forks, fork policy and auditor are not"
```

---

## After the plan

- Open one PR for the branch. Report it to the PM with `[READY]`. Do not bump the version: the PM allocates it.
- Out of scope, each a separate plan:
  - the auditor (spec §3 and §6's loop-detection signals);
  - parallel forks (§4.5, after §6's merge-semantics design and the asyncio-HTTP ruling);
  - the automatic cost-based fork policy (§4.1, once fork ledger entries exist to fit it to);
  - capability-intersection and lineage IDs beyond `fork_id` (§6);
  - PTY agents (§4.6).
