# SPDX-License-Identifier: MIT OR Apache-2.0
# RAW JSON-RPC over stdio. No MCP SDK at all. Proves what bytes leave the server.
import json, subprocess, sys, threading, time

p = subprocess.Popen(["bun","run","C:/Projects/OverMind/probe/fam-push-server.ts"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                     text=True, encoding="utf-8", bufsize=1)

def send(obj):
    p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()

send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
    "protocolVersion":"2025-06-18","capabilities":{},
    "clientInfo":{"name":"raw-stdio-client","version":"0.0.1"}}})

frames = []
def reader():
    for line in p.stdout:
        line = line.strip()
        if not line: continue
        try: msg = json.loads(line)
        except Exception: continue
        frames.append(msg)
        if msg.get("id") == 1:
            send({"jsonrpc":"2.0","method":"notifications/initialized"})
t = threading.Thread(target=reader, daemon=True); t.start()
time.sleep(4)
notifs = [f for f in frames if "method" in f and "id" not in f]
print(f"RAW: total frames={len(frames)} notifications={len(notifs)}")
for n in notifs:
    print("RAW  ->", n["method"], json.dumps(n.get("params"))[:220])
try: p.kill()
except Exception: pass
