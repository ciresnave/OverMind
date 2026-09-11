# SPDX-License-Identifier: MIT OR Apache-2.0
# NON-CLAUDE MCP CLIENT for FAM's LIVE adapter.
# Nothing Anthropic in this file: stock `mcp` Python SDK over stdio.
#
# Two phases, because the SDK fixes notification bindings at ClientSession
# CONSTRUCTION -- before the handshake. So discovery cannot drive registration
# within one session; it can only validate it. Reconnecting is what makes the
# client genuinely capability-NEGOTIATED rather than FAM-special-cased.
#
#   phase 1  connect, initialize, read capabilities.experimental, disconnect
#   phase 2  reconnect with a NotificationBinding built from what was DISCOVERED,
#            call a tool, hold for a push
import anyio, json, os, sys
from pydantic import BaseModel, ConfigDict
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.extension import NotificationBinding

FAM_DIR   = os.environ.get("FAM_DIR", r"C:\Projects\fam")
TO_ENTITY = os.environ.get("DEMO_TO_ENTITY", "")
CHANNEL   = os.environ.get("DEMO_CHANNEL", "")
TEXT      = os.environ.get("DEMO_TEXT", "hello from a non-Claude MCP client")
HOLD      = float(os.environ.get("DEMO_HOLD", "25"))
LISTEN_ONLY = os.environ.get("DEMO_LISTEN_ONLY", "") == "1"

pushes = []

class ChannelParams(BaseModel):
    model_config = ConfigDict(extra="allow")
    content: str
    meta: dict = {}

async def on_push(p: ChannelParams) -> None:
    pushes.append(p)
    print(f"[PUSH] content={p.content!r}", flush=True)
    print(f"[PUSH] meta={json.dumps(p.meta, default=str)}", flush=True)

def server_params():
    return StdioServerParameters(
        command="bun", args=["run", os.path.join(FAM_DIR, "src", "adapters", "mcp", "server.ts")],
        cwd=FAM_DIR, env={**os.environ})

async def phase1_discover():
    async with stdio_client(server_params()) as (read, write):
        async with ClientSession(read, write) as s:
            info = await s.initialize()
            caps = info.capabilities
            exp = getattr(caps, "experimental", None) or {}
            print(f"[DISCOVER] server={info.server_info.name} {info.server_info.version}", flush=True)
            print(f"[DISCOVER] capabilities.experimental = {json.dumps(exp, default=str)}", flush=True)
            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            print(f"[DISCOVER] tools={len(names)} fam_send_message present={'fam_send_message' in names}", flush=True)
            return list(exp.keys()), names

async def phase2_run(methods):
    bindings = [NotificationBinding(method=m, params_type=ChannelParams, handler=on_push) for m in methods]
    print(f"[BIND] registering {len(bindings)} binding(s) DERIVED FROM DISCOVERY: {methods}", flush=True)
    async with stdio_client(server_params()) as (read, write):
        async with ClientSession(read, write, notification_bindings=bindings) as s:
            await s.initialize()
            print("[OK] reconnected with negotiated bindings", flush=True)
            if LISTEN_ONLY:
                print(f"[..] listen-only; holding {HOLD}s", flush=True)
            elif TO_ENTITY or CHANNEL:
                args = {"text": TEXT}
                if TO_ENTITY: args["to_entity"] = TO_ENTITY
                else: args["channel_id"] = CHANNEL
                r = await s.call_tool("fam_send_message", args)
                body = "".join(getattr(c, "text", "") for c in r.content)
                print(f"[SEND] isError={r.is_error} result={body[:500]!r}", flush=True)
            else:
                print("[SKIP] no DEMO_TO_ENTITY / DEMO_CHANNEL set", flush=True)
            await anyio.sleep(HOLD)

async def main():
    if os.environ.get("SKIP_DISCOVERY") == "1":
        print("[SKIP-DISCOVERY] binding directly, no probe connection", flush=True)
        await phase2_run(["notifications/claude/channel"])
        print(f"[RESULT] pushes_received={len(pushes)} bound_methods=['notifications/claude/channel']", flush=True)
        return
    exp_keys, _ = await phase1_discover()
    # capability key "claude/channel" -> notification method "notifications/claude/channel"
    methods = ["notifications/" + k for k in exp_keys]
    if not methods:
        print("[WARN] server advertised NO experimental capabilities; nothing to bind", flush=True)
        methods = []
    await phase2_run(methods)
    print(f"[RESULT] pushes_received={len(pushes)} bound_methods={methods}", flush=True)

anyio.run(main)
