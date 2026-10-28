# SPDX-License-Identifier: MIT OR Apache-2.0
"""The child half of `spawn`: one harness process for one session (spec §3 item 2).

Started by `session.spawn` as `python -I -c <bootstrap> --path ... --registry
module:attr`. Reads ONE line of JSON (the `SessionConfig`) from stdin, builds the
pipeline, starts it, and reports on a protocol channel; then waits for a `stop`
command (or EOF, which means the parent is gone) and exits.

⚠️ The protocol channel is a DUP of the real stdout taken before anything else
runs; fd 1 and `sys.stdout` are then pointed at stderr. A plug-in that prints, or
a C library that writes to fd 1, cannot corrupt the protocol or forge an event.

The arming point for the audit hook (plan P3.3) is the top of `main`: it must run
before `load_pipeline` imports a registry or any plug-in module. Nothing here
arms it yet.

Exit codes: 0 stopped (or parent gone), 2 refused (config, plug-in set, or a
plug-in's `on_session_start`), 1 any other failure.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from typing import Any, Mapping, TextIO

from .config import ConfigError, SessionConfig
from .pipeline import PluginError, load_pipeline
from .plugin_api import Refuse


def _emit(proto: TextIO, event: dict[str, Any]) -> None:
    proto.write(json.dumps(event, default=repr) + "\n")
    proto.flush()


def _load_registry(spec: str) -> Mapping[str, Any]:
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise PluginError(f"registry {spec!r} is not 'module:attr'")
    try:
        registry = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise PluginError(f"registry {spec!r} cannot be loaded: {type(exc).__name__}: {exc}") from exc
    if not isinstance(registry, Mapping):
        raise PluginError(f"registry {spec!r} is not a mapping")
    return registry


def main(argv: list[str]) -> int:
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    registry_spec = "overmind.harness.registry:REGISTRY"
    if "--registry" in argv:
        registry_spec = argv[argv.index("--registry") + 1]

    try:
        config = SessionConfig.from_json(sys.stdin.readline())
        pipeline = load_pipeline(config, _load_registry(registry_spec))
        started = pipeline.start()
        if isinstance(started, Refuse):
            _emit(proto, {"event": "refused", "reason": started.reason})
            return 2
        manifest = pipeline.manifest()
    except (ConfigError, PluginError) as exc:
        _emit(proto, {"event": "refused", "reason": f"{type(exc).__name__}: {exc}"})
        return 2
    except Exception as exc:                          # noqa: BLE001 - reported, not swallowed
        _emit(proto, {"event": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 1

    _emit(proto, {"event": "loaded", "pid": os.getpid(), "manifest": manifest.to_dict()})

    for line in sys.stdin:
        try:
            command = json.loads(line)
        except ValueError:
            continue
        if isinstance(command, dict) and command.get("cmd") == "stop":
            _emit(proto, {"event": "stopped", "records": pipeline.stop(None)})
            return 0
    pipeline.stop(None)                               # EOF: the parent is gone
    return 0
