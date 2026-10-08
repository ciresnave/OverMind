# Changelog

## Unreleased

- `pyproject.toml` (the Python `overmind` package) was 0.7.2 while the crates were 0.11.1; it is now in lockstep, and `tests/test_version_lockstep.py` fails CI when pyproject, `[workspace.package] version`, the `lane-state` pin or a member's `version.workspace` differ (one number per project). No code change.

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
