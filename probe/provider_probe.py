# SPDX-License-Identifier: MIT OR Apache-2.0
# WHICH OF THE FIVE FREE-TIER PROVIDERS REACHES A WORKING TOOL CALL?
#
# ⚠️ KEYS COME FROM THE with-secret VAULT (since 2026-10-15), NOT FROM THE
# ENVIRONMENT OR THE REGISTRY. A secret reaches this process only through
#   with-secret NAME --reason "..." -- python probe/provider_probe.py <provider>
# and that releases ONE secret per invocation, so the probe takes the
# provider(s) to run and needs exactly their tokens. A missing one is a named
# error and exit 2 before any request is made.
#   groq       GROQ_CLOUD_API_TOKEN
#   nvidia     NVIDIA_NIM_API_TOKEN
#   openrouter OPENROUTER_API_TOKEN
#   google     GOOGLE_AI_STUDIO_API_TOKEN
#   cloudflare CLOUDFLARE_WORKERS_AI_API_TOKEN (+ CLOUDFLARE_ACCOUNT_ID if set,
#              else discovered through the API; the id is not a secret)
# Secrets are never printed: only presence, length and the request outcome.
#
# ⚠️ VERDICT OFF THE TOOL SIDE. A model that writes a tool-call-shaped JSON blob
# into `content` is scored FAIL and reported as such - that was 4 of 5 local
# failures and there is no reason for it to be rarer on a hosted model.
#
# ⚠️ SPEND SPARINGLY. At most one /models GET and two chat calls per provider.
import json, os, sys, time, urllib.error, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from overmind.providers import SecretNotSet, require_secret  # noqa: E402

SECRETS = {
    "groq": "GROQ_CLOUD_API_TOKEN",
    "nvidia": "NVIDIA_NIM_API_TOKEN",
    "openrouter": "OPENROUTER_API_TOKEN",
    "google": "GOOGLE_AI_STUDIO_API_TOKEN",
    "cloudflare": "CLOUDFLARE_WORKERS_AI_API_TOKEN",
}

def secrets_for(only):
    """The secret names the selected providers need, in order."""
    return [SECRETS[p] for p in only]

TOOLS = [{"type": "function", "function": {
    "name": "get_build_status",
    "description": "Return the CI build status for a repository branch.",
    "parameters": {"type": "object", "properties": {
        "repo": {"type": "string", "description": "owner/name"},
        "branch": {"type": "string", "description": "branch name"}},
        "required": ["repo", "branch"]}}}]
ASK = ("What is the CI build status of branch 'main' in the repository "
       "ciresnave/fuel? Use the tool.")

def http(url, token, payload=None, extra=None, timeout=90):
    hdrs = {"Content-Type": "application/json",
            "User-Agent": "OverMind-probe/0.1 (+https://github.com/ciresnave/OverMind)",
            "Accept": "application/json"}
    if token: hdrs["Authorization"] = "Bearer " + token
    if extra: hdrs.update(extra)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=hdrs,
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        return e.code, {"__error__": body}, dict(e.headers or {})
    except Exception as e:
        return None, {"__error__": type(e).__name__ + ": " + str(e)[:200]}, {}

PREF = ("gemini-3.6-flash", "gemini-3.5-flash", "gemini-3", "llama-3.3", "llama-3.1",
        "llama3.3", "llama3.1", "llama-4", "qwen", "mistral", "gpt-oss", "deepseek", "gemma")
# ⚠️ NOT every model on a roster is a chat model. A guardrail, embedder or
# reranker whose NAME contains "llama-3.1" will be selected by a naive
# substring match and then fail with "tool use is unsupported" - which reads
# as a PROVIDER verdict and is actually a model-selection bug.
BAD = ("guard", "safety", "embed", "rerank", "reward", "ocr", "moderation",
       "whisper", "tts", "stt", "diffusion", "image", "video", "retriever",
       "classifier", "parse", "extract")
def pick_models(ids, require_free=False, k=int(__import__("os").environ.get("KCAND","4"))):
    out = []
    for _ in range(k):
        m = pick_model([i for i in ids if i not in out], require_free)
        if not m: break
        out.append(m)
    return out

def pick_model(ids, require_free=False):
    cands = [m for m in ids if (":free" in m) or not require_free]
    if require_free and not cands: cands = ids
    cands = [m for m in cands if not any(b in m.lower() for b in BAD)]
    inst = [m for m in cands if "instruct" in m.lower() or "chat" in m.lower()]
    for pool in (inst, cands):
        for p in PREF:
            for m in pool:
                if p in m.lower(): return m
    return (inst or cands or [None])[0]

def ratelimit_bits(h):
    keys = [k for k in h if k.lower().startswith(("x-ratelimit", "ratelimit", "retry-after",
                                                  "x-request-id"))]
    return {k: h[k] for k in sorted(keys)[:8]}

def score_tool(msg):
    """Tool-side verdict: a REAL protocol tool_call, or JSON smuggled into content."""
    tcs = msg.get("tool_calls") or []
    content = (msg.get("content") or "")
    if tcs:
        fn = (tcs[0].get("function") or {})
        args = fn.get("arguments")
        if isinstance(args, str):
            try: args = json.loads(args or "{}")
            except Exception: args = {"__unparseable__": True}
        return "TOOL_CALL", fn.get("name"), args
    if content and ("get_build_status" in content and ("{" in content)):
        return "JSON_IN_CONTENT", None, content[:160]
    return "NO_TOOL_CALL", None, content[:160]

