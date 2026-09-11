# SPDX-License-Identifier: MIT OR Apache-2.0
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


def target_entity(source) -> str | None:
    """The id the task must send to, read from the LIVE listing.

    ⚠️ DISCOVERED, NOT HARDCODED. A constant here would keep passing after the
    server's entities changed, which is the staleness this project keeps finding
    in its own readings.
    """
    import re
    out = str(source.callables()["fam_list_entities"]())
    ids = sorted(set(re.findall(r"ID:\s*(\S+)", out)))
    others = [i for i in ids if i != "probe@probe@example.com"]
    return others[0] if len(others) == 1 else None


def task_done(ledger, target: str) -> bool:
    """Did the run ACTUALLY do the task - not merely call the right tools?

    🔴 THIS EXISTS BECAUSE `reconcile(required=...)` CANNOT ANSWER IT, AND I USED
    IT AS IF IT COULD. `reconcile` compares tool NAMES against the ledger; it
    never inspects arguments, which is its documented contract and exactly right
    for what it is for. It is not a task-completion predicate.

    Measured on llama3.2:3b, and this is the whole reason the file changed:

        transcript mode sent  to_entity='<entity_id>'
        ledger mode sent      to_entity='NOT probe@probe@example.com'
        the real id is        claudeside@probe@example.com

    ⚠️ BOTH ARMS SCORED "COMPLETE" ON 39 OF 40 RUNS AND NEITHER EVER DID THE
    TASK. One sent a placeholder, the other sent a phrase copied out of the
    brief. The metric was counting tool invocations and I was reading it as
    task completions.

    ⚠️ AND THE SAME INSTRUMENT UNDERWRITES §21's "2 of 2 carried state across
    turns" - a claim that was itself a RETRACTION of §19. That table's `complete`
    column proves both tools ran and nothing about the id. It is not falsified;
    it is UNVERIFIED, which is a different and quieter problem.
    """
    for entry in ledger.entries:
        if (entry.call.name == "fam_send_message" and entry.executed
                and entry.error is None
                and str(entry.call.arguments.get("to_entity", "")) == target):
            return True
    return False


def one_run(source, model, schemas, mode, max_tokens, target=None):
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
    # ⚠️ TWO DIFFERENT QUESTIONS, KEPT APART. `tools_ran` is what reconcile can
    # answer: were the required tools invoked. `did_task` is what the experiment
    # actually asked: was the message sent to the id the listing returned.
    # Reporting only the first made both arms look ~100% while NEITHER ever did
    # the task.
    return {"mode": mode, "seconds": seconds, "verdict": rec.verdict,
            "tools_ran": rec.complete,
            "did_task": task_done(gate.ledger, target) if target else None,
            "steps": run.steps, "executed": list(rec.executed),
            "usage": run.usage, "stop": str(run.stop_reason)}


def summarise(rows, label):
    done = [r for r in rows if r.get("did_task")]
    ran = [r for r in rows if r.get("tools_ran")]
    secs = [r["seconds"] for r in rows if "seconds" in r]
    ins = [r["usage"].prompt_tokens for r in rows
           if r.get("usage") is not None and r["usage"].reported]
    # ⚠️ STEPS ARE THE DISCRIMINATOR FOR THE TOKEN FIGURE, not decoration. A
    # ledger prompt is SMALLER per step and RE-SENT every step, so total input
    # is (prompt size x steps) and the two arms move in opposite directions.
    # Reading total tokens without steps would attribute a step-count effect to
    # the context mechanism.
    steps = [r["steps"] for r in rows if "steps" in r]
    print(f"  {label:<16} DID THE TASK {len(done)}/{len(rows)}"
          f"   (tools ran {len(ran)}/{len(rows)})"
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
    parser.add_argument("--arms", nargs="*",
                        default=["transcript", "ledger"],
                        help="context modes to compare, run ALTERNATING")
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
    target = target_entity(source)
    if target is None:
        # ⚠️ Refuse rather than score. Without a known target every run would
        # read as a failure, and a probe that cannot tell right from wrong
        # produces a table of zeros that looks like a finding.
        print("could not identify a single target entity from the live "
              "listing; refusing to score.", file=sys.stderr)
        return 2
    rows = []
    try:
        print(f"target entity (read from the LIVE listing): {target}")
        print(f"model={args.model}  n={args.n} per arm  "
              f"max_tokens={args.max_tokens}  schemas={len(schemas)}")
        print("arms ALTERNATE - anything that drifts over the run hits both "
              "equally\n")
        for i in range(args.n):
            for mode in args.arms:
                r = one_run(source, args.model, schemas, mode, args.max_tokens,
                            target=target)
                rows.append(r)
                mark = "DID " if r.get("did_task") else "no  "
                extra = r.get("error") or r.get("stop", "")
                print(f"  {i + 1:>3} {mode:<11} {mark} {r['seconds']:6.1f}s  "
                      f"{extra}", flush=True)

        print()
        print("=" * 78)
        for mode in args.arms:
            summarise([r for r in rows if r["mode"] == mode], mode)
        print()
        # ⚠️ STOP REASON IS REPORTED SEPARATELY FROM COMPLETION, BECAUSE THEY
        # CAME APART. Measured at n=20: the plain ledger arm COMPLETED the task
        # 20/20 and TERMINATED VOLUNTARILY 0/20 - every run executed both
        # required tools and then ran to the step cap. A success rate alone
        # hides that completely, and it is the whole finding.
        print("  how each arm STOPPED - a different question from whether it "
              "finished the task:")
        for mode in args.arms:
            reasons: dict[str, int] = {}
            for r in rows:
                if r["mode"] == mode:
                    key = r.get("stop") or str(r.get("error", "?"))[:20]
                    reasons[key] = reasons.get(key, 0) + 1
            pretty = "  ".join(f"{k}={v}" for k, v in sorted(reasons.items()))
            print(f"    {mode:<16} {pretty}")
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
