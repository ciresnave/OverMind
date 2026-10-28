# SPDX-License-Identifier: MIT OR Apache-2.0
"""Contain a process AND its descendants, so they can be killed together.

`Popen.kill` alone leaves grandchildren running and holding the output pipes, so
a timeout would block until they exit, and a stopped session would leave its
plug-ins' own children behind. Imports nothing from the harness.

⚠️ The tree must be contained BEFORE its leader exits. `taskkill /T` walks parent
links, and a grandchild whose parent has exited is no longer reachable (found by
test: a clean `stop()` left a plug-in's child running). So on Windows the process
is put in a Job Object with KILL_ON_JOB_CLOSE right after it starts (nested jobs
are fine on Windows 8 and later); on POSIX `start_new_session` makes it a process
group leader and `killpg` reaches the group even after the leader has gone. If the
job cannot be made, Windows falls back to `taskkill /T`, which is weaker, and says
so in `ProcessTree.contained`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any


def _windows_job(pid: int) -> Any:
    """A kill-on-close Job Object holding process `pid`, or None."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IOC(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IOC),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    proc = k32.OpenProcess(0x0100 | 0x0001, False, pid)      # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    if ok and proc and k32.AssignProcessToJobObject(job, proc):
        k32.CloseHandle(proc)
        return (k32, job)
    if proc:
        k32.CloseHandle(proc)
    k32.CloseHandle(job)
    return None


class ProcessTree:
    """Create it right after `Popen` (on POSIX the process must have been started
    with `start_new_session=True`). `kill()` ends the leader and every descendant,
    including ones whose parent already exited; safe to call more than once."""

    def __init__(self, proc: "subprocess.Popen[Any]") -> None:
        self._proc = proc
        self._job: Any = None
        if sys.platform == "win32":
            try:
                self._job = _windows_job(proc.pid)
            except (OSError, AttributeError, ValueError):
                self._job = None

    @property
    def contained(self) -> bool:
        """True when the descendants are covered even after the leader exits."""
        return sys.platform != "win32" or self._job is not None

    def kill(self) -> None:
        if self._job is not None:
            k32, job = self._job
            self._job = None
            k32.CloseHandle(job)                 # KILL_ON_JOB_CLOSE ends everything in it
        elif sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                           capture_output=True, check=False)
        else:
            import signal
            try:
                os.killpg(self._proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            self._proc.kill()
        except OSError:
            pass

