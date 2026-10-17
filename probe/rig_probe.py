# SPDX-License-Identifier: MIT OR Apache-2.0
"""What does a local OpenAI-compatible server actually give OverMind?

    OVERMIND_RIG_BASE_URL=http://HOST:PORT/v1 python probe/rig_probe.py [--model TAG]
    python probe/rig_probe.py --base-url http://127.0.0.1:11434/v1 --ladder 1000,4000

Meant for the multi-GPU box (LOCAL-MODEL-DISPATCH-DESIGN.md sections 3 and 5), run
once the server is up. It measures, per model, the three numbers the design says are
missing before `default_timeout_s` / `default_max_tokens` on the `rig` provider can
be set from data instead of guessed:

  1. CONTEXT: the largest prompt the server takes WITHOUT CLIPPING, on a ladder of sizes
     (the first step calibrates tokens-per-target, so it cannot itself be judged clipped).
  2. TOOL CALL: does it return a real, parseable `tool_calls` entry?
  3. DECODE: completion tokens per second.

⚠️ A TRANSPORT FAILURE IS NOT A CAPABILITY VERDICT. A server that is down, times
out, or answers HTTP 4xx/5xx is reported as such (`kind`), never as "the model
could not". The tool-call verdict is scored off the tool side: a call written into
`content` is a FAIL.

⚠️ THE DECODE RATE INCLUDES PREFILL. It is completion tokens over the whole request
wall time, on a short prompt; it is not a pure generation speed, and one sample is
an extrapolation. The report says so in the field itself.

The prompt size is counted from the server's own `usage.prompt_tokens`, not from the
filler's character count; the ladder's `target` is only what was asked for.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from http.client import HTTPException

# A step whose counted prompt is under this fraction of what the first step implies
# was clipped by the server.
CLIP_FRACTION = 0.8
DEFAULT_LADDER = (2_000, 8_000, 16_000, 32_000, 64_000)

TOOLS = [{"type": "function", "function": {
    "name": "get_build_status",
    "description": "Return the CI build status for a repository branch.",
    "parameters": {"type": "object", "properties": {
        "repo": {"type": "string", "description": "owner/name"},
        "branch": {"type": "string", "description": "branch name"}},
        "required": ["repo", "branch"]}}}]
ASK = ("What is the CI build status of branch 'main' in the repository "
       "ciresnave/fuel? Use the tool.")


class Transport(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


def filler(tokens: int) -> str:
    """About `tokens` tokens of text (4 characters per token is a rough rule; the
    real count is read back from `usage`)."""
    word = "alpha "
    return word * max(1, (tokens * 4) // len(word))


def http(url: str, payload=None, timeout: float = 600.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method="POST" if data else "GET",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": "Bearer " + os.environ.get("OVERMIND_RIG_API_KEY", "unused")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise Transport(f"http-{exc.code}", exc.read()[:200].decode("utf-8", "replace")) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise Transport("timeout", str(exc)) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise Transport("timeout", str(exc.reason)) from exc
        raise Transport("unreachable", str(exc.reason)) from exc
    except (ConnectionError, HTTPException) as exc:
        # dropped mid-request or mid-read (Ollama closing on a model load, a reset)
        raise Transport("disconnected", f"{type(exc).__name__}: {exc}") from exc
    except ValueError as exc:
        raise Transport("bad-body", str(exc)) from exc


def tool_verdict(msg: dict) -> tuple[str, str]:
    calls = msg.get("tool_calls") or []
    if not calls:
        where = "in content" if (msg.get("content") or "").lstrip().startswith("{") else "none"
        return "FAIL", f"no tool_calls entry (call text: {where})"
    fn = calls[0].get("function") or {}
    raw = fn.get("arguments")
    try:
        args = raw if isinstance(raw, dict) else json.loads(raw or "")
    except (ValueError, TypeError):
        return "FAIL", "tool_calls[0].function.arguments is not JSON"
    if fn.get("name") != "get_build_status" or not isinstance(args, dict):
        return "FAIL", f"unexpected call {fn.get('name')!r}"
    missing = {"repo", "branch"} - set(args)
    if missing:
        return "FAIL", f"missing required argument(s) {sorted(missing)}"
    return "PASS", f"called {fn['name']} with {sorted(args)}"


def chat(base: str, model: str, messages, timeout, **extra):
    t0 = time.monotonic()
    body = http(base + "/chat/completions",
                {"model": model, "messages": messages, "temperature": 0, **extra}, timeout)
    return body, time.monotonic() - t0


def run(base_url: str, model: str | None, ladder=DEFAULT_LADDER, timeout: float = 600.0) -> dict:
    base = base_url.rstrip("/")
    report: dict = {"base_url": base, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        roster = [m.get("id") for m in http(base + "/models", None, timeout).get("data", [])]
        report["roster"] = roster
        report["model"] = model or (roster[0] if roster else None)
        if not report["model"]:
            raise Transport("empty-roster", "the server lists no models; pass --model")
        model = report["model"]

        steps, largest, ratio = [], None, None
        for target in ladder:
            step = {"target": target}
            try:
                body, secs = chat(base, model, [{"role": "user", "content": filler(target) + "\nReply: ok"}],
                                  timeout, max_tokens=16)
                pt = (body.get("usage") or {}).get("prompt_tokens")
                step.update(ok=True, prompt_tokens=pt, seconds=round(secs, 2))
                if ratio is None and pt:
                    ratio = pt / target       # tokens per requested token, from the first step
                elif ratio is not None and (pt or 0) < CLIP_FRACTION * ratio * target:
                    # ⚠️ Ollama accepts a prompt over its context with HTTP 200 and
                    # reports the CLIPPED size (measured 0.40.4: a 40k request came
                    # back as 8194 tokens, fewer than a 20k one). Not a pass.
                    step.update(ok=False, kind="clipped",
                                detail=f"asked ~{round(ratio * target)} tokens, server counted {pt}")
                    steps.append(step)
                    break
                largest = pt
            except Transport as exc:
                step.update(ok=False, kind=exc.kind, detail=exc.detail)
                steps.append(step)
                break
            steps.append(step)
        last = steps[-1]
        stopped = None if last["ok"] else (
            "context-limit" if last["kind"] in ("clipped", "http-400") else "transport")
        if len(ladder) < 2:
            note = "clipping not checked: it needs at least two ladder steps"
        elif ratio is None:
            note = "clipping not checked: the server reported no prompt_tokens"
        else:
            note = (f"clip heuristic: a step counted under {CLIP_FRACTION:.0%} of what the first "
                    "step implies is called clipped; milder clips are not caught")
        report["context"] = {"steps": steps, "largest_ok_prompt_tokens": largest,
                             "stopped_by": stopped, "note": note}

        try:
            body, _ = chat(base, model, [{"role": "user", "content": ASK}], timeout,
                           tools=TOOLS, max_tokens=256)
            msg = ((body.get("choices") or [{}])[0]).get("message") or {}
            verdict, why = tool_verdict(msg)
        except Transport as exc:
            if exc.kind != "http-400":
                raise
            # a model the server says cannot take tools: a fact about the model
            verdict, why = "UNSUPPORTED", f"server refused the tools request: {exc.detail}"
        report["tool_call"] = {"verdict": verdict, "why": why}

        body, secs = chat(base, model, [{"role": "user", "content": "Count from 1 to 200, comma separated."}],
                          timeout, max_tokens=256)
        ct = (body.get("usage") or {}).get("completion_tokens") or 0
        report["decode"] = {"completion_tokens": ct, "seconds": round(secs, 2),
                            "tokens_per_s": round(ct / secs, 1) if secs > 0 else None,
                            "note": "one sample; includes prefill; an extrapolation, not a benchmark"}
    except Transport as exc:
        report["transport_error"] = {"kind": exc.kind, "detail": exc.detail}
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", default=os.environ.get("OVERMIND_RIG_BASE_URL"))
    ap.add_argument("--model")
    ap.add_argument("--ladder", help="comma-separated prompt-token targets")
    ap.add_argument("--timeout", type=float, default=600.0)
    a = ap.parse_args(argv)
    if not a.base_url:
        print("rig_probe: no address: set OVERMIND_RIG_BASE_URL or pass --base-url", file=sys.stderr)
        return 2
    ladder = tuple(int(x) for x in a.ladder.split(",")) if a.ladder else DEFAULT_LADDER
    rep = run(a.base_url, a.model, ladder, a.timeout)
    print(json.dumps(rep, indent=1))
    stopped = (rep.get("context") or {}).get("stopped_by")
    return 1 if "transport_error" in rep or stopped == "transport" else 0


if __name__ == "__main__":
    sys.exit(main())
