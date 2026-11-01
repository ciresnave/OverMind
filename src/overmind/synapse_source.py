# SPDX-License-Identifier: MIT OR Apache-2.0
"""A Synapse SOURCE for `DispatchService` (ROADMAP A3 R4; PM ruling 2026-10-30).

It reads Synapse's inbox through the `synapse` CLI (`synapse inbox`, `ack`, `send`) and turns each
line into the notification shape `dispatch.classify` takes. Synapse as it is today, no new contract:
the CLI already prints, per message, `from` (the sender's global id, `role@account`, as the DAEMON
verified it against the sender's certificate) and `body_b64`.

⚠️ THE ONE RULE: the notification's top-level `authenticated_sender` is set from the line's `from`
and from nothing else. Not the body, not `from_summary` / `from_cwd` (the sender's own words), not
any other field of the line, not the `meta` this adapter builds. A `from` that is not a plain
`role@account` authenticates nobody (`None`), and `DispatchService` then runs nothing.

Delivery: the message is acked BEFORE it is handed over (at-most-once for the job). A structured
request can start a repo job, so running it twice after a lost ack is the worse failure; the cost is
that a crash between the ack and the job drops the request, and the sender gets no reply. A message
Synapse re-sends after a failed ack is recognised by its id and not handed over again.

This runs in the parent process, never in the sandboxed child.
"""

from __future__ import annotations

import base64
import binascii
import collections
import json
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

from .dispatch import _IDENTITY, DispatchService

__all__ = ["SynapseSource", "service_from_config"]

Runner = Callable[..., "tuple[int, str]"]

_SEEN_LIMIT = 4096
_ID_LIMIT = 256


def _run(args: Sequence[str], stdin: str | None = None) -> tuple[int, str]:
    done = subprocess.run(list(args), input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30, check=False)
    return done.returncode, done.stdout


class SynapseSource:
    """`receive()` / `schemas()` as `DispatchService` wants them, plus `send` as its reply tool."""

    def __init__(self, role: str, *, binary: str = "synapse", run: Runner | None = None,
                 sleep: Callable[[float], None] = time.sleep, batch: int = 10) -> None:
        self.role = role
        self.binary = binary
        self._run = run or _run
        self._sleep = sleep
        self._batch = batch
        self._buffer: collections.deque[dict[str, Any]] = collections.deque()
        self._seen: collections.OrderedDict[str, None] = collections.OrderedDict()
        #: Why something was dropped or failed; nothing here raises into the serve loop.
        self.errors: list[str] = []

    def schemas(self) -> list[dict[str, Any]]:
        return []

    # -- receiving ----------------------------------------------------------- #

    def receive(self, timeout: float = 1.0) -> dict[str, Any] | None:
        while True:
            if not self._buffer:
                self._poll()
                if not self._buffer:
                    self._sleep(timeout)
                    return None
            note = self._take(self._buffer.popleft())
            if note is not None:
                return note

    def _cli(self, *args: str, stdin: str | None = None) -> tuple[int, str]:
        return self._run((self.binary, *args), stdin=stdin)

    def _poll(self) -> None:
        try:
            code, out = self._cli("inbox", "--role", self.role, "--max", str(self._batch))
        except Exception as exc:  # noqa: BLE001 - recorded; the serve loop keeps waiting
            self.errors.append(f"synapse inbox could not run: {type(exc).__name__}: {exc}"[:300])
            return
        if code != 0:
            self.errors.append(f"synapse inbox failed: exit {code}")
            return
        for raw in out.splitlines():
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw)
            except (ValueError, RecursionError):
                msg = None
            if not isinstance(msg, dict):
                self.errors.append("synapse inbox: a line that is not a JSON object was skipped")
                continue
            self._buffer.append(msg)

    def _ack(self, message_id: str) -> bool:
        try:
            code, _ = self._cli("ack", "--role", self.role, "--", message_id)
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"synapse ack could not run: {type(exc).__name__}: {exc}"[:300])
            return False
        if code != 0:
            self.errors.append(f"synapse ack failed: exit {code}")
            return False
        return True

    def _take(self, msg: Mapping[str, Any]) -> dict[str, Any] | None:
        message_id = msg.get("message_id")
        if not isinstance(message_id, str) or not message_id or len(message_id) > _ID_LIMIT:
            self.errors.append("synapse inbox: a message without a usable id was skipped (it cannot be acked)")
            return None
        if message_id in self._seen:                  # Synapse re-sent it: try the ack again, hand over nothing
            self._ack(message_id)
            return None
        self._seen[message_id] = None
        if len(self._seen) > _SEEN_LIMIT:
            self._seen.popitem(last=False)
        self._ack(message_id)                          # BEFORE the hand-over: see the module docstring
        body = self._body(msg)
        if body is None:
            self.errors.append(f"synapse inbox: {message_id[:64]!r} has no UTF-8 body; acked, not shown")
            return None
        frm = msg.get("from")
        authenticated = frm if isinstance(frm, str) and _IDENTITY.fullmatch(frm) else None
        return {"content": body,
                "meta": {"message_id": message_id, "from_entity": frm[:128] if isinstance(frm, str) else ""},
                "authenticated_sender": authenticated}

    @staticmethod
    def _body(msg: Mapping[str, Any]) -> str | None:
        b64 = msg.get("body_b64")
        if not isinstance(b64, str):
            return None
        try:
            return base64.b64decode(b64, validate=True).decode("utf-8")
        except (binascii.Error, ValueError):
            return None

    # -- replying ------------------------------------------------------------- #

    def send(self, to_entity: Any = None, text: Any = "") -> str:
        """The reply tool (`reply_tool="synapse_send"`, `to_entity` / `text`). The target must be a plain
        `role@account` (never an option), and the text goes on stdin, never into argv."""
        if not isinstance(to_entity, str) or not _IDENTITY.fullmatch(to_entity):
            return "refused: the target is not a role@account identity"
        try:
            code, out = self._cli("send", "--role", self.role, "--to", to_entity, stdin=str(text))
        except Exception as exc:  # noqa: BLE001
            return f"error: synapse send could not run: {type(exc).__name__}"
        return out.strip()[:200] if code == 0 else f"error: synapse send failed: exit {code}"


def service_from_config(config: Any, source: Any, executor: Any, client: Any, **kw: Any) -> DispatchService:
    """A `DispatchService` whose allowlist is `SessionConfig.dispatch_senders` (empty: every request refused)."""
    return DispatchService(source, executor, client, allowed_senders=config.dispatch_senders, **kw)
