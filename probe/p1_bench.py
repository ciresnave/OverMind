# SPDX-License-Identifier: MIT OR Apache-2.0
"""P1: can a free-tier model fix a real regression, judged by the harness?

    python probe/p1_bench.py <out.json> [provider ...]

Each task is a bug this repository has ACTUALLY had or been mutation-tested
against, re-introduced on a local branch built with git plumbing (the working
tree is never touched). The model is told which command fails and may write
ONLY the source file - never the tests - so it cannot pass by weakening the
check. Nothing is published.

⚠️ TWO CONTROLS PER TASK BEFORE ANY MODEL RUNS, so a result means something:

  null    a model that does nothing must get NO_CHANGE with a FAILING check.
          Otherwise the bug is not live and every "PASS" is free.
  oracle  a model that applies the known fix must get PASS. Otherwise the task
          is unsolvable inside `writable`, and every failure is the task's.

A task whose controls do not come out that way is reported and skipped.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from overmind import lanework as lw                                      # noqa: E402
from overmind.providers import ChatResult, ProviderClient, Usage         # noqa: E402
from overmind.quota import QuotaBook, default_path                        # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

PY = sys.executable

#: (name, file, correct text, buggy text, test module, what the regression was)
TASKS = [
    ("number-substring", "probe/paper_summaries.py",
     '    return any(re.search(r"(?<![\\d])" + re.escape(f) + r"(?![\\d])", text) for f in forms)',
     "    return any(f in text for f in forms)",
     "tests.test_paper_summaries",
     "a figure check matched 100 inside 1000"),
    ("timeout-escapes", "src/overmind/providers.py",
     "        except (TimeoutError, ConnectionError) as exc:",
     "        except (ZeroDivisionError,) as exc:",
     "tests.test_providers",
     "a socket timeout escaped the client instead of failing over"),
    ("glob-crosses-dirs", "src/overmind/lanework.py",
     "        return i < len(path) and fnmatch.fnmatchcase(path[i], pat[j]) and match(i + 1, j + 1)",
     "        return i < len(path) and fnmatch.fnmatchcase('/'.join(path[i:]), '/'.join(pat[j:]))",
     "tests.test_lanework.TestPieces",
     "a `*` in a writable glob matched across directories"),
    ("max-of-nothing", "tools/gate_fleet.py",
     "    top = max((c for c in counts.values() if c is not None), default=None)",
     "    top = max(c for c in counts.values() if c)",
     "tests.test_gate_fleet",
     "max() of an empty sequence crashed the fleet report"),
]


def git(*args: str, env: dict | None = None, data: bytes | None = None) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], input=data, capture_output=True,
                          check=True, env=env).stdout.decode("utf-8", "replace").strip()


def bench_branch(name: str, path: str, good: str, bad: str) -> str:
    """origin/main with ONE line changed, as a local branch. Plumbing only."""
    blob = subprocess.run(["git", "-C", str(ROOT), "show", f"origin/main:{path}"],
                          capture_output=True, check=True).stdout.decode("utf-8")
    if blob.count(good) != 1:
        raise RuntimeError(f"{name}: anchor matched {blob.count(good)} times")
    new = git("hash-object", "-w", "--stdin", data=blob.replace(good, bad).encode("utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(pathlib.Path(tmp) / "index"))
        git("read-tree", "origin/main", env=env)
        git("update-index", "--cacheinfo", f"100644,{new},{path}", env=env)
        tree = git("write-tree", env=env)
    commit = git("-c", "user.name=p1-bench", "-c", "user.email=p1-bench@example.invalid",
                 "commit-tree", tree, "-p", "origin/main", "-m", f"p1-bench: {name}")
    branch = f"p1-bench/{name}"
    git("branch", "-f", branch, commit)
    return branch


def task_for(name: str, path: str, test: str, why: str, base: str, provider: str) -> lw.Task:
    """`provider` is a key, or `key=model` to pin one model (local runs)."""
    key, _, model = provider.partition("=")
    goal = (f"This command fails: `python -m unittest {test}` (run it with the run_check tool). "
            f"The bug is in {path}; the tests are correct and you may not change them. "
            f"Find the bug and fix it with the smallest possible change.")
    tag = re.sub(r"[^A-Za-z0-9._-]", "-", provider)[-40:]
    return lw.Task(id=f"p1-{name}-{tag}", repo=str(ROOT), goal=goal, base=base, fetch=False,
                   check=[PY, "-m", "unittest", test], writable=[path], provider=key,
                   model=model or None, max_steps=16, check_timeout_s=300,
                   check_name=f"python -m unittest {test}")


def client_for(task: lw.Task) -> ProviderClient:
    # ⚠️ A local model can spend minutes loading before its first token, so
    # the runner's 180 s default would score a cold start as a failure.
    # ⚠️ And a thinking model spends its output budget before it answers:
    # NVIDIA's gpt-oss-20b was cut off at 4,096 in §27.
    local = task.provider == "ollama"
    return ProviderClient(task.provider, timeout=900.0 if local else 180.0,
                          max_tokens=8192 if local else 4096, model=task.model,
                          quota=None if local else QuotaBook(path=default_path()))


class Scripted:
    def __init__(self, turns):
        self.turns = list(turns)

    def chat(self, messages, tools=None, max_tokens=None):
        if self.turns:
            return self.turns.pop(0)
        return ChatResult(message={"role": "assistant", "content": "done"}, model="scripted",
                          provider="control", latency_s=0, usage=Usage.zero(), finish_reason="stop")


def oracle(path: str, bad: str, good: str) -> Scripted:
    call = {"id": "o1", "type": "function", "function": {
        "name": "replace_in_file", "arguments": json.dumps({"path": path, "old": bad, "new": good})}}
    return Scripted([ChatResult(message={"role": "assistant", "content": "", "tool_calls": [call]},
                                model="scripted", provider="control", latency_s=0,
                                usage=Usage.zero(), finish_reason="tool_calls")])


def main(argv: list[str]) -> int:
    out_path = pathlib.Path(argv[0])
    providers = argv[1:] or ["google", "cloudflare", "nvidia", "groq"]
    results = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    git("fetch", "-q", "origin")
    main_sha = git("rev-parse", "--short=8", "origin/main")
    branches = []
    try:
        # P1_TASKS=name,name limits the run - a free tier's daily allowance
        # covers about two tasks per model (MEASUREMENTS §27).
        wanted = [n for n in os.environ.get("P1_TASKS", "").split(",") if n]
        for name, path, good, bad, test, why in TASKS:
            if wanted and name not in wanted:
                continue
            branch = bench_branch(name, path, good, bad)
            branches.append(branch)
            null = lw.run_task(task_for(name, path, test, why, branch, "control"), Scripted([]))
            orc = lw.run_task(task_for(name, path, test, why, branch, "control"), oracle(path, bad, good))
            live = null.verdict == "NO_CHANGE" and null.check_exit not in (0, None)
            fair = orc.verdict == "PASS"
            print(f"{name:18} controls: null={null.verdict}/exit {null.check_exit} "
                  f"oracle={orc.verdict}  -> {'OK' if live and fair else 'SKIPPED'}", flush=True)
            if not (live and fair):
                results[name] = {"skipped": True, "null": null.verdict, "oracle": orc.verdict}
                continue
            for prov in providers:
                key = f"{name}/{prov}"
                if key in results:
                    continue
                task = task_for(name, path, test, why, branch, prov)
                r = lw.run_task(task, client_for(task))
                results[key] = {"main": main_sha, "task": name, "regression": why,
                                **json.loads(r.to_json())}
                print(f"  {prov:11} {r.verdict:13} model={r.model} steps={r.steps} "
                      f"{r.seconds}s tokens={r.tokens.get('total_tokens')} "
                      f"ran_check={r.model_ran_check} claim={r.model_claimed_success} "
                      f"numstat={r.diff_numstat} {('ERR ' + r.error[:80]) if r.error else ''}",
                      flush=True)
                out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
                time.sleep(5)
    finally:
        for b in branches:
            subprocess.run(["git", "-C", str(ROOT), "branch", "-D", b], capture_output=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
