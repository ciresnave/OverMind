# SPDX-License-Identifier: MIT OR Apache-2.0
"""One-off scratch restore test of the claude-peers broker (port 7897, scratch DB).
Never touches 7899. Prints a transcript."""
import json, os, subprocess, sys, tempfile, time, threading, queue, urllib.request

REPO = r"C:\Users\cires\claude-peers-mcp"
PORT = "7897"
scratch = tempfile.mkdtemp(prefix="peers-restore-")
db = os.path.join(scratch, "peers.db")
cwdA = os.path.join(scratch, "sessA"); cwdB = os.path.join(scratch, "sessB")
os.makedirs(cwdA); os.makedirs(cwdB)
env = {**os.environ, "CLAUDE_PEERS_PORT": PORT, "CLAUDE_PEERS_DB": db}


def http(path, body=None, port=PORT):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def live_peers():
    return http("/health", None, "7899")["peers"]


print("scratch dir:", scratch)
before = live_peers()
print("live broker (7899) peers BEFORE:", before)

broker = subprocess.Popen(["bun", os.path.join(REPO, "broker.ts")], env=env, cwd=scratch,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
for _ in range(50):
    try:
        print("scratch broker health:", http("/health")); break
    except Exception:
        time.sleep(0.2)
else:
    print("BROKER DID NOT START"); broker.kill(); sys.exit(1)


class Session:
    def __init__(self, name, cwd):
        self.name = name
        self.p = subprocess.Popen(["bun", os.path.join(REPO, "server.ts")], env=env, cwd=cwd,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        self.out = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.n = 0

    def _pump(self):
        for line in self.p.stdout:
            try:
                self.out.put(json.loads(line))
            except Exception:
                pass

    def send(self, method, params=None, notify=False):
        self.n += 1
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n"); self.p.stdin.flush()
        return self.n

    def wait(self, pred, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            try:
                m = self.out.get(timeout=0.5)
            except queue.Empty:
                continue
            if pred(m):
                return m
        return None

    def init(self):
        i = self.send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": "restore-test", "version": "0"}})
        r = self.wait(lambda m: m.get("id") == i)
        self.send("notifications/initialized", notify=True)
        return r

    def call(self, tool, args):
        i = self.send("tools/call", {"name": tool, "arguments": args})
        return self.wait(lambda m: m.get("id") == i)


try:
    A, B = Session("A", cwdA), Session("B", cwdB)
    ra = A.init(); B.init()
    print("A initialize ->", json.dumps(ra)[:300])
    time.sleep(2)
    peers = http("/list-peers", {"scope": "machine", "cwd": scratch, "git_root": None})
    print("scratch peers:", [(p["id"], p["pid"], os.path.basename(p["cwd"])) for p in peers])
    ida = next(p["id"] for p in peers if p["cwd"].endswith("sessA"))
    idb = next(p["id"] for p in peers if p["cwd"].endswith("sessB"))

    r = A.call("send_message", {"to_id": idb, "message": "ping from A"})
    print("A send_message ->", json.dumps(r)[:250])
    got = B.wait(lambda m: m.get("method") == "notifications/claude/channel", 20)
    print("B received channel notification:", json.dumps(got)[:300])

    r = B.call("send_message", {"to_id": ida, "message": "pong from B"})
    print("B send_message ->", json.dumps(r)[:250])
    got2 = A.wait(lambda m: m.get("method") == "notifications/claude/channel", 20)
    print("A received channel notification:", json.dumps(got2)[:300])
    ok = bool(got) and bool(got2) and "ping from A" in json.dumps(got) and "pong from B" in json.dumps(got2)
    print("RESULT:", "PASS" if ok else "FAIL")
finally:
    for s in (locals().get("A"), locals().get("B")):
        if s:
            try:
                s.p.stdin.close(); s.p.terminate()
            except Exception:
                pass
    try:
        http("/shutdown", {})
    except Exception:
        pass
    time.sleep(1)
    broker.poll() is None and broker.terminate()
    print("scratch broker stderr:", (broker.stderr.read() or b"").decode()[:400])
    print("live broker (7899) peers AFTER:", live_peers(), "(before was %s)" % before)
    print("7897 still listening:", subprocess.run("netstat -ano | findstr :7897", shell=True, capture_output=True, text=True).stdout.strip()  # fixed literal, no input or "no")
