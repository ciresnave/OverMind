# STOCK PYTHON MCP SDK — same client, plus the SDK's own NotificationBinding
# extension point for a vendor notification method. Nothing patched.
import anyio, json
from pydantic import BaseModel, ConfigDict
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.extension import NotificationBinding

got, control = [], []

class ChannelParams(BaseModel):
    model_config = ConfigDict(extra="allow")
    content: str
    meta: dict = {}

async def on_channel(params: ChannelParams) -> None:
    got.append(params)
    print(f"[PY-BOUND] content={params.content!r} meta={json.dumps(params.meta)}", flush=True)

async def message_handler(message):
    if isinstance(message, Exception):
        print("[PY-EXC]", repr(message)[:200], flush=True); return
    m = getattr(getattr(message, "root", message), "method", None)
    control.append(m); print(f"[PY-TEE] method={m}", flush=True)

async def main():
    params = StdioServerParameters(command="bun", args=["run","C:/Projects/OverMind/probe/fam-push-server.ts"])
    binding = NotificationBinding(
        method="notifications/claude/channel", params_type=ChannelParams, handler=on_channel
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, message_handler=message_handler,
                                 notification_bindings=[binding]) as session:
            await session.initialize()
            print("[PY] initialized", flush=True)
            await anyio.sleep(3.5)
    print(f"[PY] RESULT bound_deliveries={len(got)} tee_methods={control}", flush=True)

anyio.run(main)
