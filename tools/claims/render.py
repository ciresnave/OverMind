# SPDX-License-Identifier: MIT OR Apache-2.0
"""Render the Markdown body of STALE-CLAIMS.md.

This module RENDERS ONLY. Classification - deciding whether a claim is
current, stale, unverified or orphaned, and which commit invalidated it - is
`tools.claims.checker.classify_claim`, built separately; its output shape is
the `results` mapping this function consumes. Nothing here decides anything.

⚠️ A "current" claim NEVER appears in the output. The report exists to name
what needs attention; padding it with what is fine would train nobody to read
it and hide the lines that matter.
"""

from __future__ import annotations

#: Statuses that earn a line in the report. Everything else is current.
REPORTED_STATUSES: frozenset[str] = frozenset({"stale", "unverified", "orphaned"})

#: The exact body when nothing needs to be shown.
NOTHING_TO_REPORT: str = "No stale, unverified, or orphaned claims."

#: The single top-level heading every rendering starts with.
HEADING: str = "# Stale Claims"


def _status_word(status: str, invalidating_commit: str | None) -> str:
    """Return the parenthesised status phrase for one claim."""
    if status == "unverified":
        return "unverified: anchor unreachable"
    if invalidating_commit is not None:
        return f"{status} since {invalidating_commit}"
    return f"{status} (no invalidating commit found)"


def render_stale_claims(claims: list[dict], results: dict[str, dict]) -> str:
    """Return the exact Markdown text for STALE-CLAIMS.md.

    `results` maps each claim's `id` to
    `{"status": "current"|"stale"|"unverified"|"orphaned",
      "invalidating_commit": str|None}` - the output shape of
    `classify_claim`. Only claims whose status is "stale", "unverified" or
    "orphaned" are rendered; when every claim is "current" the body is the
    single sentence `NOTHING_TO_REPORT`.
    """
    lines: list[str] = [HEADING, ""]

    for claim in claims:
        claim_id = claim["id"]
        result = results.get(claim_id)
        if result is None:
            continue
        status = result["status"]
        if status not in REPORTED_STATUSES:
            continue
        status_word = _status_word(status, result.get("invalidating_commit"))
        lines.append(f"- **{claim_id}** ({claim['repo']}: {claim['file']}) - {status_word}")

    if len(lines) == 2:
        lines.append(NOTHING_TO_REPORT)

    return "\n".join(lines) + "\n"
