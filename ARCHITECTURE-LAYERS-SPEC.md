# OverMind architecture: layers and spin-outs (spec, NOT implementation)

**Status: a draft for CireSnave's approval. No code moves until it is approved.** Asked for by the PM on
2026-10-07, for CireSnave. His words (verbatim, relayed by the PM):

- *"Grab bag programs are bugs waiting to happen."*
- *"OverMind was originally envisioned as an orchestrator over top of many agents. As such, it
  providing the harness for each singular agent feels incorrect."*
- *"I think several pieces of it will need to be spun out of OverMind so OverMind can remain the
  orchestrator it was envisioned to be."*

He approved the layering in §1 as "on the same page". This document makes it concrete enough to approve
or reject.

**Evidence rules.**
- Every claim about code is read from `origin/main` at **`cbeaba6`** (2026-10-07) with `git show`.
  `src/`, `tests/`, `tools/`, `probe/`, `examples/` and `.github/` are byte-identical at `94ff807`.
- A citation `file:line` is a line of that blob. Python paths are under `src/overmind/` unless shown.
- Each claim is marked **MEASURED** (read or run in this repo or on this machine, with the date) or
  **ASSUMED** (inferred, or a proposal not yet tested).

---

## 1. The layers, and the contract between each pair

```
ORCHESTRATOR  (stays OverMind)
   │ writes a SessionConfig; calls spawn / observe / stop; reads back what loaded
   ▼
HARNESS       (one per session; a model-agnostic plug-in host)
   │ drives the turn loop; every model output and every tool call passes the plug-in pipeline
   ├──► PLUG-INS (gate, ledger, checks, tools, MCP, Synapse connection, fork, ...)
   ▼
HANDLER       (speaks ONE model wire format: OpenAI-shaped chat today)
   ▼
MODEL CLIENT  (HTTP to providers; quota; failover WITHIN the route it is given)
```

| Layer | Owns | Does not own |
|---|---|---|
| **Orchestrator** | Choosing sessions and routes: task type, cost, model cards, results ledger, spend book. Writing each session's config, including its plug-ins, limits and services. Spawning, observing and stopping sessions. A2A discovery. | The turn loop. Any wire format. Messaging between modules: that is Synapse, not OverMind. |
| **Harness** | The turn loop. Loading plug-ins from a config. **The chokepoint (§3).** Reporting what loaded. | Choosing providers. Knowing any wire format. |
| **Handler** | Turning harness messages into one provider family's request, and its response back into `{text, tool_calls, finish, usage}`. | Policy. Executing tools. |
| **Model client** | HTTP, auth, per-provider quirks, quota bookkeeping, failover within the route it was handed. | Choosing the route. |

**Call directions and imports.** "May import" lists the only allowed import edges.

| From → To | Interface (proposed names) | May import |
|---|---|---|
| Orchestrator → Harness | `spawn(config: SessionConfig) -> SessionHandle` (one harness process per session, §3); `handle.loaded() -> LoadedManifest`; `handle.events()`; `handle.stop()` | `harness.api` only |
| Harness → Plug-in | the hooks in §2 | plug-ins import `harness.plugin_api` only |
| Harness → Handler | `Handler.turn(messages, tool_schemas, limits) -> Turn` (text, tool calls, finish reason, usage) | `handler.api` only |
| Handler → Model client | `ModelClient.post(route, payload) -> Response`; failover inside the route | `model_client.api` only |
| Orchestrator → Model client | **read-only data**: model cards, quota view (§6) | `model_client.cards`, `model_client.quota_view` |

