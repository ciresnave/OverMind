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
import re
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


def expect_object(value: Any, where: str, required: tuple[str, ...],
                  optional: tuple[str, ...] = ()) -> Mapping[str, Any]:
    return _obj(value, where, required, optional)


def _text(value: Any, where: str) -> str:
    """A name, id or path-like string. `type(...) is str` (a subclass could
    override anything), not blank, no control characters, and encodable: a lone
    surrogate would break the first write to disk, and session ids and repos
    end up in paths and argv."""
    if type(value) is not str or not value.strip():
        raise ConfigError(f"{where}: expected a non-blank string")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ConfigError(f"{where}: control characters are not allowed")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ConfigError(f"{where}: not valid text (lone surrogate)") from None
    return value


def check_text(value: Any, where: str) -> str:
    return _text(value, where)


def _json_value(value: Any, where: str) -> Any:
    """Deep-validate AND deep-copy a plug-in's config value: only what JSON can
    carry (objects with string keys, lists, strings, finite numbers, bools,
    null). The copy detaches the config from the caller's data."""
    if value is None or type(value) is bool or type(value) is int or type(value) is str:
        return value
    if type(value) is float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ConfigError(f"{where}: NaN and Infinity are not JSON")
        return value
    if type(value) is list:
        return [_json_value(v, f"{where}[{i}]") for i, v in enumerate(value)]
    if type(value) is dict:
        out = {}
        for k, v in value.items():
            if type(k) is not str:
                raise ConfigError(f"{where}: object keys must be strings, got {type(k).__name__}")
            out[k] = _json_value(v, f"{where}.{k}")
        return out
    raise ConfigError(f"{where}: {type(value).__name__} is not JSON data")


def _count(value: Any, where: str) -> int:
    # type(...) is int, not isinstance: a bool must not pass as 1
    if type(value) is not int or value < 1:
        raise ConfigError(f"{where}: expected a positive integer")
    return value


@dataclass(frozen=True)
class Handler:
    kind: str
    version: int


#: An authenticated transport identity: `role@account`, no wildcards, no whitespace.
SENDER_RE = re.compile(r"[A-Za-z0-9._-]{1,64}@[A-Za-z0-9._-]{1,64}")


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
class ServiceRef:
    """A service the HARNESS opens for the session (spec section 2): `kind` picks
    the opener the orchestrator named at spawn, `options` is its JSON config."""
    name: str
    kind: str
    options: dict[str, Any]


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
    #: Optional on the wire: a config without `services` is valid and means none.
    services: tuple[ServiceRef, ...] = ()
    #: Optional on the wire: absent means NONE. The authenticated transport identities
    #: (`role@account`, e.g. a Synapse sender) whose structured task requests a dispatch
    #: service may act on. Never a name from a message body.
    dispatch_senders: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Any) -> "SessionConfig":
        top = _obj(data, "config",
                   ("schema_version", "session_id", "handler", "route", "limits", "plugins", "workspace"),
                   ("services", "dispatch_senders"))
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
            plugins.append(PluginRef(name, _text(p["version"], f"plugins[{i}].version"),
                                     _json_value(p["config"], f"plugins[{i}].config")))

        services: list[ServiceRef] = []
        if "services" in top:
            if not isinstance(top["services"], list):
                raise ConfigError("services: expected a list")
            seen_services: set[str] = set()
            for i, s in enumerate(top["services"]):
                s = _obj(s, f"services[{i}]", ("name", "kind", "options"))
                sname = _text(s["name"], f"services[{i}].name")
                if sname in seen_services:
                    raise ConfigError(f"services[{i}].name: duplicate service {sname!r}")
                seen_services.add(sname)
                if not isinstance(s["options"], dict):
                    raise ConfigError(f"services[{i}].options: expected an object")
                services.append(ServiceRef(sname, _text(s["kind"], f"services[{i}].kind"),
                                           _json_value(s["options"], f"services[{i}].options")))

        senders: list[str] = []
        if "dispatch_senders" in top:
            if not isinstance(top["dispatch_senders"], list):
                raise ConfigError("dispatch_senders: expected a list")
            for i, who in enumerate(top["dispatch_senders"]):
                if type(who) is not str or not SENDER_RE.fullmatch(who):
                    raise ConfigError(f"dispatch_senders[{i}]: expected an identity like role@account, "
                                      f"got {who!r}")
                if who in senders:
                    raise ConfigError(f"dispatch_senders[{i}]: duplicate sender {who!r}")
                senders.append(who)

        w = _obj(top["workspace"], "workspace", ("repo", "base"))
        workspace = Workspace(_text(w["repo"], "workspace.repo"), _text(w["base"], "workspace.base"))

        return cls(session_id=_text(top["session_id"], "session_id"), handler=handler,
                   route=tuple(route), limits=limits, plugins=tuple(plugins), workspace=workspace,
                   services=tuple(services), dispatch_senders=tuple(senders))

    @classmethod
    def from_json(cls, text: str) -> "SessionConfig":
        try:
            data = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        out = {
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
        if self.services:                             # absent when empty: an old config round-trips as itself
            out["services"] = [{"name": s.name, "kind": s.kind, "options": s.options} for s in self.services]
        if self.dispatch_senders:
            out["dispatch_senders"] = list(self.dispatch_senders)
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=False)
