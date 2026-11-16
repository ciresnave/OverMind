# SPDX-License-Identifier: MIT OR Apache-2.0
"""The sandbox seam (plan P3.7): run an AGENT-SUPPLIED python check through AntSpawn.

CireSnave 2026-10-28 (DESIGN-PROPOSAL 7.5): *"yes...but only in a sandbox."* A check the
host did not choose never runs on this host. It becomes an AntSpawn job spec and runs
through the `spawn run` CLI (PM ruling 2026-10-30: option A, subprocess, `OVERMIND_SPAWN`).
OverMind names NO launcher: `spawn` picks only one certified for every need, and with
none it exits 125, which this module treats as "not run" (never a host run, never a PASS).

⚠️ EVERY SANDBOX CLAIM HERE IS UNMEASURED. No VM was started to write this: live Windows
Sandbox runs are held (board 321, CireSnave's call). The guest paths, the python mapping
and the isolation of a real run are the shape AntSpawn's spec allows, not a measurement.

The contract, from the antspawn lane (origin/main ecb6f76, `src/main.rs`):
  * success prints nothing; the result is the exit code; the job's stdout/stderr are NOT
    returned (a job must write a file and declare it an `[[output]]`); this version
    declares none. The `windows-sandbox` launcher alone copies the guest's combined
    stdout+stderr to `<results>/windows-sandbox.cmd.log` (antspawn lane, 2026-10-30), which is
    appended when present;
  * antspawn#49 (0.0.49): job exit c -> c (c > 255 or negative -> 1); timeout -> 124; killed
    -> 137; a refusal (no certified launcher, unmet need) -> 125; any other spawn-side error
    -> 126. A job that itself exits 125/126 looks the same by code, so
    `<results>/spawn-result.json` (`status`: exited|timed_out|killed|refused|error, real
    `exit_code`, `message`) is authoritative and is read first; the code alone is the
    fallback. `<results>/stdout.tail` + `stderr.tail` (last 64 KiB) are the job's output
    (windows-sandbox: one combined `stdout.tail`). `available()` still runs `spawn providers`
    first, a precheck that refuses without starting a VM. A job that exits 2 is a job failure.

v1 scope (PM): python checks only. `cargo`, `go`, `npm`, `pnpm`, `make` and anything else
are refused with a stated reason (they need the network, which the rule forces off, or a
toolchain nobody has mapped or measured). A flat module: `lanework` and the policy plug-in
import it; it imports neither.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Sequence

#: Where the job sees things. UNMEASURED: the shape a Windows Sandbox guest takes.
GUEST_WORK = "C:\\work"
GUEST_PYTHON = "C:\\python"
GUEST_SCRATCH = "C:\\scratch"

PYTHON_PROGRAMS = frozenset({"python", "python3", "py"})
#: Named in the refusal so the record says WHY, not just "no".
NEEDS_NETWORK = frozenset({"cargo", "go", "npm", "pnpm", "yarn", "pip", "uv"})

ENV_SPAWN = "OVERMIND_SPAWN"
ENV_PYTHON = "OVERMIND_SANDBOX_PYTHON"

#: `run(argv, cwd, timeout, env)` -> an object with `.returncode`, `.stdout`, `.stderr`
#: (bytes), like `Workspace._run`: through the primitives when the harness runs it.
Execute = Callable[..., Any]


class SandboxUnavailable(RuntimeError):
    """The check cannot run in a sandbox. The caller refuses; it NEVER runs on the host."""


def _program(argv: Sequence[str]) -> str:
    name = pathlib.PurePath(argv[0].replace("\\", "/")).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def refusal_for(argv: Sequence[str]) -> str | None:
    """Why this argv cannot be sandboxed in v1, or `None` when it can."""
    if not argv or not all(isinstance(a, str) and a for a in argv):
        return "the check is not a non-empty argv of strings"
    prog = _program(argv)
    if prog in PYTHON_PROGRAMS:
        return None
    if prog in NEEDS_NETWORK:
        return (f"`{prog}` needs the network to fetch dependencies, which a sandboxed check "
                f"never gets (network = off)")
    return f"only python checks run in the sandbox in this version, not `{prog}`"


def _toml(value: Any) -> str:
    # JSON strings, numbers and arrays are valid TOML for the values used here
    return json.dumps(value, ensure_ascii=False)


def job_spec(name: str, argv: Sequence[str], checkout: pathlib.Path, python_home: pathlib.Path,
             time_s: int) -> str:
    """The AntSpawn job spec (TOML) for `argv`. The checkout and the python install are
    mapped READ-ONLY; the only writable place is an empty scratch mount discarded at the
    end; `network = "off"`; the isolation capabilities are left at AntSpawn's default
    (`require`), which is what makes `spawn` refuse a launcher that lacks them."""
    reason = refusal_for(argv)
    if reason:
        raise SandboxUnavailable(reason)
    cmd = [GUEST_PYTHON + "\\python.exe", *argv[1:]]
    env = {"PYTHONDONTWRITEBYTECODE": "1", "TEMP": GUEST_SCRATCH, "TMP": GUEST_SCRATCH}
    return "\n".join([
        "[job]",
        f"name = {_toml(name)}",
        f"cmd = {_toml(cmd)}",
        f"cwd = {_toml(GUEST_WORK)}",
        "",
        "[job.env]",
        *[f"{k} = {_toml(v)}" for k, v in env.items()],
        "",
        "[[mount]]", f"host = {_toml(str(checkout))}", f"guest = {_toml(GUEST_WORK)}", 'mode = "ro"', "",
        "[[mount]]", f"host = {_toml(str(python_home))}", f"guest = {_toml(GUEST_PYTHON)}", 'mode = "ro"', "",
        "[[mount]]", f"guest = {_toml(GUEST_SCRATCH)}", 'mode = "scratch"', "",
        "[limits]", f"time_s = {max(1, int(time_s))}", "",
        "[needs]", 'network = "off"', "",
    ])


def _default_execute(argv: Sequence[str], cwd: pathlib.Path, timeout: float = 120,
                     env: Mapping[str, str] | None = None) -> Any:
    return subprocess.run(list(argv), cwd=str(cwd), timeout=timeout, capture_output=True,
                          env=None if env is None else dict(env), check=False)



def _read_tail(path: pathlib.Path, limit: int = 1500) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()[-limit:] if path.is_file() else ""
    except OSError:
        return ""


def _read_result(path: pathlib.Path) -> tuple[str | None, str, Any]:
    """(status, message, exit_code) from spawn-result.json; (None, "", None) when absent or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError, RecursionError):
        return None, "", None
    if not isinstance(data, dict):
        return None, "", None
    status, message = data.get("status"), data.get("message")
    # exit_code is returned raw (absent -> 0): the caller fails closed on anything but an int 0
    return (status if isinstance(status, str) else None,
            message if isinstance(message, str) else "",
            data.get("exit_code", 0))


