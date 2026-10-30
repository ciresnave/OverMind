# SPDX-License-Identifier: MIT OR Apache-2.0
"""Effect primitives and the thread-local allowance (spec §2, §3).

The harness tracks two separate states, per THREAD:

  * `allowed_call`: the tool call that this pipeline itself allowed, set only
    while that call's effect function runs, and CLEARED while any hook runs
    (`after_tool_call` included) and while a plug-in's `declares()` runs. A
    fork's child pipeline runs inside the parent's allowed `fork` call, but the
    child's hooks still run with it clear.
  * `primitive_frame`: set only while a primitive itself executes, and
    attributed to the call that was allowed (spec §2): `(primitive name, call)`.

A primitive called when `allowed_call` is clear is refused (it raises). So a
plug-in hook, or code a plug-in started on another thread, cannot reach the
outside world through a primitive; only an allowed call's own effect can.

⚠️ `threading.local`, NOT `contextvars`: on Python 3.14 a new thread does not
inherit the context by default, but with `-X thread_inherit_context=1` (the
default on free-threaded builds) it does, and it would inherit the allowance.
A new thread starts here with nothing set, whatever the interpreter's setting.

Limit (spec §3): this stops accidental bypass by a plug-in author, not a
hostile plug-in. An allowed effect may write any path or run any argv it is
given: binding a primitive's arguments to the allowed call's arguments is the
plug-in's business (and a later piece), not this layer's.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Sequence, cast

from .plugin_api import HarnessCall
from .proctree import ProcessTree
from .services import get_service

_state = threading.local()


class PrimitiveRefused(RuntimeError):
    """A primitive was called while no tool call was allowed on this thread."""


def allowed_call() -> HarnessCall | None:
    return getattr(_state, "allowed", None)


def primitive_frame() -> tuple[str, HarnessCall] | None:
    """`(primitive name, the allowed call it runs for)` while a primitive runs."""
    return getattr(_state, "frame", None)


def in_primitive_frame() -> bool:
    return primitive_frame() is not None


def in_harness_owned_frame() -> bool:
    """True while the harness's OWN I/O runs on this thread (the model call, a
    service launch, the ledger) or on a thread the harness marked for its life.
    Never true while plug-in code runs: `cleared()` clears it."""
    return bool(getattr(_state, "owned", False))


@contextmanager
def harness_owned() -> Iterator[None]:
    """Mark the harness's own I/O. ⚠️ Never wrap a call into plug-in code: spec §3,
    "harness-owned frames never call plug-in code" (the pipeline clears it for hooks)."""
    previous = in_harness_owned_frame()
    _state.owned = True
    try:
        yield
    finally:
        _state.owned = previous


def mark_thread_harness_owned() -> None:
    """For the first line of a thread the HARNESS starts (a service's loop): the
    mark lives as long as the thread. A thread a plug-in starts never has it."""
    _state.owned = True


@contextmanager
def allowing(call: HarnessCall) -> Iterator[None]:
    """Set the allowance for the duration of one allowed call's effect."""
    previous = allowed_call()
    _state.allowed = call
    try:
        yield
    finally:
        _state.allowed = previous


@contextmanager
def cleared() -> Iterator[None]:
    """Clear the allowance for the duration of a hook or a `declares()`,
    restoring it after (a fork's child pipeline nests inside the parent's
    allowed call)."""
    previous = allowed_call()
    previous_owned = in_harness_owned_frame()
    _state.allowed = None
    _state.owned = False
    try:
        yield
    finally:
        _state.allowed = previous
        _state.owned = previous_owned


@contextmanager
def _frame(name: str, call: HarnessCall) -> Iterator[None]:
    previous = primitive_frame()
    _state.frame = (name, call)
    try:
        yield
    finally:
        _state.frame = previous


def _do_write(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


class Primitives:
    """What an effect function is handed. Every method refuses unless a tool
    call is allowed on THIS thread."""

    def _require(self, what: str) -> HarnessCall:
        call = allowed_call()
        if call is None:
            raise PrimitiveRefused(
                f"{what}: no tool call is allowed on this thread; a primitive runs only "
                f"inside the effect of a call the pipeline allowed")
        return call

    def write_file(self, path: str, data: str | bytes, *, parents: bool = False) -> None:
        """Write `data` to `path`. `parents=True` also creates missing parent
        directories, inside the same frame (a mkdir outside one is a violation)."""
        call = self._require("write_file")
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        with _frame("write_file", call):
            if parents:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            _do_write(path, payload)

    def call_service(self, service: str, tool: str, arguments: Mapping[str, Any]) -> Any:
        """Call `tool` on a service the HARNESS opened (services.py). Refuses unless
        a tool call is allowed on this thread, so a hook cannot use an already-open
        connection: no audit event fires for it, this refusal is the only guard."""
        call = self._require("call_service")
        if tool != call.name:
            # the gate allowed THIS call; another tool on the same connection was never decided
            raise PrimitiveRefused(
                f"call_service: tool {tool!r} is not the allowed call {call.name!r}")
        with _frame("call_service", call):
            return get_service(service).call(tool, dict(arguments))

    def run_process(self, argv: Sequence[str], *, cwd: str, timeout: float,
                    env: Mapping[str, str]) -> "subprocess.CompletedProcess[str]":
        """Run `argv` (no shell). `env` is REQUIRED: the caller says exactly what
        the child inherits (lanework scrubs secrets from its checks' environment),
        so nothing is inherited by default. On timeout the whole process tree is
        killed and `subprocess.TimeoutExpired` is raised."""
        call = self._require("run_process")
        if not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("run_process: argv must be a non-empty list of strings")
        group: dict[str, Any] = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                                 if sys.platform == "win32" else {"start_new_session": True})
        with _frame("run_process", call):
            # binary pipes (no text=): `**group` is Any, so the checker would guess str
            proc = cast("subprocess.Popen[bytes]", subprocess.Popen(
                list(argv), cwd=cwd, env=dict(env), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, shell=False, **group))
            tree = ProcessTree(proc)
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                tree.kill()
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    pass                          # a detached grandchild still holds a pipe
                raise
            finally:
                tree.kill()                       # nothing the command started outlives the call
            # bytes decoded here, not by text mode: no newline translation, no decode error
            return subprocess.CompletedProcess(list(argv), proc.returncode,
                                               out.decode("utf-8", "replace"),
                                               err.decode("utf-8", "replace"))
