# SPDX-License-Identifier: MIT OR Apache-2.0
"""LoadedManifest: what a harness reports it actually loaded (spec §2).

⚠️ "A state read, not an assumption." The orchestrator wrote a config; the
harness says what it loaded; `compare` lists EVERY difference between the two
and the orchestrator stops the session before turn 1 if there is any. It
returns all of them rather than raising on the first so one mismatch cannot
hide another.

The SHA-256 is of the plug-in's source file as it is on disk, byte for byte, so
a CRLF checkout of the same source hashes differently: pin per checkout.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .config import ConfigError, SessionConfig, check_text, expect_object

_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class PluginRecord:
    name: str
    version: str
    api_version: int
    sha256: str

    def __post_init__(self) -> None:
        check_text(self.name, "plug-in record name")
        check_text(self.version, f"plug-in {self.name!r} version")
        if type(self.api_version) is not int or self.api_version < 1:
            raise ConfigError(f"plug-in {self.name!r}: api_version must be a positive integer")
        if type(self.sha256) is not str or not _SHA256.fullmatch(self.sha256):
            raise ConfigError(f"plug-in {self.name!r}: sha256 must be 64 lowercase hex digits")


@dataclass(frozen=True)
class LoadedManifest:
    plugins: tuple[PluginRecord, ...]
    tools: tuple[str, ...]
    services: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugins": [{"name": r.name, "version": r.version, "api_version": r.api_version,
                         "sha256": r.sha256} for r in self.plugins],
            "tools": list(self.tools),
            "services": list(self.services),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "LoadedManifest":
        top = expect_object(data, "manifest", ("plugins", "tools", "services"))
        if not isinstance(top["plugins"], list):
            raise ConfigError("manifest.plugins: expected a list")
        records = []
        for i, p in enumerate(top["plugins"]):
            p = expect_object(p, f"manifest.plugins[{i}]", ("name", "version", "api_version", "sha256"))
            records.append(PluginRecord(p["name"], p["version"], p["api_version"], p["sha256"]))
        for field in ("tools", "services"):
            if not isinstance(top[field], list):
                raise ConfigError(f"manifest.{field}: expected a list")
        return cls(plugins=tuple(records), tools=tuple(top["tools"]), services=tuple(top["services"]))

    def __post_init__(self) -> None:
        if type(self.plugins) is not tuple or not all(isinstance(r, PluginRecord) for r in self.plugins):
            raise ConfigError("manifest.plugins: expected a tuple of PluginRecord")
        for field in ("tools", "services"):
            v = getattr(self, field)
            if type(v) is not tuple or not all(type(x) is str and x for x in v):
                raise ConfigError(f"manifest.{field}: expected a tuple of non-empty strings")


def source_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def compare(config: SessionConfig, manifest: LoadedManifest,
            expected_sha256: Mapping[str, str] | None = None) -> list[str]:
    """Every difference between what the orchestrator wrote and what loaded.
    An empty list means they agree. `expected_sha256` pins source hashes by
    plug-in name; a pin for a plug-in that did not load is itself a difference.

    ⚠️ Without pins this checks NO integrity: any hash passes. The orchestrator
    must pass a pin for every plug-in it relies on. Also not compared, because
    the config names nothing to compare them with: `api_version` (the major
    check is the pipeline's, plan P3.2), the tool names, the services, and the
    config a plug-in actually received. Whether a tool or service the config
    never named should stop the session is open (plan P3.2)."""
    diffs: list[str] = []
    wanted = {p.name: p for p in config.plugins}
    loaded = {r.name: r for r in manifest.plugins}
    counts: dict[str, int] = {}
    for r in manifest.plugins:
        counts[r.name] = counts.get(r.name, 0) + 1
    for name, n in counts.items():
        if n > 1:
            diffs.append(f"plug-in {name!r} was loaded twice ({n} times)")

    for name, p in wanted.items():
        r = loaded.get(name)
        if r is None:
            diffs.append(f"plug-in {name!r} is in the config but not loaded")
        elif r.version != p.version:
            diffs.append(f"plug-in {name!r}: config version {p.version!r}, loaded version {r.version!r}")
    for name in loaded:
        if name not in wanted:
            diffs.append(f"plug-in {name!r} is loaded but not in the config")

    common_wanted = [n for n in wanted if n in loaded]
    common_loaded = [n for n in loaded if n in wanted]
    if common_wanted != common_loaded:
        diffs.append(f"plug-in order differs: config {common_wanted}, loaded {common_loaded}")

    for name, sha in (expected_sha256 or {}).items():
        r = loaded.get(name)
        if r is None:
            diffs.append(f"sha256 pinned for {name!r}, which did not load")
        elif r.sha256 != sha:
            diffs.append(f"plug-in {name!r}: sha256 {r.sha256}, expected {sha}")
    return diffs
