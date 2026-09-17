# SPDX-License-Identifier: MIT OR Apache-2.0
"""A non-Claude model doing one piece of a lane's work, end to end.

    python -m overmind.lanework task.json            # dry run: no commit, no push
    python -m overmind.lanework task.json --publish  # PR only if the check passes

Written 2026-09-17 against CireSnave's ruling that the portfolio throttles until
non-Anthropic agents carry as much of the work as possible.

THE SHAPE, AND WHY EACH PART IS WHERE IT IS:

  1. A FRESH WORKTREE per task, detached at the task's base. The model's
     writes land in a throwaway directory; nothing it does touches a lane's
     checkout, and discarding the worktree undoes all of it.

  2. THE MODEL GETS SIX TOOLS AND NO MORE: list, read, search, write, replace,
     and run the task's declared check. It has no git, no network and no
     shell. ⚠️ COMMITTING, PUSHING AND OPENING THE PR ARE DONE BY THIS FILE,
     NOT BY THE MODEL - an outward action taken by deterministic code after a
     measured result has no model judgement left in it to get wrong.

  3. `WorkspaceConfined` IS A GATE POLICY, NOT A CHECK INSIDE THE TOOLS. Every
     path must resolve inside the worktree and outside `.git`, and a WRITE must
     also match the task's declared `writable` globs - deny by default. The
     gate sits where the model cannot reach, and a refusal lands in the ledger.

  4. 🔴 THE HARNESS RUNS THE CHECK ITSELF AFTER THE MODEL STOPS. On 2026-09-16
     a cheap-tier implementer in another lane reported five mutation runs its
     transcript shows it never performed. A model's report of what it ran is
     testimony; the verdict here is computed from the tree and the exit code.

  5. THE CHECK RUNS WITH SECRETS SCRUBBED FROM ITS ENVIRONMENT. It executes
     repository code the model may have edited through `writable`, so it must
     not inherit the provider keys this process holds. ⚠️ Keeping `writable`
     narrow is the other half of that control - a task that lets the model
     edit build scripts or tests has handed it code execution on this host.

Result verdicts: PASS · CHECK_FAILED · NO_CHANGE · ERROR. A PR is opened only
on PASS and only with `--publish`.
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .agent import AgentRun, run_agent
from .gate import (
    Decision, DenyUnlessDeclared, FactSource, Gate, GatedExecutor, Ledger,
    StaticFacts, ToolCall,
)
from .outcome import claims_success
from .providers import ProviderClient

__all__ = ["Task", "LaneResult", "WorkspaceConfined", "run_task", "main"]

#: The whole tool surface a model is given. ⚠️ Anything not here is refused by
#: `DenyUnlessDeclared`, whatever the model asks for.
READ_TOOLS = ("list_files", "read_file", "search")
WRITE_TOOLS = ("write_file", "replace_in_file")
TOOL_NAMES = READ_TOOLS + WRITE_TOOLS + ("run_check",)

#: Tool output is truncated to this many characters. ⚠️ Groq's free tier
#: allows 8,000 tokens a MINUTE, so one unbounded file read can stall a run.
MAX_TOOL_CHARS = 6000

AGENT_NAME = "OverMind Agent"
AGENT_EMAIL = "ciresnave+overmind@gmail.com"

#: Environment variables withheld from the check. Matched on the NAME.
SECRET_NAME = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSKEY|CREDENTIAL|^GH_|^GITHUB_",
                         re.IGNORECASE)


# --------------------------------------------------------------------------- #
# The task
# --------------------------------------------------------------------------- #

@dataclass
class Task:
    id: str
    repo: str
    goal: str
    check: list[str]
    writable: list[str]
    base: str = "origin/main"
    fetch: bool = True
    check_timeout_s: int = 900
    provider: str = "google"
    model: str | None = None
    max_steps: int = 20
    pr_title: str | None = None
    pr_body: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Task":
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            # ⚠️ A misspelt key would otherwise fall back to a default - and a
            # misspelt `writable` falls back to "nothing", which fails safe, but
            # a misspelt `check` must not silently become something else.
            raise ValueError(f"unknown task keys: {unknown}")
        task = cls(**data)
        # ⚠️ `isinstance(list)` FIRST: a string is iterable and every element
        # of "cargo test" is a string, so the element test alone accepts it.
        if (not isinstance(task.check, list) or not task.check
                or not all(isinstance(a, str) for a in task.check)):
            raise ValueError("`check` must be a non-empty argv list of strings")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,60}", task.id):
            raise ValueError("`id` must be 1-60 chars of [A-Za-z0-9._-]")
        return task


@dataclass
class LaneResult:
    task_id: str
    verdict: str
    provider: str | None
    model: str | None
    stop_reason: str | None
    check_exit: int | None
    check_tail: str
    changed_files: list[str]
    model_ran_check: bool
    model_claimed_success: bool
    unsupported_claim: bool
    denied_calls: int
    steps: int
    tokens: dict[str, Any]
    ledger_digest: str
    final_text: str
    branch: str | None = None
    pr_url: str | None = None
    error: str | None = None
    seconds: float = 0.0
    #: The worktree, when `keep=True`; otherwise it has been removed.
    workspace: str | None = None
    #: `added removed path` per file. ⚠️ A check asserts what it asserts; the
    #: size of the diff is what a reviewer uses to see everything else.
    diff_numstat: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2)


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

class OutsideWorkspace(ValueError):
    pass


def resolve_inside(root: pathlib.Path, rel: str) -> pathlib.Path:
    """`rel` resolved under `root`, or OutsideWorkspace.

    ⚠️ RESOLVED, NOT STRING-CHECKED. `a/../../x` and a tracked symlink that
    points out of the tree both look relative until they are resolved.
    """
    if not isinstance(rel, str):
        raise OutsideWorkspace("path must be a string")
    text = rel.replace("\\", "/").strip()
    if not text or text in (".", "./"):
        return root.resolve()
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise OutsideWorkspace(f"absolute path refused: {rel!r}")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p.lower() == ".git" for p in parts):
        raise OutsideWorkspace(f"the repository's .git is off limits: {rel!r}")
    base = root.resolve()
    target = (base / "/".join(parts)).resolve()
    if target != base and base not in target.parents:
        raise OutsideWorkspace(f"path escapes the workspace: {rel!r}")
    return target


def glob_match(rel: str, pattern: str) -> bool:
    """Segment-wise glob: `*` stays inside one segment, `**` spans any number.

    ⚠️ NOT `fnmatch` ON THE WHOLE PATH, where `*` also matches `/` - that makes
    `crates/*/Cargo.toml` writable at any depth, which is wider than declared.
    """
    path = [p for p in rel.replace("\\", "/").split("/") if p]
    pat = [p for p in pattern.replace("\\", "/").split("/") if p]

    def match(i: int, j: int) -> bool:
        if j == len(pat):
            return i == len(path)
        if pat[j] == "**":
            return any(match(k, j + 1) for k in range(i, len(path) + 1))
        return i < len(path) and fnmatch.fnmatchcase(path[i], pat[j]) and match(i + 1, j + 1)

    return match(0, 0)


@dataclass
class WorkspaceConfined:
    """Every path inside the worktree; every WRITE inside `writable`."""
    root: pathlib.Path
    writable: Sequence[str]
    name: str = "workspace-confined"

    def applies_to(self, call: ToolCall) -> bool:
        return call.name in READ_TOOLS + WRITE_TOOLS

    def definitely_denies(self, tool: str) -> bool:
        return False      # argument-dependent, so never "always"

    def decide(self, call: ToolCall, facts: FactSource) -> Decision:
        path = call.arg("path", "")
        try:
            target = resolve_inside(self.root, path)
        except OutsideWorkspace as exc:
            return Decision.deny(str(exc), self.name)
        if call.name in WRITE_TOOLS:
            rel = target.relative_to(self.root.resolve()).as_posix()
            if not any(glob_match(rel, p) for p in self.writable):
                return Decision.deny(
                    f"{rel} is not writable for this task; declared writable: "
                    f"{list(self.writable)}", self.name)
        return Decision.allow("inside the workspace", self.name)


# --------------------------------------------------------------------------- #
# Processes
# --------------------------------------------------------------------------- #

def _run(argv: Sequence[str], cwd: pathlib.Path, timeout: float = 120,
         env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess:
    exe = shutil.which(argv[0]) or argv[0]
    return subprocess.run(  # noqa: S603 - argv list, shell=False
        [exe, *argv[1:]], cwd=str(cwd), capture_output=True, timeout=timeout,
        shell=False, check=False, env=dict(env) if env is not None else None)


def _git(cwd: pathlib.Path, *args: str, timeout: float = 120) -> str:
    proc = _run(["git", *args], cwd, timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed ({proc.returncode}): "
                           f"{proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return proc.stdout.decode("utf-8", "replace")


def scrubbed_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not SECRET_NAME.search(k)}


def _clip(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + f"\n... [{len(text) - limit} chars omitted] ...\n" + text[-limit // 2:]


# --------------------------------------------------------------------------- #
# The six tools
# --------------------------------------------------------------------------- #

class Workspace:
    """The tools a model may call, bound to one worktree."""

    def __init__(self, root: pathlib.Path, task: Task) -> None:
        self.root = root
        self.task = task

    def _path(self, rel: str) -> pathlib.Path:
        # Defence in depth: the gate already refused anything this would.
        return resolve_inside(self.root, rel)

    def list_files(self, pattern: str = "**", path: str = "") -> str:
        names = [n for n in _git(self.root, "ls-files", "-z").split("\0") if n]
        names = [n for n in names if glob_match(n, pattern if "/" in pattern or pattern == "**"
                                                else "**/" + pattern)]
        if path:
            prefix = self._path(path).relative_to(self.root.resolve()).as_posix()
            names = [n for n in names if prefix in ("", ".") or n.startswith(prefix.rstrip("/") + "/")]
        head = names[:300]
        more = f"\n... and {len(names) - 300} more" if len(names) > 300 else ""
        return f"{len(names)} tracked files\n" + "\n".join(head) + more

    def read_file(self, path: str, start: int = 1, count: int = 200) -> str:
        target = self._path(path)
        lines = target.read_bytes().decode("utf-8", "replace").replace("\r\n", "\n").split("\n")
        start = max(1, int(start))
        chunk = lines[start - 1: start - 1 + max(1, min(int(count), 400))]
        body = "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(chunk))
        return _clip(f"{path}: lines {start}-{start + len(chunk) - 1} of {len(lines)}\n{body}")

    def search(self, pattern: str, path: str = "") -> str:
        args = ["grep", "-n", "-I", "-e", pattern]
        if path:
            args += ["--", self._path(path).relative_to(self.root.resolve()).as_posix() or "."]
        proc = _run(["git", *args], self.root)
        if proc.returncode == 1:
            return "no matches"
        if proc.returncode != 0:
            return f"search failed: {proc.stderr.decode('utf-8', 'replace').strip()[:300]}"
        out = proc.stdout.decode("utf-8", "replace").splitlines()
        return _clip("\n".join(out[:200]) + (f"\n... {len(out) - 200} more" if len(out) > 200 else ""))

    def _write(self, target: pathlib.Path, text: str) -> None:
        # ⚠️ KEEP THE FILE'S LINE ENDINGS. Writing LF lines back into a CRLF
        # checkout mixes endings, and re-adding CRLF to text that already has
        # CR produces CR CR LF - the defect that sat in four merged files.
        if "\r" in text.replace("\r\n", ""):
            raise ValueError("content contains a bare CR")
        text = text.replace("\r\n", "\n")
        before = target.read_bytes() if target.exists() else b""
        crlf = b"\r\n" in before
        # ⚠️ AND KEEP ITS FINAL NEWLINE. The first live run rewrote a README
        # through write_file and silently dropped the last one - a change the
        # task said not to make and its check could not see.
        if before.endswith(b"\n") and text and not text.endswith("\n"):
            text += "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))

    def write_file(self, path: str, content: str) -> str:
        target = self._path(path)
        self._write(target, content)
        return f"wrote {path} ({len(content)} chars)"

    def replace_in_file(self, path: str, old: str, new: str) -> str:
        target = self._path(path)
        text = target.read_bytes().decode("utf-8").replace("\r\n", "\n")
        old_n = old.replace("\r\n", "\n")
        count = text.count(old_n)
        if count != 1:
            raise ValueError(f"`old` must occur exactly once in {path}; found {count}")
        self._write(target, text.replace(old_n, new.replace("\r\n", "\n")))
        return f"replaced 1 occurrence in {path}"

    def run_check(self) -> str:
        code, tail = self.check()
        return f"exit={code}\n{tail}"

    def check(self) -> tuple[int, str]:
        """The declared check. ⚠️ No argument reaches it from the model."""
        try:
            proc = _run(self.task.check, self.root, self.task.check_timeout_s, scrubbed_env())
        except subprocess.TimeoutExpired:
            return 124, f"check timed out after {self.task.check_timeout_s}s"
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        return proc.returncode, _clip(out, 3000)

    def changed_files(self) -> list[str]:
        out = _git(self.root, "status", "--porcelain", "-z", "--untracked-files=all")
        return sorted({entry[3:] for entry in out.split("\0") if len(entry) > 3})

    def tools(self) -> dict[str, Callable[..., Any]]:
        return {n: getattr(self, n) for n in TOOL_NAMES}


def schemas() -> list[dict[str, Any]]:
    def fn(name, desc, props, required=()):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(required)}}}
    s = {"type": "string"}
    i = {"type": "integer"}
    return [
        fn("list_files", "List tracked files. `pattern` is a glob such as '*.toml' or 'src/**/*.rs'.",
           {"pattern": s, "path": s}),
        fn("read_file", "Read a file, with line numbers. Use start/count for long files.",
           {"path": s, "start": i, "count": i}, ("path",)),
        fn("search", "Search tracked files for a regular expression (git grep).",
           {"pattern": s, "path": s}, ("pattern",)),
        fn("write_file", "Replace a file's entire content. Only paths the task declares writable.",
           {"path": s, "content": s}, ("path", "content")),
        fn("replace_in_file", "Replace one exact, unique occurrence of `old` with `new`.",
           {"path": s, "old": s, "new": s}, ("path", "old", "new")),
        fn("run_check", "Run the task's acceptance check and see its exit code and output.", {}),
    ]


SYSTEM = """You are a software engineer completing one small, well-defined change in a
git repository. You can only use the provided tools. You cannot run shell commands,
use git, or reach the network; the harness commits and opens the pull request if,
and only if, the acceptance check passes when the harness runs it itself.