**Enforced import direction (ASSUMED: a new test; nothing like it exists yet).**
- A unit test parses every module's imports with `ast` and fails on any edge not in the table above.
- The nearest existing check is much weaker. `TestGateBoundaryDidNotMove` lowercases `gate.py`'s source
  and asserts that four words ("mcp", "clientsession", "stdio", "jsonrpc") do not appear in it
  (MEASURED: test_mcp_tools.py:248-257, run by CI's `gate-boundary` job). It is a substring check, not an
  import check; nothing in `tests/` parses imports today.
- The new test replaces convention with a test. It is part of Phase 0 (§9).

**Today, against that picture (MEASURED, cbeaba6).**
- The **harness core** exists:
  - `run_agent` is the loop (agent.py:191).
  - `GatedExecutor.execute` is the only path from a model's tool call to an effect: it decides at
    gate.py:580 and calls the tool at gate.py:596. agent.py:504 is the loop's only call site.
- There is **no handler layer**. The loop reads OpenAI-shaped data itself: `call.get("function")`
  (agent.py:114), the `role: "tool"` message shape (agent.py:135), `result.tool_calls` (agent.py:401).
  The OpenAI request is built inside `ProviderClient.chat` (providers.py:589 onward).
- The **orchestrator** is spread across `lanework.py` (task spec, session recipe, PR), `dispatch_mcp.py`
  (the front door for other agents) and `repo_probe.py` (task-type to check).
- The two are wired by hand in `lanework.run_task`:
  - it builds the gate and executor (lanework.py:635-639);
  - it calls `run_agent` (lanework.py:644);
  - it runs the check itself afterwards (lanework.py:649).

## 2. The plug-in interface

**Shape (ASSUMED proposal).** A plug-in is an object with a declaration and any subset of these hooks.
Every hook receives a `SessionContext` and returns a value the harness acts on. **No hook returns
"proceed anyway".** That is gate.py's first constraint, kept (MEASURED: gate.py:10-14).

| Hook | Called | May return |
|---|---|---|
| `on_session_start(ctx)` | once, before the first turn | `Ok` or `Refuse(reason)`: the session does not start |
| `on_message_in(ctx, msg)` | each message entering the session (task text, inbound channel message) | `Pass`, `Rewrite(msg)`, `Drop(reason)` |
| `on_message_out(ctx, msg)` | each message leaving the session for another party (for example a reply on a channel; today `DispatchService`'s reply, dispatch.py:172) | `Pass`, `Redact(msg)`, `Drop(reason)` |
| `on_model_output(ctx, turn)` | each handler `Turn`, before any tool call in it runs | `Pass`, `Correct(note)` (another turn), `Stop(reason)` |
| `before_tool_call(ctx, call)` | each tool call, in pipeline order | `Allow`, `Deny(reason)`; any `Deny` wins (today's `Gate.decide` rule) |
| `after_tool_call(ctx, call, result)` | each executed call | `Pass`, `Redact(result)` |
| `on_stop(ctx, run)` | once, at the end | a verdict or record (checks, ledger flush) |

**Declaration** (`declares()`):
- the plug-in's name and version;
- `api_version` (integer);
- the **tools** it contributes: name, JSON schema, and an effect function the harness will call. An
  effect function does its I/O only through the **effect primitives** `plugin_api` hands it (run a
  process, make a request, write a file).
  - The harness tracks **two separate states**, per thread (`threading.local`, §3):
    - **`allowed_call`**: the tool call that **this pipeline itself** allowed, set only while that
      call's effect function runs. It is cleared while any hook runs, `after_tool_call` included.
      That rule also covers a fork. A fork's child pipeline runs inside the parent's allowed `fork`
      call (fork.py:398-400, through gate.py:596), but the child's hooks still run with
      `allowed_call` cleared.
    - **`primitive_frame`**: set only while a primitive itself executes.
  - A primitive called when `allowed_call` is clear **is refused** (it raises). Otherwise it runs
    inside its own `primitive_frame`, attributed to that call.
  - Today's `_run`/`_git` helpers (lanework.py:329-342) become the first effect primitives.
  - These are for plug-ins only. Orchestrator code is not a plug-in: it keeps its own helpers for work
    before and after a session, such as `run_task`'s fetch and worktree (lanework.py:623, 630-631);
- the **services** it needs: for example, a Synapse connection or an MCP server it wants launched;
- the **facts** it provides to policies (today's `FactSource`).

**Ordering.** The pipeline runs plug-ins in config order. For `before_tool_call` it keeps today's
semantics exactly (MEASURED: `Gate.decide`, gate.py:501-536):
- only the plug-ins whose policy applies to the call are asked;
- the first denial returns at once ("any denial wins, immediately", gate.py:534);
- a call that no policy applies to is denied (gate.py:502-507).

**SessionConfig** (ASSUMED schema, JSON; a file or an in-memory object):

```json
{
  "schema_version": 1,
  "session_id": "…",
  "handler": {"kind": "openai-chat", "version": 1},
  "route": [{"provider": "groq", "model": "…"}, {"provider": "ollama", "model": "…"}],
  "limits": {"max_steps": 40, "max_tokens_total": 200000, "wall_seconds": 1800},
  "plugins": [
    {"name": "gate", "version": "1", "config": {"policies": ["workspace-confined", "deny-unless-declared"]}},
    {"name": "workspace-tools", "version": "1", "config": {"root": "…", "writable": ["…"]}},
    {"name": "ledger", "version": "1", "config": {}},
    {"name": "check", "version": "1", "config": {"argv": ["python", "-m", "unittest"]}}
  ],
  "workspace": {"repo": "…", "base": "main"}
}
```

**Versioning.**
- The harness refuses a config whose `schema_version` it does not know.
- It refuses a plug-in whose `api_version` major differs from its own. It never runs a partial
  pipeline.
- `plugin_api` changes are versioned like any other crate or package interface: a breaking change
  bumps the major.

**How the orchestrator verifies what loaded (a state read, not an assumption).**
- After loading, the harness writes a **LoadedManifest**: each plug-in's name, version, `api_version`
  and the SHA-256 of its source file, plus the tool names the session exposes.
- `SessionHandle.loaded()` returns it.
- The orchestrator compares it with the config it wrote. Any difference stops the session before the
  first turn.
- The model cannot change the config: only the orchestrator writes it. **So nothing in the harness
  needs to be non-pluggable except the chokepoint**, which is CireSnave's point, accepted by the PM.
  "Required" (for example, "every session loads the gate") is orchestrator policy, checked against the
  manifest. It is not harness policy.

## 3. The chokepoint, and how it is tested

**The guarantee.**
- Every model output and every tool call passes the plug-in pipeline.
- A plug-in cannot act around the pipeline. It has no subprocess, connection or file write of its own.
  Its effects run through effect functions the harness calls, after `before_tool_call` allowed them.
- Without this, the gate is advisory.

**Today (MEASURED, cbeaba6).**
- **Tool calls:** within a run, every model-initiated tool call goes through `GatedExecutor.execute`.
  - The loop: agent.py:504.
  - A fork: agent.py:492, then fork.py:398-400. The fork's own gate inherits the parent's policies,
    built at fork.py:315.
  - A service-initiated call goes through it too: `DispatchService`'s reply (dispatch.py:172). The
    service makes that call, not the model.
  - gate.py:580 decides and gate.py:596 calls the tool. No call site was found that skips it. The
    search was a regex over `src/`, so a dynamic call (`getattr`) is not ruled out.
- **Model text is never gated.** That is gate.py's second constraint: it never reads prose (MEASURED:
  gate.py:16-22). The loop's only check on text is `detect_smuggled_tool_call` (agent.py:420). It can
  ask for a correction or stop the run, but never execute anything.
- **Not gated, by construction, because the model cannot reach them:**
  - the model HTTP call itself (providers.py:506-508);
  - before the session: the requirements-URL fetch (dispatch_mcp.py:86), the clone
    (`prepare_clone`, dispatch_mcp.py:115-127, which runs it at 123), `run_task`'s own fetch, worktree
    add and switch (lanework.py:623, 630-631), and the MCP server spawn (mcp_tools.py:252);
  - after the run: the check, commit, push and PR (lanework.py:649 and 667-682).
- **The weak point:** the chokepoint holds by **convention**, not by structure.
  - `GatedExecutor` keeps a plain `self.tools` dict (gate.py:575).
  - `McpToolSource.call` (mcp_tools.py:390) and the `Workspace` methods are public and directly
    callable.
  - Nothing in `src/` calls them around the executor. But nothing would stop a new plug-in from doing
    so.

**The tests that make it structural (ASSUMED design; written red-first with the plug-in API, §9 Phase 3).**

1. **Static.** A test parses every plug-in module with `ast`. It fails on:
   - any import of `subprocess`, `socket`, `urllib`, `http.client`, `ctypes`, `multiprocessing` or
     `asyncio.subprocess`;
   - any use of `os.system`, `os.popen`, `os.spawn*` or `os.exec*`;
   - a direct import of another plug-in's tool module.
   The scan catches the **direct** routes to I/O. It does not, alone, guarantee "I/O only through the
   primitives": `import asyncio` with `asyncio.create_subprocess_exec`, `os.posix_spawn`, a third-party
   HTTP library, `open(..., "w")`, `pathlib` writes and `__import__("subprocess")` all pass it. Those are
   the dynamic layer's job. The test is new: today's boundary test is a substring check (§1).
2. **Dynamic.** **One session per harness process**: `spawn` (§1) starts a process for each session.
   - The harness installs its `sys.addaudithook` and **arms** it as the first thing the process does,
     before any plug-in module is imported. It **never disarms**: the process ends with the session.
     Python offers no way to remove an audit hook (MEASURED, Python 3.14.7: `sys` has `addaudithook`
     and `audit`, nothing else).
   - The armed flag is process-wide. `allowed_call` and `primitive_frame` are **thread-local**
     (`threading.local`), so every new thread starts with neither set, whatever the interpreter's
     context-inheritance setting.
     - Not `contextvars`. On Python 3.14.7 a new thread does not inherit context by default, so a
       contextvar switch would leave threads unarmed. With `-X thread_inherit_context=1` (the default on
       free-threaded builds), threads inherit context, so they would inherit a frame's exemption. Both
       were measured by the fifth audit of this spec, 2026-10-07.
   - It records `subprocess.Popen`, `socket.connect`, `urllib.Request` and `open` (with a write mode)
     events.
   - **Default deny.** Any such event in the process that is not inside a `primitive_frame` or a
     harness-owned frame stops the session and is reported. Harness-owned frames are the harness's own
     I/O: the model call, MCP server launch, the ledger.
     - This holds whenever the event happens, because the process is armed from before plug-ins load
       until it exits. That covers code run when a plug-in module is imported or `declares()` is
       called, and work deferred to a thread or `atexit`. A new thread starts with neither frame set, so
       its I/O is flagged.
     - **Harness-owned frames never call plug-in code.** For example, the harness installs no logging
       handler a plug-in supplies, so plug-in code never runs inside a harness exemption.
     - ASSUMED: that the harness can mark all of its own I/O as harness-owned (third-party libraries
       it calls included) without false stops. This is to be measured in Phase 3.
   - ASSUMED: that these audit events cover the cases. Python documents them as stdlib audit events;
     this is not yet tested here.
3. **Rogue plug-in fixtures**, each built so that **exactly one** layer catches it. That way, each
   layer's test can be shown to fail without it:
   - **only the dynamic layer catches:**
     - one that starts a process through a helper module outside the plug-in package (the scan sees no
       banned import), from `before_tool_call`. Expected event: `subprocess.Popen`;
     - one that connects to a local listener with `smtplib`, which is not on the ban list and connects
       through `socket.create_connection`, from `on_model_output`. Expected event: `socket.connect`
       (MEASURED 2026-10-07, Python 3.14.7: an audit hook around `smtplib.SMTP` connecting to a local
       listener saw one `socket.connect` and no `subprocess.Popen`. The MCP SDK's `stdio_client` would **not** do, because it
       starts a process and opens no socket, as measured by the third audit of this spec, 2026-10-07);
     - one whose **effect function** (not a hook) writes a file with `open(..., "w")` instead of using a
       primitive, when its allowed tool call runs. Expected event: `open` with a write mode, outside
       any `primitive_frame`;
     - one that starts a `threading.Thread` from `on_session_start`, which opens a socket after the hook
       has returned. Expected event: `socket.connect`, from a thread with no frame;
   - **only the static layer catches:**
     - one that imports another plug-in's tool module and calls a tool that does **no I/O** (it returns
       data, which skips the gate and the ledger). No audit event fires and no primitive is called, so
       only the scan sees it;
   - **only the pipeline catches:**
     - one whose `before_tool_call` runs the tool's own effect function itself, before returning
       `Allow` (approving its own call after doing it). The effect's process primitive is called with
       `allowed_call` clear, so it refuses (§2). Expected: that refusal, raised inside
       `before_tool_call`. The scan sees nothing banned, and the audit hook sees no event, because the
       refused primitive never starts the process;
     - the same plug-in loaded into a **fork's** child pipeline, run from the parent's allowed `fork`
       call. Expected: the same refusal, because the child pipeline starts with `allowed_call` clear.
   Each test names the event or refusal it expects, so a test that passes for the wrong reason is
   caught.
4. **Mutation.** Each layer must be shown necessary:
   - with the audit hook switched off, the dynamic-only fixtures' tests must turn red;
   - with the static scan removed, the static-only fixture's test must turn red;
   - with the primitives' refusal removed, both pipeline-only fixtures' tests must turn red. Without
     the refusal, the process start would run inside a `primitive_frame`, and nothing else stops it;
   - with hooks **not** clearing `allowed_call`, the fork fixture's test must turn red: the child's
     hook would then run with the parent's `fork` call still allowed. The first pipeline-only fixture
     stays green, because no call is allowed in its case;
   - with the armed flag held in a `contextvars` variable instead of process-wide, the thread
     fixture's test must turn red: on Python 3.14's default, the new thread would start unarmed.
   This repo's practice is to prove that a test fails, not just that it passes.

**Honest limit.** In-process Python cannot stop a deliberately hostile plug-in. An audit hook cannot be
removed, but code that means to can get around it, for example with `ctypes`, by patching the harness's
own state, or through calls that raise no audit event. This guarantee is against **accidental** bypass
by plug-in authors, and against a model's influence. Plug-ins are chosen by the orchestrator, never by
the model. That is the same line with-secret draws (WITH-SECRET-DESIGN.md §3).

## 4. File-by-file map (cbeaba6)

Layer key: **O** orchestrator, **H** harness, **P** harness plug-in, **D** handler, **M** model client,
**S** spin-out.

### src/overmind

| File | Today | Goes to | Untangling needed |
|---|---|---|---|
| `agent.py` | the turn loop `run_agent` (191-523) | **H** core | Move the OpenAI shapes into the handler: agent.py:114, 135 and 401, and `normalise_for_echo` from providers.py:359-370. |
| `gate.py` | `Gate`, policies, `GatedExecutor`, and the per-session `Ledger` (315-451) | **H**: `GatedExecutor` becomes the pipeline core; policies become a **P** gate plug-in; `Ledger` becomes a **P** ledger plug-in | `detect_smuggled_tool_call` (627-639) is a wire-format check, so it moves to **D**. |
| `fork.py` | depth-1 sub-session tool | **P** | It imports `agent._tool_result_message`, a private name (fork.py:23). Give it a public harness API. |
| `mcp_tools.py` | MCP server as a tool source, plus FAM/Synapse channel bindings | **P** (MCP plug-in; Synapse plug-in) | `convert_tool` (94-142) produces OpenAI function schemas, which is **D** work. |
| `outcome.py` | `claims_success`, `reconcile` | **P** (check) | `reconcile` has no caller in `src/` (only examples and probe). |
| `dispatch.py` | `classify` (71-94) plus `DispatchService` (124-209), which calls `run_agent` (151) once per inbound message | `classify` is the Synapse **P**'s `on_message_in`. `DispatchService` is **O**: it starts one session per message. | The PM's suspicion that it is a plug-in is **partly refuted**: it calls the pipeline, it isn't called by it (dispatch.py:151). |
| `lanework.py` | task spec `Task` (124-176), `LaneResult` (180-213), path confinement and `WorkspaceConfined` policy (220-322), `Workspace` tools (359-455), schemas and system prompt (458-491), `claims_done` (507-514), the PR recipe (531-590), `build_client` (593-615), `run_task` (618-708), CLI (711-725) | split: **O** (Task, LaneResult, recipe, `build_client`, `run_task`, CLI); **P** (WorkspaceConfined, Workspace tools, claims_done) | The suspicion "loop plus recipe plus check" is **partly refuted**: the loop is not here; `run_task` calls `run_agent` (lanework.py:644). The shared `_run`/`_git` helpers (329-342) become the first effect primitives (§2); `scrubbed_env` and `_clip` (345-352) go with them. The orchestrator side keeps its own copy for work before and after a session. |
| `providers.py` | provider registry `PROVIDERS`, `ProviderClient` (469-707), `RoutedClient` (710-738), `available_providers` (741-749) | **M**, with the request building and response parsing in `ProviderClient.chat` moving to **D** | `RoutedClient` is **partly refuted** as a mix: it only fails over across providers in the order it is given (providers.py:727-738). The choice is the caller's: `Task.provider_keys` (lanework.py:174) and `build_client`. The real mix is `ProviderClient.chat`: HTTP, failover across models, quota recording and the wire format in one method. |
| `quota.py` | free-tier allowance book | **M** (the book), plus a new read view for **O** | Today only the client uses it: it reads the book before choosing a model (`self.quota.blocked`, providers.py:574-578 and 611-626) and writes it on every request (`record_request`, providers.py:655, saving at quota.py:245-247). `build_client` only constructs it (lanework.py:600, 613). Routing does not read it yet. |
| `ledger.py` | per-dispatch record, collection only | **O** (results ledger) | It shares a name with `gate.Ledger`, which is a different thing. Rename one. |
| `dispatch_mcp.py` | MCP front door: `dispatch_lane_task` | **O** | It has its own `_run` (dispatch_mcp.py:94-97), a near-duplicate of lanework's: no `shutil.which`, no `env` argument, a 300 s default instead of 120 s. |
| `repo_probe.py` | infers a repo's check command | **O** (task-type → check) | none |

### Everything else

| Path | Goes to |
|---|---|
| `tests/` | Split along the modules above. `test_gate_fleet.py` and `test_spdx.py` go with tools (below). `test_desktop_grade.py` and `test_paper_summaries.py` go with the probe home. |
| `crates/lane-restart`, `crates/user-request`, `crates/with-secret` | **S**: §5 |
| `tools/spdx.py`, `tools/preflight.py`, `tools/gate_fleet.py`, `.github/workflows/gate-fleet.yml` | **S**: portfolio licence tooling. gate-fleet already compares the copies of `spdx_gate.py` across four repos. |
| `.github/spdx_gate.py` | **stays**: it is OverMind's own deployment of the licence gate, run by ci.yml's `spdx` job (ci.yml:41-52) and checked by the fleet as one of its four copies. Only the tooling that manages the copies moves. |
| `probe/` (34 files), `research/`, `MEASUREMENTS.md` | **S**: a measurements home. ⚠️ Six probes import `overmind` (daily_limits, free_capacity, ledger_context, local_throughput, p1_bench, paper_summaries), so they cannot leave before §8 Q4 is settled. `probe/desktop_grade.py` also runs through the installed `with-secret.exe`, so the measurements home depends on that binary at runtime. |
| `examples/` (5) | **H**/**O** documentation; they move with whatever they demonstrate |
| `RESTART-TOOL-DESIGN.md`, `USER-REPO-APP-FOLDERS-SPEC.md` | with lane-restart |
| `WITH-SECRET-DESIGN.md`, `docs/WITH-SECRET-RUNBOOK.md`, `docs/superpowers/plans/2026-10-01-with-secret.md` | with with-secret |
| `SUBAGENT-FORK-AND-AUDITOR-DESIGN.md`, `docs/superpowers/plans/2026-10-01-fork-and-curate.md` | **H** (the fork plug-in) |
| `LOCAL-MODEL-DISPATCH-DESIGN.md`, `LIGHTBULB-OPENAI-COMPAT-CHECKLIST.md` | **D**/**M** contract docs |
| `DESIGN-PROPOSAL.md`, `README.md` | stay; README is rewritten for the orchestrator |

## 5. The spin-out plan

### 5.1 What depends on what (MEASURED, cbeaba6)

- **with-secret depends on both other crates, by path:** `lane-restart = { path = "../lane-restart" }`
  and `user-request = { path = "../user-request" }` (crates/with-secret/Cargo.toml:12-13).
  - It uses `lane_restart::facts` and `lane_restart::state` (with-secret src/identity.rs:9-10,
    src/main.rs:11).
  - So lane-restart cannot leave alone. Either with-secret follows it, or the shared pieces become
    their own crate first.
- **user-request and lane-restart** have no path dependency on another workspace crate.
- **No Python code or test uses any crate.** All three take `version.workspace = true` from
  Cargo.toml:9.
- **agentlife carries two temporary copies** of lane-restart items (agentlife
  `docs/COPIED-FROM-OVERMIND.md` at `ad558af`, which is agentlife's `origin/main`):
  - `src/claude_proc.rs`: items from `lane_state_writer.rs` and `paths.rs`;
  - `src/launch.rs`: `SESSION_IDENTITY_ENV_VARS`, from lane-restart's `main.rs`, inside the private
    `mod relaunch` (that doc's section "Second copy (M3b)").
  - Both have the same named blocker: *"OverMind extracts a small crate containing these items, and
    CireSnave clears its publish"*. Owner: the OverMind lane. End state: both copies are deleted.
- **agentlife's extraction note asks for more than a move** (agentlife `docs/OVERMIND-EXTRACTION-NOTE.md`;
  it says it is "a design note, not a request to start", and that its `V0.1.md` does not depend on it):
  - `mod relaunch` (private, `crates/lane-restart/src/main.rs:730`) moves into the library as
    `pub mod relaunch`, with about a dozen of its private items made `pub`;
  - three seams, each its own commit: `LaunchSpec` (launching from a roster entry, not a state file),
    `LivenessTiming` (today's hard-coded 20 s / 15 min become parameters) and `launch_and_wait` (launch
    without the kill);
  - no behaviour change to the installed `lane-restart.exe` in the move commit (same tests before and
    after, and the same `describe_dry_run` output);
  - the two real-process tests must run in the library crate's CI.
- **Names are free on crates.io:** `lane-restart`, `user-request`, `with-secret`, `overmind`,
  `lane-state` and `consent` all answer 404, and `serde` answers 200 as a control (MEASURED,
  2026-10-07). The API needs a `User-Agent` header (no email address in it); without one, every name,
  `serde` included, answers 403.
- **How lanes use the binaries:**
  - Every lane's hooks call absolute paths: `C:/Projects/.claude-hooks/lane-restart.exe state <event>`
    and `C:/Projects/.claude-hooks/with-secret.exe hook pre-tool-use|post-tool-use`.
  - They are wired only in the user settings, `C:\Users\cires\.claude\settings.json`: the
    lane-restart and with-secret entries are at lines 70-166 of a `hooks` block that starts at line 60
    (MEASURED 2026-10-07; no project settings file references them).
  - `user-request.exe` is installed there too (2026-10-07), but no hook calls it: it is the person's
    `list`, `revoke` and `repair` command.
  - **Moving source code changes nothing a lane runs.** Lanes break only if a binary's name, location
    or command-line interface changes.

### 5.2 Proposed homes (ASSUMED; §8 Q1 and Q2 are his calls)

| Piece | Proposed home | Why |
|---|---|---|
| lane-state facts: `facts`, `state`, `paths`, the `lane_state_writer` items agentlife copied, and `SESSION_IDENTITY_ENV_VARS` | a small crate, **`lane-state`**, that lives **with lane-restart** (same repo, same version) and is published from there | with-secret and agentlife both need it. Publishing it ends both of agentlife's copies and with-secret's path dependency on lane-restart. |
| lane-restart (its `relaunch` module moved into the library) | **agentlife**, or its own repo | agentlife's README says it "absorbs and supersedes OverMind's lane-restart over time" (agentlife README.md, *Intended scope*). |
| user-request + with-secret | **one repo** (for example `consent`): asking a person, and the secrets that need asking | They are installed together, and with-secret calls user-request's store. One repo means one version number. |
| licence tooling (`tools/` and gate-fleet; **not** OverMind's own `.github/spdx_gate.py`, which stays, §4) | a portfolio tooling repo | not OverMind's job |
| probes, research, MEASUREMENTS.md | a measurements repo | evidence, not product |

### 5.3 Rules every move follows (CLAUDE.md §9; CIRESNAVE-EXPECTATIONS §6.4a, §6.5, §6.6, §6.8)

- **Versions.**
  - One number per project. Each new repo starts its own sequence.
  - §8 Q3 asks whether to continue from OverMind's current 0.6.x, so "which goes with which" stays
    readable for the binaries already installed.
  - Pre-1.0, a breaking change bumps the second number. The PM allocates numbers at gate time.
- **CI and branch protection on day one.**
  - The new repo's first commit carries its CI, and protection requires those checks before any
    second PR.
  - **OverMind's required contexts must change when its last crate leaves.** Today they require `rust
    (ubuntu-latest)` and `rust (windows-latest)` (MEASURED: branch protection, 9 contexts,
    `enforcement_level: non_admins`). The `rust` job has no path filter and runs `cargo test
    --workspace` (ci.yml:127-159).
    - A PR that deletes the job never reports those two contexts, so it would wait on them forever.
    - So the order is: the PR that deletes the job is reviewed and green on everything else; **then the
      PM removes the two contexts from branch protection; then the PR merges.**
  - Related gap: the licence gate's context ("Every source file declares the licence") is **not**
    required today. Each new repo should require its own from day one.
- **Dependencies are crates.io versions**, never `path =` or `git =` across repos.
  - So the order is: publish `lane-state`, then switch with-secret to it by version. Then move
    with-secret.
  - Each publish needs CireSnave's clearance.
- **Provenance.**
  - History moves with the code (`git filter-repo --subdirectory-filter`, or a subtree split), so blame
    survives.
  - Licences (`MIT OR Apache-2.0`, LICENSE files) and SPDX headers go with it.
  - OverMind keeps a one-line pointer to the new home for each piece it gave up.
  - OverMind keeps its own deployment of the licence gate (`.github/spdx_gate.py` and ci.yml's `spdx`
    job); only the tooling that manages the copies moves.
- **No lane may break.**
  - Binary names, the `.claude-hooks` paths and the hook command lines stay the same.
  - A binary is installed from its new repo only after that repo's first release passes the same smoke
    test used for the 0.6.0 install (2026-10-07).
  - The same rename-then-copy install, with the old binary kept as `<name>.<version>.prev` (for
    example `with-secret.exe.0.5.4.prev`).
  - Hooks fail open, and their errors go to `C:/Projects/.lane-state/hook-errors.log`.

### 5.4 Order of moves, and rollback

The spin-outs do not depend on the harness work (§9 phases 3-5), so they come first.

1. **Extract, in place (one OverMind PR, no repo move).**
   - Create the `lane-state` crate in OverMind's workspace: `facts`, `state`, `paths`, the
     `lane_state_writer` items agentlife copied, and `SESSION_IDENTITY_ENV_VARS`.
   - lane-restart and with-secret depend on it by workspace path (still one repo).
   - Move `mod relaunch` into lane-restart's library, per agentlife's note: a pure move, no behaviour
     change, the same tests before and after.
   - Rollback: revert the PR. Nothing is installed differently.
2. **agentlife's three seams** (`LaunchSpec`, `LivenessTiming`, `launch_and_wait`), each its own commit.
   - Only when agentlife needs them: its note says `V0.1.md` does not.
   - Rollback: revert.
3. **Move lane-restart and `lane-state` to their home** (§8 Q1), with history, CI and protection.
   - OverMind keeps its copies until step 5.
   - Rollback: OverMind's copies are still there; the installed binary is untouched.
4. **Publish `lane-state` from its home** (with CireSnave's clearance).
   - agentlife then deletes `src/claude_proc.rs` and its copy in `src/launch.rs`, and depends on the
     published crate.
   - Rollback: yank; agentlife restores its copies from git.
5. **OverMind switches with-secret to the published `lane-state`, and deletes lane-restart and
   `lane-state`.**
   - Only after the new home's first `lane-restart.exe` is installed and smoke-tested.
   - Rollback: reinstall the `.prev` binary, and revert the OverMind PR.
6. **Move user-request and with-secret** to their repo (§8 Q2), with history.
   - The new repo's first release is installed and smoke-tested before OverMind deletes its copies.
   - The same OverMind PR deletes the `rust` job. The PM removes its two required contexts just before
     merging (§5.3).
   - Rollback: reinstall the `.prev` binaries; until that PR merges, OverMind's copy is still there.
7. **Move the licence-tooling sources and the measurements home.**
   - OverMind keeps `.github/spdx_gate.py`.
   - The six probes that import `overmind` wait for §8 Q4.
   - Rollback: revert, since nothing here is installed.

## 6. Where the routing layer goes

Routing belongs to the **orchestrator** (ASSUMED placement; the facts it uses are MEASURED).

| Piece | Today (MEASURED) | In the orchestrator |
|---|---|---|
| **Model cards**: what each model can do and its limits | `PROVIDERS` registry data in providers.py, plus per-model budgets (`resolve_max_tokens`, `resolve_timeout`) | Read-only data the model client publishes, and the orchestrator reads. Kept separate from **agent cards** (A2A: what an agent offers other agents), which are a different thing. |
| **Results ledger**: success and cost per task type and route | `ledger.py` `DispatchRecord`, collection only; nothing reads it for routing (ledger.py docstring) | Read by routing. Renamed so it is not confused with the gate's `Ledger`. |
| **Spend book**: money | does not exist; `quota.py` tracks free-tier allowances, not spend | New. **Paid providers need CireSnave's explicit approval**, per provider and per budget (PM brief). The book refuses a route that would exceed an approved budget. |
| **Choice across providers** | `Task.provider_keys` (lanework.py:174), passed to `build_client` (lanework.py:593-615) | The orchestrator writes `route` into the SessionConfig. |
| **Failover within the route** | `RoutedClient` (providers.py:710-738) tries the given clients in order; `ProviderClient.chat` fails over across models inside a provider | Stays in the **model client**. It executes a route; it does not choose one. |
| **Quota** | `QuotaBook`: only the client uses it. It reads it before choosing a model (providers.py:574-578, 611-626) and writes it on every request (providers.py:655). `build_client` only constructs it (lanework.py:600, 613). | Owned by the model client, plus a new read view for routing (nothing reads it for routing today). |

## 7. The Claude Code question

A Claude Code lane is also a harnessed session (MEASURED for the parts named; the mapping itself is
ASSUMED):

| Concept | OverMind session | Claude Code lane |
|---|---|---|
| Harness | `run_agent` plus plug-ins | Claude Code itself |
| Handler / model client | providers.py | Claude Code's own (Anthropic API) |
| Plug-ins | gate, tools, ledger, MCP… | **hooks** (`settings.json` lines 70-166: `PreToolUse` → `with-secret hook pre-tool-use`, `SessionStart`/`Stop`/… → `lane-restart state`) and **MCP servers** |
| Chokepoint | `GatedExecutor` | Claude Code's `PreToolUse` hook sees each tool call before it runs. with-secret's deny on `env` uses it today (smoke-tested 2026-10-07). |
| Config written by | the orchestrator | today, the person (user `settings.json`); in future, a generated settings file per launch |

**One orchestrator contract covers both:** `spawn(config) → handle`, `handle.loaded()`,
`handle.events()`, `handle.stop()`.
- **An OverMind session** implements it in-process (§2).
- **A Claude Code lane** implements it through **agentlife**, which already owns launching, verifying
  and stopping lanes ("brings them up, down and back", agentlife README):
  - `spawn` is a launch with a generated settings file, whose hooks and MCP servers are the plug-in
    list;
  - `events` are the lane-state files the hooks already write (`C:\Projects\.lane-state`);
  - `stop` is agentlife `down`.
- **agentlife's place:** it is the **session launcher for the Claude Code kind**, called by the
  orchestrator. It does not decide which sessions to start.
- **`loaded()` for a Claude Code lane is an open problem.** No way is known to read back, from a running
  Claude Code, which hooks it actually loaded (ASSUMED; §8 Q5). Until there is one, the orchestrator
  can check what it wrote, but not what took effect.

## 8. Not decided: questions for CireSnave

1. **Where does lane-restart go: agentlife, or a repo of its own?** *Recommend agentlife*, since its
   README already plans to absorb it. `lane-state` goes wherever lane-restart goes, and is published from there.
2. **user-request and with-secret: one repo, or two?** *Recommend one* (one version, installed together,
   with-secret already calls user-request's store).
3. **Version numbers after the move.**
   - **A new repo** (consent; or lane-restart's own repo, if Q1 is answered that way): start fresh at
     0.1.0, or continue from 0.6.x? *Recommend continuing* (0.6.x → 0.7.0 on the move), so an installed
     binary's number still says which release it came from.
   - **If lane-restart goes into agentlife** (Q1's recommendation): agentlife is at 0.2.7 (agentlife
     Cargo.toml at `ad558af`), and one version per project means `lane-restart.exe` would take
     agentlife's number. That reads as a downgrade from 0.6.0. *Recommend* that agentlife's release which
     takes lane-restart in jumps to **0.7.0**, which is still one number for the project. The other
     options are accepting the apparent downgrade, or answering Q1 "its own repo".
4. **Probes that import `overmind`: do they stay with OverMind, or does `overmind` become a published
   Python package they depend on?** *Recommend they stay* until the harness is its own package; only the
   standalone probes and records move now.
5. **Claude Code `loaded()`: acceptable for now to verify the settings file written, not what loaded?**
   *Recommend yes, labelled* as an unverified state in the observer, until a read-back exists.
6. **One program or two for harness and handler?** *Recommend one program* with a small versioned
   handler interface, per the PM's brief, split only when a second handler (Anthropic API, or Claude
   Code hooks) exists.
7. **Language.** gate.py says the language is open (gate.py:30-32, DESIGN-PROPOSAL.md §5). *Recommend
   no change* as part of this split: move first, port later if ever.

## 9. Phased plan

Each phase is its own PR or PRs, red-first where it adds code, reviewed and gated by the PM.

- **Phase 0 (the cheapest safe first step: tests only, nothing moves).**
  - Add the import-direction test (new; §1) with **today's** actual edges as its allow-list. It
    documents the graph and freezes it.
  - No behaviour changes; this is a test every later phase must keep green.
  - The rogue plug-in tests (§3) are **not** in Phase 0: the hooks they act from do not exist until
    Phase 3. They are written red-first in Phase 3, with the plug-in API.
- **Phase 1: extract in place** (§5.4 step 1): the `lane-state` crate, and `relaunch` into
  lane-restart's library.
- **Phase 2: the spin-outs** (§5.4 steps 2-7), one PR per move, each with its rollback.
  - agentlife's blocker clears at step 4, without waiting for any harness work.
- **Phase 3: harness and plug-in API inside OverMind.**
  - `SessionConfig`, `LoadedManifest`, and the hooks of §2.
  - Today's gate, ledger, workspace tools, fork and MCP become plug-ins.
  - lanework becomes an orchestrator recipe that writes a config.
  - The chokepoint tests of §3 are written red-first and turn green: the static scan, the audit hook,
    the rogue fixtures and their mutations.
- **Phase 4: the handler.** OpenAI shapes leave `agent.py` and `providers.py` for `handler/openai_chat`.
  The model client keeps HTTP, quota and failover within a route.
- **Phase 5: routing.** Route choice moves to the orchestrator: model cards, results ledger, the quota
  read view, and the spend book with its approval rule.

---

*This document is the draft; the PR that adds it changes nothing else.*
