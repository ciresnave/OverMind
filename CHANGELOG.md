# Changelog

## 0.14.21 (A3 P3.3: the chokepoint, 2026-10-28; provisional number)

- `overmind.harness.chokepoint` (the dynamic layer: one audit hook, armed once and never disarmed, default deny for process, network, file-write and `ctypes.dlopen` events outside a `primitive_frame` or a harness-owned frame; a violation is reported and the process exits with code 4), `static_scan` (the static layer: the direct routes to I/O in a plug-in module, imports of another plug-in's tool module, and of harness internals) and, in `primitives`, `harness_owned()` / `mark_thread_harness_owned()` (the harness's own I/O; `cleared()` clears the mark so plug-in code never runs under it). `session_main` arms the hook first, before any registry or plug-in is imported, with bytecode writing off; `SessionHandle.violations` and `SessionViolation` report it. Nothing else calls `spawn` yet, so only a spawned session is affected.
- Measured first (MEASUREMENTS section 38, Windows / 3.15.0 only): an audit hook cannot be removed; the events the spec names are the ones the routes raise; harness-owned marking is feasible for a real MCP session but is not built for MCP yet (P3.4). Review-found and fixed: events an accident could reach with no violation (`sqlite3.connect`, `socket.sendto`, `os.startfile`, `webbrowser.open`, `os.kill`, `os.utime`), `import os.path` not binding `os` in the scan, BOM/`# coding:` source read wrongly, a `BaseException` from the report skipping the stop, `exec*`/`spawn*` matched by prefix, `import overmind.harness` reaching every internal, and a session that passed `loaded()` then violated (the handle now exposes `violations` and `StopResult.violations`; exit code 4 with no report is a `SessionViolation`). Accepted false stops and unmeasured event names are listed in `chokepoint.py` and MEASUREMENTS section 38. Tests: the spec's rogue plug-in fixtures, each caught by exactly one layer (4 dynamic-only, 1 static-only, 2 pipeline-only, in child processes), and the spec's five mutations each turn exactly the predicted test red. **Not built:** the fixture whose `on_model_output` calls a declared service directly (service primitives do not exist yet, P3.4). Not closed: a plug-in thread can still read fd 0 and consume a `stop` command, and code that reads the nonce from the child's frame can forge an event; both need a hostile plug-in, outside the spec's stated limit.

## 0.14.20 (A3 P3.2b: one harness process per session, 2026-10-28; provisional number)

- `overmind.harness.session` (`spawn(config) -> SessionHandle` with `loaded()`, `events()`, `stop()`), `session_main` (the child: reads the config from stdin, builds and starts the pipeline, reports on a protocol channel that is a dup of the real stdout taken before anything runs, so a plug-in cannot print or forge an event) and `registry` (the default plug-in registry, empty until P3.4). The child gets an allowlisted environment, not the parent's; `stop()` kills a wedged child's whole tree. `LoadedManifest` gains `to_dict`/`from_dict`; New `proctree.ProcessTree` contains a process and its descendants (a Windows Job Object with kill-on-close made right after start, `killpg` on POSIX), used by `spawn` and by `run_process`: a clean `stop()` no longer leaves a plug-in's child running, which `taskkill /T` cannot reach once the parent has exited. Every protocol event carries a per-session nonce and the parent drops the rest (`rejected_events`); an unserialisable plug-in record costs only that plug-in its record; a session that never reports is killed when `loaded()` times out. Not closed (P3.3): stdin is shared, so a plug-in thread can read fd 0 and consume the stop command, and code that reads the nonce out of the child's frame can still forge. Nothing calls `spawn` yet and the audit hook is not armed (P3.3 arms it at the top of `session_main.main`), so no behaviour changes. Plan P3.2b.

## 0.14.19 (A3 P3.2: the plug-in pipeline, 2026-10-28; provisional number)

