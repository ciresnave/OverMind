# SPDX-License-Identifier: MIT OR Apache-2.0
"""SessionConfig: what the orchestrator tells a harness to be (spec §2).

⚠️ The harness "never runs a partial pipeline", so every refusal here is of the
whole config: an unknown `schema_version`, an unknown or missing field, a wrong
type. `bool` is an `int` in Python, so `true` is refused where a number is
wanted (the same hole `lanework.Task._check_types` closed in the 2026-10-18
audit). Nothing here enforces policy: "every session loads the gate" is the
orchestrator's rule, checked against the manifest (spec §2), so an empty
plug-in list is a valid config.

The model never writes one of these; only the orchestrator does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

SCHEMA_VERSION = 1


class ConfigError(ValueError):
    """The config (or manifest) is refused whole."""


def _obj(value: Any, where: str, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected an object, got {type(value).__name__}")
    unknown = sorted(set(value) - set(required) - set(optional))
    if unknown:
        raise ConfigError(f"{where}: unknown field(s) {unknown}")
    missing = [k for k in required if k not in value]
    if missing:
        raise ConfigError(f"{where}: missing field(s) {missing}")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: expected a non-empty string")
    return value


def _count(value: Any, where: str) -> int:
    # type(...) is int, not isinstance: a bool must not pass as 1
    if type(value) is not int or value < 1:
        raise ConfigError(f"{where}: expected a positive integer")
    return value


@dataclass(frozen=True)
class Handler:
    kind: str
    version: int


@dataclass(frozen=True)
class RouteEntry:
    provider: str
    model: str


@dataclass(frozen=True)
class Limits:
    max_steps: int
    max_tokens_total: int
    wall_seconds: int


@dataclass(frozen=True)
class PluginRef:
    name: str
    version: str
    config: dict[str, Any]


@dataclass(frozen=True)
class Workspace:
    repo: str
    base: str


@dataclass(frozen=True)
class SessionConfig:
    session_id: str
    handler: Handler
    route: tuple[RouteEntry, ...]
    limits: Limits
    plugins: tuple[PluginRef, ...]
    workspace: Workspace
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Any) -> "SessionConfig":
        top = _obj(data, "config",
                   ("schema_version", "session_id", "handler", "route", "limits", "plugins", "workspace"))
        if type(top["schema_version"]) is not int or top["schema_version"] != SCHEMA_VERSION:
            raise ConfigError(f"schema_version: this harness knows {SCHEMA_VERSION}, got {top['schema_version']!r}")

        h = _obj(top["handler"], "handler", ("kind", "version"))
        handler = Handler(_text(h["kind"], "handler.kind"), _count(h["version"], "handler.version"))

        if not isinstance(top["route"], list) or not top["route"]:
            raise ConfigError("route: expected a non-empty list")
        route = []
        for i, r in enumerate(top["route"]):
            r = _obj(r, f"route[{i}]", ("provider", "model"))
            route.append(RouteEntry(_text(r["provider"], f"route[{i}].provider"),
                                    _text(r["model"], f"route[{i}].model")))

        lim = _obj(top["limits"], "limits", ("max_steps", "max_tokens_total", "wall_seconds"))
        limits = Limits(*(_count(lim[k], f"limits.{k}") for k in ("max_steps", "max_tokens_total", "wall_seconds")))

        if not isinstance(top["plugins"], list):
            raise ConfigError("plugins: expected a list")
        plugins: list[PluginRef] = []
        seen: set[str] = set()
        for i, p in enumerate(top["plugins"]):
            p = _obj(p, f"plugins[{i}]", ("name", "version", "config"))
            name = _text(p["name"], f"plugins[{i}].name")
            if name in seen:
                raise ConfigError(f"plugins[{i}].name: duplicate plug-in {name!r}")
            seen.add(name)
            if not isinstance(p["config"], dict):
                raise ConfigError(f"plugins[{i}].config: expected an object")
            plugins.append(PluginRef(name, _text(p["version"], f"plugins[{i}].version"), dict(p["config"])))

        w = _obj(top["workspace"], "workspace", ("repo", "base"))
        workspace = Workspace(_text(w["repo"], "workspace.repo"), _text(w["base"], "workspace.base"))

        return cls(session_id=_text(top["session_id"], "session_id"), handler=handler,
                   route=tuple(route), limits=limits, plugins=tuple(plugins), workspace=workspace)

    @classmethod
    def from_json(cls, text: str) -> "SessionConfig":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ConfigError(f"not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "handler": {"kind": self.handler.kind, "version": self.handler.version},
            "route": [{"provider": r.provider, "model": r.model} for r in self.route],
            "limits": {"max_steps": self.limits.max_steps,
                       "max_tokens_total": self.limits.max_tokens_total,
                       "wall_seconds": self.limits.wall_seconds},
            "plugins": [{"name": p.name, "version": p.version, "config": p.config} for p in self.plugins],
            "workspace": {"repo": self.workspace.repo, "base": self.workspace.base},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=False)
