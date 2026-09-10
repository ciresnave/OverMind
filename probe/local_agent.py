# A NON-CLAUDE AGENT: a local LLM driving MCP tools.
#
# Nothing Anthropic anywhere in this file or its dependency set.
#   model      : Ollama, local, zero API cost
#   transport  : Ollama's OpenAI-COMPATIBLE endpoint (http://127.0.0.1:11434/v1)
#                -> the same code reaches OpenRouter / Gemini / NIM by changing
#                   base_url and api_key only.
#   tools      : discovered over MCP from whatever stdio server is passed in,
#                so the target swaps between the mock and FAM's real adapter
#                without touching the loop.
import anyio, json, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
API_KEY = os.environ.get("LLM_API_KEY", "ollama")   # Ollama ignores it; real providers do not
MODEL = os.environ.get("LLM_MODEL", "llama3.1:8b")
MAX_STEPS = int(os.environ.get("MAX_STEPS", "6"))

SERVER_CMD = os.environ.get("MCP_CMD", sys.executable)
SERVER_ARGS = json.loads(os.environ.get("MCP_ARGS", '["mock_mcp_server.py"]'))
TASK = os.environ.get(
    "TASK",
    "Send the message 'hello from a local model' to the entity fam-lane@local. "
    "First list the entities to confirm the id exists, then send it.",
)


def to_openai_tools(mcp_tools):
    out = []
    for t in mcp_tools:
        schema = t.input_schema or {"type": "object", "properties": {}}
        out.append({"type": "function", "function": {
            "name": t.name, "description": (t.description or "")[:900], "parameters": schema}})
    return out


async def main():
    llm = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY)
    params = StdioServerParameters(command=SERVER_CMD, args=SERVER_ARGS, env={**os.environ},
                                   cwd=os.environ.get("MCP_CWD") or None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            info = await s.initialize()
            listed = await s.list_tools()
            tools = to_openai_tools(listed.tools)
            print("[mcp] server=%s tools=%d -> %s" % (
                info.server_info.name, len(tools), [t["function"]["name"] for t in tools]), flush=True)

            messages = [
                {"role": "system", "content":
                 "You are an agent with tools. Use them to complete the task. "
                 "Call one tool at a time. When the task is done, reply with the single word DONE "
                 "followed by a one-line summary."},
                {"role": "user", "content": TASK},
            ]
            calls_made = []
            for step in range(MAX_STEPS):
                r = await llm.chat.completions.create(
                    model=MODEL, messages=messages, tools=tools, temperature=0)
                m = r.choices[0].message
                messages.append(m.model_dump(exclude_none=True))
                if not m.tool_calls:
                    print("[llm] final: %s" % (m.content or "")[:400], flush=True)
                    break
                for tc in m.tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except Exception:
                        args = {}
                    print("[llm->mcp] step %d call %s(%s)" % (step, name, json.dumps(args)), flush=True)
                    calls_made.append((name, args))
                    res = await s.call_tool(name, args)
                    text = "".join(getattr(c, "text", "") for c in res.content)
                    print("[mcp->llm] %s" % text[:200], flush=True)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
            print("[RESULT] model=%s tool_calls=%d %s" % (
                MODEL, len(calls_made), [c[0] for c in calls_made]), flush=True)

anyio.run(main)
