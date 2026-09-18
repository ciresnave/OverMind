# SPDX-License-Identifier: MIT OR Apache-2.0
"""An MCP server exposing one tool: dispatch real lane work to a free-tier
model, verified by `lanework.py`, published only on PASS.

Decided with CireSnave, 2026-09-17, closing the gap DESIGN-PROPOSAL.md §7 left
open: a Claude/GPT/other agent calls this tool with a repo and a prompt,
never with a `check` argv - the check the run is verified against always
comes from `repo_probe.infer_check`'s fixed, host-owned table, keyed by
marker files present in the repo, never from the repo's own CI config or
from anything the caller supplies. See `repo_probe.py`'s own docstring for
why that split matters.

⚠️ EVERY REPO IS CLONED FRESH, NEVER OPERATED ON IN PLACE. A caller may name
a local path that happens to be a shared working checkout (this repo's own,
say) - fetching or adding a worktree directly against that path would touch
state other lanes read. Cloning into a scratch directory first means this
tool never runs `git fetch`/`git worktree` against anyone else's checkout,
only against its own throwaway copy. `git clone` accepts a local path as its
source the same way it accepts a URL, so this is one code path, not two.

⚠️ CAPABILITY HINTS ARE ACCEPTED AND RECORDED, NOT YET ROUTED ON. There is no
measured per-capability model data yet (MEASUREMENTS.md's P1 bench is all one
capability: tool-calling code edits) - `capabilities` is carried into the PR
body for transparency and is a place to hang real routing once that data
exists, but pretending it changes provider selection today would be exactly
the "narrating success" failure §2.4 of DESIGN-PROPOSAL.md exists to prevent.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Sequence

from .lanework import Task, build_client, run_task
from .quota import QuotaBook, default_path
from .repo_probe import RepoProbe

#: Tried in order, quota-aware, each falling over to the next. Order reflects
#: MEASUREMENTS.md §30-§32: nvidia and google are the only two providers with
#: a confirmed 3-or-4-of-4 fixer; mistral's only confirmed model was 0/4.
DEFAULT_PROVIDER_ROUTE = "nvidia,google,openrouter,huggingface,mistral"

#: ⚠️ A caller-supplied URL is fetched by THIS process. Capped and scheme-
#: restricted the same way a repo's own CI config is capped in repo_probe.py -
#: it becomes prompt text, never anything executed, but "informational" does
#: not mean "unbounded" or "any scheme".
MAX_REQUIREMENTS_CHARS = 6000


class UnsafeRequirementsURL(ValueError):
    pass


def fetch_requirements_text(url: str, *, opener=None,
                            max_chars: int = MAX_REQUIREMENTS_CHARS) -> str:
    """A caller-supplied requirements URL, read as text for the goal - never
    parsed, never executed. https only; a size cap applies regardless of what
    the server reports, because a Content-Length header is the server's claim,
    not a guarantee."""
    if not url.lower().startswith("https://"):
        raise UnsafeRequirementsURL(f"requirements_url must be https://, got: {url!r}")
    opener = opener or urllib.request.urlopen
    with opener(url, timeout=30) as resp:  # noqa: S310 - scheme checked above
        data = resp.read(max_chars + 1)
    text = data.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + f"\n... truncated at {max_chars} chars ..."
    return text


def _run(argv: Sequence[str], cwd: pathlib.Path | None = None,
         timeout: float = 300) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), cwd=str(cwd) if cwd else None,
                          capture_output=True, timeout=timeout, shell=False, check=False)


def prepare_clone(repo_spec: str, scratch: pathlib.Path) -> pathlib.Path:
    """A fresh, throwaway clone of `repo_spec` (a local path or a URL) under
    `scratch`. ⚠️ NEVER the caller's own path, whatever `repo_spec` names -
    see the module docstring."""
    clone_dir = scratch / "clone"
    proc = _run(["git", "clone", "--quiet", repo_spec, str(clone_dir)])
    if proc.returncode != 0:
        raise RuntimeError(f"clone of {repo_spec!r} failed: "
                           f"{proc.stderr.decode('utf-8', 'replace')[:500]}")
    return clone_dir


@dataclass
class DispatchRequest:
    repo: str
    prompt: str
    capabilities: Sequence[str] = field(default_factory=tuple)
    extra_requirements: str | None = None
    requirements_url: str | None = None
    writable: Sequence[str] = ("**",)
    provider: str = DEFAULT_PROVIDER_ROUTE
    max_steps: int = 20


def build_goal(prompt: str, probe: RepoProbe, request: DispatchRequest,
               requirements_text: str = "") -> str:
    """Everything the model sees. ⚠️ ALL OF THIS IS TEXT THE MODEL READS, NOT
    CODE THIS HOST RUNS - `probe.check` (the argv that IS executed) is kept
    completely separate and never built from any of these sections."""
    sections = [prompt]
    if request.capabilities:
        sections.append(f"Requested model capabilities (informational): "
                        f"{', '.join(request.capabilities)}")
    if probe.context:
        sections.append(probe.context)
    if request.extra_requirements:
        sections.append(f"--- Additional requirements from the caller ---\n"
                        f"{request.extra_requirements}")
    if requirements_text:
        sections.append(f"--- Additional requirements from "
                        f"{request.requirements_url} ---\n{requirements_text}")
    return "\n\n".join(sections)


class NoCheckInferred(ValueError):
    """No marker in `repo_probe.CHECK_TABLE` matched this repo. Refuse rather
    than guess a check - an unverified task is not a task this tool runs."""


def build_task(task_id: str, clone_dir: pathlib.Path, probe: RepoProbe,
               request: DispatchRequest, *, fetch_requirements=fetch_requirements_text) -> Task:
    if probe.check is None:
        raise NoCheckInferred(
            f"no known project marker (see repo_probe.CHECK_TABLE) found in "
            f"{request.repo!r}; refusing to guess a check")
    requirements_text = ""
    if request.requirements_url:
        requirements_text = fetch_requirements(request.requirements_url)
    goal = build_goal(request.prompt, probe, request, requirements_text)
    pr_body = ""
    if request.capabilities:
        pr_body = f"Requested capabilities: {', '.join(request.capabilities)}"
    return Task(
        id=task_id, repo=str(clone_dir), goal=goal, check=probe.check,
        writable=list(request.writable), base="origin/HEAD", fetch=False,
        provider=request.provider, max_steps=request.max_steps,
        check_name=probe.check_name, pr_body=pr_body,
    )


_TASK_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def make_task_id(prefix: str = "dispatch") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def dispatch(request: DispatchRequest, *, publish: bool = True,
             quota: QuotaBook | None = None) -> dict:
    """Clone, probe, build the task, run it, report the verdict - the whole
    thing a Claude/GPT/other agent triggers with one tool call."""
    with tempfile.TemporaryDirectory(prefix="overmind-dispatch-") as tmp:
        scratch = pathlib.Path(tmp)
        clone_dir = prepare_clone(request.repo, scratch)
        probe = RepoProbe.run(clone_dir)
        task = build_task(make_task_id(), clone_dir, probe, request)
        quota = quota if quota is not None else QuotaBook(path=default_path())
        client = build_client(task, quota)
        result = run_task(task, client, publish=publish)
        return {
            "verdict": result.verdict,
            "provider": result.provider,
            "model": result.model,
            "check": probe.check_name,
            "pr_url": result.pr_url,
            "steps": result.steps,
            "error": result.error,
        }


def register(server) -> None:
    """Attach the dispatch tool to an MCP server instance. Kept separate from
    server construction so tests can register onto a fake server."""

    @server.tool()
    def dispatch_lane_task(repo: str, prompt: str,
                           capabilities: list[str] | None = None,
                           extra_requirements: str | None = None,
                           requirements_url: str | None = None,
                           writable: list[str] | None = None) -> dict:
        """Dispatch a piece of real work to a free-tier model.

        The acceptance check is never caller-supplied - it is inferred from
        the target repo's own project markers (Cargo.toml, pyproject.toml,
        ...). If no marker is recognised, this refuses rather than guessing.
        A PR is opened only if that check genuinely passes.
        """
        request = DispatchRequest(
            repo=repo, prompt=prompt, capabilities=tuple(capabilities or ()),
            extra_requirements=extra_requirements, requirements_url=requirements_url,
            writable=tuple(writable) if writable else ("**",),
        )
        return dispatch(request)


def main() -> None:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="overmind-dispatch",
                       instructions="Dispatch real lane work to a free-tier model, "
                                   "verified before anything is published.")
    register(server)
    server.run()


if __name__ == "__main__":
    main()
