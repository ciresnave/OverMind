# SPDX-License-Identifier: MIT OR Apache-2.0
"""The dynamic layer of the chokepoint: an audit hook, armed once, never disarmed
(spec §3, tests item 2).

`arm()` installs ONE `sys.addaudithook` hook and a process-wide armed flag. Python
has no way to remove an audit hook (`sys` offers `addaudithook` and `audit` only:
measured on 3.15.0, MEASUREMENTS.md §38), so the process ends with the session.
Arm it FIRST in a session process, before any registry or plug-in module is
imported (`session_main.main` does); code run at import time is then covered too.

DEFAULT DENY. An event of a class below that happens outside a `primitive_frame`
or a harness-owned frame is a violation: it is reported and the process exits
(`os._exit`, so no plug-in can catch it and carry on). The names were measured on
Python 3.15.0 / Windows (MEASUREMENTS.md §38) except where marked; nothing was
measured on POSIX or 3.11-3.13 (CI's test matrix is that measurement):

  process   subprocess.Popen, _winapi.CreateProcess, os.system, os.spawn,
            os.startfile, webbrowser.open, os.kill; NOT measured here: os.posix_spawn,
            os.exec, os.fork, os.forkpty, pty.spawn (names taken from the Python docs)
  network   socket.connect, socket.bind, socket.sendto, urllib.Request
  file      `open` that WRITES (by its mode OR its flags, since `os.open` raises
            the event with mode None), `sqlite3.connect` to anything but
            ":memory:", and os.remove/rename/rmdir/mkdir/symlink/link/truncate/chmod/
            utime/mkfifo/mknod; NOT measured here: os.chown
  load      ctypes.dlopen

KNOWN FALSE STOPS, accepted and not worked around (a plug-in does these through a
primitive or not at all): `import ctypes` itself raises `ctypes.dlopen('kernel32')`
on Windows; `asyncio.run()` on Windows raises `socket.bind` and `socket.connect`
(the proactor loop's self-pipe), so a plug-in must not run its own event loop (an
MCP service's loop thread is harness-owned instead); `tempfile.gettempdir()` probes
by creating a file; `socket.socketpair()` raises `socket.bind` on Windows.

KNOWN GAPS, not detected by this layer (nothing here sees them; the pipeline and
review do): `os.putenv`/`os.environ` writes, DNS (`socket.gethostbyname`),
`mmap`, the `winreg` writers, `_winapi.CreateFile` with write access (raises no
event at all), UDP via `socket.sendmsg`.

Reads are NOT violations: importing a module opens files, and the interpreter is
started with bytecode writing off so an import does not write a .pyc.

State is THREAD-LOCAL for frames (primitives.py) and PROCESS-WIDE for armed-ness.
A new thread starts with no frame, so I/O from a thread a plug-in started is
flagged. A service's loop thread that the harness itself starts is marked
harness-owned for its whole life with `mark_thread_harness_owned()`.

Limit (spec §3): this stops accidental bypass. A deliberately hostile plug-in can
get around any in-process hook (ctypes, patching harness state, a call that
raises no audit event).
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .primitives import in_harness_owned_frame, in_primitive_frame

PROCESS_EVENTS = frozenset({
    "subprocess.Popen", "_winapi.CreateProcess", "os.system", "os.spawn", "os.posix_spawn",
    "os.exec", "os.fork", "os.forkpty", "pty.spawn", "os.startfile", "webbrowser.open", "os.kill",
})
NETWORK_EVENTS = frozenset({"socket.connect", "socket.bind", "socket.sendto", "urllib.Request"})
FILE_EVENTS = frozenset({
    "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.symlink", "os.link", "os.truncate",
    "os.chmod", "os.chown", "os.utime", "os.mkfifo", "os.mknod",
})
LOAD_EVENTS = frozenset({"ctypes.dlopen"})

_WRITE_FLAGS = (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC)


@dataclass(frozen=True)
class Violation:
    event: str
    category: str
    args: str
    thread: str


def _opens_for_write(args: tuple[Any, ...]) -> bool:
    path, mode, flags = (tuple(args) + (None, None, None))[:3]
    if isinstance(mode, str) and any(c in mode for c in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def classify(event: str, args: tuple[Any, ...]) -> str | None:
    """The violation class of an audit event, or None if it is not one."""
    if event in PROCESS_EVENTS:
        return "process"
    if event in NETWORK_EVENTS:
        return "network"
    if event in FILE_EVENTS or (event == "open" and _opens_for_write(args)):
        return "file-write"
    if event == "sqlite3.connect" and args and args[0] not in (":memory:", "", None):
        return "file-write"                           # creating or opening a database file
    if event in LOAD_EVENTS:
        return "load"
    return None


# -- armed-ness: process-wide ---------------------------------------------------

_armed = False


def _is_armed() -> bool:
    return _armed


def _set_armed() -> None:
    global _armed
    _armed = True


# -- the verdict ----------------------------------------------------------------

def check(event: str, args: tuple[Any, ...]) -> Violation | None:
    """Pure decision: is this event, here and now, a violation?"""
    if not _is_armed():
        return None
    category = classify(event, args)
    if category is None:
        return None
    if in_primitive_frame() or in_harness_owned_frame():
        return None
    try:
        text = repr(args)[:200]
    except Exception:                                 # noqa: BLE001 - never let reporting break the verdict
        text = "?"
    return Violation(event, category, text, threading.current_thread().name)


class ChokepointViolation(RuntimeError):
    """Raised in place of the I/O when a violation is detected and the stop handler returned."""


_report: Callable[[dict[str, Any]], None] | None = None
_stop: Callable[[], None] | None = None
_reentry = threading.local()


def _hard_stop() -> None:
    os._exit(4)


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if getattr(_reentry, "busy", False):
        return                                        # reporting must not judge itself
    violation = check(event, args)
    if violation is None:
        return
    _reentry.busy = True
    try:
        try:
            if _report is not None:
                _report(asdict(violation))
        except BaseException:                         # noqa: BLE001 - whatever the report did, the stop happens
            pass
        (_stop or _hard_stop)()
    finally:
        _reentry.busy = False
    raise ChokepointViolation(f"{violation.category}: {violation.event}")


def arm(report: Callable[[dict[str, Any]], None], stop: Callable[[], None] | None = None) -> None:
    """Install the hook and set the armed flag. Once per process; there is no disarm.
    `report` gets the violation as a dict before the stop. `stop` defaults to a
    hard exit (code 4); a test may pass one that returns, in which case the
    offending operation raises `ChokepointViolation` instead."""
    global _report, _stop
    if _is_armed():
        raise RuntimeError("the chokepoint is already armed in this process")
    _report, _stop = report, stop
    sys.addaudithook(_audit)
    _set_armed()
