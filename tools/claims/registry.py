# SPDX-License-Identifier: MIT OR Apache-2.0
"""Load and validate a claims.json file.

A claim file is a JSON array of claim objects. Each claim object carries
EXACTLY these keys, no more and no fewer:

    id, file, quote, repo, anchor, depends_on

`depends_on` is a list of strings; every other key is a string.

⚠️ NOTHING IS DROPPED OR DEFAULTED. A claim missing a required key is not a
claim with an empty field - it is a malformed record, and rendering it would
put a half-true line in a report whose whole purpose is to be exact. The same
goes for an unknown extra key: a key this tool does not know about is either a
typo in a claim's own schema or a field a later phase added, and silently
ignoring it would let both pass unnoticed.
"""

from __future__ import annotations

import json

#: The exact key set every claim object must carry.
REQUIRED_KEYS: frozenset[str] = frozenset(
    {"id", "file", "quote", "repo", "anchor", "depends_on"}
)

#: Keys whose value must be a list of strings. Everything else is a string.
LIST_KEYS: frozenset[str] = frozenset({"depends_on"})


def load_claims(path: str) -> list[dict]:
    """Read the JSON file at `path` and return its claims as a list of dicts.

    Raises ValueError with a clear message when the file is not valid JSON,
    the top level is not a list, an entry is missing a required key, an entry
    carries an unknown extra key, or a value has the wrong type.
    """
    with open(path, "r", encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError(
            f"{path}: top level must be a JSON array of claim objects, "
            f"got {type(data).__name__}"
        )

    claims: list[dict] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{path}: claim {index} must be a JSON object, "
                f"got {type(entry).__name__}"
            )

        missing = sorted(REQUIRED_KEYS - set(entry))
        if missing:
            raise ValueError(
                f"{path}: claim {index} ({entry.get('id', '<no id>')}) is "
                f"missing required key(s): {', '.join(missing)}"
            )

        unknown = sorted(set(entry) - REQUIRED_KEYS)
        if unknown:
            raise ValueError(
                f"{path}: claim {index} ({entry['id']}) has unknown extra "
                f"key(s): {', '.join(unknown)}"
            )

        for key in sorted(REQUIRED_KEYS - LIST_KEYS):
            if not isinstance(entry[key], str):
                raise ValueError(
                    f"{path}: claim {index} ({entry['id']}) key {key!r} must "
                    f"be a string, got {type(entry[key]).__name__}"
                )

        depends_on = entry["depends_on"]
        if not isinstance(depends_on, list) or not all(
            isinstance(item, str) for item in depends_on
        ):
            raise ValueError(
                f"{path}: claim {index} ({entry['id']}) key 'depends_on' must "
                f"be a list of strings"
            )

        claims.append(entry)

    return claims
