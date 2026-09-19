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
only against its own throwaway copy.

⚠️ THE CHECK ARGV IS FIXED, BUT `cargo test`/`npm test`/`pytest` STILL RUN THE
TARGET REPO'S OWN CODE - build scripts, test fixtures, package.json's own
lifecycle hooks. Raised by the PM reviewing this file, 2026-09-17: a
host-owned check table stops a caller choosing the COMMAND, not the fact
that a repo's own test suite is, definitionally, code that repo's author
wrote. **CireSnave ruled on repo scope directly, 2026-09-18 (PM session,
confirmed on OverMind#48 and matching what he told this lane directly): "Any
repo."** He accepts that dispatching to a caller-named repo means running
that repo's own code on this host - no owner allowlist. What's still
mandatory regardless: `prepare_clone` puts `--` before `repo_spec` in the
`git clone` argv, so a spec beginning with `-` is read as a (failing)
repository path, never parsed as an option - confirmed live, writing this
fix, that without `--` a crafted `--upload-pack=<cmd>` value gets git to
actually execute `<cmd>` as a real shell command.

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
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Sequence

from . import ledger
from .lanework import LaneResult, Task, build_client, gh_pr_create, run_task
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


def clone_argv(repo_spec: str, clone_dir: pathlib.Path) -> list[str]:
    """The argv `prepare_clone` runs. Split out so the `--` placement can be
    asserted directly, without depending on git's actual runtime behaviour
    for a given repo_spec - that behaviour turned out to be ENVIRONMENT-
    DEPENDENT while writing this fix (a crafted `--upload-pack=<cmd>` value
    executed `<cmd>` for real when git was invoked from an MSYS/Git-Bash
    parent, but not from a native Windows Python parent on the same box,
    same git binary). `--` is still mandatory - it is git's own documented
    argument-parsing contract, not something conditional on which shell
    happened to be running - a test that only watches for the exploit to
    fire would be exactly as unreliable as the behaviour it's protecting
    against. This test instead asserts the argv shape itself."""
    return ["git", "clone", "--quiet", "--", repo_spec, str(clone_dir)]


def prepare_clone(repo_spec: str, scratch: pathlib.Path, *, runner=_run) -> pathlib.Path:
    """A fresh, throwaway clone of `repo_spec` (a local path or a URL) under
    `scratch`. ⚠️ NEVER the caller's own path, whatever `repo_spec` names -
    see the module docstring. No repo-scope check runs here or anywhere else
    in this module: CireSnave ruled "Any repo" (2026-09-18) after being told
    directly what that means - the target repo's own code runs on this host
    via its own check command, whichever repo is named."""
    clone_dir = scratch / "clone"
    proc = runner(clone_argv(repo_spec, clone_dir))
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
    #: DESIGN-PROPOSAL.md §7.4, approved by CireSnave verbatim on board item
    #: 42 ("On board 42, yes."): an EXPLICIT, caller-chosen mode for work
    #: with no executable check at all (a README fix, a markdown file with
    #: no test suite behind it, not even indirectly) - `infer_check` still
    #: refuses outright for that class of task, correctly, since it never
    #: guesses "no check needed" on its own. When `True`: `writable` above
    #: is IGNORED and forced to `DOCS_ONLY_WRITABLE` (so this mode can never
    #: touch code, whatever the caller passed), no real check ever runs, and
    #: the result is ALWAYS a DRAFT PR - never auto-mergeable, always routed
    #: to a human/the PM gate.
    docs_only: bool = False
    #: PM point 2, 2026-09-19: `repo_probe.CHECK_TABLE`'s table-order
    #: tie-break picks the wrong marker for a repo with more than one
    #: project type at its root (OverMind's own repo: `Cargo.toml` always
    #: wins over `pyproject.toml`, even when the dispatched work is
    #: entirely Python). `check_profile` names an entry from
    #: `repo_probe.NAMED_CHECK_PROFILES` to use INSTEAD of that
    #: tie-break - still a host-owned table, still validated, still
    #: requiring that profile's own marker to be present; only the
    #: SELECTION changes, never the source of the argv. `None` (the
    #: default) keeps auto-inference exactly as it was.
    check_profile: str | None = None


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
    """Either no marker in `repo_probe.CHECK_TABLE` matched this repo, or one
    did but its own content didn't validate (`probe.refused_reason` - PM
    finding, 2026-09-19: a marker's bare presence isn't enough; see
    `repo_probe`'s own module docstring). Refuse rather than guess a check -
    an unverified task is not a task this tool runs, and this now refuses
    BEFORE any model time is spent, not after a run nothing could verify.
    NOT raised in `docs_only` mode - that mode's whole point is real work
    with no check at all, per §7.4."""


#: DESIGN-PROPOSAL.md §7.4: what `docs_only` runs against, in place of a
#: REAL check - always exits 0, and never asserts anything about the
#: change. `check_name` (shown everywhere a check is labelled: the goal
#: brief, the PR body) says so plainly, on purpose - the point isn't to
#: LOOK like a check ran, it's to keep `lanework.Task`'s own schema (which
#: requires a real, non-empty argv) satisfied while making the absence of
#: real verification impossible to miss.
DOCS_ONLY_CHECK: list[str] = [sys.executable, "-c", "pass"]
DOCS_ONLY_CHECK_NAME = "docs-only mode: no executable check ran - unverified, human review required"

#: §7.4, CireSnave's own ruling: NEVER caller-overridable, so a docs_only
#: request can't smuggle in a wider `writable` (e.g. the default `"**"`)
#: and edit code under cover of "docs-only."
DOCS_ONLY_WRITABLE: tuple[str, ...] = ("*.md", "docs/**")

#: PM finding, 2026-09-19 (defence in depth, after §7.4 shipped): some
#: `*.md` files are AGENT INSTRUCTIONS, not documentation - `CLAUDE.md`,
#: `AGENTS.md`, `GEMINI.md`, a `.claude/` config, a skill's own
#: `SKILL.md`. A free-tier model editing one of these steers a FUTURE
#: Claude/agent session reading it - prompt injection by the back door,
#: not a docs fix. Denied REGARDLESS of `DOCS_ONLY_WRITABLE` matching them
#: (`**/CLAUDE.md` matches `*.md` too) - `WorkspaceConfined.
#: extra_denied_globs` is checked BEFORE the `writable` allowlist, so
#: these refuse even though `*.md` alone would have let them through.
#: `.github/**` is also already covered by `lanework.PROTECTED` for every
#: task, not just docs_only - included here too so this list is a complete
#: statement of intent on its own, not one that depends on reading a
#: different module to understand.
AGENT_INSTRUCTION_GLOBS: tuple[str, ...] = (
    "**/CLAUDE.md", "**/AGENTS.md", "**/GEMINI.md",
    "**/.claude/**", "**/SKILL.md", "**/skills/**", "**/.github/**",
)


def build_task(task_id: str, clone_dir: pathlib.Path, probe: RepoProbe,
               request: DispatchRequest, *, fetch_requirements=fetch_requirements_text) -> Task:
    requirements_text = ""
    if request.requirements_url:
        requirements_text = fetch_requirements(request.requirements_url)
    goal = build_goal(request.prompt, probe, request, requirements_text)

    if request.docs_only:
        pr_body_lines = [
            "⚠️ **DOCS-ONLY MODE**: no executable check ran for this change - it is "
            "NOT verified by any automated test, build, or lint. This PR requires human "
            "review before merge, and is opened as a DRAFT for exactly that reason.",
        ]
        if request.capabilities:
            pr_body_lines.append(f"Requested capabilities: {', '.join(request.capabilities)}")
        return Task(
            id=task_id, repo=str(clone_dir), goal=goal, check=list(DOCS_ONLY_CHECK),
            writable=list(DOCS_ONLY_WRITABLE), base="origin/HEAD", fetch=False,
            provider=request.provider, max_steps=request.max_steps,
            check_name=DOCS_ONLY_CHECK_NAME, pr_body="\n\n".join(pr_body_lines),
            protected_globs=list(AGENT_INSTRUCTION_GLOBS),
        )

    if probe.check is None:
        reason = probe.refused_reason or (
            f"no known project marker (see repo_probe.CHECK_TABLE) found in {request.repo!r}")
        raise NoCheckInferred(f"refusing to guess a check: {reason}")
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


def _draft_pr_creator(root: pathlib.Path, branch: str, base: str, title: str, body: str) -> str:
    """§7.4: docs_only's result is ALWAYS a draft, whatever `publish` the
    caller passed - never auto-mergeable, always routed to a human/the PM
    gate. Kept as its own function (rather than a lambda) so it can be
    swapped out in a test the same way `gh_pr_create` already is."""
    return gh_pr_create(root, branch, base, title, body, draft=True)


def pr_creator_for(request: DispatchRequest):
    """Which `pr_creator` `dispatch` uses - pulled out as its own pure,
    directly-testable decision (§7.4's "never auto-publishes" guard) rather
    than an inline conditional only exercisable through a full `dispatch()`
    run (real clone, real provider client, real `gh`)."""
    return _draft_pr_creator if request.docs_only else gh_pr_create


def dispatch_record_from(task: Task, request: DispatchRequest,
                         result: LaneResult) -> ledger.DispatchRecord:
    """What `dispatch` appends to the durable ledger - pulled out as its own
    pure, directly-testable function (same reason as `pr_creator_for`): the
    CONTENT logic is testable with fake `Task`/`DispatchRequest`/
    `LaneResult` objects, without needing a full `dispatch()` run."""
    return ledger.DispatchRecord(
        task_id=task.id, repo=request.repo, task_type=task.check_name or "unknown",
        docs_only=request.docs_only, provider=result.provider, model=result.model,
        verdict=result.verdict, steps=result.steps, tokens=result.tokens,
        seconds=result.seconds, pr_url=result.pr_url, error=result.error,
        max_tokens=result.max_tokens,
    )


def dispatch(request: DispatchRequest, *, publish: bool = True,
             quota: QuotaBook | None = None) -> dict:
    """Clone, probe, build the task, run it, report the verdict - the whole
    thing a Claude/GPT/other agent triggers with one tool call."""
    with tempfile.TemporaryDirectory(prefix="overmind-dispatch-") as tmp:
        scratch = pathlib.Path(tmp)
        clone_dir = prepare_clone(request.repo, scratch)
        probe = RepoProbe.run(clone_dir, check_profile=request.check_profile)
        task = build_task(make_task_id(), clone_dir, probe, request)
        quota = quota if quota is not None else QuotaBook(path=default_path())
        client = build_client(task, quota)
        result = run_task(task, client, publish=publish, pr_creator=pr_creator_for(request))
        # ⚠️ EVERY DISPATCH IS RECORDED, whatever the verdict - a
        # CHECK_FAILED or ERROR run is exactly the data future cost-per-
        # success routing needs, not just the PASS runs. Best-effort:
        # ledger.append never raises, so a logging failure can't take down
        # the result this function is about to return.
        ledger.append(dispatch_record_from(task, request, result))
        return {
            "verdict": result.verdict,
            "provider": result.provider,
            "model": result.model,
            "check": task.check_name,
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
                           writable: list[str] | None = None,
                           docs_only: bool = False,
                           check_profile: str | None = None) -> dict:
        """Dispatch a piece of real work to a free-tier model.

        ⚠️ `repo` MUST BE A REAL GITHUB URL (e.g.
        "https://github.com/owner/name.git"), NOT A LOCAL PATH. This tool
        clones `repo` fresh into a scratch directory before running anything
        (see the module docstring), and the resulting clone's own `origin`
        remote is whatever `repo` named - a local path clones with `origin`
        pointing at that local path, so the PASS-path `gh pr create` call
        fails at the very end with "none of the git remotes configured for
        this repository point to a known GitHub host", after the model has
        already done real work and a commit has already been pushed nowhere
        useful. Always pass the repo's real GitHub URL.

        The acceptance check is never caller-supplied - it is inferred from
        the target repo's own project markers (Cargo.toml, pyproject.toml,
        ...). If no marker is recognised, this refuses rather than guessing.
        A PR is opened only if that check genuinely passes.

        Set check_profile to a name from repo_probe.NAMED_CHECK_PROFILES
        (e.g. "python-unittest", "python-pytest", "cargo", "go",
        "pnpm-workspace") to SELECT a specific profile instead of relying on
        auto-inference's table-order tie-break - useful when a repo has more
        than one project type at its root and auto-inference would pick the
        wrong one for the work being dispatched. The chosen profile's own
        marker must still be present in the repo; this only changes which
        marker is considered, never the argv it maps to.

        Set docs_only=True for work with NO executable check at all (a
        README fix, a markdown file with no test suite behind it) -
        CireSnave-approved (DESIGN-PROPOSAL.md §7.4). In this mode: writable
        is forced to markdown/docs paths only, whatever `writable` says; no
        real check ever runs; the PR is ALWAYS opened as a draft, never
        auto-mergeable - it always needs a human to review and mark it
        ready.
        """
        request = DispatchRequest(
            repo=repo, prompt=prompt, capabilities=tuple(capabilities or ()),
            extra_requirements=extra_requirements, requirements_url=requirements_url,
            writable=tuple(writable) if writable else ("**",),
            docs_only=docs_only, check_profile=check_profile,
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
