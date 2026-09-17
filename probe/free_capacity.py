# SPDX-License-Identifier: MIT OR Apache-2.0
"""Which free-tier models can make a tool call today? One request each.

    python probe/free_capacity.py google:gemini-3.8-flash openrouter:some/model:free ...

⚠️ CAPACITY IS PER MODEL. Google's free tier gives each model its own daily
allowance (20 requests for gemini-3.6-flash, MEASUREMENTS §27), so every other
model that can make a tool call adds to what the runner can do in a day. This
spends ONE request per model, recorded in the shared quota book, to find out
which ones can - before a benchmark spends ten on each.

Verdict off the tool side, as in §12: a real `tool_calls` entry naming the
offered tool, with the path argument. Prose that looks like a call is FAIL.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.providers import ProviderClient                            # noqa: E402
from overmind.quota import QuotaBook, default_path                        # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

TOOL = [{"type": "function", "function": {
    "name": "read_file", "description": "Read a file from the repository.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                   "required": ["path"]}}}]
MSG = [{"role": "user", "content": "Read the file README.md using the tool. Do not answer from memory."}]


def probe(spec: str, book: QuotaBook) -> str:
    provider, _, model = spec.partition(":")
    client = ProviderClient(provider, timeout=120.0, max_tokens=1024, model=model, quota=book)
    started = time.time()
    try:
        result = client.chat(MSG, tools=TOOL, retries_on_429=0)
    except Exception as exc:  # noqa: BLE001 - recorded per model
        reasons = getattr(exc, "attempts", None)
        why = reasons[0][1] if reasons else str(exc)
        return f"FAIL  {spec:55} {why[:90]}"
    ok = False
    for call in result.message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        ok = ok or (fn.get("name") == "read_file"
                    and "readme" in str(args.get("path", "")).lower())
    text = (result.message.get("content") or "")[:40].replace("\n", " ")
    return (f"{'PASS' if ok else 'FAIL'}  {spec:55} {time.time() - started:5.1f}s "
            f"calls={len(result.message.get('tool_calls') or [])} content={text!r}")


def main(argv: list[str]) -> int:
    book = QuotaBook(path=default_path())
    for spec in argv:
        print(probe(spec, book), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