def probe(label, base, token, model_hint=None, require_free=False, extra=None):
    print("\n===== %s =====" % label, flush=True)
    if not token:
        print("  KEY: ABSENT"); return
    print("  KEY: present (len %d)" % len(token))
    # 1. reachability + roster (one GET)
    st, body, hdrs = http(base.rstrip("/") + "/models", token, extra=extra)
    ids = []
    if st == 200:
        data = body.get("data") or body.get("models") or []
        ids = [d.get("id") or d.get("name") for d in data if isinstance(d, dict)]
        ids = [i for i in ids if i]
        print("  /models: HTTP 200, %d models" % len(ids))
    else:
        print("  /models: HTTP %s  %s" % (st, str(body.get("__error__", body))[:220]))
    cands = [model_hint] if model_hint else pick_models(ids, require_free)
    if not cands or not cands[0]:
        print("  -> no model selectable; stopping"); return
    print("  candidates: %s" % cands)
    # 2. one tool-calling completion
    payload = {"model": cands[0], "messages": [{"role": "user", "content": ASK}],
               "tools": TOOLS, "temperature": 0, "max_tokens": 200}
    st = None
    for model in cands:
        payload["model"] = model
        t0 = time.time()
        st, body, hdrs = http(base.rstrip("/") + "/chat/completions", token, payload, extra=extra)
        dt = time.time() - t0
        rl = ratelimit_bits(hdrs)
        if st == 200:
            print("  model used: %s" % model); break
        print("  %s -> HTTP %s: %s" % (model, st, str(body.get("__error__", body))[:200]))
        if st in (401, 403):   # auth: trying more models tells us nothing
            break
    if st != 200:
        if rl: print("  rate-limit headers: %s" % rl)
        return
    msg = ((body.get("choices") or [{}])[0].get("message") or {})
    verdict, fname, args = score_tool(msg)
    print("  chat: HTTP 200 in %.1fs -> %s" % (dt, verdict))
    print("     function=%s args=%s" % (fname, json.dumps(args)[:200] if isinstance(args, dict) else str(args)[:200]))
    if rl: print("  rate-limit headers: %s" % rl)
    if verdict != "TOOL_CALL":
        return
    # 3. close the loop: feed a tool result back, confirm it uses it (one more call)
    tc = (msg.get("tool_calls") or [])[0]
    msgs = [{"role": "user", "content": ASK}, msg,
            {"role": "tool", "tool_call_id": tc.get("id") or "call_1",
             "name": fname, "content": "status=FAILING, failing_step=clippy, run_id=34371759050"}]
    st2, body2, _ = http(base.rstrip("/") + "/chat/completions", token,
                         {"model": model, "messages": msgs, "tools": TOOLS,
                          "temperature": 0, "max_tokens": 120}, extra=extra)
    if st2 == 200:
        txt = (((body2.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        used = "clippy" in txt.lower() or "failing" in txt.lower()
        print("  round-trip: HTTP 200, used_tool_result=%s | %r" % (used, txt[:160]))
    else:
        print("  round-trip: HTTP %s %s" % (st2, str(body2.get("__error__", body2))[:200]))

def cloudflare_account(token):
    st, body, _ = http("https://api.cloudflare.com/client/v4/accounts", token)
    if st == 200 and body.get("result"):
        a = body["result"][0]
        return a.get("id"), a.get("name")
    return None, str(body.get("__error__", body))[:200]

def main(argv):
    wanted = list(dict.fromkeys(argv))
    unknown = [a for a in wanted if a not in SECRETS]
    if not wanted or unknown:
        if unknown:
            print("unknown provider(s): %s" % ", ".join(unknown), file=sys.stderr)
        print("usage: provider_probe.py <%s> [...]  (one with-secret run releases "
              "ONE secret: probe one provider per run)" % "|".join(SECRETS), file=sys.stderr)
        return 2
    only = set(wanted)
    K = {}
    missing = []
    for n in secrets_for(wanted):
        try:
            K[n] = require_secret(n)
        except SecretNotSet as exc:
            missing.append(str(exc))
    if missing:
        for m in missing:
            print(m, file=sys.stderr)
        return 2
    print("secrets (present = injected by with-secret; values never printed):")
    for n, v in K.items():
        print("  %-34s present len=%d" % (n, len(v)))

    if "groq" in only:
        probe("GROQ", "https://api.groq.com/openai/v1", K["GROQ_CLOUD_API_TOKEN"])
    if "nvidia" in only:
        probe("NVIDIA NIM", "https://integrate.api.nvidia.com/v1", K["NVIDIA_NIM_API_TOKEN"])
    if "openrouter" in only:
        probe("OPENROUTER", "https://openrouter.ai/api/v1", K["OPENROUTER_API_TOKEN"],
              require_free=True, extra={"HTTP-Referer": "https://github.com/ciresnave/OverMind",
                                        "X-Title": "OverMind provider probe"})
    if "google" in only:
        probe("GOOGLE AI STUDIO (OpenAI compat)",
              "https://generativelanguage.googleapis.com/v1beta/openai",
              K["GOOGLE_AI_STUDIO_API_TOKEN"])
    if "cloudflare" in only:
        tok = K["CLOUDFLARE_WORKERS_AI_API_TOKEN"]
        print("\n===== CLOUDFLARE WORKERS AI =====")
        print("  KEY: present (len %d)" % len(tok))
        acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID"); note = "from the environment"
        if not acct:
            acct, note = cloudflare_account(tok)
        if not acct:
            print("  !! NO ACCOUNT ID and none in the environment; /accounts said: %s" % note)
            print("  -> Workers AI's OpenAI-compatible path needs the account id. BLOCKED.")
            return 0
        print("  account discovered: %s (%s)" % (acct, note))
        probe("CLOUDFLARE WORKERS AI",
              "https://api.cloudflare.com/client/v4/accounts/%s/ai/v1" % acct, tok)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
