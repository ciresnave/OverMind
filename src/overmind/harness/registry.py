# SPDX-License-Identifier: MIT OR Apache-2.0
"""The default plug-in registry a spawned session loads from.

Empty on purpose until plan P3.4 turns today's gate, ledger, workspace tools,
fork and MCP into plug-ins: a config that names a plug-in this registry does not
hold is refused whole (`load_pipeline`). The orchestrator names another registry
with `spawn(..., registry="module:attr")`; the config, which a model's text could
influence, never does.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .plugin_api import Plugin


def requires(*names: Any):
    """Declare the plug-ins a factory needs built before it, by registry name.

    The factory then takes `(config, ctx)` and reads them from `ctx.built`. A
    factory with no `requires` and one argument, `factory(config)`, is the
    original form and still works unchanged. `names` may instead be ONE callable
    `config -> tuple of names`, for a need that depends on the config.
    """
    def mark(factory):
        factory.requires = names[0] if len(names) == 1 and callable(names[0]) else names
        return factory
    return mark

REGISTRY: dict[str, Callable[[Mapping[str, Any]], Plugin]] = {}