class SandboxRunner:
    def __init__(self, spawn: str, python_home: pathlib.Path, execute: Execute | None = None) -> None:
        self.spawn = spawn
        self.python_home = pathlib.Path(python_home)
        self._execute = execute or _default_execute

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None,
                 execute: Execute | None = None) -> "SandboxRunner":
        env = os.environ if environ is None else environ
        spawn, home = env.get(ENV_SPAWN), env.get(ENV_PYTHON)
        if not spawn:
            raise SandboxUnavailable(f"{ENV_SPAWN} is not set: there is no `spawn` to run the check in")
        if not home or not pathlib.Path(home).is_dir():
            raise SandboxUnavailable(f"{ENV_PYTHON} is not set to a directory: no python to map into the sandbox")
        return cls(spawn, pathlib.Path(home), execute)

    def _write_spec(self, workdir: pathlib.Path, argv: Sequence[str], checkout: pathlib.Path,
                    time_s: int) -> pathlib.Path:
        path = workdir / "job.toml"
        path.write_text(job_spec("overmind-check", argv, checkout, self.python_home, time_s),
                        encoding="utf-8", newline="\n")
        return path

    def available(self, argv: Sequence[str], checkout: pathlib.Path, time_s: int = 900) -> None:
        """Raise `SandboxUnavailable` unless `spawn` reports a launcher certified for every
        need of this check's spec. Starts no VM (`spawn providers` only reads the matrix)."""
        with tempfile.TemporaryDirectory(prefix="overmind-sbx-") as tmp:
            spec = self._write_spec(pathlib.Path(tmp), argv, checkout, time_s)
            try:
                done = self._execute([self.spawn, "providers", str(spec)], pathlib.Path(tmp), 60)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SandboxUnavailable(f"`spawn providers` did not run: {exc}") from exc
            out = done.stdout.decode("utf-8", "replace") if isinstance(done.stdout, bytes) else str(done.stdout)
            if done.returncode != 0:
                raise SandboxUnavailable(f"`spawn providers` failed (exit {done.returncode})")
            if not any(line.split("\t")[-1].strip() == "satisfies all needs" for line in out.splitlines()):
                raise SandboxUnavailable("no launcher is certified for every need of this check "
                                         "(isolation set, network off); it does not run on the host")

    def run_check(self, argv: Sequence[str], checkout: pathlib.Path, timeout_s: int,
                  execute: Execute | None = None) -> tuple[int, str]:
        """(exit code, tail). Only 0 is a pass. A failure to start or to collect is never 0.
        `execute` is the caller's process runner (the harness primitive, when there is one)."""
        run = execute or self._execute
        with tempfile.TemporaryDirectory(prefix="overmind-sbx-") as tmp:
            workdir = pathlib.Path(tmp)
            try:
                spec = self._write_spec(workdir, argv, checkout, timeout_s)
                done = run([self.spawn, "run", str(spec), "--results", str(workdir / "results")],
                           workdir, timeout_s + 60)
            except SandboxUnavailable as exc:
                return 1, f"sandboxed check not run: {exc}"
            except subprocess.TimeoutExpired:
                return 124, f"sandboxed check timed out after {timeout_s}s"
            except OSError as exc:
                return 1, f"sandboxed check not run: cannot start `spawn`: {exc}"
            results = workdir / "results"
            logged = _read_tail(results / "stdout.tail") or _read_tail(results / "windows-sandbox.cmd.log")
            errlog = _read_tail(results / "stderr.tail")
            if errlog:
                logged = (logged + "\nstderr:\n" if logged else "stderr:\n") + errlog
            status, message, real = _read_result(results / "spawn-result.json")
        err = done.stderr.decode("utf-8", "replace") if isinstance(done.stderr, bytes) else str(done.stderr)
        err = err.strip()[-600:]
        code = done.returncode
        if status in ("refused", "error"):       # authoritative: spawn itself did not run the job
            return (code if code else 1), (f"sandboxed check NOT run ({status}): {message or err or 'no message'}")
        if code == 0 and status is not None and not (status == "exited" and type(real) is int and real == 0):
            # any other status (timed_out, killed, unknown) or a malformed/nonzero exit_code: never a pass
            code = real if type(real) is int and real else 1
        if code == 0:
            return 0, "sandboxed check exited 0" + (f"; its log:\n{logged}" if logged
                                                    else " (no output captured)")
        if status is None and code in (125, 126):
            return code, (f"sandboxed check NOT run or not collected (exit {code}, no spawn-result.json; "
                          f"a job exiting {code} looks the same): {err}")
        return code, (f"sandboxed check exited {code}" + (f"; {err}" if err else "")
                      + (f"\nits log:\n{logged}" if logged else ""))
