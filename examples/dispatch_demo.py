"""An end-to-end dispatch: a free-tier model is SENT work and reports back.

    python examples/dispatch_demo.py [provider]

This is the smallest unit of "could this replace a Claude lane": a task arrives
from another agent, a non-Claude model does it through the gate, and the answer
comes back to the sender.

⚠️ DELIVERY MUST BE LIVE, and that is a measured constraint rather than a
convenience. FAM's offline-backlog path pushes the SEALED ENVELOPE UNOPENED
(MEASUREMENTS.md §7.2), and `dispatch.classify` correctly refuses to feed a JSON
wrapper to a model as if it were a task. So the agent connects and subscribes
FIRST, and the sender transmits afterwards.

⚠️ AND THE SUBSCRIPTION IS REGISTERED BEFORE THE HANDSHAKE, NOT AFTER
DISCOVERING CAPABILITIES. §7.1: an authenticated connection dispatches and ACKS
the undelivered backlog, so a "harmless" discovery connection would consume and
destroy this agent's mail. Negotiation is a check here, not a probe.
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.dispatch import DispatchService
from overmind.gate import (
    DenyUnlessDeclared, ForbidTools, Gate, GatedExecutor, Ledger, StaticFacts,
)
from overmind.mcp_tools import McpToolSource, McpUnavailable
from overmind.providers import ProviderClient

FAM_DIR = os.environ.get("FAM_DIR", r"C:\Projects\fam")
SERVER = os.path.join(FAM_DIR, "src", "adapters", "mcp", "server.ts")

AGENT_CREDS = os.environ.get("AGENT_CREDENTIALS",
                             r"C:\Users\cires\.fam\credentials.probe.json")
AGENT_ENTITY = os.environ.get("AGENT_ENTITY", "probe@probe@example.com")
SENDER_CREDS = os.environ.get("SENDER_CREDENTIALS",
                              r"C:\Users\cires\.fam\credentials.claudeside.json")

TASK = os.environ.get(
    "DISPATCH_TASK",
    "Please list the entities on this FAM server and tell me how many there are.")

ALLOWED = frozenset({"fam_list_entities", "fam_list_channels", "fam_get_history",
                     "fam_send_message", "fam_list_invitations", "fam_set_status"})
FORBIDDEN = frozenset({"fam_kick_member", "fam_set_member_role"})


def source_for(creds: str, *, subscribe: bool) -> McpToolSource:
    env = dict(os.environ)
    env["FAM_CREDENTIALS"] = creds
    return McpToolSource.stdio(
        "bun", ["run", SERVER], env=env, cwd=FAM_DIR,
        expect_channels=("claude/channel",) if subscribe else ()).open()


def send_task_after(delay: float, results: dict) -> None:
    """A second agent sends the dispatch, on its own connection."""
    time.sleep(delay)
    try:
        # ⚠️ THE SENDER MUST SUBSCRIBE TOO. §1 + §7.1: an authenticated client
        # with no binding registered receives the reply, DROPS it at
        # logger.debug, and FAM acks it anyway - so the message is consumed and
        # destroyed and nothing anywhere reports the loss. This demo had that
        # bug: it connected the sender unsubscribed and would have "observed"
        # no reply while quietly eating it.
        sender = source_for(SENDER_CREDS, subscribe=True)
    except McpUnavailable as exc:
        results["error"] = f"sender could not connect: {exc}"
        return
    try:
        out = sender.call("fam_send_message", to_entity=AGENT_ENTITY, text=TASK)
        results["sent"] = out
        print(f"  [sender] dispatched -> {out[:110]}", flush=True)
        # Hold the connection open so the reply lands on the LIVE path too.
        time.sleep(float(os.environ.get("SENDER_HOLD", "45")))
        results["inbox"] = sender.drain()
    finally:
        sender.close()


def main() -> int:
    provider = sys.argv[1] if len(sys.argv) > 1 else "groq"
    print(f"provider={provider}  agent={AGENT_ENTITY}")

    try:
        agent_source = source_for(AGENT_CREDS, subscribe=True)
    except McpUnavailable as exc:
        print(f"agent could not connect: {exc}")
        return 1

    shared: dict = {}
    try:
        print(f"  [agent] {len(agent_source.tool_names())} tools; "
              f"advertised={agent_source.experimental_capabilities}")
        for warning in agent_source.channel_warnings:
            print(f"  [agent] !! channel warning: {warning}")

        gate = Gate([ForbidTools(FORBIDDEN, reason="member administration is not this agent's call"),
                     DenyUnlessDeclared(reversible=ALLOWED)],
                    facts=StaticFacts(), ledger=Ledger())
        executor = GatedExecutor(gate, agent_source.callables())
        client = ProviderClient(provider, max_tokens=600,
                                model=os.environ.get("OVERMIND_MODEL"))

        # Drain the connect notice before the sender transmits, so the wait is
        # for the DISPATCH rather than for FAM's own "Connected" push.
        time.sleep(2.0)
        agent_source.drain()

        sender_thread = threading.Thread(target=send_task_after, args=(1.0, shared), daemon=True)
        sender_thread.start()

        service = DispatchService(agent_source, executor, client,
                                  on_event=lambda kind, msg: print(f"  [agent] {kind}: {msg}",
                                                                   flush=True))
        print("  [agent] waiting to be dispatched to ...", flush=True)
        results = service.serve(max_dispatches=1, timeout=90.0)

        print("\n" + "=" * 62)
        if not results:
            print("NO DISPATCH HANDLED")
            print(f"  skipped: {service.skipped}")
            print(f"  sender:  {shared.get('sent') or shared.get('error')}")
            return 2

        r = results[0]
        print(f"dispatch from  : {r.dispatch.from_entity} "
              f"(vouched={r.dispatch.vouched}, id={r.dispatch.message_id})")
        print(f"task           : {r.dispatch.content[:100]!r}")
        print(f"model          : {r.run.provider}:{r.run.model}")
        print(f"tools EXECUTED : {list(r.executed_tools)}")
        print(f"tools DENIED   : {r.denied}")
        print(f"answer         : {r.run.final_text[:220]!r}")
        print(f"tokens spent   : {r.run.usage}")
        print(f"replied        : {r.replied}" + (f"  ({r.reply_error})" if r.reply_error else ""))
        # ⚠️ Read the round trip off the SENDER's inbox, not off our own claim
        # to have replied. `replied=True` only says the ledger recorded a send;
        # whether it ARRIVED is a different subject and needs the other end.
        sender_thread.join(timeout=float(os.environ.get("SENDER_HOLD", "45")) + 20)
        inbox = shared.get("inbox")
        if inbox is None:
            print("reply seen by sender: NOT OBSERVED (sender did not report an inbox)")
        else:
            # ⚠️ FILTER ON THE SENDER, NOT ON A MARKER. An earlier version
            # looked for the "[overmind]" prefix - which only the SERVICE adds.
            # When the MODEL replies itself the prefix is absent, so the check
            # reported "no reply" about a reply that had arrived. A wrong query
            # returning the expected-looking answer is the failure this file is
            # full of warnings about.
            landed = [n for n in inbox
                      if str((n.get("meta") or {}).get("from_entity") or "") == AGENT_ENTITY]
            other = [n for n in inbox if n not in landed]
            print(f"reply seen by sender: {bool(landed)}  "
                  f"({len(inbox)} notification(s) total, {len(other)} not from the agent)")
            for n in landed[:1]:
                print(f"  -> {(n.get('content') or '')[:220]!r}")
            if not landed and inbox:
                for n in inbox[:3]:
                    print(f"  (other) from={(n.get('meta') or {}).get('from_entity')!r} "
                          f"{(n.get('content') or '')[:90]!r}")
        return 0
    finally:
        agent_source.close()


if __name__ == "__main__":
    raise SystemExit(main())
