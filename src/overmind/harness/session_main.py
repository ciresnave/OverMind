# SPDX-License-Identifier: MIT OR Apache-2.0
"""The child half of `spawn`: one harness process for one session (spec §3 item 2).

Started by `session.spawn` as `python -I -c <bootstrap> --path ... --registry
module:attr`. Reads two lines from stdin, a per-session NONCE and then the
`SessionConfig` as JSON, builds the pipeline, starts it, and reports on a
protocol channel; then waits for a `stop` command (or EOF, which means the
parent is gone) and exits. Every event carries the nonce and the parent drops
any event that does not.

What the channel does and does not guarantee (spec §3's honest limit: accidental
bypass, not a hostile plug-in):
  * The channel is a DUP of the real stdout taken before anything else runs; fd 1
    and `sys.stdout` are then pointed at stderr. A plug-in that prints, or a C
    library that writes to fd 1, cannot corrupt the channel or forge an event.
  * Code in this process that writes to the channel's fd directly (`os.write`)
    does not know the nonce, so its event is dropped and counted. Code that reads
    the nonce out of this frame could still forge: that is deliberate action, and
    P3.3's audit hook is the layer that must catch `os.write`/`os.dup`/frame
    inspection on an inherited fd.
  * NOT closed here, and P3.3's scope: stdin is shared. A plug-in thread that
    reads fd 0 can consume the `stop` command; one that closes fd 0 ends the
    command loop (reported as an `error` event and exit 1, not a crash).

The audit hook (chokepoint.py) is armed in `main` before `load_pipeline` imports a
registry or any plug-in module. A violation reports a `violation` event and ends
the process with exit code 4.

Exit codes: 0 stopped (or parent gone), 2 refused (config, plug-in set, or a
plug-in's `on_session_start`), 4 chokepoint violation, 1 any other failure.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from typing import Any, Mapping, TextIO

from . import chokepoint
from .config import ConfigError, SessionConfig
from .pipeline import PluginError, load_pipeline
from .plugin_api import Refuse


def _emit(proto: TextIO, nonce: str, event: dict[str, Any]) -> None:
    proto.write(json.dumps({**event, "nonce": nonce}, default=repr) + "\n")
    proto.flush()


def _records_for_the_wire(records: dict[str, Any]) -> dict[str, Any]:
    """A plug-in's `on_stop` record can be anything. One that cannot be
    serialised must cost that plug-in its record, not the whole session its records."""
    try:
        json.dumps(records, default=repr)
        return records
    except (TypeError, ValueError):
        out: dict[str, Any] = {}
        for name, record in records.items():
            try:
                json.dumps(record, default=repr)
                out[name] = record
            except (TypeError, ValueError):
                out[name] = {"unserialisable": repr(record)[:500]}
        return out


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

    nonce = sys.stdin.readline().strip()
    if not nonce:
        return 1                                      # no handshake: nobody to tell

    def emit(event: dict[str, Any]) -> None:
        _emit(proto, nonce, event)

    # ARMED FIRST: before the registry or any plug-in module is imported, so code run
    # at import time is covered, and a violation anywhere ends this process (code 4).
    # Bytecode writing is off so an import is not itself a file write.
    sys.dont_write_bytecode = True
    chokepoint.arm(lambda violation: emit({"event": "violation", "violation": violation}))

    try:
        config = SessionConfig.from_json(sys.stdin.readline())
        pipeline = load_pipeline(config, _load_registry(registry_spec))
        started = pipeline.start()
        if isinstance(started, Refuse):
            emit({"event": "refused", "reason": started.reason})
            return 2
        manifest = pipeline.manifest()
    except (ConfigError, PluginError) as exc:
        emit({"event": "refused", "reason": f"{type(exc).__name__}: {exc}"})
        return 2
    except Exception as exc:                          # noqa: BLE001 - reported, not swallowed
        emit({"event": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 1

    emit({"event": "loaded", "pid": os.getpid(), "manifest": manifest.to_dict()})

    try:
        for line in sys.stdin:
            try:
                command = json.loads(line)
            except ValueError:
                continue
            if isinstance(command, dict) and command.get("cmd") == "stop":
                emit({"event": "stopped", "records": _records_for_the_wire(pipeline.stop(None))})
                return 0
    except (OSError, ValueError) as exc:              # stdin closed or broken under us
        emit({"event": "error", "error": f"command channel failed: {type(exc).__name__}: {exc}"})
        return 1
    pipeline.stop(None)                               # EOF: the parent is gone
    return 0
