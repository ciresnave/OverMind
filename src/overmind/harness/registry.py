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

REGISTRY: dict[str, Callable[[Mapping[str, Any]], Plugin]] = {}
