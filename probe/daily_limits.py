# SPDX-License-Identifier: MIT OR Apache-2.0
"""What do the free tiers actually allow per day?

⚠️ THE DISCIPLINE, which is the point of the file: a provider that does not
publish a daily cap is UNKNOWN, NOT UNLIMITED. That is the same defect just
fixed in the token accounting (a provider reporting no usage recorded as zero
looks free), one level up — and here the comfortable direction is "more
capacity than we have".

⚠️ AND A PER-MINUTE LIMIT IS NOT A DAILY LIMIT DIVIDED BY 1440. Providers
commonly enforce BOTH, and the binding one is whichever runs out first. This
reads the headers verbatim and refuses to multiply.

One minimal call per provider. The point is the HEADERS, not the answer.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.providers import PROVIDERS, ProviderClient, read_secret  # noqa: E402

# A Windows console defaults to cp1252 and this file prints warning glyphs, so
# printing the RESULT crashes after the measurement was taken. Fourth occurrence
# this session, each time in a different script, each time fixed by remembering.
# Doing it at import means the next script that copies this header inherits the
# fix rather than the habit.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

#: Header prefixes worth capturing. Deliberately wide - a header not captured is
#: a limit not seen, and the cost of a wide net here is a few printed lines.
INTERESTING = ("ratelimit", "x-ratelimit", "retry-after", "x-request-id",
               "anthropic-ratelimit", "x-quota", "quota", "x-daily")

# ⚠️ CLASSIFY BY THE WINDOW, NOT BY THE HEADER NAME.
# The first version matched "day"/"daily"/"rpd" in header NAMES and reported
# UNKNOWN for OpenRouter - which publishes a plain `X-RateLimit-Limit: 50` with
# a millisecond reset timestamp landing at midnight UTC. That IS a daily cap and
# it was sitting in the response. A KEYWORD LIST IS NOT A MEASUREMENT: it finds
# providers that use the vocabulary you guessed, and calls the rest unknown.
DAY_WORDS = ("day", "daily", "rpd", "tpd")
LONG_WINDOW_SECONDS = 3600


def _reset_window(headers: dict) -> tuple[float | None, str]:
    """Seconds until the limit refills, and how that was read.

    Providers express this three ways and none of them announces which.
    """
    import datetime as _dt
    for key, value in headers.items():
        if "reset" not in key.lower():
            continue
        raw = str(value).strip()
        # absolute epoch, seconds or milliseconds
        if raw.isdigit():
            number = int(raw)
            when = _dt.datetime.fromtimestamp(number / (1000 if number > 10**11 else 1),
                                              _dt.timezone.utc)
            return (when - _dt.datetime.now(_dt.timezone.utc)).total_seconds(), f"{key} (epoch)"
        # duration like "1m26.4s" or "105ms"
        import re as _re
        match = _re.findall(r"([\d.]+)\s*(ms|s|m|h|d)", raw)
        if match:
            scale = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}
            return sum(float(n) * scale[u] for n, u in match), f"{key} (duration)"
    return None, "no reset header"


def probe(key: str) -> dict:
    provider = PROVIDERS[key]
    token = read_secret(provider.secret_name)
    if not token:
        return {"provider": key, "status": "no-credential"}
    try:
        base = provider.resolved_base()
    except Exception as exc:                                # noqa: BLE001
        return {"provider": key, "status": f"unconfigured: {str(exc)[:80]}"}

    # Most providers publish no fallback list - the model comes from the roster.
    # An earlier version read only `fallback_models` and reported "no known
    # model" for three providers that were perfectly reachable: a probe that
    # cannot pick a model reads exactly like a provider that cannot be called.
    try:
        candidates = ProviderClient(key).candidates(limit=1)
    except Exception:                                       # noqa: BLE001
        candidates = list(provider.fallback_models)
    model = candidates[0] if candidates else None
    if model is None:
        return {"provider": key, "status": "no model selectable from roster or fallbacks"}

    payload = {"model": model, "messages": [{"role": "user", "content": "hi"}],
               "max_tokens": 1, "temperature": 0}
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                 "User-Agent": "OverMind-probe/0.1", "Accept": "application/json",
                 **dict(provider.extra_headers)})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            headers, status = dict(resp.headers), resp.status
    except urllib.error.HTTPError as exc:
        headers, status = dict(exc.headers or {}), exc.code
    except Exception as exc:                                # noqa: BLE001
        return {"provider": key, "status": f"unreachable: {type(exc).__name__}"}

    limits = {k: v for k, v in headers.items()
              if any(k.lower().startswith(p) for p in INTERESTING)}
    by_name = {k: v for k, v in limits.items() if any(w in k.lower() for w in DAY_WORDS)}
    seconds, how = _reset_window(limits)
    long_window = seconds is not None and seconds > LONG_WINDOW_SECONDS
    return {"provider": key, "status": status, "model": model,
            "limit_headers": limits, "daily_headers": by_name,
            "reset_seconds": seconds, "reset_read_from": how,
            "long_window": long_window}


def main() -> int:
    wanted = sys.argv[1:] or [k for k in PROVIDERS if k != "ollama"]
    results = [probe(k) for k in wanted]

    for r in results:
        print(f"\n===== {r['provider']} =====")
        if "limit_headers" not in r:
            print(f"  {r['status']}")
            continue
        print(f"  HTTP {r['status']}  model={r.get('model')}")
        if not r["limit_headers"]:
            print("  rate-limit headers: NONE RETURNED")
        for k in sorted(r["limit_headers"]):
            print(f"    {k}: {r['limit_headers'][k]}")
        if r.get("reset_seconds") is not None:
            print(f"  refill window: {r['reset_seconds']:.0f}s  "
                  f"(read from {r['reset_read_from']})")
        if r["daily_headers"] or r.get("long_window"):
            print(f"  LONG-WINDOW CAP OBSERVED: {r['daily_headers'] or r['limit_headers']}")
        else:
            print("  LONG-WINDOW CAP: UNKNOWN - none observed. "
                  "NOT 'unlimited', and NOT derivable from a per-minute figure.")

    print("\n" + "=" * 66)
    known = [r for r in results if r.get("daily_headers") or r.get("long_window")]
    reachable = [r for r in results if "limit_headers" in r]
    print(f"providers reached: {len(reachable)}/{len(results)}")
    print(f"long-window caps OBSERVED: {len(known)}/{len(reachable)}  "
          f"-> {[r['provider'] for r in known]}")
    print(f"UNKNOWN (no long-window cap observed): "
          f"{[r['provider'] for r in reachable if r not in known]}")
    print()
    print("UNKNOWN means NOT MEASURED, never 'unlimited'. A per-minute limit "
          "multiplied by 1440 would be an invention, not a measurement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
