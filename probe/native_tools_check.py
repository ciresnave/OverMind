# SPDX-License-Identifier: MIT OR Apache-2.0
# DOES THE FAILURE BELONG TO THE MODEL, OR TO MY TRANSPORT?
#
# The matrix reached the models through Ollama's OPENAI-COMPATIBLE shim (/v1).
# Several emitted a tool call as JSON *in the content* instead of in the
# protocol's tool_calls field, which the loop cannot see. That is a failure of
# SOMETHING; this asks WHICH.
#
# Same model, same tools, same prompt, through Ollama's NATIVE /api/chat.
# If native succeeds where /v1 failed, the defect is the shim, not the model.
import json, sys, urllib.request

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5-coder:14b"

TOOLS = [
    {"type": "function", "function": {
        "name": "fam_list_entities",
        "description": "List the entities reachable on this FAM server.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "fam_send_message",
        "description": "Send a message to another entity by entity id.",
        "parameters": {"type": "object", "properties": {
            "to_entity": {"type": "string", "description": "Entity id"},
            "text": {"type": "string", "description": "Message text"}},
            "required": ["to_entity", "text"]}}},
]

BODY = {
    "model": MODEL,
    "messages": [
        {"role": "system", "content": "You are an agent with tools. Use them. Call one tool at a time."},
        {"role": "user", "content":
         "Send the message 'hello from a local model' to the entity fam-lane@local. "
         "First list the entities to confirm the id exists, then send it."},
    ],
    "tools": TOOLS,
    "stream": False,
    "options": {"temperature": 0},
}

req = urllib.request.Request(
    "http://127.0.0.1:11434/api/chat",
    data=json.dumps(BODY).encode(),
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=600) as r:
    out = json.loads(r.read())

msg = out.get("message", {})
tc = msg.get("tool_calls") or []
content = (msg.get("content") or "").strip()
print("model            :", MODEL)
print("native tool_calls:", len(tc))
for c in tc:
    fn = c.get("function", {})
    print("   ->", fn.get("name"), json.dumps(fn.get("arguments")))
print("content          :", (content[:220] or "<empty>"))
print("VERDICT          :", "NATIVE-EMITS-TOOL_CALLS" if tc else "NATIVE-ALSO-FAILS")
