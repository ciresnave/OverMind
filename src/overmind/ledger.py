# SPDX-License-Identifier: MIT OR Apache-2.0
"""A durable, append-only record of every `dispatch_lane_task` run - one JSON
line each: task type, provider/model, verdict, tokens and wall time.

⚠️ COLLECTED NOW, NOT ROUTED ON YET. PM relay of CireSnave's long-term goal
(EXPECTATIONS §2.3a, verbatim there): route work to "the cheapest LLMs that
can do the work well", ending with a smaller Claude plan. The planned
meaning: routing evolves to COST PER SUCCESSFUL TASK (the measured success
rate per task type, times the price), and cheap PAID providers become
eligible eventually - but ONLY after CireSnave's own explicit approval;
"free tiers only" still stands today. This module is the DATA COLLECTION
half only. Nothing here reads this ledger to make a routing decision - that
is real design work for later, deliberately not done yet (PM, 2026-09-19:
"No design work now; just don't lose the data").

Mirrors `crates/lane-restart/src/log.rs`'s own discipline, the same
portfolio-wide pattern: one line per event, JSON, appended, NEVER rewritten
or rotated silently, and EVERY dispatch is recorded - a verdict of
CHECK_FAILED or ERROR is exactly the data future cost-per-success routing
needs, not just the PASS runs.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any


def default_path() -> pathlib.Path:
    """`OVERMIND_LEDGER_FILE`, else `~/.overmind/dispatch_ledger.jsonl`.

    Per USER, not per checkout - matches `quota.py`'s own `default_path`:
    every worktree and every run appends to the same durable record."""
    env = os.environ.get("OVERMIND_LEDGER_FILE")
    return (pathlib.Path(env) if env
            else pathlib.Path.home() / ".overmind" / "dispatch_ledger.jsonl")


@dataclass
class DispatchRecord:
    """One dispatch, exactly what CireSnave's routing goal will eventually
    need: what KIND of task it was, which provider/model did it, whether it
    actually succeeded, and what it cost (tokens, wall time)."""
    task_id: str
    repo: str
    #: The check a run was verified against (`task.check_name`) - the
    #: closest thing to a "task type" this harness already computes; the
    #: docs-only label is itself informative here (see `check_name`'s own
    #: docstring in `dispatch_mcp.py`).
    task_type: str
    docs_only: bool
    provider: str | None
    model: str | None
    verdict: str
    steps: int
    tokens: dict[str, Any]
    seconds: float
    pr_url: str | None
    error: str | None
    #: Wall-clock UNIX time this record was appended - NOT when the run
    #: started (`seconds` already carries the run's own duration).
    at: float = field(default_factory=time.time)
    #: The `max_tokens` budget the model actually got (`LaneResult.max_tokens`)
    #: - `0` when no call ever completed. PM finding, 2026-09-19: a flat
    #: 4096 truncated a thinking model before it answered
    #: (`providers.DEFAULT_MAX_TOKENS` fixed the default; this records what
    #: a given run actually had, since routing may vary it per model later).
    max_tokens: int = 0


def append(record: DispatchRecord, path: pathlib.Path | None = None) -> None:
    """Appends one JSON line. Never truncates, never opens for write-
    replace - `open(path, "a")` is the whole safety property here, same as
    `log.rs`'s own `OpenOptions::append`.

    ⚠️ BEST-EFFORT: a write failure is reported to stderr, never allowed to
    take down the dispatch it was trying to record - the same "never lets
    logging break the thing it's observing" discipline
    `host.rs::append_host_log` already applies in the Rust crate."""
    path = path if path is not None else default_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), sort_keys=True) + "\n")
    except OSError as exc:
        print(f"overmind.ledger: could not append to {path}: {exc}")
