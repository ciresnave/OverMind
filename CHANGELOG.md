# Changelog

## 0.9.0

- user-request #5: a `LaneDialogBypass` grant is for ONE lane's ONE dialog (PM ruling 2026-10-08, per-lane only). The subject is `lane_dialog_subject(lane, dialog)`, shown whole in the Hello prompt; a grant for lane X never answers lane Y's dialog.
- **Breaking:** a `LaneDialogBypass` request, approval or pending request whose subject is not that shape (the old bare handler id) is refused (`Request::check_subject`, `Store::add`, `Store::submit`), and `Store::find` never matches one. Nothing outside this workspace's tests used the kind yet.

## 0.8.0

- user-request: durable pending requests (`submit`, `begin_answer`, `withdraw`, `bound_hash`; PR #125).
- **Breaking:** `store::Attempt` gained the public field `pending: Option<String>`, so code that builds an `Attempt` by struct literal must set it (nothing in this workspace does).