- `overmind.harness.plugin_api` (the seven hooks and their closed result types; `NoOpinion` added for `before_tool_call`), `primitives` (thread-local `allowed_call` and `primitive_frame`; `write_file` and `run_process` refuse unless a call is allowed on that thread), `pipeline` (`Pipeline`, `load_pipeline`: config order, `Gate.decide` semantics for `before_tool_call`, hooks fail closed, the whole pipeline refused for a missing/mismatched plug-in or a different `api_version`) and `gate_plugin` (today's `Gate` as the first plug-in, decisions passed through unchanged). Nothing calls the pipeline yet (`run_agent` still uses `GatedExecutor`), so no behaviour changes. Plan P3.2. `declares()` is called once, with the allowance cleared, and snapshotted; every hook gets its own copy of the call and the effect runs on a pristine copy (what was decided is what executes); `run_process` requires an explicit `env` and kills the whole process tree on timeout; the manifest hash covers every class file in the plug-in's MRO. **Not in this piece:** service primitives (so the spec's first pipeline-only fixture, a model-output hook calling a declared service, is not built), hook timeouts, and binding a primitive's arguments to the allowed call's (an allowed effect may write any path or run any argv it is given).

## 0.14.18 (A3 P3.1: SessionConfig and LoadedManifest, 2026-10-28; provisional number)

- `overmind.harness.config` (`SessionConfig`: strict, refuses an unknown `schema_version`, unknown or missing fields and wrong types, `true` is not a number) and `overmind.harness.manifest` (`LoadedManifest`, `source_sha256`, `compare`: every difference, not the first). Pure data, stdlib only; nothing calls them yet, so no behaviour changes. `tests/test_import_direction.py` now scans the `harness/` subpackage too. Plan: `docs/superpowers/plans/2026-10-28-phase3-harness.md` P3.1.

## 0.14.17 (A3 plan and the sandbox policy, 2026-10-28; provisional number)

- docs only: `DESIGN-PROPOSAL.md` 7.5 records CireSnave's 2026-10-28 ruling on section 7's questions 1-3 (agent-generated commands only in a sandbox; anything with effects outside one only from a pre-defined list), with what exists today (AntSpawn is a real crate with two certified launchers; the sandbox seam is plan step P3.7). `docs/superpowers/plans/2026-10-28-phase3-harness.md` plans ROADMAP A3 (his 2026-10-25 "Build the harness/plug-in API") as PRs P3.1-P3.7; nothing is built. ROADMAP rows A3, C2, D9 updated. No code change.

## 0.14.16 (CireSnave-asleep rulings recorded, 2026-10-25; provisional number)

- docs only: `ROADMAP.md` section 6 records the PM's rulings of 2026-10-25 on 11 of the 15 audit calls, A10 and D9 only for their `CLAUDE.md` half (A3 defer, B3 no-go, D6 free-tier retry first, E1 read docs, E4 16k, E5 no, E6 skip, E7 to synapse, F1 MIT OR Apache-2.0 in agentlife); B7, C2(a,b), D5, A5 stay CireSnave's, as do D5 and the dispatch-wiring half of D9. `MEASUREMENTS.md` section 37 holds the E1 docs reading (Cloudflare and OpenRouter publish a daily number; Groq, Gemini, NVIDIA: none readable). agentlife#175 opened for F1. No code change.

## 0.14.15 (docs: statements the code contradicts, 2026-10-24; provisional number)

- docs only, from the spec-to-roadmap audit (ROADMAP 6.H, OverMind#162): `ARCHITECTURE-LAYERS-SPEC.md`, `DESIGN-PROPOSAL.md` (status line and 7.4 `docs_only`, built), `RESTART-TOOL-DESIGN.md` (built and moved; the carried-over flags; installed), `LOCAL-MODEL-DISPATCH-DESIGN.md` and the Lightbulb checklist (merged in #91, pre-#151 line numbers), `README.md` (nine backends), `MEASUREMENTS.md` (dated addenda, results unchanged), `docs/LANE-LAUNCH-POLICY.md`, `docs/PILOT-OVERMIND-LANE-CONFIG.md`, and a note on the fork-and-curate plan. Each correction cites its code. Not changed, because the code cannot settle them from here: RESTART-TOOL-DESIGN 11.1 (the re-run hold) and 12.11 (first fetched-approval relaunch). No code change.

## 0.14.14 (spec-to-roadmap audit, 2026-10-24; provisional number)

- docs only: `ROADMAP.md` section 6 adds every open, held or done item found in the 18 spec/design docs that the roadmap did not carry (55 rows; 15 need a CireSnave call). Corrects the M8 row (phases 1-3 merged, agentlife#123/#124/#126) and the open-PRs row (#161). No code change; no new row is started here.

## 0.14.13 (ROADMAP row 6b steps 2-3: vault residue gone, 2026-10-24; provisional number)

- docs only: `WITH-SECRET-REQUESTER-ALLOWLIST-DESIGN.md` removed here; it moved byte-identical to `docs/` in `ciresnave/consent` (consent#20). Sweep of `git ls-files` for `with-secret`/`user-request`: what remains are callers (`probe/`, `src/overmind/providers.py`, their tests, README) and `ARCHITECTURE-LAYERS-SPEC.md`, which describes the move itself. No code change.

## 0.14.12 (ROADMAP row 6b: finish the vault move, 2026-10-24)

- docs only: `ROADMAP.md` row 6b orders the rest of step 6 (the code already lives in `ciresnave/consent`; what remains here is the requester-allowlist design doc and its references). No code change.

## 0.14.11 (advisory CodeRipper portfolio check, 2026-10-24)

- CI: `portfolio` job calls `ciresnave/coderipper`'s reusable `portfolio.yml` pinned to `c665210`. Advisory only (CireSnave 2026-10-23): not a required check, no rule silenced. Baseline findings become ROADMAP rows, one per failing rule.

## 0.14.10 (Synapse cutover G5/G6 design note, probe MCP SDK 1.32.0, 2026-10-22)

- `docs/SYNAPSE-CUTOVER-G5-G6-DESIGN.md` (design only, nothing run): G5/G6 are mostly built in agentlife (relay as `agentlife-launcher`, pilot dev-channels records); remaining gap is tests against the real approval records. PM ruled: keep the relay, no replies, one scratch-home send first.
- probe: `@modelcontextprotocol/sdk` 1.31.0 -> 1.32.0 (folds dependabot #156; probe only, no runtime change).

## 0.14.9 (pyright in CI + compile-time audit group5, 2026-10-19)

- **CI: a `types` job runs pyright** (`[tool.pyright]` in `pyproject.toml`): `basic` over `src`, `strict` for `gate.py`. Baseline before this change was 30 errors in 7 files; all fixed with annotations, one real typing gap (`StaticFacts` keyed by a tuple but typed `dict[str, ...]`), and three `pyright: ignore` on `agent.py`'s lazily imported fork names (bound by the `fork is not None` guard, which pyright cannot see). No behaviour change.
- **gate: `requires_vouched_sender` is now a required `Policy` member**, read directly and checked when a `Gate` is built (`TypeError` for a missing, misspelt or non-bool flag). It used to be `getattr(policy, ..., False)`, so a per-sender policy that misspelt it silently skipped the unvouched-sender refusal. Every in-repo policy declares it; a custom policy must too (**breaking for out-of-tree policies**).
- **providers: only a local provider may take its address from an env var** (`Provider.__post_init__`), the address must be an http(s) URL with a host and no `user:pass`, and a local provider's real key is sent only over https or to loopback, never in cleartext to an env-supplied host. Severity: latent for the shipped table (`rig` ignores its key), real for anyone who sets `OVERMIND_RIG_API_KEY` and points `OVERMIND_RIG_BASE_URL` at a remote http host.
- **lanework: `Task.from_json` type-checks every field** (`fetch: "false"` was truthy) and refuses a `repo` or `base` that is empty, option-shaped (leading `-`) or contains whitespace or control characters; `git worktree add` now ends options with `--` before the base. Argument-injection class; the task file is operator-written, so severity is low.

## 0.14.8

- `docs/LAUNCH-ON-MESSAGE-DESIGN.md` (design only, nothing run): OverMind's part in replacing claude-peers with Synapse. Finding: OverMind runs no subscriber (the AgentLife `agentlife-launcher` service polls and applies the policy). Message shapes, needs from Synapse/AgentLife/CireSnave, 12 failure modes (F4: a launched lane can lose its first pushes), 5 options with a recommendation, 8 pilot measures. `LANE-LAUNCH-POLICY.md` section 1 corrected: the bearer role is `agentlife-launcher`, and Synapse's `epoch` is built.

## 0.14.7

- `probe/package-lock.json` committed (npm) and no longer ignored, so `probe/`'s one dependency (`@modelcontextprotocol/sdk` 1.31.0) resolves reproducibly (CodeRipper SUP-001). No code change.

## 0.14.6

- `rig` provider (local, address from `OVERMIND_RIG_BASE_URL`, off until set) and `Provider.local` replacing the `key == "ollama"` checks; `probe/rig_probe.py` (context ladder with clip detection, tool-call verdict, decode rate) for the multi-GPU box. Measured: Ollama 0.40.4 silently clips an over-context prompt and reports the clipped count (`LOCAL-MODEL-DISPATCH-DESIGN.md` 5a). `probe/daily_limits.py` and `probe/p1_bench.py` use `.local` instead of the `ollama` name (a `rig` task gets the local timeout and token budget). No behaviour change for existing providers.

## 0.14.5 (docs only)

- `docs/PILOT-OVERMIND-LANE-CONFIG.md`: the exact `overmind.mcp.json` and `overmind.launch.json` `launch_args` for the Synapse pilot (from `synapse@519bd52` runbook section 3), plus a code read of `lane-restart` (`agentlife@e609da5`) answering the runbook's open question: `--mcp-config` and the channels flag are on the carry-over allowlist, but a restart rebuilds the spec from the state file, so a spec-only edit is lost and a hand launch (whose `SessionStart` re-records `launch_args`) is required; the runbook's rollback has the same gap. Also flags `RESTART-TOOL-DESIGN.md` line 241 as stale. Proposal only; nothing was run or written to `.lane-state`.
- `ROADMAP.md` section 4: the stale row "Issue #110 and the `git grep -c` over-deny" now says #110 is done (agentlife#115, 0.16.5, merged 2026-10-16T18:20Z) and that the over-deny has no record.
- This version also covers the already-merged vault-allowlist design (#149, `WITH-SECRET-REQUESTER-ALLOWLIST-DESIGN.md`, now `docs/` in `ciresnave/consent`), which merged without a version bump.

## 0.14.4 (docs only)

- `docs/LANE-LAUNCH-POLICY.md` section 3: `model-not-pinned` is `refused`, not `failed` (PM ruling 2026-10-16: terminal, retrying cannot help; AgentLife already answers `refused`). The old text's alarm (three attempts then `unreachable`) goes with `failed`; no rule replaces it; section 6 now lists the missing alarm sink as an open gap owned by the AgentLife service. New section 3a maps every outcome of AgentLife's `lane-restart start --json` (`started|already-running|refused|failed|dry-run`) to section 3, including `disk-floor`, which `main.rs` emits directly, each row with its agentlife ref (`1df1b98`); rows AgentLife emits that the policy does not name are marked. Spec only; nothing was run.

## 0.14.3 (docs only)

- `docs/LANE-LAUNCH-POLICY.md` section 6 and rule 7: board 216 is ruled. CireSnave, verbatim: "216: yes"; the breakdown below is the PM's record of that ruling. Soft-stopped roles (idle/waiting shutdown, task-boundary restart, crash) are launchable on mail; hard stop, hold and unclassified are `refused: no-grant`; conditions unchanged (spec only, sonnet, allowlisted signed senders, caps, ledger). The last-stop classification input is not built, so the service still refuses every role until it exists. Spec only; nothing was run.

## 0.14.2 (docs only)

- `docs/SOAK-RESTARTTEST-END-REPORT.md` and the ROADMAP rows: the restarttest soak (board 175) ended 2026-10-15T18:52Z. 435 sent, 435 seen, 0 tool_use, 0 compactions, daemon unchanged. Not a clean soak: one `auth_failure` line (the synapse lane's own curl, their statement), the compaction bound is untested and the weekly-limit delta is unmeasured.

## 0.14.1 (docs only)

- `docs/LANE-LAUNCH-POLICY.md`: OverMind's policy for lane launch on message (Synapse signals, OverMind decides, AgentLife's service implements): decision order, dedupe key `(signal_id, attempt)`, model pin, rate caps, named refusals, durable outcome store, the tests the service must pass, and what is not decided (consent for an unattended launch; `launch_wait` unmeasured). Spec only; nothing was run.

## 0.14.0

- **Breaking: provider tokens are read from the process environment only, and a missing one is an error.** CireSnave moved every provider token out of the Windows User environment into the `with-secret` vault (2026-10-15), so a process holds one only when launched `with-secret NAME --reason "..." -- <command>` (one secret per invocation). `read_secret` no longer consults the registry. New `require_secret(name)` and `SecretNotSet` (a `ProviderError`, so `RoutedClient` skips the provider): the message is `NAME not set: run via with-secret NAME --reason "..." -- <command>`.
- `ProviderClient` no longer sends the placeholder bearer token `unused` for a hosted provider: no token, no request. Only `ollama` (a local server that ignores the header) keeps it.
- **One provider = one secret.** `probe/provider_probe.py` takes the provider(s) to probe (`groq|nvidia|openrouter|google|cloudflare`; none is a usage error, not "all"), reads only their tokens and exits 2 naming every missing one before any request. `probe/compliance_api.py` and `probe/nvidia_entitlement.py` exit 2 with the named error and no longer import `winreg`. `probe/daily_limits.py` reports the named error in its status. `CLOUDFLARE_ACCOUNT_ID` is read from the environment, else discovered through the API (it is not a secret).
- README: how to run each probe through the vault; `tests/test_secrets.py`; `tests/test_providers.py` and `tests/test_quota.py` set dummy tokens in `setUpModule`.
- Not measured: nesting two `with-secret` calls in one command (needs two Windows Hello approvals). From the source only: the child inherits the parent environment plus the one secret, so the outer secret survives into an inner `with-secret`.

## 0.13.6

- **Breaking for anyone who invokes these from OverMind: `tools/spdx.py`, `tools/preflight.py`, `tools/gate_fleet.py`, `tests/test_spdx.py`, `tests/test_gate_fleet.py` and `.github/workflows/gate-fleet.yml` are deleted** (OverMind split step 7a, PM ruling 2026-10-14). They live in `ThinkersJournal/licence-tools` 0.1.0 (`main` `3ce26593`) with history kept; command lines are unchanged there. The daily six-way fleet comparison now runs from that repository.
- `.github/spdx_gate.py` stays (this repository's own deployment). Its `COPYRIGHT_EXPECTED` loses the four basenames of the deleted files (a constant, which the fleet does not compare).
- `probe/p1_bench.py` loses its fourth task, `max-of-nothing` (it planted a bug in `tools/gate_fleet.py`). Recorded results in MEASUREMENTS sections 27, 30 and 32 stand; they were taken at earlier commits.
- New `tests/test_p1_bench_tasks_exist.py` so a bench task whose file left the tree fails a test instead of crashing the probe.

## 0.13.5

- CI hygiene: `.github/CODEOWNERS` (`* @ciresnave`) added, satisfying WSP-001 (portfolio triage, PM ruling 2026-10-14). No behaviour change.

## 0.13.4

- Docs only: `ROADMAP.md` added (in-flight work, fleet-gate coverage, the OverMind split with each step marked RULED or UNRULED, ties to `PORTFOLIO-ROADMAP.md`). No code change.

## 0.13.3

- `gate-fleet.yml` now compares `consent`'s `.github/spdx_gate.py` too (the fifth deployment; the trigger `gate_fleet.py` names). The wiring control, the planted-divergence control and the final comparison all take five repositories. Measured before the change: consent `62b1788` agrees with OverMind `ca9e208`, kiss-ref, vulkane and synapse at their `origin/main` (25 functions, 42 case-tuples each). `tests/test_gate_fleet.py` gains `TestWorkflowCoversTheFleet`, which reads the workflow so a deployment missing from it fails a test. No tooling moved.

## 0.13.2

- CI only: every GitHub Action in `ci.yml` and `gate-fleet.yml` is pinned to a release commit SHA (PM pin patch 2026-10-14). No code change.

## 0.13.1

- `ARCHITECTURE-LAYERS-SPEC.md` 5.4: steps 5 and 6 are marked done, with the merged SHAs (`57fae94`, `56764a2`; consent `45a6856`). Docs only, no code change.

## 0.13.0

- **Breaking: `crates/user-request` and `crates/with-secret` are deleted from this repository** (ARCHITECTURE-LAYERS-SPEC 5.4 step 6). Both live in [`ciresnave/consent`](https://github.com/ciresnave/consent) now (one repo, two crates, history kept); `ciresnave/consent`@`45a6856` (0.12.0) was verified blob-identical to this repo's `57fae94` apart from the two crate manifests (the root workspace files differ too; they are deleted, below), `user-request` 0.12.0 is published on crates.io, and `C:\Projects\.claude-hooks\user-request.exe` and `with-secret.exe` were installed from that build and smoke-tested (the `.pre-0.12.0.prev` binaries are kept). `with-secret` is not published. Anyone who depended on either crate by path takes `user-request` from crates.io instead.
- Also gone, with the code they describe: `WITH-SECRET-DESIGN.md`, `docs/WITH-SECRET-RUNBOOK.md` and `docs/superpowers/plans/2026-10-01-with-secret.md` (byte-identical copies are in `ciresnave/consent`); the root `Cargo.toml`/`Cargo.lock` (no Rust remains here); the CI `rust` job; `tests/test_version_lockstep.py` (it only compared pyproject with the Cargo workspace, which is gone: `pyproject.toml` is now the single version).
- `probe/desktop_grade.py` reads the runbook at its pinned `REF` (`3bf513f`), so it is unaffected.
- README points at the new home.
- **Required CI contexts:** `rust (ubuntu-latest)` and `rust (windows-latest)` no longer exist; the PM removes them from branch protection just before merging this PR.

## 0.12.0

- **Breaking: `crates/lane-restart` and `crates/lane-state` are deleted from this workspace** (ARCHITECTURE-LAYERS-SPEC 5.4 step 5). Both live in the agentlife repo now (two crates); `lane-restart.exe` 0.12.15 is installed from there and was smoke-tested by a real `lane-restart start`. Anyone who depended on either crate by path takes `lane-state` from crates.io instead.
- `with-secret` takes `lane-state` 0.12.15 from crates.io (`[workspace.dependencies] lane-state = "0.12.15"`, no path). It carries its own number, not this project's, so `tests/test_version_lockstep.py` no longer pins it to the workspace version; it now asserts the dependency has no `path` and no workspace member is `lane-state`.
- Workspace members are `user-request` and `with-secret`. Step 6 (move those two and delete the `rust` job) is separate.
- Checked on this branch: `cargo fmt --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --workspace`, and the Python suite (556 tests, 4 skipped), on Windows only.

## 0.11.2

- `pyproject.toml` (the Python `overmind` package) was 0.7.2 while the crates were 0.11.1; it is now in lockstep, and `tests/test_version_lockstep.py` fails CI when pyproject, `[workspace.package] version`, the `lane-state` pin or a member's `version.workspace` differ (one number per project). No code change.
- probe: `@modelcontextprotocol/sdk` 1.27.1 -> 1.31.0 (dependabot #126; `probe/` is a dev-only TypeScript harness, nothing shipped depends on it). CI never runs the TS probes, so the bump was measured by hand: `probe/ts-client.ts` fallback and specific modes against `probe/fam-push-server.ts` under bun at 1.27.1 and at 1.31.0 give identical results (fallback surfaces 2 notifications, the control and the subject; specific surfaces 1). `ts-client.ts` no longer hard-codes the SDK version in its header; `MEASUREMENTS.md` rows 42-43 stay at 1.27.1, the version they were measured at. No code change.

## 0.11.1

- `lane-state` is publish-ready (not published): crate metadata (readme, keywords, categories, `rust-version = "1.95"`, both licence texts and a README with the honest scope inside the crate), and `lane-state` is a `[workspace.dependencies]` entry carrying both `version` and `path`, so `lane-restart` and `with-secret` name it by version once packaged. `tests/publish_ready.rs` pins all of it. No code change.

## 0.11.0

- lane-restart: a restart is now a stop then a start, each usable alone (spec 8b; agentlife's extraction note, three seams). New public API, `lane-restart.exe` unchanged:
  - `launch::LaunchSpec` (a lane described by role, name, cwd, permission mode, remote control and launch flags, `From<&LaneState>`) with `launch_argv` and `prepare_launch` (every check before anything is killed or spawned); a restore has a roster entry, not a state file.
  - `launch::LivenessTiming` (`Default` is the old 20 s / 15 min / 1 s) and `launch::wait_for_liveness` (the previous session is optional).
  - `stop::stop_lane` (`lane-stop`: the verified kill, the settle wait, the clock margin) and `launch::launch_and_wait` (`lane-start`: spawn and wait, no kill); `relaunch::kill_and_relaunch` is `kill_and_relaunch_with`: checks, `stop_lane`, spawn, `wait_for_liveness`. The old entry points (`claude_argv`, `spawn_relaunch`, `wait_for_relaunch_liveness`, `kill_and_relaunch`) remain, delegating, with their tests unedited.
- Fix: the clock margin was subtracted in signed arithmetic, so a clock within five seconds of the epoch produced a start-time bound near `u64::MAX`; it is now `stop::launched_after`.

## 0.10.1

- lane-restart: `mod relaunch` moves out of the binary into the library as `pub mod relaunch` (PR #132), with its tests, so agentlife can use it instead of copying it. A pure move: the text is unchanged apart from layout and crate paths. Newly `pub`: `claude_argv`, `extra_launch_args`, `has_dev_channels_flag`, `first_unsafe_argument`, `strip_session_identity_env`, `host_wrapped_argv`, `spawn_relaunch`, `wait_for_relaunch_liveness`; `lane_restart::state_dir()` is the fixed state directory the binary already used. No behaviour change to `lane-restart.exe`.

## 0.10.0

- user-request: a new request kind, `RestorePlan` (agentlife; board 150, CireSnave 2026-10-08, verbatim: "One-shot."). It is a ONE-USE approval: no duration, never forever, spent by `Store::spend_one_use` before the restore runs (a second use is refused; a crash mid-run needs a fresh approval). It binds exactly one frozen plan: the subject is `restore_plan_subject(hash)` and the pending request's `bound_hash` must be that hash, so a changed plan voids it without asking. Restored-after-restart requests re-prompt and Cancel closes (as #4). The Hello prompt shows who, what (the plan) and `one use`. One plan, one use: while an approval for a plan is unspent no second request or approval for it is accepted. A plan hash is lowercase only (the store compares subjects case-folded). It does not cover other plans, wider permission modes, stop/park or later wakes. `user-request list` shows an unspent one-use approval as `ONE USE, not yet spent`, never as FOREVER.
- Install `user-request`, `with-secret` and `lane-restart` together from this release: a binary built before `RestorePlan` cannot read a store that holds one, so it sets that grants file aside and treats the store as untrusted (the existing newer-schema rule: revoking still works, nothing is overwritten).
- Store files are now flushed to disk before the rename that publishes them (`write_atomic`), so a spent one-use approval survives a power loss.
- **Breaking:** `Grant` gained `OneUse` and `MaxGrant` gained `OneUse`, so an exhaustive `match` on either must handle it; `KindId::ALL` is now `[KindId; 3]`; `Grant::OneUse` is within no existing kind's maximum, and `Grant::within` for a `RestorePlan` accepts nothing but `OneUse`. Nothing outside this workspace matches on them.

## 0.9.1

- New crate `lane-state` (spec 5.4 step 1, PR #130): `facts`, `state`, `paths` and the pure claude-process items `lane_state_writer` shared with agentlife (`claude_proc`, including `SESSION_IDENTITY_ENV_VARS`), moved out of `lane-restart` with their tests. A pure move: `lane-restart` re-exports the old paths, and `with-secret` now depends on `lane-state` instead of `lane-restart`. Visibility widened: `ModelField::into_string`, `transcript_project_dir`, `facts::exe_matches`, `SESSION_IDENTITY_ENV_VARS`. Not published.
- This bump also covers the docs-only and test-only PRs that rode it: #128 (the approved architecture spec) and #129 (the Python import-direction test).

## 0.9.0

- user-request #5: a `LaneDialogBypass` grant is for ONE lane's ONE dialog (PM ruling 2026-10-08, per-lane only). The subject is `lane_dialog_subject(lane, dialog)`, shown whole in the Hello prompt; a grant for lane X never answers lane Y's dialog.
- **Breaking:** a `LaneDialogBypass` request, approval or pending request whose subject is not that shape (the old bare handler id) is refused (`Request::check_subject`, `Store::add`, `Store::submit`), and `Store::find` never matches one. Nothing outside this workspace's tests used the kind yet.

## 0.8.0

- user-request: durable pending requests (`submit`, `begin_answer`, `withdraw`, `bound_hash`; PR #125).
- **Breaking:** `store::Attempt` gained the public field `pending: Option<String>`, so code that builds an `Attempt` by struct literal must set it (nothing in this workspace does).
