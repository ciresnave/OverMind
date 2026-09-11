# SPDX-License-Identifier: MIT OR Apache-2.0
# WHAT IS A LOCAL MODEL'S ACTUAL USABLE WINDOW?
#
# ⚠️ THE TRAP THIS EXISTS TO CATCH: Ollama's DEFAULT num_ctx is usually far below
# a model's advertised maximum, and an over-long prompt is TRUNCATED SILENTLY.
# A model that "forgot" the needle may never have been shown it.
#
# So this reports TWO numbers per trial and they answer different questions:
#   prompt_eval_count  - how many tokens the model ACTUALLY evaluated (its own
#                        tokenizer, not bytes/4). If this stops tracking the
#                        prompt we built, the prompt was truncated.
#   recall             - whether the needle came back.
# Truncation and forgetting look identical without the first number.
#
# POSITIVE CONTROL: a short prompt carrying the same needle must succeed, or the
# question is not being asked properly and no failure below means anything.
import json, sys, urllib.request

OLLAMA = "http://127.0.0.1:11434/api/chat"
NEEDLE = "SEQ-8842-MARLIN"

def chat(model, messages, num_ctx=None, num_predict=256):
    opts = {"temperature": 0, "num_predict": num_predict}
    if num_ctx:
        opts["num_ctx"] = num_ctx
    body = {"model": model, "messages": messages, "stream": False, "options": opts, "think": False}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())

def count_tokens(model, text):
    """The model's OWN tokenizer count, via prompt_eval_count. Not bytes/4."""
    out = chat(model, [{"role": "user", "content": text}], num_ctx=131072, num_predict=1)
    return out.get("prompt_eval_count")

def filler(n_lines):
    return "\n".join(
        "Line %05d: routine padding text with no information content whatsoever." % i
        for i in range(n_lines))

def trial(model, n_lines, num_ctx=None):
    prompt = ("Remember this value: NEEDLE=%s\n\n%s\n\n"
              "What was the NEEDLE value stated at the very top? "
              "Reply with only the value." % (NEEDLE, filler(n_lines)))
    out = chat(model, [{"role": "user", "content": prompt}], num_ctx=num_ctx)
    got = (out.get("message", {}) or {}).get("content", "") or ""
    return {
        "lines": n_lines,
        "prompt_eval_count": out.get("prompt_eval_count"),
        "recall": NEEDLE in got,
        "said": got.strip()[:60],
    }

def main():
    model = sys.argv[1]
    num_ctx = int(sys.argv[2]) if len(sys.argv) > 2 else None
    label = "num_ctx=%s" % (num_ctx if num_ctx else "OLLAMA DEFAULT")
    print("== %s  %s ==" % (model, label), flush=True)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ctrl = trial(model, 5, num_ctx)
    print("  CONTROL (5 lines): recall=%s eval=%s said=%r"
          % (ctrl["recall"], ctrl["prompt_eval_count"], ctrl["said"]), flush=True)
    if not ctrl["recall"]:
        print("  !! CONTROL FAILED - the question is not being asked properly; "
              "no result below is interpretable.", flush=True)
        return

    for n in (200, 500, 1000, 2000, 4000, 8000, 16000):
        try:
            r = trial(model, n, num_ctx)
        except Exception as e:
            print("  %6d lines: ERROR %s" % (n, str(e)[:80]), flush=True)
            break
        print("  %6d lines: eval=%-7s recall=%-5s said=%r"
              % (n, r["prompt_eval_count"], r["recall"], r["said"]), flush=True)

main()
