# SPDX-License-Identifier: MIT OR Apache-2.0
# WHICH NVIDIA MODELS IS THIS ACCOUNT ACTUALLY ENTITLED TO?
# A 404 "Function <uuid>: Not found for account" is returned BEFORE inference,
# so sweeping the roster costs no quota. max_tokens=1 keeps the entitled ones cheap.
import json, sys, time, urllib.error, urllib.request, winreg
def reg(n):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        return winreg.QueryValueEx(k, n)[0]
T = reg("NVIDIA_NIM_API_TOKEN")
H = {"Authorization": "Bearer " + T, "Content-Type": "application/json",
     "User-Agent": "OverMind-probe/0.1", "Accept": "application/json"}
def roster():
    r = urllib.request.Request("https://integrate.api.nvidia.com/v1/models", headers=H)
    return [m["id"] for m in json.load(urllib.request.urlopen(r, timeout=60))["data"]]
def probe(m):
    body = {"model": m, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
    r = urllib.request.Request("https://integrate.api.nvidia.com/v1/chat/completions",
                               data=json.dumps(body).encode(), headers=H)
    try:
        with urllib.request.urlopen(r, timeout=12) as resp:
            json.load(resp); return "ENTITLED"
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf-8", "replace")
        if e.code == 404 and "Not found for account" in b: return "NOT-ENTITLED"
        return "HTTP%s" % e.code
    except Exception as e:
        return "ROUTED-SLOW" if "timed out" in str(e).lower() else type(e).__name__
ids = roster()
print("roster: %d" % len(ids), flush=True)
buckets = {}
for m in ids:
    v = probe(m)
    buckets.setdefault(v, []).append(m)
    time.sleep(0.15)
for k in sorted(buckets):
    print("\n%s: %d" % (k, len(buckets[k])), flush=True)
    if k != "NOT-ENTITLED":
        for m in buckets[k]: print("   ", m, flush=True)
