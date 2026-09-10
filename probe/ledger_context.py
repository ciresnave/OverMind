"""Can the ledger replace the transcript as the agent's memory?

    python probe/ledger_context.py [--n 20] [--model llama3.2:3b]

⚠️ THE ARCHITECTURE THIS PROJECT IMPLIES, AND IT WAS UNMEASURED.

A Claude session gets "what have I already done" for free: the harness keeps its
transcript, and a scheduler is useful to it precisely because the context is
still there when it fires. An OverMind agent is a cheap model, a small context
and one dispatch. It has no such luxury, so the state has to live somewhere.

The ledger already holds it. It was built as evidence FOR the reconciler - the
surface a claim gets checked against - and this points it the other way: hand it
back to the model as context.

⚠️ SAME MOVE AS THE GATE, ONE LAYER OVER. The gate refuses instead of trusting
the model to refuse. This remembers instead of trusting the model to remember.
In both cases the property is held OUTSIDE the model and handed back.

WHAT IS ACTUALLY BEING ASKED, because it is not "is the ledger nice to have":

  transcript  every assistant turn and every tool result, accumulating. The
              model's own words between steps are in there.
  ledger      the brief plus a digest of what the LEDGER records, rebuilt from
              scratch each step. The model's reasoning between steps is GONE.

⚠️ A DIGEST IS LOSSY AND THAT IS THE POINT OF THE EXPERIMENT. It records that a
tool ran and an excerpt of what came back, not why the model chose it. Whether
that reasoning was load-bearing is measurable rather than arguable.

⚠️ THE TASK REQUIRES CARRIED STATE OR IT MEASURES NOTHING. Step two must use a
value that only step one can produce - an entity id read from a live listing.
A task whose second step is independent of its first would pass in both arms
and would prove only that the harness runs.

⚠️ ARMS ALTERNATE, THEY DO NOT RUN IN BLOCKS. All-A-then-all-B lets anything
that drifts over the run - GPU thermal state, another lane starting work on this
box, the FAM server's own accumulated state - land entirely on one arm. Vary the
subject, hold the clock.

⚠️ AND THE VERDICT IS THE LEDGER, as everywhere else. A model that narrates
success is not a model that did the work. `reconcile` compares the two.

PRE-REGISTERED PREDICTION, WRITTEN BEFORE THE FIRST RUN AND LEFT HERE WHETHER
IT HOLDS OR NOT:

  * transcript completes 17-20 of 20. It is the measured arm - the same task on
    this model completed 2 of 2 previously - and nothing about it has changed.
  * ledger completes FEWER, and I expect 10-16 of 20. The digest carries the
    entity id, which is the load-bearing value, but it discards the model's own
    statement of what it intends to do next, and a 3B model re-deriving intent
    from the brief every step has more chances to wander.
  * ledger is CHEAPER in input tokens, and by more on later steps, because the
    prompt does not grow.
  * ⚠️ THE OUTCOME I WOULD FIND MOST SURPRISING is ledger BEATING transcript.
    If that happens the likely explanation is not that memory improved but that
    a shorter prompt is easier for a small model to follow - which would be a
    finding about PROMPT LENGTH, not about the ledger, and would need a third
    arm (a truncated transcript) to tell the two apart.

⚠️ A COUNT NOBODY PREDICTED CANNOT SURPRISE ANYONE. This paragraph exists so a
result in any direction is informative rather than narratable, and it is not
edited after the fact - if it is wrong, the correction goes BELOW it.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from overmind.agent import run_agent                                    # noqa: E402
from overmind.gate import (                                             # noqa: E402
    DenyUnlessDeclared, Gate, GatedExecutor, Ledger, StaticFacts,
)
from overmind.mcp_tools import McpToolSource, McpUnavailable            # noqa: E402
from overmind.outcome import reconcile                                  # noqa: E402
from overmind.providers import ProviderClient                           # noqa: E402

FAM_DIR = os.environ.get("FAM_DIR", r"C:\Projects\fam")
SERVER = os.path.join(FAM_DIR, "src", "adapters", "mcp", "server.ts")

#: Where FAM's own gitignored env file lives. ⚠️ VALUES ARE NEVER PRINTED,
#: LOGGED, OR PUT IN A RESULT - only their presence and length, and only when
#: something is missing. The server needs `FAM_PASSKEY` to decrypt its keys, and
#: a probe that cannot start reads exactly like a server that is down.
FAM_ENV = os.environ.get(
    "FAM_ENV", r"C:\Projects\fam\.claude\worktrees\firstrun\.env")


def load_fam_env() -> dict:
    """FAM's environment, from the process plus its gitignored env file.

    ⚠️ THE PROCESS WINS. A value already exported is a deliberate override, and
    silently replacing it with a file's contents would make the probe's
    behaviour unpredictable from its inputs.
    """
    env = dict(os.environ)
    try:
        text = open(FAM_ENV, encoding="utf-8", errors="replace").read()
    except OSError:
        return env
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env

ALLOWED = frozenset({"fam_list_entities", "fam_send_message"})
NEEDED = ["fam_list_entities", "fam_send_message"]

#: ⚠️ STEP TWO CANNOT BE DONE WITHOUT STEP ONE'S RESULT. The entity id is only
#: obtainable from the live listing, so a model that has lost its memory of
#: step one cannot fake its way through step two.
TASK = ("List the entities on this server. Then send the message 'ledger-probe' "
        "to whichever entity is NOT probe@probe@example.com, using the exact "
        "entity id you got back from the listing.")
REQUIRED = ("fam_list_entities", "fam_send_message")


def one_run(source, model, schemas, mode, max_tokens):
    gate = Gate([DenyUnlessDeclared(reversible=ALLOWED)],
                facts=StaticFacts(), ledger=Ledger())
    executor = GatedExecutor(gate, source.callables())
    client = ProviderClient("ollama", model=model, max_tokens=max_tokens, timeout=600)
    started = time.time()
    try:
        run = run_agent(client, executor, schemas, TASK, max_steps=6,
                        context_mode=mode)
    except Exception as exc:                                # noqa: BLE001
        return {"mode": mode, "error": f"{type(exc).__name__}: {str(exc)[:80]}",
                "seconds": time.time() - started, "complete": False}
    seconds = time.time() - started
    rec = reconcile(run, required=REQUIRED)
    return {"mode": mode, "seconds": seconds, "verdict": rec.verdict,
            "complete": rec.complete, "steps": run.steps,
            "executed": list(rec.executed), "usage": run.usage,
            "stop": str(run.stop_reason)}


def summarise(rows, label):
    done = [r for r in rows if r.get("complete")]
    secs = [r["seconds"] for r in rows if "seconds" in r]
    ins = [r["usage"].prompt_tokens for r in rows
           if r.get("usage") is not None and r["usage"].reported]
    # ⚠️ STEPS ARE THE DISCRIMINATOR FOR THE TOKEN FIGURE, not decoration. A
    # ledger prompt is SMALLER per step and RE-SENT every step, so total input
    # is (prompt size x steps) and the two arms move in opposite directions.
    # Reading total tokens without steps would attribute a step-count effect to
    # the context mechanism.
    steps = [r["steps"] for r in rows if "steps" in r]
    print(f"  {label:<12} {len(done)}/{len(rows)} complete"
          + (f"   median {statistics.median(secs):5.1f}s" if secs else "")
          + (f"   steps {statistics.median(steps):.1f}" if steps else "")
          + (f"   input {statistics.median(ins):6.0f} tok" if ins else "")
          + (f"   ({statistics.median(ins) / statistics.median(steps):.0f}/step)"
             if ins and steps and statistics.median(steps) else ""))
    return len(done), len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=20,
                        help="runs PER ARM (default 20)")
    parser.add_argument("--model", default="llama3.2:3b")
    parser.add_argument("--max-tokens", type=int, default=2000,
                        help="⚠️ NOT small. A thinking model spends this budget "
                             "BEFORE it answers, and too little produces an "
                             "empty reply indistinguishable from silence - "
                             "misread three times in this project already.")
    args = parser.parse_args()

    env = load_fam_env()
    if not env.get("FAM_PASSKEY"):
        # ⚠️ Named, never shown. A probe that cannot start reads exactly like a
        # server that is down, so say WHICH it is.
        print(f"FAM_PASSKEY is not set and was not found in {FAM_ENV}. "
              f"The MCP server needs it to decrypt its keys.", file=sys.stderr)
        return 2
    try:
        source = McpToolSource.stdio("bun", ["run", SERVER],
                                     env=env, cwd=FAM_DIR).open()
    except McpUnavailable as exc:
        print(f"FAM unavailable: {exc}")
        return 1

    schemas = source.schemas(only=NEEDED)
    rows = []
    try:
        print(f"model={args.model}  n={args.n} per arm  "
              f"max_tokens={args.max_tokens}  schemas={len(schemas)}")
        print("arms ALTERNATE - anything that drifts over the run hits both "
              "equally\n")
        for i in range(args.n):
            for mode in ("transcript", "ledger"):
                r = one_run(source, args.model, schemas, mode, args.max_tokens)
                rows.append(r)
                mark = "ok  " if r.get("complete") else "FAIL"
                extra = r.get("error") or r.get("stop", "")
                print(f"  {i + 1:>3} {mode:<11} {mark} {r['seconds']:6.1f}s  "
                      f"{extra}", flush=True)

        print("\n" + "=" * 70)
        t_done, t_n = summarise([r for r in rows if r["mode"] == "transcript"],
                                "transcript")
        l_done, l_n = summarise([r for r in rows if r["mode"] == "ledger"],
                                "ledger")
        print()
        print(f"  transcript {t_done}/{t_n}   ledger {l_done}/{l_n}")
        print()
        # ⚠️ NO p-VALUE. n=20 per arm on one model on one machine does not
        # support one, and printing one would dress a sample up as a result.
        # The honest statement is the two fractions and the denominator.
        print("⚠️ ONE MODEL, ONE MACHINE, ONE TASK, ONE DAY. This says whether")
        print("   a ledger digest CAN carry a two-step task on this model. It")
        print("   does not say it generalises - section 11 measured obedience")
        print("   as per-model with no predictor, and there is no reason to")
        print("   expect memory to behave better.")
        return 0
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
