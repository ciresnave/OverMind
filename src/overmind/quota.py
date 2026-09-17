# SPDX-License-Identifier: MIT OR Apache-2.0
"""Daily free-tier quotas: learned from the provider, remembered across runs.

⚠️ THE FREE TIERS ARE THE CAPACITY, AND THE CAPACITY IS SMALL. Measured
2026-09-17 (MEASUREMENTS §27): Google's free tier allows `gemini-3.6-flash` 20
requests a DAY, per model; OpenRouter allows 50 a day. Before this module the
client rediscovered an exhausted model on EVERY call - a wasted request, plus
retries - and forgot it again when the process ended.

WHAT IS RECORDED, AND FROM WHERE:

  · A model is BLOCKED when the provider says its DAILY quota is spent. The
    evidence is the 429 body: Google names a quota id containing `PerDay`;
    OpenRouter's free-model limit says "per-day". A per-MINUTE 429 is not a
    daily verdict and is never recorded as one - it clears in seconds.
  · The CAP, when the body states it (`quotaValue`). ⚠️ UNKNOWN STAYS UNKNOWN:
    a model with no recorded cap has no known budget, never an unlimited one.
  · Requests made, per model, per quota day. A count this client made; it
    cannot see requests made by anything else on the same key.

⚠️ THE RESET TIME IS THE PROVIDER'S, AND NOT UTC FOR GOOGLE. Google documents
its daily quotas as resetting at midnight Pacific; OpenRouter's measured refill
was midnight UTC (§23). Providers with no known reset use midnight UTC. A wrong
guess costs one request after the reset, which re-blocks the model.

Stdlib only, like the rest of the core: Pacific time is computed from the US
daylight-saving rule rather than from `zoneinfo`, which has no database on
Windows without an extra package.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

__all__ = ["QuotaBook", "daily_limit_in", "next_reset", "default_path"]

#: A 429 body that means the DAILY allowance is spent.
_DAILY = re.compile(r"per[-_ ]?day", re.IGNORECASE)
_QUOTA_VALUE = re.compile(r'"quotaValue"\s*:\s*"?(\d+)')

#: Providers whose quota day ends at midnight Pacific; everything else, UTC.
PACIFIC_RESET = frozenset({"google"})


def daily_limit_in(body: str) -> tuple[bool, int | None]:
    """(is this a DAILY-quota refusal?, the cap if the body states one)."""
    if not body or not _DAILY.search(body):
        return False, None
    m = _QUOTA_VALUE.search(body)
    return True, int(m.group(1)) if m else None


def _nth_sunday(year: int, month: int, n: int) -> datetime:
    first = datetime(year, month, 1)
    offset = (6 - first.weekday()) % 7          # Monday=0 ... Sunday=6
    return first + timedelta(days=offset + 7 * (n - 1))


def _pacific_offset(utc: datetime) -> timedelta:
    """UTC offset of US Pacific time at `utc` (PDT -7 / PST -8).

    US rule since 2007: DST from 02:00 local on the second Sunday of March
    (10:00 UTC) to 02:00 local on the first Sunday of November (09:00 UTC).
    """
    y = utc.year
    start = _nth_sunday(y, 3, 2).replace(hour=10, tzinfo=timezone.utc)
    end = _nth_sunday(y, 11, 1).replace(hour=9, tzinfo=timezone.utc)
    return timedelta(hours=-7) if start <= utc < end else timedelta(hours=-8)


def next_reset(provider: str, now: float) -> float:
    """Epoch seconds of the next quota-day boundary after `now`."""
    utc = datetime.fromtimestamp(now, tz=timezone.utc)
    if provider in PACIFIC_RESET:
        local = utc + _pacific_offset(utc)
        midnight_local = (local + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        # Convert back with the offset in force AT that midnight - a DST change
        # happens at 02:00, so the offset at 00:00 is the pre-change one.
        guess = midnight_local.replace(tzinfo=timezone.utc) + timedelta(hours=8)
        boundary = midnight_local.replace(tzinfo=timezone.utc) - _pacific_offset(guess)
        return boundary.timestamp()
    midnight = (utc + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


def default_path() -> pathlib.Path:
    """`OVERMIND_QUOTA_FILE`, else `~/.overmind/quota.json`.

    Per USER, not per checkout: every worktree and every run spends from the
    same keys, so they must read the same book."""
    env = os.environ.get("OVERMIND_QUOTA_FILE")
    return pathlib.Path(env) if env else pathlib.Path.home() / ".overmind" / "quota.json"


@dataclass
class QuotaBook:
    """Per provider/model: requests this quota day, the known cap, a block."""
    path: pathlib.Path | None = None
    clock: callable = time.time
    _data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path is not None and self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                # ⚠️ A corrupt book is an empty book, not a crash: the cost is
                # one wasted request per exhausted model, which re-learns it.
                self._data = {}

    # -- reading ------------------------------------------------------------ #

    def _entry(self, provider: str, model: str, *, create: bool = False) -> dict:
        """Today's entry. ⚠️ A READ NEVER STORES ONE: the client asks about every
        model on a roster - 58 on Google's - and the first live run filled the
        book with 57 entries that said nothing."""
        now = self.clock()
        key = f"{provider}/{model}"
        entry = self._data.get(key)
        if entry is None or now >= entry.get("day_ends", 0):
            # A new quota day: the count and any block start over; a learned
            # cap is kept, because it describes the model, not the day.
            entry = {"day_ends": next_reset(provider, now), "used": 0,
                     "blocked": False, "cap": (entry or {}).get("cap")}
            if create or key in self._data:
                self._data[key] = entry
        return entry

    def blocked(self, provider: str, model: str) -> bool:
        return bool(self._entry(provider, model)["blocked"])

    def used(self, provider: str, model: str) -> int:
        return int(self._entry(provider, model)["used"])

    def cap(self, provider: str, model: str) -> int | None:
        return self._entry(provider, model)["cap"]

    def remaining(self, provider: str, model: str) -> int | None:
        """Requests left today, or None when the cap is UNKNOWN."""
        entry = self._entry(provider, model)
        if entry["blocked"]:
            return 0
        if entry["cap"] is None:
            return None
        return max(0, entry["cap"] - entry["used"])

    # -- writing ------------------------------------------------------------ #

    def record_request(self, provider: str, model: str) -> None:
        self._entry(provider, model, create=True)["used"] += 1
        self._save()

    def record_refusal(self, provider: str, model: str, body: str) -> bool:
        """Record a 429. True when it was a DAILY refusal (the model is now
        blocked until the provider's reset); False for a per-minute one."""
        daily, cap = daily_limit_in(body)
        if not daily:
            return False
        entry = self._entry(provider, model, create=True)
        entry["blocked"] = True
        if cap is not None:
            entry["cap"] = cap
        self._save()
        return True

    def snapshot(self) -> dict:
        return json.loads(json.dumps(self._data))

    def report(self) -> list[str]:
        """One line per model: requests today, cap, what is left, and when
        the day ends. UNKNOWN is printed as such."""
        lines = []
        for key in sorted(self._data):
            if not self._says_something(self._data[key]):
                continue
            provider, _, model = key.partition("/")
            entry = self._entry(provider, model)
            left = self.remaining(provider, model)
            ends = datetime.fromtimestamp(entry["day_ends"], tz=timezone.utc)
            lines.append(f"{key:60} used {entry['used']:>3}  "
                         f"cap {entry['cap'] if entry['cap'] is not None else 'UNKNOWN':>7}  "
                         f"left {left if left is not None else 'UNKNOWN':>7}"
                         f"{'  BLOCKED' if entry['blocked'] else ''}  "
                         f"day ends {ends:%Y-%m-%d %H:%MZ}")
        return lines

    @staticmethod
    def _says_something(entry: dict) -> bool:
        return bool(entry.get("used") or entry.get("blocked")
                    or entry.get("cap") is not None)

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Written whole and renamed, so a reader never sees half a book.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".quota-", suffix=".json")
        # An entry with no request, no cap and no block says nothing; books
        # written before reads stopped storing entries are cleaned on save.
        keep = {k: v for k, v in self._data.items() if self._says_something(v)}
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(keep, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)


def main() -> int:
    """`python -m overmind.quota` - what is left today, per model."""
    path = default_path()
    lines = QuotaBook(path=path).report()
    print(f"quota book: {path}")
    print("\n".join(lines) if lines else "(empty - no request recorded yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
