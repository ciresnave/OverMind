# SPDX-License-Identifier: MIT OR Apache-2.0
"""Services the HARNESS opened (spec section 2: "the plug-in never holds the connection").

A service is anything with `call(tool, arguments)`. The orchestrator side registers
it under a name; a plug-in declares the name in `Declaration.services` and reaches
it only through `Primitives.call_service`, which refuses outside an allowed call.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class Service(Protocol):
    def call(self, tool: str, arguments: Mapping[str, Any]) -> Any: ...


class UnknownService(KeyError):
    pass


_services: dict[str, Service] = {}


def register_service(name: str, service: Service) -> None:
    if name in _services:
        raise ValueError(f"service {name!r} is already registered")
    _services[name] = service


def unregister_service(name: str) -> None:
    _services.pop(name, None)


def get_service(name: str) -> Service:
    try:
        return _services[name]
    except KeyError:
        raise UnknownService(f"no service {name!r} is open") from None
