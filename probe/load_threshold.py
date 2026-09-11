# SPDX-License-Identifier: MIT OR Apache-2.0
# AT WHAT INSTRUCTION LOAD DOES A LOCAL MODEL STOP DOING THE WORK?
#
# This is the number a CLAUDE.md restructure is cutting TO. Without it, cutting
# is tidiness; with it, it is engineering.
#
# Method: prepend N tokens of REAL instruction text (the portfolio + fuel
# CLAUDE.md files, so the prose is representative rather than lorem) as a system
# message, then run the same two-step MCP tool task and read the verdict OFF THE
# SERVER'S OWN RECORD -- never off the model's narration.
#
# ⚠️ TWO CONTROLS, because three different things can produce a FAIL here and
# they need different responses:
#   load=0 must PASS          -> otherwise the model was never able to do it
#   prompt_eval_count         -> if it stops tracking the load we asked for, the
#                                prompt was TRUNCATED and the model never saw
#                                the instructions. Truncation and overload look
#                                identical without this.
import json, os, subprocess, sys, urllib.request

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = os.environ.get("LLM_MODEL", "qwen3:8b")
CALLS = os.path.abspath("mock_calls.jsonl")

TOOLS = [
    {"type": "function", "function": {
        "name": "fam_list_entities", "description": "List the entities reachable on this FAM server.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "fam_send_message", "description": "Send a message to another entity by entity id.",
        "parameters": {"type": "object", "properties": {
            "to_entity": {"type": "string"}, "text": {"type": "string"}},
            "required": ["to_entity", "text"]}}},
]
ENTITIES = ["overmind@local", "fam-lane@local", "portfolio-pm@local"]
TASK = ("Send the message 'hello from a local model' to the entity fam-lane@local. "
        "First list the entities to confirm the id exists, then send it.")

def load_corpus():
    parts = []
    for p in (r"C:\Projects\CLAUDE.md", r"C:\Projects\fuel\CLAUDE.md"):
        if os.path.exists(p):
            parts.append(open(p, encoding="utf-8", errors="replace").read())
    return "\n\n".join(parts)

CORPUS = load_corpus()

NUM_CTX = int(os.environ.get("NUM_CTX", "40960"))
LOADS = [int(x) for x in os.environ.get("LOADS","0,2000,4000,8000,12000,16000,24000,32000").split(",")]

def chat(messages, num_ctx=None, num_predict=256):
    num_ctx = num_ctx or NUM_CTX
    body = {"model": MODEL, "messages": messages, "tools": TOOLS, "stream": False,
            "think": False, "options": {"temperature": 0, "num_ctx": num_ctx,
                                        "num_predict": num_predict}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1200) as r:
        return json.loads(r.read())

def exec_tool(name, args, calls):
    calls.append((name, args))
    if name == "fam_list_entities":
        return "Entities: " + ", ".join(ENTITIES)
    if name == "fam_send_message":
        if args.get("to_entity") not in ENTITIES:
            return "ERROR: unknown entity %r" % args.get("to_entity")
        return "DELIVERED to %s" % args["to_entity"]
    return "ERROR: unknown tool " + name

def run(load_tokens):
    # ~4 bytes/token is close enough to SIZE the slice; the real load is read
    # back from prompt_eval_count, which is what the verdict uses.
    system = "You are an agent with tools. Use them. Call one tool at a time."
    if load_tokens:
        system = CORPUS[: load_tokens * 4] + "\n\n---\n\n" + system
    messages = [{"role": "system", "content": system}, {"role": "user", "content": TASK}]
    calls, evals = [], []
    for _ in range(6):
        out = chat(messages)
        evals.append(out.get("prompt_eval_count"))
        m = out.get("message", {}) or {}
        tcs = m.get("tool_calls") or []
        messages.append(m)
        if not tcs:
            break
        for tc in tcs:
            fn = tc.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try: args = json.loads(args)
                except Exception: args = {}
            res = exec_tool(fn.get("name"), args, calls)
            messages.append({"role": "tool", "content": res})
    sent = any(n == "fam_send_message" and a.get("to_entity") == "fam-lane@local" and a.get("text")
               for n, a in calls)
    return {"load": load_tokens, "first_eval": evals[0] if evals else None,
            "calls": [c[0] for c in calls], "PASS": sent}

def main():
    print("model=%s  corpus_bytes=%d" % (MODEL, len(CORPUS)), flush=True)
    for load in LOADS:
        try:
            r = run(load)
        except Exception as e:
            print("  load=%-6s ERROR %s" % (load, str(e)[:90]), flush=True); continue
        tag = "PASS" if r["PASS"] else "FAIL"
        note = ""
        if load and r["first_eval"] and r["first_eval"] < load * 0.6:
            note = "  <-- TRUNCATED: model never saw the load we sent"
        print("  load~%-6s first_eval=%-7s %s  calls=%s%s"
              % (load, r["first_eval"], tag, r["calls"], note), flush=True)
        if load == 0 and not r["PASS"]:
            print("  !! CONTROL FAILED at load=0 - nothing below is interpretable", flush=True)
            return

main()
