# A minimal MCP stdio server, Python stdlib only, no SDK.
# Stands in for FAM's adapter while FAM's live server is being brought up.
# Its tools are deliberately shaped like FAM's so the agent swaps target with
# one argument. It RECORDS every call to a side file so the agent's claim that
# it called a tool can be checked against the server's own record.
import json, sys, os

CALLS_PATH = os.environ.get("MOCK_CALLS", "mock_calls.jsonl")
open(CALLS_PATH, "w").close()

TOOLS = [
    {
        "name": "fam_list_entities",
        "description": "List the entities (agents/humans) reachable on this FAM server.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "fam_send_message",
        "description": "Send a message to another entity by entity id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to_entity": {"type": "string", "description": "Entity id, e.g. name@example.com"},
                "text": {"type": "string", "description": "The message text to send"},
            },
            "required": ["to_entity", "text"],
        },
    },
]

ENTITIES = ["overmind@local", "fam-lane@local", "portfolio-pm@local"]


def record(name, args):
    with open(CALLS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"tool": name, "args": args}) + "\n")


def call(name, args):
    record(name, args)
    if name == "fam_list_entities":
        return "Entities: " + ", ".join(ENTITIES)
    if name == "fam_send_message":
        to, text = args.get("to_entity"), args.get("text")
        if to not in ENTITIES:
            return "ERROR: unknown entity %r. Known: %s" % (to, ", ".join(ENTITIES))
        return "DELIVERED to %s (%d chars)" % (to, len(text or ""))
    return "ERROR: unknown tool " + name


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mock-fam", "version": "0.0.1"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        p = msg.get("params", {})
        out = call(p.get("name"), p.get("arguments") or {})
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"content": [{"type": "text", "text": out}], "isError": out.startswith("ERROR")}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}})