Work method: inspect the relevant files, make the smallest change that achieves the
goal, run the check, fix what it reports, and stop. To change an existing file, use
replace_in_file; use write_file only to create a new file. When you are done, reply with a
short plain-text summary of what you changed and what the check reported. Do not
claim to have run anything you did not run: every tool call is recorded."""


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

_CODE_CLAIM = re.compile(
    r"\b(fixed|changed|updated|modified|implemented|resolved|bumped|applied)\b"
    r"|\b(checks?|tests?|build)\s+(now\s+)?(pass(es|ed)?|succeed(s|ed)?|(is|are)\s+green)\b",
    re.IGNORECASE)
_CODE_DENIAL = re.compile(
    r"\b(fail(ed|s|ing)?|could\s*n[o']?t|cannot|unable|did\s*n[o']?t|"
    r"not\s+(yet\s+)?(pass|fixed|changed))\b", re.IGNORECASE)


def claims_done(text: str) -> bool:
    """⚠️ HEURISTIC over prose, and only ever used to FLAG disagreement with
    the harness's own verdict - never to reach one. `outcome.claims_success`
    was written for messaging tasks ("sent", "created") and misses "fixed" and
    "the check passed", which is how a code task reports itself."""
    if not text or _CODE_DENIAL.search(text):
        return False
    return claims_success(text) or bool(_CODE_CLAIM.search(text))


def _verdict(check_exit: int, changed: list[str], stop_reason: str) -> str:
    if not changed:
        return "NO_CHANGE"
    # ⚠️ AN INTERRUPTED RUN IS NEVER PUBLISHABLE, EVEN WHEN THE CHECK PASSES.
    # Measured on the first live run: Groq hit its per-minute token limit
    # after the model had edited the file, the check happened to pass, and the
    # verdict read PASS on a run that ended in a provider error. A partial edit
    # that satisfies a weak check is exactly what a check cannot see.
    if stop_reason != "completed":
        return "INCOMPLETE"
    return "PASS" if check_exit == 0 else "CHECK_FAILED"


def _numstat(root: pathlib.Path) -> list[str]:
    """`added removed path` per changed file, untracked files included."""
    _git(root, "add", "-A", "--intent-to-add")
    out = _git(root, "diff", "--numstat")
    return [line.replace("\t", " ") for line in out.splitlines() if line.strip()]


def gh_pr_create(root: pathlib.Path, branch: str, base: str, title: str, body: str) -> str:
    proc = _run(["gh", "pr", "create", "--head", branch, "--base", base,
                 "--title", title, "--body", body], root, 120)
    if proc.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    return proc.stdout.decode("utf-8", "replace").strip().splitlines()[-1]


def run_task(task: Task, client: Any = None, *, publish: bool = False, keep: bool = False,
             pr_creator: Callable[..., str] = gh_pr_create) -> LaneResult:
    started = time.time()
    repo = pathlib.Path(task.repo)
    if task.fetch:
        _git(repo, "fetch", "-q", "origin")
    holder = pathlib.Path(tempfile.mkdtemp(prefix=f"overmind-{task.id}-"))
    root = holder / "wt"
    branch = f"agent/{task.id}-{uuid.uuid4().hex[:6]}"
    run: AgentRun | None = None
    result: LaneResult | None = None
    try:
        _git(repo, "worktree", "add", "-q", "--detach", str(root), task.base)
        _git(root, "switch", "-q", "-c", branch)
        ws = Workspace(root, task)
        gate = Gate([WorkspaceConfined(root, task.writable),
                     DenyUnlessDeclared(reversible=frozenset(TOOL_NAMES))],
                    facts=StaticFacts(), ledger=Ledger())
        executor = GatedExecutor(gate, ws.tools())
        client = client or ProviderClient(task.provider, timeout=180.0, max_tokens=4096,
                                          model=task.model)
        brief = (f"GOAL:\n{task.goal}\n\nFILES YOU MAY WRITE (globs): {task.writable}\n"
                 f"ACCEPTANCE CHECK (run with the run_check tool): {' '.join(task.check)}")
        run = run_agent(client, executor, schemas(), brief, system=SYSTEM,
                        max_steps=task.max_steps, offer="permitted")

        # 🔴 THE HARNESS'S OWN READING. Nothing below consults the model.
        changed = ws.changed_files()
        check_exit, check_tail = ws.check()
        verdict = _verdict(check_exit, changed, str(run.stop_reason))
        claimed = claims_done(run.final_text)
        # ⚠️ An errored run carries no provider or model; fall back to what
        # the client was configured with, so a failure is still attributable.
        provider = run.provider or getattr(getattr(client, "provider", None), "key", None)
        model = run.model or getattr(client, "pinned_model", None)
        result = LaneResult(
            task_id=task.id, verdict=verdict, provider=provider, model=model,
            diff_numstat=_numstat(root) if changed else [],
            stop_reason=str(run.stop_reason), check_exit=check_exit, check_tail=check_tail,
            changed_files=changed, model_ran_check=run.did("run_check"),
            model_claimed_success=claimed, unsupported_claim=claimed and verdict != "PASS",
            denied_calls=run.denied_count, steps=run.steps,
            tokens=dataclasses.asdict(run.usage) if dataclasses.is_dataclass(run.usage) else {},
            ledger_digest=run.ledger.digest(limit=30), final_text=run.final_text[:2000],
            error=run.error)

        if publish and verdict == "PASS":
            _git(root, "add", "-A")
            _git(root, "-c", f"user.name={AGENT_NAME}", "-c", f"user.email={AGENT_EMAIL}",
                 "commit", "-q", "-m", task.pr_title or f"agent: {task.id}", "-m",
                 f"Made by {run.provider}/{run.model} through OverMind; the harness ran "
                 f"`{' '.join(task.check)}` itself and it exited 0.")
            _git(root, "push", "-q", "origin", branch)
            body = (f"{task.pr_body}\n\n---\n**Made by a non-Claude model through OverMind.**\n\n"
                    f"- model: `{run.provider}/{run.model}` · steps {run.steps} · "
                    f"refused calls {run.denied_count}\n"
                    f"- the harness ran `{' '.join(task.check)}` itself: exit {check_exit}\n"
                    f"- changed (added removed path): {'; '.join(result.diff_numstat)}\n")
            base_branch = task.base.split("/", 1)[1] if task.base.startswith("origin/") else task.base
            result.branch = branch
            result.pr_url = pr_creator(root, branch, base_branch,
                                       task.pr_title or f"agent: {task.id}", body)
    except Exception as exc:  # noqa: BLE001 - reported as a verdict, never hidden
        if result is None:
            result = LaneResult(
                task_id=task.id, verdict="ERROR", provider=getattr(run, "provider", None),
                model=getattr(run, "model", None), stop_reason=None, check_exit=None,
                check_tail="", changed_files=[], model_ran_check=False,
                model_claimed_success=False, unsupported_claim=False, denied_calls=0,
                steps=getattr(run, "steps", 0), tokens={},
                ledger_digest=run.ledger.digest(limit=30) if run else "", final_text="")
        result.error = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if keep and result is not None and root.exists():
            result.workspace = str(root)
        if not keep:
            # ⚠️ THE BRANCH TOO. Removing a worktree keeps its branch, so the
            # first three live runs left three `agent/*` refs in OverMind's own
            # repository. A published branch is on the remote already.
            for args in (("worktree", "remove", "--force", str(root)),
                         ("branch", "-D", branch)):
                try:
                    _git(repo, *args)
                except Exception:  # noqa: BLE001 - cleanup must not mask the result
                    pass
            shutil.rmtree(holder, ignore_errors=True)
    result.seconds = round(time.time() - started, 1)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m overmind.lanework")
    parser.add_argument("task", help="path to a task JSON file")
    parser.add_argument("--publish", action="store_true",
                        help="commit, push and open a PR - only if the verdict is PASS")
    parser.add_argument("--keep", action="store_true", help="keep the worktree for inspection")
    args = parser.parse_args(argv)
    task = Task.from_json(json.loads(pathlib.Path(args.task).read_text(encoding="utf-8")))
    result = run_task(task, publish=args.publish, keep=args.keep)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(result.to_json())
    return 0 if result.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
