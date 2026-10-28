# SPDX-License-Identifier: MIT OR Apache-2.0
"""`spawn(config) -> SessionHandle`: the orchestrator's side (spec §1, §3 item 2).

One harness process per session. The handle offers `loaded()`, `events()` and
`stop()`; it does NOT compare the manifest with the config: the orchestrator
does that (`manifest.compare`) and stops the session before the first turn if
anything differs (plan P3.6).

⚠️ What the child inherits is an ALLOWLIST (`default_child_env`), not the
parent's environment: the same stance as lanework's scrubbed check environment.
The registry and extra import paths are orchestrator arguments. They are never
read from the config.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Iterator, Mapping, Sequence

from .config import SessionConfig
from .manifest import LoadedManifest
from .primitives import kill_process_tree

_BOOT = (
    "import sys\n"
    "paths, rest, a, i = [], [], sys.argv[1:], 0\n"
    "while i < len(a):\n"
    "    if a[i] == '--path':\n"
    "        paths.append(a[i + 1]); i += 2\n"
    "    else:\n"
    "        rest.append(a[i]); i += 1\n"
    "sys.path[:0] = paths\n"
    "from overmind.harness.session_main import main\n"
    "raise SystemExit(main(rest))\n"
)

_ENV_ALLOWLIST = ("PATH", "SystemRoot", "SYSTEMROOT", "SystemDrive", "TEMP", "TMP", "TMPDIR",
                  "LANG", "LC_ALL")


class SessionError(RuntimeError):
    """The session process failed or died without reporting."""


class SessionRefused(SessionError):
    """The harness refused to start the session (config, plug-in set, a plug-in's own refusal)."""


@dataclass(frozen=True)
class StopResult:
    records: dict[str, Any]
    returncode: int | None
    killed: bool


def default_child_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env["PYTHONUTF8"] = "1"
    return env


def _overmind_src_root() -> str:
    # `overmind` is a namespace package (no __init__.py), so it has no __file__:
    # this file is <src>/overmind/harness/session.py
    return str(Path(__file__).resolve().parents[2])


class SessionHandle:
    def __init__(self, proc: "subprocess.Popen[str]", stderr: IO[bytes]) -> None:
        self._proc = proc
        self._stderr = stderr
        self._events: list[dict[str, Any]] = []
        self._eof = False
        self._cond = threading.Condition()
        self._stopped: StopResult | None = None
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    # -- the reader ----------------------------------------------------------

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and isinstance(event.get("event"), str):
                    with self._cond:
                        self._events.append(event)
                        self._cond.notify_all()
        finally:
            with self._cond:
                self._eof = True
                self._cond.notify_all()

    def _wait(self, want: tuple[str, ...], timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for e in self._events:
                    if e["event"] in want:
                        return e
                if self._eof:
                    return None
                left = deadline - time.monotonic()
                if left <= 0:
                    raise SessionError(f"no report from the session within {timeout}s")
                self._cond.wait(left)

    def _stderr_tail(self) -> str:
        try:
            self._stderr.flush()
            self._stderr.seek(0)
            return self._stderr.read()[-800:].decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            return ""

    # -- the contract ----------------------------------------------------------

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        return self._proc.poll()

    def loaded(self, timeout: float = 30.0) -> LoadedManifest:
        e = self._wait(("loaded", "refused", "error"), timeout)
        if e is None:
            try:
                code = self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code = None
            raise SessionError(f"the session process exited (code {code}) before it reported; "
                               f"stderr: {self._stderr_tail()!r}")
        if e["event"] == "refused":
            raise SessionRefused(str(e.get("reason")))
        if e["event"] == "error":
            raise SessionError(str(e.get("error")))
        return LoadedManifest.from_dict(e["manifest"])

    def events(self) -> Iterator[dict[str, Any]]:
        """Every event so far, then each new one, until the process has closed its channel."""
        i = 0
        while True:
            with self._cond:
                while i >= len(self._events) and not self._eof:
                    self._cond.wait(0.2)
                if i < len(self._events):
                    event = self._events[i]
                else:
                    return
            i += 1
            yield event

    def stop(self, timeout: float = 10.0) -> StopResult:
        if self._stopped is not None:
            return self._stopped
        killed = False
        records: dict[str, Any] = {}
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
            self._proc.stdin.flush()
            e = self._wait(("stopped",), timeout)
            if e is not None:
                records = dict(e.get("records") or {})
        except (SessionError, OSError, ValueError):
            pass
        try:
            self._proc.wait(timeout=2 if records else 0.5)
        except subprocess.TimeoutExpired:
            killed = True
            kill_process_tree(self._proc)
            self._proc.wait(timeout=10)
        for stream in (self._proc.stdin, self._proc.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        self._stopped = StopResult(records, self._proc.returncode, killed)
        self._reader.join(timeout=2)
        self._stderr.close()
        return self._stopped

    def __enter__(self) -> "SessionHandle":
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def spawn(config: SessionConfig, *, registry: str = "overmind.harness.registry:REGISTRY",
          extra_paths: Sequence[str] = (), env: Mapping[str, str] | None = None,
          python: str | None = None) -> SessionHandle:
    """Start one harness process for `config`. The child's code is this
    process's own `overmind` (its source root goes first on the child's path)."""
    argv = [python or sys.executable, "-I", "-c", _BOOT, "--path", _overmind_src_root()]
    for p in extra_paths:
        argv += ["--path", str(p)]
    argv += ["--registry", registry]
    group: dict[str, Any] = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                             if sys.platform == "win32" else {"start_new_session": True})
    stderr = tempfile.TemporaryFile()
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                            text=True, encoding="utf-8", shell=False,
                            env=dict(env if env is not None else default_child_env()), **group)
    assert proc.stdin is not None
    try:
        proc.stdin.write(config.to_json() + "\n")
        proc.stdin.flush()
    except OSError:
        pass                                          # the child is already gone; loaded() will say so
    return SessionHandle(proc, stderr)
