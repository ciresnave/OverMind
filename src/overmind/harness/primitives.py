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

import subprocess
import sys
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Sequence

from .plugin_api import HarnessCall
from .proctree import ProcessTree

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
    _state.allowed = None
    try:
        yield
    finally:
        _state.allowed = previous


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

    def write_file(self, path: str, data: str | bytes) -> None:
        call = self._require("write_file")
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        with _frame("write_file", call):
            _do_write(path, payload)

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
            proc = subprocess.Popen(list(argv), cwd=cwd, env=dict(env), stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, shell=False, **group)
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
            return subprocess.CompletedProcess(list(argv), proc.returncode, out, err)
