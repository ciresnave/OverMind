# SPDX-License-Identifier: MIT OR Apache-2.0
"""Grade a locally served model on REAL portfolio chores, to decide which
kinds of bulk work can leave Claude (CireSnave's cost rule).

    LLAMA_DESKTOP_API_KEY=... python probe/desktop_grade.py <out.json> [--controls-only]

Run it through with-secret, which holds the key and needs CireSnave's
Windows Hello approval (docs/WITH-SECRET-RUNBOOK.md §4).

Every input is fetched at run time from a fixed ref (CI logs by run id, docs
by commit), so a result can be reproduced. Every grade is computed, never
judged by a model:

  facts        each required fact is a list of acceptable spellings; the
               score is the share of facts the answer contains.
  fabrication  every identifier-shaped token in the answer (error codes,
               advisory ids, file names, inline code, numbers) must occur in
               the input. One that does not was made up.
  sweep        an exact set, scored by precision and recall.

⚠️ CONTROLS FIRST, as in p1_bench.py: for every task, a hand-written oracle
answer must score full marks with nothing fabricated, an empty answer must
score zero, and an answer with an invented identifier must be flagged.
A task whose controls fail is reported and not sent to the model.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("LLAMA_DESKTOP_BASE_URL", "http://192.168.4.23:8080/v1")
MODEL = os.environ.get("LLAMA_DESKTOP_MODEL", "qwen3.8-27b")
CONTEXT = int(os.environ.get("LLAMA_DESKTOP_CONTEXT", "8192"))
INPUT_BUDGET = 10_000  # characters of task input; about 3k tokens
REF = "3bf513f"        # OverMind ref the doc and sweep inputs are read at
PORTFOLIO_REF = "fe70df1"  # portfolio-history ref for CLAUDE.md


# -- inputs ------------------------------------------------------------------

def sh(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True,
                          encoding="utf-8", errors="replace", cwd=cwd).stdout


ANSI = re.compile(r"\x1b\[[0-9;]*m|\^\[\[[0-9;]*m")
GH_PREFIX = re.compile(r"^[^\t]*\t[^\t]*\t[0-9TZ:.-]+ ")


def ci_excerpt(raw: str) -> str:
    """What a delegating lane would send: the failed step's output up to its
    first `##[error]`, timestamps and colour codes removed, last part only."""
    lines = [ANSI.sub("", GH_PREFIX.sub("", l)) for l in raw.splitlines()]
    end = next((i for i, l in enumerate(lines) if l.startswith("##[error]")), len(lines))
    text = "\n".join(lines[:end + 1])
    return text[-INPUT_BUDGET:]


# -- grading -----------------------------------------------------------------

TOKEN = re.compile(
    r"RUSTSEC-\d{4}-\d{4}|E\d{4}|[\w./\\-]+\.(?:rs|py|md|toml|json|ts)(?::\d+)?|`[^`\n]+`|\b\d{3,}\b"
)


def facts_score(answer: str, facts: list[list[str]]) -> tuple[float, list[str]]:
    a = answer.lower()
    missing = [alts[0] for alts in facts if not any(x.lower() in a for x in alts)]
    return (len(facts) - len(missing)) / len(facts), missing


def fabricated(answer: str, source: str) -> list[str]:
    """Identifier-shaped tokens of the answer that the input never contains.
    Paths are compared by their last component, so `a/b/x.rs` matches an
    input that says `x.rs` with a different prefix or separator."""
    src = source.lower().replace("\\", "/")
    out = []
    for m in TOKEN.findall(answer):
        t = m.strip("`").lower().replace("\\", "/")
        if t in src:
            continue
        last = t.rsplit("/", 1)[-1].split(":")[0]
        if "." in last and last in src:
            continue
        out.append(m)
    return sorted(set(out))


def sweep_score(answer: str, truth: set[str]) -> dict:
    found = {x for x in truth if re.search(rf"(?<![\w./-]){re.escape(x)}(?![\w-])", answer)}
    claimed = set(re.findall(r"[\w./-]+", answer))
    extra = sorted(c for c in claimed if c not in truth and c in UNIVERSE.get("sweep", set()))
    return {"recall": len(found) / len(truth) if truth else 1.0,
            "missed": sorted(truth - found), "wrongly_listed": extra}


UNIVERSE: dict[str, set[str]] = {}


# -- tasks -------------------------------------------------------------------

CI_RUNS = [
    # (repo, run id, facts, oracle)
    ("OverMind", 36302592323,
     [["test_a_real_failing_go_project_actually_fails"], ["timed out", "TimeoutExpired", "timeout"],
      ["120"], ["PermissionError", "WinError 32", "being used by another process"]],
     "test_a_real_failing_go_project_actually_fails errored: `go test ./...` hit TimeoutExpired "
     "after 120 seconds, then the temp-dir cleanup raised PermissionError (WinError 32)."),
    ("fuel", 37171696454,
     [["crate::judge"], ["judge_cache.rs"], ["undispositioned"]],
     "The doc-link gate failed: 1 undispositioned link, crate::judge at "
     "fuel-dispatch/src/judge_cache.rs:18."),
    ("baracuda", 37190034084,
     [["E0308", "mismatched types"], ["TargetId"], ["ArchSku"],
      ["E0599", "no method named"], ["baracuda-kernels-bench"]],
     "Tests in baracuda-kernels-bench do not compile: E0308 expected `TargetId`, found `ArchSku`, "
     "and E0599 no method named `compile` on the Result the constructor now returns."),
    ("mlmf", 36212630172,
     [["descriptors_agree_over_the_corpus"], ["attn_k"],
      ["15750944", "15750945", "off by one", "off-by-one"], ["fuel_differential.rs"]],
     "descriptors_agree_over_the_corpus failed at fuel_differential.rs:470: blk.0.attn_k.weight's "
     "absolute byte range starts at 15750944 on one side and 15750945 on the other."),
    ("lightbulb", 37145666174,
     [["fmt", "rustfmt", "format"], ["scheduled_runner.rs"]],
     "cargo fmt --check failed: src/engine/scheduled_runner.rs:332 needs the long "
     "AdmitError::Rejected line wrapped."),
    ("auth-framework", 37239565404,
     [["RUSTSEC-2026-0258"], ["RUSTSEC-2023-0071"], ["h2"], ["rsa"], ["0.4.16"]],
     "cargo audit found 2 vulnerabilities: h2 0.3.27 (RUSTSEC-2026-0258, upgrade to >=0.4.16) "
     "and rsa 0.9.10 (RUSTSEC-2023-0071, Marvin attack, no fixed upgrade)."),
]

CI_PROMPT = ("Here is the failing part of a CI log. In at most 4 sentences, say which check "
             "failed and why, naming the exact test, file, error code or advisory id.\n\n")


def doc_tasks(repo: str) -> list[dict]:
    show = lambda path: sh("git", "show", f"{REF}:{path}", cwd=repo)
    runbook = show("docs/WITH-SECRET-RUNBOOK.md")
    s4 = runbook[runbook.index("## 4."):runbook.index("## 5.")]
    # the portfolio CLAUDE.md lives in its own history repo, at a pinned commit
    claude = sh("git", "--git-dir=C:/Projects/.portfolio-history.git", "show",
                f"{PORTFOLIO_REF}:CLAUDE.md")
    s3 = claude[claude.index("## 3."):claude.index("## 4.")]
    msg = sh("git", "log", "-1", "--format=%B", "c6c8c1e", cwd=repo)
    msg = msg[:msg.index("Claude-Session")] if "Claude-Session" in msg else msg
    return [
        {"id": "doc-commit-summary", "input": msg,
         "prompt": "Summarise this commit message for a project manager in exactly 3 bullet "
                   "points. Do not add facts it does not state.\n\n",
         "facts": [["transcript"], ["cwd", "working directory"], ["same pid", "same process", "recorded pid"],
                   ["Humboldt"]],
         "oracle": "- Humboldt's --self was refused because humboldt.json had a wrong cwd.\n"
                   "- The transcript_path cross-check only ran at SessionStart.\n"
                   "- Now every event from the recorded pid (the same process) can fix the cwd."},
        {"id": "doc-runbook-checklist", "input": s4,
         "prompt": "Turn this runbook section into a numbered checklist (at most 7 items) for an "
                   "agent about to use a secret. Keep every command and flag exact.\n\n",
         "facts": [["--reason"], ["600000", "9 minutes"], ["MSYS_NO_PATHCONV"], ["midnight"],
                   ["--window-mins"], ["Hello"]],
         "oracle": "1. Run with-secret.exe NAME --reason \"...\" -- <command>.\n"
                   "2. Use the Bash tool with timeout: 600000; Windows Hello waits up to 9 minutes.\n"
                   "3. Prefix MSYS_NO_PATHCONV=1 for slash flags.\n"
                   "4. An approval lasts until local midnight or --window-mins."},
        {"id": "doc-claude-steps", "input": s3,
         "prompt": "Rewrite this section as numbered steps an agent can follow after merging a "
                   "PR. Keep every command exact.\n\n",
         "facts": [["gh pr merge"], ["--squash"], ["refs/heads"], ["absent"], ["worktree remove"],
                   ["porcelain"]],
         "oracle": "1. gh pr merge <pr> --squash.\n2. gh api -X DELETE repos/<nwo>/git/refs/heads/<branch>.\n"
                   "3. Verify the branch is ABSENT.\n4. Check git status --porcelain, then git worktree "
                   "remove <path> and git worktree prune."},
    ]


def sweep_tasks(repo: str, state_dir: str) -> list[dict]:
    # top-level files (docs and configs, several without SPDX) and the probes
    top = [l.split("	", 1)[1] for l in sh("git", "ls-tree", REF, cwd=repo).splitlines()
           if l.split()[1] == "blob"]
    probes = [f for f in sh("git", "ls-tree", "-r", "--name-only", REF, "probe/", cwd=repo).split()
              if f.endswith((".py", ".ts", ".sh"))][:20]
    files = top + probes
    rows, missing = [], set()
    for f in files:
        head = "\n".join(sh("git", "show", f"{REF}:{f}", cwd=repo).splitlines()[:2])
        rows.append(f"=== {f}\n{head}")
        if "SPDX-License-Identifier" not in head:
            missing.add(f)
    spdx_input = "\n".join(rows)[:INPUT_BUDGET]

    recs = []
    for name in sorted(os.listdir(state_dir)):
        if name.endswith(".json"):
            try:
                s = json.load(open(os.path.join(state_dir, name), encoding="utf-8"))
                recs.append((name, s.get("pid"), str(s.get("session_id", ""))[:8]))
            except (OSError, ValueError):
                pass
    by_pid: dict = {}
    for name, pid, _ in recs:
        by_pid.setdefault(pid, []).append(name)
    dup = {str(p) for p, ns in by_pid.items() if len(ns) > 1}
    pid_input = "\n".join(f"{n}\tpid={p}\tsession={s}" for n, p, s in recs)[:INPUT_BUDGET]
    UNIVERSE["sweep"] = set(files) | {str(p) for _, p, _ in recs}
    return [
        {"id": "sweep-spdx", "input": spdx_input, "truth": missing,
         "prompt": "Below are the first two lines of each file. List the path of every file whose "
                   "first two lines do NOT contain `SPDX-License-Identifier`. Reply with the paths "
                   "only, one per line, or NONE.\n\n",
         "oracle": "\n".join(sorted(missing)) or "NONE"},
        {"id": "sweep-dup-pids", "input": pid_input, "truth": dup,
         "prompt": "Each line is a state file with its pid. List every pid that appears on MORE "
                   "than one line, one per line, or NONE.\n\n",
         "oracle": "\n".join(sorted(dup)) or "NONE"},
    ]


# -- the model ---------------------------------------------------------------

def chat(prompt: str, thinking: bool, key: str) -> dict:
    est_in = len(prompt) // 3 + 50
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.6 if thinking else 0.2,
            "max_tokens": max(256, CONTEXT - est_in - 128),
            "chat_template_kwargs": {"enable_thinking": thinking}}
    req = urllib.request.Request(f"{BASE_URL}/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json",
                                  "Authorization": f"Bearer {key}"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read()[:300]!r}", "seconds": time.time() - t}
    except (urllib.error.URLError, TimeoutError) as e:
        return {"error": str(e), "seconds": time.time() - t}
    ch = data["choices"][0]
    msg = ch["message"]
    content = msg.get("content") or ""
    # some servers inline the reasoning; never grade it as the answer
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    return {"answer": content, "reasoning_chars": len(msg.get("reasoning_content") or ""),
            "finish": ch.get("finish_reason"), "usage": data.get("usage"),
            "seconds": round(time.time() - t, 1)}


# -- run ---------------------------------------------------------------------

def grade(task: dict, answer: str) -> dict:
    if "truth" in task:
        return sweep_score(answer, task["truth"])
    score, missing = facts_score(answer, task["facts"])
    return {"facts": round(score, 2), "missing": missing,
            "fabricated": fabricated(answer, task["input"])}


def controls(task: dict) -> list[str]:
    """Problems with the GRADER for this task; empty means usable."""
    bad = []
    o, n = grade(task, task["oracle"]), grade(task, "")
    if "truth" in task:
        if task["truth"] and o["recall"] != 1.0:
            bad.append(f"oracle recall {o['recall']}")
        if task["truth"] and n["recall"] != 0.0:
            bad.append("empty answer scored recall > 0")
        return bad
    unsolvable = [alts[0] for alts in task["facts"]
                  if not any(x.lower() in task["input"].lower() for x in alts)]
    if unsolvable:
        bad.append(f"input lacks facts {unsolvable} (excerpt too short?)")
    if o["facts"] != 1.0:
        bad.append(f"oracle facts {o['facts']} missing {o['missing']}")
    if o["fabricated"]:
        bad.append(f"oracle flagged as fabricating {o['fabricated']}")
    if n["facts"] != 0.0:
        bad.append("empty answer scored facts > 0")
    if not grade(task, task["oracle"] + " See also RUSTSEC-1999-9999 in nowhere_made_up.rs.")["fabricated"]:
        bad.append("an invented identifier was not flagged")
    return bad


def main() -> int:
    out = sys.argv[1]
    controls_only = "--controls-only" in sys.argv
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tasks = []
    for name, run_id, facts, oracle in CI_RUNS:
        raw = sh("gh", "run", "view", str(run_id), "-R", f"ciresnave/{name}", "--log-failed")
        tasks.append({"id": f"ci-{name}", "input": ci_excerpt(raw), "prompt": CI_PROMPT,
                      "facts": facts, "oracle": oracle})
    tasks += doc_tasks(repo)
    tasks += sweep_tasks(repo, os.environ.get("LANE_STATE_DIR", "C:/Projects/.lane-state"))

    report = {"base_url": BASE_URL, "model": MODEL, "context": CONTEXT, "ref": REF, "tasks": []}
    usable = []
    for t in tasks:
        problems = controls(t)
        size = f" truth={len(t['truth'])}" if "truth" in t else ""
        report["tasks"].append({"id": t["id"], "input_chars": len(t["input"]),
                                "truth_size": len(t["truth"]) if "truth" in t else None,
                                "controls": problems or "ok"})
        print(f"{t['id']:24} input {len(t['input']):6} chars{size}  controls: {problems or 'ok'}",
              flush=True)
        if not problems:
            usable.append(t)
    if controls_only:
        json.dump(report, open(out, "w", encoding="utf-8"), indent=2, default=list)
        return 0 if len(usable) == len(tasks) else 1

    key = os.environ.get("LLAMA_DESKTOP_API_KEY")
    if not key:
        print("LLAMA_DESKTOP_API_KEY is not set: run this through with-secret", file=sys.stderr)
        return 2
    runs = []
    for t in usable:
        for thinking in (False, True):
            r = chat(t["prompt"] + t["input"], thinking, key)
            g = grade(t, r["answer"]) if "answer" in r else None
            row = {"task": t["id"], "thinking": thinking, **{k: v for k, v in r.items() if k != "answer"},
                   "grade": g, "answer": r.get("answer", "")[:1500]}
            runs.append(row)
            print(json.dumps({k: row[k] for k in ("task", "thinking", "seconds", "grade") if k in row},
                             default=list), flush=True)
            json.dump({**report, "runs": runs}, open(out, "w", encoding="utf-8"), indent=2, default=list)
    return 0


if __name__ == "__main__":
    sys.exit(main())
