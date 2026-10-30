# SPDX-License-Identifier: MIT OR Apache-2.0
"""The `check-policy` plug-in: which task check may run on this host (plan P3.5).

CireSnave 2026-10-28 (DESIGN-PROPOSAL 7.5): *"yes...but only in a sandbox. For commands
that have effects outside of a sandbox on my system, only pre-defined ones."* The
sandbox seam is P3.7, so until it exists a task's check is accepted only when it is
one the HOST chose, and refused otherwise, at `on_session_start`, before any process:

  * it EQUALS what `repo_probe.infer_check(root, check_profile)` derives from the task
    worktree, and that registry entry DECLARES its effects (`repo_probe.check_effect`:
    `sandbox-safe` or `external`; an entry with no declaration is refused); or
  * it equals `DOCS_ONLY_CHECK` (7.4, a check that does nothing); or
  * `trusted_author` is set, which only the CLI entry does for a hand-run `task.json`.

Anything else, including an argv equal to a registry argv for a DIFFERENT repo or
profile, an unknown profile, or a refused inference, is refused. The plug-in has no
tools and no opinion on tool calls: it only decides whether the session starts.

A flat module (imports `repo_probe` and the harness); `default_registry` names it.
"""

from __future__ import annotations

import pathlib
from typing import Any, Callable, Sequence

from . import repo_probe
from .harness.plugin_api import API_VERSION, Declaration, Ok, Plugin, Refuse, SessionContext
from .sandbox_check import SandboxRunner, SandboxUnavailable, refusal_for


class CheckPolicyPlugin(Plugin):
    def __init__(self, root: pathlib.Path, check: Sequence[str], docs_only_check: Sequence[str],
                 check_profile: str | None = None, trusted_author: bool = False,
                 name: str = "check-policy", version: str = "1",
                 sandbox: Callable[[], SandboxRunner] | None = None) -> None:
        self._root = pathlib.Path(root)
        self._check = list(check)
        self._docs_only = list(docs_only_check)
        self._profile = check_profile
        self._trusted = trusted_author
        #: Builds the sandbox runner (from the environment) when the orchestrator OFFERS the
        #: sandbox for a check the host did not choose. None: no sandbox is offered.
        self._offer = sandbox
        self._sandbox: SandboxRunner | None = None
        self._decl = Declaration(name=name, version=version, api_version=API_VERSION)

    def declares(self) -> Declaration:
        return self._decl

    def on_session_start(self, ctx: SessionContext) -> Ok | Refuse:
        reason = self._refusal()
        return Refuse(reason) if reason else Ok()

    @property
    def check_runner(self) -> Callable[..., tuple[int, str]] | None:
        """Set once the session started with the check sent to the sandbox; `Workspace.check`
        then calls it INSTEAD of running the argv here. None: the host runs it (registry,
        docs-only, trusted author)."""
        sandbox = self._sandbox
        if sandbox is None:
            return None

        def run(execute: Any, task: Any, root: pathlib.Path) -> tuple[int, str]:
            return sandbox.run_check(task.check, root, task.check_timeout_s, execute)
        return run

    def isolation(self) -> str:
        """What the run record says about where the check ran. EVERY value says
        "unsandboxed" or "UNMEASURED" until P3.7 is measured: a PASS must never be read as a
        proven sandboxed one."""
        if self._sandbox is not None:
            return "sandboxed by spawn (UNMEASURED: no VM was started)"
        if self._trusted:
            return "trusted author's check, unsandboxed"
        if self._check == self._docs_only:
            return "docs-only: no check ran"
        return f"{repo_probe.check_effect(self._root, self._profile) or 'undeclared'}, unsandboxed"

    def _refusal(self) -> str | None:
        if self._trusted or self._check == self._docs_only:
            return None
        found = repo_probe.infer_check(self._root, self._profile)
        if found.check is not None and self._check == found.check:
            if repo_probe.check_effect(self._root, self._profile) is None:
                return "the registry entry for this check declares no effects (sandbox-safe | external)"
            return None
        # not a check the host chose: only a sandbox may run it
        why = ("the task's check differs from the registry's for this repo and profile"
               if found.check is not None else
               "the task's check is not one the host registry derives for this repo"
               + (f": {found.refused_reason}" if found.refused_reason else " (no registry entry applies)"))
        if self._offer is None:
            return why + "; an agent-supplied check runs only in a sandbox, and none was offered"
        reason = refusal_for(self._check)
        if reason:
            return f"{why}; it cannot run in the sandbox: {reason}"
        try:
            runner = self._offer()
            runner.available(self._check, self._root)
        except SandboxUnavailable as exc:
            return f"{why}; no sandbox can run it, and it does not run on the host: {exc}"
        self._sandbox = runner
        return None
