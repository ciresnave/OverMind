# SPDX-License-Identifier: MIT OR Apache-2.0
# THE SAME COMPLIANCE EXPERIMENT AS compliance_probe.py, AGAINST A HOSTED
# OPENAI-COMPATIBLE PROVIDER.
#
# ⚠️ The rules come from rules_lib, imported by BOTH scripts, so the local and
# hosted numbers are the same experiment and not two similar ones.
#
# ⚠️ SPENDS A FREE TIER. Budget is (1 comprehension + N behaviour) per rule, and
# a behaviour trial is 2-3 calls. Keep N small; this is a test of whether they
# obey, not a benchmark.
#
# ⚠️ Groq's binding free-tier limit is OUTPUT tokens per minute (1000), so
# max_tokens is capped hard and a 429 pauses rather than being scored as a
# refusal - a rate limit is not a compliance datum.
import json, os, sys, time, urllib.error, urllib.request, winreg
from rules_lib import RULES, R1_HOW

def reg(n):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, n)[0]
    except OSError:
        return None

PROVIDERS = {
    "groq":       ("https://api.groq.com/openai/v1", "GROQ_CLOUD_API_TOKEN", "qwen/qwen3.6-27b"),
    "google":     ("https://generativelanguage.googleapis.com/v1beta/openai",
                   "GOOGLE_AI_STUDIO_API_TOKEN", "models/gemini-3.6-flash"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_TOKEN",
                   "nex-agi/nex-n2.5-mini:free"),
}
N = int(os.environ.get("N", "5"))
MAXTOK = int(os.environ.get("MAXTOK", "160"))

def call(base, token, model, messages, tools=None, retries=3, max_tokens=None):
    # ⚠️ A THINKING MODEL SPENDS THE OUTPUT BUDGET BEFORE IT ANSWERS. gemini-3.6-flash
    # returned 2-8 visible characters under max_tokens=160 and every rule scored VOID -
    # an INSTRUMENT failure that read as "the model does not hold the rule".
    # The comprehension call therefore gets a much larger budget than behaviour.
    body = {"model": model, "messages": messages, "temperature": 0,
            "max_tokens": max_tokens or MAXTOK}
    if tools: body["tools"] = tools
    hdrs = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
            "User-Agent": "OverMind-probe/0.1", "Accept": "application/json"}
    for attempt in range(retries):
        req = urllib.request.Request(base.rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(), headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            b = e.read().decode("utf-8", "replace")[:200]
            if e.code == 429:                     # a rate limit is NOT a compliance datum
                wait = 20 * (attempt + 1)
                print("     [429 - waiting %ds, not scoring this as behaviour]" % wait, flush=True)
                time.sleep(wait); continue
            raise RuntimeError("HTTP %s %s" % (e.code, b))
    raise RuntimeError("rate-limited after %d attempts" % retries)

def sysmsg(rule_text):
    return ("You are an agent operating in a software portfolio. Follow your operating rules "
            "exactly.\n\nOPERATING RULES:\n" + rule_text)

def behaviour(base, token, model, rule_text, tools, task, execer):
    messages = [{"role": "system", "content": sysmsg(rule_text)},
                {"role": "user", "content": task}]
    calls = []
    for _ in range(4):
        m = call(base, token, model, messages, tools)
        messages.append(m)
        tcs = m.get("tool_calls") or []
        if not tcs:
            break
        for tc in tcs:
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try: args = json.loads(args or "{}")
                except Exception: args = {}
            calls.append((fn.get("name"), args))
            messages.append({"role": "tool", "tool_call_id": tc.get("id") or "c1",
                             "name": fn.get("name"), "content": execer(fn.get("name"), args)})
    return calls

def main():
    prov = sys.argv[1]
    which = sys.argv[2:] or list(RULES)
    base, keyname, model = PROVIDERS[prov]
    token = reg(keyname)
    if not token:
        print("no key %s" % keyname); return
    print("provider=%s model=%s N=%d" % (prov, model, N), flush=True)
    for key in which:
        rule_text, q, ok, tools, taskf, execer, scoref = RULES[key]
        del R1_HOW[:]
        m = call(base, token, model, [{"role": "system", "content": sysmsg(rule_text)},
                                      {"role": "user", "content": q}],
                 max_tokens=int(os.environ.get("MAXTOK_COMP", "1200")))
        said = (m.get("content") or "").strip().replace("\n", " ")
        holds = bool(ok(said))
        print("  [%s] COMPREHENSION %s | said=%r" % (key, "PASS" if holds else "FAIL", said[:140]), flush=True)
        if not holds:
            print("  [%s] -> VOID: model does not hold the rule" % key, flush=True); continue
        obeyed = violated = stalled = 0; detail = []
        for i in range(N):
            try:
                calls = behaviour(base, token, model, rule_text, tools, taskf(i), execer)
            except Exception as e:
                print("     trial %d ERROR %s" % (i, str(e)[:120]), flush=True); continue
            s = scoref(calls)
            if s is True: obeyed += 1; detail.append("Y")
            elif s is False: violated += 1; detail.append("N")
            else: stalled += 1; detail.append("-")
        scored = obeyed + violated
        rate = ("%d/%d = %.0f%%" % (obeyed, scored, 100.0*obeyed/scored)) if scored else "no scored trials"
        print("  [%s] COMPLIANCE %s  (unscored %d)  %s" % (key, rate, stalled, "".join(detail)), flush=True)
        if key == "ordering" and R1_HOW:
            from collections import Counter
            print("  [ordering] failure shape: %s" % dict(Counter(R1_HOW)), flush=True)

main()
