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

Every event from the child must carry the session's nonce; others are dropped and
counted (`rejected_events`). A session that never reports, or reports a
malformed event, is a `SessionError`, and the handle kills it: a session the
orchestrator cannot read back is not left running.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .config import ConfigError, SessionConfig
from .manifest import LoadedManifest
from .proctree import ProcessTree

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
    """The session process failed, died, or sent something unreadable."""


class SessionViolation(SessionError):
    """The session broke the chokepoint (I/O outside a primitive) and was ended."""


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
    def __init__(self, proc: "subprocess.Popen[str]", tree: ProcessTree, nonce: str,
                 stderr_path: str) -> None:
        self._proc = proc
        self._tree = tree
        self._nonce = nonce
        self._stderr_path = stderr_path
        self._events: list[dict[str, Any]] = []
        self.rejected_events = 0
        self._eof = False
        self._cond = threading.Condition()
        self._stop_lock = threading.Lock()
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
                ok = (isinstance(event, dict) and isinstance(event.get("event"), str)
                      and event.get("nonce") == self._nonce)
                with self._cond:
                    if ok:
                        self._events.append(event)
                    else:
                        self.rejected_events += 1
                    self._cond.notify_all()
        except (OSError, ValueError, UnicodeDecodeError):
            pass
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
        """The last 800 bytes the child wrote to stderr, read through a separate
        handle (seeking the child's own would move ITS write offset)."""
        try:
            with open(self._stderr_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 800))
                return f.read().decode("utf-8", "replace").strip()
        except OSError:
            return ""

    # -- the contract ----------------------------------------------------------

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        return self._proc.poll()

    def loaded(self, timeout: float = 30.0) -> LoadedManifest:
        """The manifest the harness reports. On a refusal, an error, a silence or
        a malformed report the session is stopped (killed if need be) and the
        cause raised: a session that cannot be read back is not left running."""
        try:
            e = self._wait(("loaded", "refused", "error", "violation"), timeout)
        except SessionError:
            self.stop(timeout=0)
            raise
        if e is None:
            try:
                code = self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code = None
            tail = self._stderr_tail()
            self.stop(timeout=0)
            raise SessionError(f"the session process exited (code {code}) before it reported; "
                               f"stderr: {tail!r}")
        if e["event"] in ("refused", "error", "violation"):
            self.stop(timeout=1)
            if e["event"] == "violation":
                raise SessionViolation(f"chokepoint violation: {e.get('violation')}")
            text = str(e.get("reason") if e["event"] == "refused" else e.get("error"))
            raise (SessionRefused if e["event"] == "refused" else SessionError)(text)
        try:
            return LoadedManifest.from_dict(e["manifest"])
        except (KeyError, ConfigError) as exc:
            self.stop(timeout=0)
            raise SessionError(f"the session sent a malformed manifest: {type(exc).__name__}: {exc}") from exc

    @property
    def violations(self) -> list[dict[str, Any]]:
        """Chokepoint violations the session reported (it ends after the first)."""
        with self._cond:
            return [e["violation"] for e in self._events if e["event"] == "violation"]

    def events(self) -> Iterator[dict[str, Any]]:
        """Every accepted event so far, then each new one, until the child has
        closed its channel. Blocks for as long as a live child stays silent."""
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
        with self._stop_lock:
            if self._stopped is not None:
                return self._stopped
            killed = False
            records: dict[str, Any] = {}
            reported = False
            try:
                try:
                    assert self._proc.stdin is not None
                    self._proc.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
                    self._proc.stdin.flush()
                    e = self._wait(("stopped",), timeout) if timeout > 0 else None
                    if e is not None:
                        reported = True
                        r = e.get("records")
                        records = dict(r) if isinstance(r, dict) else {}
                except (SessionError, OSError, ValueError):
                    pass
                try:
                    self._proc.wait(timeout=5 if reported else 0.5)
                except subprocess.TimeoutExpired:
                    killed = True
                # the child's own children die with it, clean stop or not
                self._tree.kill()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            finally:
                for stream in (self._proc.stdin, self._proc.stdout):
                    try:
                        if stream:
                            stream.close()
                    except OSError:
                        pass
                self._reader.join(timeout=2)
                try:
                    os.unlink(self._stderr_path)
                except OSError:
                    pass
                self._stopped = StopResult(records, self._proc.poll(), killed)
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
    nonce = secrets.token_hex(16)
    fd, stderr_path = tempfile.mkstemp(prefix="overmind-session-", suffix=".stderr")
    with os.fdopen(fd, "wb") as stderr:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                text=True, encoding="utf-8", shell=False,
                                env=dict(env if env is not None else default_child_env()), **group)
    tree = ProcessTree(proc)                          # contained before it can start children
    assert proc.stdin is not None
    try:
        proc.stdin.write(nonce + "\n" + config.to_json() + "\n")
        proc.stdin.flush()
    except OSError:
        pass                                          # the child is already gone; loaded() will say so
    return SessionHandle(proc, tree, nonce, stderr_path)
