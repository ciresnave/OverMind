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
from typing import Sequence

from . import repo_probe
from .harness.plugin_api import API_VERSION, Declaration, Ok, Plugin, Refuse, SessionContext


class CheckPolicyPlugin(Plugin):
    def __init__(self, root: pathlib.Path, check: Sequence[str], docs_only_check: Sequence[str],
                 check_profile: str | None = None, trusted_author: bool = False,
                 name: str = "check-policy", version: str = "1") -> None:
        self._root = pathlib.Path(root)
        self._check = list(check)
        self._docs_only = list(docs_only_check)
        self._profile = check_profile
        self._trusted = trusted_author
        self._decl = Declaration(name=name, version=version, api_version=API_VERSION)

    def declares(self) -> Declaration:
        return self._decl

    def on_session_start(self, ctx: SessionContext) -> Ok | Refuse:
        reason = self._refusal()
        return Refuse(reason) if reason else Ok()

    def isolation(self) -> str:
        """What the run record says about where the check ran. EVERY value says
        "unsandboxed" until P3.7: a PASS must never be read as a sandboxed one."""
        if self._trusted:
            return "trusted author's check, unsandboxed"
        if self._check == self._docs_only:
            return "docs-only: no check ran"
        return f"{repo_probe.check_effect(self._root, self._profile) or 'undeclared'}, unsandboxed"

    def _refusal(self) -> str | None:
        if self._trusted:
            return None
        if self._check == self._docs_only:
            return None
        found = repo_probe.infer_check(self._root, self._profile)
        if found.check is None:
            return ("the task's check is not one the host registry derives for this repo"
                    + (f": {found.refused_reason}" if found.refused_reason else " (no registry entry applies)")
                    + "; an agent-supplied check runs only in a sandbox, which is not built yet")
        if self._check != found.check:
            return ("the task's check differs from the registry's for this repo and profile; "
                    "a caller-supplied argv runs only in a sandbox, which is not built yet")
        if repo_probe.check_effect(self._root, self._profile) is None:
            return "the registry entry for this check declares no effects (sandbox-safe | external)"
        return None
