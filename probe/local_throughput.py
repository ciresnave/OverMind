# SPDX-License-Identifier: MIT OR Apache-2.0
"""Is a LOCAL model a lane's day? Capacity bounded by clock, not by quota.

    python probe/local_throughput.py [model ...]

§23 measured the only published free-tier ceiling: OpenRouter, ~17-25 dispatches
a day. The PM's reading was right - that is not close to a lane's day.

⚠️ BUT A LOCAL MODEL HAS NO DAILY CAP AT ALL, and nothing here had measured what
that is worth. Its capacity is bounded by WALL CLOCK rather than by someone
else's budget, which is a different currency and a better one: it cannot be
exhausted, only slowed.

WHAT THIS MEASURES, per model, against the LIVE FAM server through the real gate:

  1. Does a local model complete a real two-step task end to end? §8.1 showed
     qwen3:8b calling a FAM tool, but that was BEFORE the dispatch layer, the
     argument-collision fix, and the schema-trimming finding.
  2. How long does one dispatch take, wall clock?
  3. What does trimming the tool schemas do to that time? §22 measured a 59%
     TOKEN saving on a hosted model; for a local one the input is PREFILL, so
     the effect should show up as seconds.

⚠️ THE VERDICT IS THE LEDGER, as everywhere else. A model that narrates success
is not a model that did the work.

⚠️ AND DISPATCHES-PER-DAY FROM ONE SAMPLE IS AN EXTRAPOLATION, NOT A
MEASUREMENT. It is printed with that word attached, because a rate derived from
a single timed run is exactly the kind of number that gets quoted without its
denominator.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from overmind.agent import run_agent  # noqa: E402
from overmind.gate import (  # noqa: E402
    DenyUnlessDeclared, Gate, GatedExecutor, Ledger, StaticFacts,
)
from overmind.mcp_tools import McpToolSource, McpUnavailable  # noqa: E402
from overmind.outcome import reconcile  # noqa: E402
from overmind.providers import ProviderClient  # noqa: E402

FAM_DIR = os.environ.get("FAM_DIR", r"C:\Projects\fam")
SERVER = os.path.join(FAM_DIR, "src", "adapters", "mcp", "server.ts")
ALLOWED = frozenset({"fam_list_entities", "fam_list_channels", "fam_get_history",
                     "fam_send_message", "fam_list_invitations"})
NEEDED = ["fam_list_entities", "fam_send_message"]

TASK = ("List the entities on this server. Then send the message 'local-probe' to "
        "whichever entity is NOT probe@probe@example.com, using the exact entity id "
        "you got back from the listing.")
REQUIRED = ("fam_list_entities", "fam_send_message")

DEFAULT_MODELS = ["qwen3:8b", "llama3.2:3b", "qwen2.5-coder:7b"]


def one_run(source, model, schemas, label):
    gate = Gate([DenyUnlessDeclared(reversible=ALLOWED)], facts=StaticFacts(), ledger=Ledger())
    executor = GatedExecutor(gate, source.callables())
    client = ProviderClient("ollama", model=model, max_tokens=600, timeout=600)
    started = time.time()
    try:
        run = run_agent(client, executor, schemas, TASK, max_steps=6)
    except Exception as exc:                                # noqa: BLE001
        return {"label": label, "error": f"{type(exc).__name__}: {str(exc)[:90]}",
                "seconds": time.time() - started}
    seconds = time.time() - started
    rec = reconcile(run, required=REQUIRED)
    return {"label": label, "seconds": seconds, "verdict": rec.verdict,
            "complete": rec.complete, "executed": list(rec.executed),
            "steps": run.steps, "usage": run.usage,
            "said": (run.final_text or "")[:90]}


def main() -> int:
    models = sys.argv[1:] or DEFAULT_MODELS
    env = dict(os.environ)
    try:
        source = McpToolSource.stdio("bun", ["run", SERVER], env=env, cwd=FAM_DIR).open()
    except McpUnavailable as exc:
        print(f"FAM unavailable: {exc}")
        return 1

    all20 = source.schemas()
    trimmed = source.schemas(only=NEEDED)
    rows = []
    try:
        for model in models:
            print(f"\n########## {model} ##########", flush=True)
            for label, schemas in (("20 schemas", all20), ("2 schemas", trimmed)):
                r = one_run(source, model, schemas, f"{model} / {label}")
                rows.append(r)
                if "error" in r:
                    print(f"  {label:<11} ERROR after {r['seconds']:.0f}s: {r['error']}", flush=True)
                    continue
                print(f"  {label:<11} {r['seconds']:>6.1f}s  {r['verdict']:<15} "
                      f"complete={r['complete']}  steps={r['steps']}  "
                      f"executed={r['executed']}", flush=True)

        print("\n" + "=" * 74)
        done = [r for r in rows if r.get("complete")]
        print(f"completed the two-step task: {len(done)}/{len(rows)} runs")
        for r in rows:
            if "error" in r:
                continue
            # ⚠️ EXTRAPOLATION, not a measurement: one timed run, one machine,
            # nothing else competing for the GPU.
            per_day = 86400 / r["seconds"] if r["seconds"] else 0
            print(f"  {r['label']:<28} {r['seconds']:>6.1f}s/dispatch  "
                  f"-> {per_day:>7.0f} dispatches/day  (EXTRAPOLATED from 1 run)")
        print()
        print("⚠️ A local model has NO daily cap - its ceiling is wall clock, and the")
        print("   figures above are an extrapolation from a single timed run each,")
        print("   on an idle GPU, with nothing else competing. Treat as an ORDER OF")
        print("   MAGNITUDE against OpenRouter's measured ~17-25/day, not as a rate.")
        return 0
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
