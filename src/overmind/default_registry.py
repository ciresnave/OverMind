# SPDX-License-Identifier: MIT OR Apache-2.0
"""The default plug-in registry for a spawned session (plan P3.4).

`spawn(config, registry="overmind.default_registry:REGISTRY")` names it; a config
that asks for something not here is refused whole. Five entries, each built from
its JSON config alone, or from the plug-ins it DECLARES it needs (`requires`):

  ledger           {}
  gate             {"policies": [ {"type": ..., ...}, ... ]}      needs: ledger
  workspace-tools  {"root": <abs path>, "task": {<Task json>}}
  fork             {"max_steps"?, "max_tokens"?, "shared"?: [names]}   needs: gate, ledger, shared
  mcp              {"service": <name of a service the harness opened>}

⚠️ NOTHING ACCEPTED IS IGNORED. An unknown key in any config, an unknown policy
type, or a wrong type is an error at load: a misspelt `writable` or policy name
that fell back to a default is the recurring defect this registry must not have.

A flat module: it imports `lanework`, `fork_plugin` and `mcp_plugin`, which the
harness package must not.
"""

from __future__ import annotations

import pathlib
from typing import Any, Callable, Mapping

from .fork import ForkConfig
from .fork_plugin import ForkPlugin
from .gate import (DenyUnlessDeclared, ForbidTools, Gate, NoSelfMerge, RequirePrecondition)
from .harness.gate_plugin import GatePlugin
from .harness.ledger_plugin import LedgerPlugin
from .harness.plugin_api import BuildContext, Plugin
from .harness.registry import requires
from .harness.config import ServiceRef
from .harness.services import get_service
from .lanework import Task, WorkspaceConfined
from .mcp_plugin import McpPlugin, McpService
from .workspace_plugin import WorkspacePlugin

__all__ = ["REGISTRY", "POLICY_TYPES"]


class RegistryConfigError(ValueError):
    pass


def _exact(config: Mapping[str, Any], what: str, required: tuple[str, ...] = (),
           optional: tuple[str, ...] = ()) -> None:
    unknown = sorted(set(config) - set(required) - set(optional))
    if unknown:
        raise RegistryConfigError(f"{what}: unknown keys {unknown}; known: {sorted({*required, *optional})}")
    missing = [k for k in required if k not in config]
    if missing:
        raise RegistryConfigError(f"{what}: missing keys {missing}")


def _strings(value: Any, what: str) -> list[str]:
    if (not isinstance(value, list) or not all(type(v) is str for v in value)):
        raise RegistryConfigError(f"{what}: expected a list of strings")
    return list(value)


def _int(value: Any, what: str) -> int:
    if type(value) is not int or value < 1:
        raise RegistryConfigError(f"{what}: expected a positive integer")
    return value


def _abs_path(value: Any, what: str) -> pathlib.Path:
    path = pathlib.Path(_str(value, what))
    if not path.is_absolute():
        raise RegistryConfigError(f"{what}: expected an absolute path, not {value!r}")
    return path


def _str(value: Any, what: str) -> str:
    if type(value) is not str or not value:
        raise RegistryConfigError(f"{what}: expected a non-empty string")
    return value


# --- the declarative policy list ---------------------------------------------

def _p_deny_unless(c: Mapping[str, Any]) -> Any:
    _exact(c, "deny-unless-declared", ("reversible",))
    return DenyUnlessDeclared(reversible=frozenset(_strings(c["reversible"], "reversible")))


def _p_forbid(c: Mapping[str, Any]) -> Any:
    _exact(c, "forbid-tools", ("tools",), ("reason",))
    tools = frozenset(_strings(c["tools"], "tools"))
    if not tools:
        raise RegistryConfigError("forbid-tools: tools must not be empty (it would forbid nothing)")
    if "reason" in c:
        return ForbidTools(tools, reason=_str(c["reason"], "reason"))
    return ForbidTools(tools)


def _p_no_self_merge(c: Mapping[str, Any]) -> Any:
    _exact(c, "no-self-merge", (), ("merge_tools",))
    if "merge_tools" in c:
        merge_tools = frozenset(_strings(c["merge_tools"], "merge_tools"))
        if not merge_tools:
            raise RegistryConfigError("no-self-merge: merge_tools must not be empty (it would cover nothing)")
        return NoSelfMerge(merge_tools=merge_tools)
    return NoSelfMerge()


def _p_precondition(c: Mapping[str, Any]) -> Any:
    _exact(c, "require-precondition", ("tool", "requires"))
    return RequirePrecondition(tool=_str(c["tool"], "tool"), requires=_str(c["requires"], "requires"))


def _p_workspace(c: Mapping[str, Any]) -> Any:
    _exact(c, "workspace-confined", ("root", "writable"), ("tracked", "extra_denied_globs"))
    return WorkspaceConfined(
        _abs_path(c["root"], "root"), _strings(c["writable"], "writable"),
        frozenset(n.casefold() for n in _strings(c.get("tracked", []), "tracked")),
        extra_denied_globs=_strings(c.get("extra_denied_globs", []), "extra_denied_globs"))


