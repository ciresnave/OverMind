# SPDX-License-Identifier: MIT OR Apache-2.0
# STOCK PYTHON MCP SDK CLIENT — different implementation from the TS SDK.
import anyio, json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

got = []

def method_of(m):
    r = getattr(m, "root", None)
    for cand in (r, m):
        if cand is not None and hasattr(cand, "method"):
            return cand.method
    return None

async def message_handler(message):
    if isinstance(message, Exception):
        got.append(("EXC", repr(message)[:200])); print("[PY-EXC]", repr(message)[:200], flush=True); return
    meth = method_of(message)
    got.append((type(message).__name__, meth))
    print(f"[PY-SAW] class={type(message).__name__} method={meth}", flush=True)

async def main():
    params = StdioServerParameters(command="bun", args=["run","C:/Projects/OverMind/probe/fam-push-server.ts"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, message_handler=message_handler) as session:
            await session.initialize()
            print("[PY] initialized", flush=True)
            await anyio.sleep(3.5)
    print(f"[PY] RESULT saw={got}", flush=True)

anyio.run(main)
