# SPDX-License-Identifier: MIT OR Apache-2.0
"""Services the HARNESS opened (spec section 2: "the plug-in never holds the connection").

A service is anything with `call(tool, arguments)`. The orchestrator side registers
it under a name; a plug-in declares the name in `Declaration.services` and reaches
it only through `Primitives.call_service`, which refuses outside an allowed call.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol, Sequence

from .config import ServiceRef


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


#: kind -> opener. The orchestrator names the table at spawn (`spawn(openers=...)`);
#: the config, which a model's text could influence, never does. Empty by default.
OPENERS: dict[str, Callable[[ServiceRef], Any]] = {}


class ServiceOpenError(RuntimeError):
    pass


def open_services(refs: Sequence[ServiceRef], openers: Mapping[str, Callable[[ServiceRef], Any]]) -> list[str]:
    """Open and register every configured service, or none: on any failure the
    ones already opened are closed and unregistered. Returns the names, for
    `close_services`. An opener returns an object with `call`, `close`."""
    opened: list[str] = []
    try:
        for ref in refs:
            opener = openers.get(ref.kind)
            if opener is None:
                raise ServiceOpenError(f"service {ref.name!r}: no opener for kind {ref.kind!r}")
            try:
                service = opener(ref)
            except Exception as exc:                  # noqa: BLE001
                raise ServiceOpenError(
                    f"service {ref.name!r}: opening it raised {type(exc).__name__}: {exc}") from exc
            try:
                register_service(ref.name, service)
            except ValueError as exc:
                _close(service)
                raise ServiceOpenError(str(exc)) from exc
            opened.append(ref.name)
    except BaseException:
        close_services(opened)
        raise
    return opened


def _close(service: Any) -> None:
    close = getattr(service, "close", None)
    if close is not None:
        try:
            close()
        except Exception:                             # noqa: BLE001 - closing must not mask the cause
            pass


def close_services(names: Sequence[str]) -> None:
    for name in reversed(list(names)):
        service = _services.get(name)
        unregister_service(name)
        if service is not None:
            _close(service)


def get_service(name: str) -> Service:
    try:
        return _services[name]
    except KeyError:
        raise UnknownService(f"no service {name!r} is open") from None
