# Changelog

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