#: The policy types a gate config may name. `AllowSenders` is absent on purpose: it
#: refuses every call until a sender is vouched, and nothing here can vouch one.
POLICY_TYPES: dict[str, Callable[[Mapping[str, Any]], Any]] = {
    "deny-unless-declared": _p_deny_unless,
    "forbid-tools": _p_forbid,
    "no-self-merge": _p_no_self_merge,
    "require-precondition": _p_precondition,
    "workspace-confined": _p_workspace,
}


# --- the five entries --------------------------------------------------------

def _ledger(config: Mapping[str, Any]) -> Plugin:
    _exact(config, "ledger")
    return LedgerPlugin()


@requires("ledger")
def _gate(config: Mapping[str, Any], ctx: BuildContext) -> Plugin:
    _exact(config, "gate", ("policies",))
    raw = config["policies"]
    if not isinstance(raw, list) or not raw:
        raise RegistryConfigError("gate: policies must be a non-empty list")
    policies = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or type(entry.get("type")) is not str:
            raise RegistryConfigError(f"gate.policies[{i}]: expected an object with a string 'type'")
        make = POLICY_TYPES.get(entry["type"])
        if make is None:
            raise RegistryConfigError(
                f"gate.policies[{i}]: unknown policy type {entry['type']!r}; known: {sorted(POLICY_TYPES)}")
        policies.append(make({k: v for k, v in entry.items() if k != "type"}))
    ledger = ctx.built["ledger"]
    assert isinstance(ledger, LedgerPlugin)
    # the gate reads executed_tools LIVE from the ledger plug-in's ledger
    return GatePlugin(Gate(policies, facts=ledger.facts(), ledger=ledger.ledger))


def _workspace_tools(config: Mapping[str, Any]) -> Plugin:
    _exact(config, "workspace-tools", ("root", "task"))
    if not isinstance(config["task"], dict):
        raise RegistryConfigError("workspace-tools: task must be an object")
    return WorkspacePlugin(_abs_path(config["root"], "root"), Task.from_json(config["task"]))


def _fork_needs(config: Mapping[str, Any]) -> tuple[str, ...]:
    shared = config.get("shared", [])
    return ("gate", "ledger", *_strings(shared, "fork.shared"))


@requires(_fork_needs)
def _fork(config: Mapping[str, Any], ctx: BuildContext) -> Plugin:
    _exact(config, "fork", (), ("max_steps", "max_tokens", "shared"))
    kw: dict[str, Any] = {}
    if "max_steps" in config:
        kw["max_steps"] = _int(config["max_steps"], "max_steps")
    if "max_tokens" in config:
        kw["max_tokens"] = _int(config["max_tokens"], "max_tokens")
    if "ledger" not in ctx.earlier:
        # the fork absorbs its child's ledger when the parent ledger has the fork call's
        # entry, which exists only if the ledger's hooks run first
        raise RegistryConfigError("fork: the ledger plug-in must come BEFORE it in the config")
    gate, ledger = ctx.built["gate"], ctx.built["ledger"]
    assert isinstance(gate, GatePlugin) and isinstance(ledger, LedgerPlugin)
    shared = [ctx.built[n] for n in _strings(config.get("shared", []), "fork.shared")]
    return ForkPlugin(gate, ledger, gate.gate, shared=shared, config=ForkConfig(**kw))


def _mcp(config: Mapping[str, Any]) -> Plugin:
    _exact(config, "mcp", ("service",))
    name = _str(config["service"], "service")
    service: Any = get_service(name)                 # raises if the harness has not opened it
    return McpPlugin(name, service)


def _open_mcp_stdio(ref: ServiceRef) -> McpService:
    """Opener for `{"kind": "mcp-stdio", "options": {"command", "args"?, "env"?, "cwd"?}}`."""
    o = ref.options
    where = f"service {ref.name!r}"
    _exact(o, where, ("command",), ("args", "env", "cwd"))
    kw: dict[str, Any] = {}
    if "env" in o:
        env = o["env"]
        if (type(env) is not dict
                or not all(type(k) is str and type(v) is str for k, v in env.items())):
            raise RegistryConfigError(f"{where}.env: expected an object of strings")
        kw["env"] = env
    if "cwd" in o:
        kw["cwd"] = str(_abs_path(o["cwd"], f"{where}.cwd"))
    return McpService.stdio(_str(o["command"], f"{where}.command"),
                            _strings(o.get("args", []), f"{where}.args"), **kw).open()


#: Name this with `spawn(openers="overmind.default_registry:OPENERS")`.
OPENERS: dict[str, Callable[[ServiceRef], Any]] = {"mcp-stdio": _open_mcp_stdio}


REGISTRY: dict[str, Callable[..., Plugin]] = {
    "ledger": _ledger,
    "gate": _gate,
    "workspace-tools": _workspace_tools,
    "fork": _fork,
    "mcp": _mcp,
}
