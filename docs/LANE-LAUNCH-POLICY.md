# Lane launch on message: OverMind's policy

**Status: spec, not code. Nothing here was run.** It fixes the DECISION half of the split in the PM notices of
2026-10-15: Synapse detects and signals (`ciresnave/synapse` #172, section C); **OverMind owns the policy**
(this file); **AgentLife is assigned the always-on service** that implements it in Rust under Task Scheduler
(PM notice 2026-10-15 quoting CireSnave 5.1: nothing non-Rust permanent; Synapse's spec still lists the
service owner as open) and the mechanism. This repository runs no poller, and **no poller session runs on a
restarting lane**: a lane restarts at every task boundary and may itself be the role that is down.

**What this spec REQUIRES of AgentLife and does not find on `origin/main`:** a start-one-lane-by-role entry
point that takes the role and an explicit model, applies the existing launcher checks (`docs/LAUNCHER.md`:
role regex, `;`/control-character refusal, model pin, memory floor, `restore.lock`, never `--resume`) and
reports one of the section 3 results, and reports the role's last-stop class (section 6). LAUNCHER.md today executes plans through `restore`, after a person's
approval. Everything below that says "the mechanism" means that entry point.

## 1. Inputs

From Synapse `GET /v1/waiting?wait=30` (bearer session for role `overmind`, which must be in the daemon's
`launcher_roles`): per role `{role, signal_id = "<role>:<oldest queued global id>", count,
oldest_enqueued_at, attempt}`. Nothing else is trusted: no model, no body, no token (the service must not
read one if it ever appears). Per Synapse's design (spec C4a and 7.2, not yet built) `attempt` resets on a
`launch-clear` and on a daemon restart, and the
`signal_id` stays the same while the oldest message stays queued, so the same `(signal_id, attempt)` can be a
genuinely new request. **Requested of Synapse:** an `epoch` field (a counter bumped by a clear or restart) in
the signal. Until it exists, section 4's time window is what separates a replay from a new request.

## 2. Decision, in this order (first refusal wins; every refusal has a named reason)

| # | Rule | Result |
|---|---|---|
| 1 | The store is readable and writable (section 4) | else `failed: store-unavailable` (fail closed) |
| 2 | `role` matches `[A-Za-z0-9_-]{1,64}` | else `refused: invalid-role` |
| 3 | `role` is a known lane: it has existed in AgentLife's registry or plan under the same name | else `refused: unknown-role` (a sender-chosen string is never a launch) |
| 4 | **Replay check**: a stored row WHOSE MECHANISM CALL WAS MADE with the same `(signal_id, attempt, epoch)`, younger than the replay window (section 4) | answer the recorded result; do nothing else; count nothing. A stored *refusal* is never replayed: it is re-evaluated (it is deterministic, and replaying it would re-impose a refusal the service itself just cleared) |
| 5 | No more than 3 consecutive spawns (`started`, `awaiting-dialog`) for this role without the role having been seen online since (section 4 rule R7 keeps that observation) | else `refused: no-claim` (cleared when the role is seen online, section 4 rule 3) |
| 6 | Rate caps: **spawns** in the last hour (rows with result `started` or reason `spawn-failed`), per role (3) and overall (12), counted from the store | else `refused: rate` |
| 7 | Consent: the role's last stop was a soft stop, which the standing grant of section 6 covers | else `refused: no-grant` |
| 8 | Call the mechanism once, passing the role and `--model sonnet` | map as in section 3 |

Rows are written for every result including refusals at 2, 3, 5, 6, 7 (for the ledger and the `transient`
re-check); a stored refusal is re-evaluated on a repeat, never replayed (rule 4).
**Counting:** only a row where the mechanism actually tried to spawn counts toward rules 5 and 6 (`started`,
`awaiting-dialog`, `spawn-failed`). `already-running`, `held-for-memory`, `lock-held`, refusals and replays
never consume budget: the cap bounds lanes started, which is the thing to bound. Consequence, intended: a
role whose three spawns in an hour all fail is then `refused: rate` until a slot frees.

## 3. The mechanism's answer to Synapse's four outcomes

| Mechanism reports | Result to Synapse | Reason |
|---|---|---|
| a new session Working or Started | `started` | `started` |
| new session AwaitingDialog (alive, dialog not answered) | `started` | `awaiting-dialog` |
| the role is already running | `already-running` | `already-running` |
| held for memory | `failed` | `held-for-memory` |
| `restore.lock` held by another run | `failed` | `lock-held` |
| process ended, never appeared, or spawn error | `failed` | `spawn-failed` |
| model not Sonnet/Haiku (`ModelNotPinned`) | `refused` | `model-not-pinned` (a configuration bug; `refused` is terminal because retrying cannot help. No rule here raises an alarm for it: it shows only as a stored `refused: model-not-pinned` row, so naming an alarm sink is open. AgentLife answers `refused`, agentlife `1df1b98` start.rs `StartRefusal::outcome`) |
| `;` / control character / invalid argument | `refused` | `invalid-argument` (deterministic; retrying cannot help) |
| no answer within `launch_wait` minus 10 s | `failed` | `mechanism-timeout` |

The model pin is **enforced by the mechanism**, which refuses Opus or an unknown model before anything
spawns; the service's part is to pass `sonnet` and never a value from a signal. `model-not-pinned` is a
defensive row: with `sonnet` always passed it is reachable only if the mechanism's own configuration
disagrees, in which case the stored `refused` row is the only trace. If the mechanism reports Haiku it is accepted.
A late result after `mechanism-timeout` is recorded by updating that row (so a spawn that finished late is
counted in rules 5 and 6 and no longer a bare `failed`); the next attempt then finds the lane running and
answers `already-running`. If no late result ever arrives the row stays `failed: mechanism-timeout`,
uncounted: the budget can be undercounted by exactly those rows, which the 24 h prune bounds.

### 3a. What AgentLife's `lane-restart start --json` actually answers

Read from agentlife `origin/main` `1df1b98`: `crates/lane-restart/src/start.rs` (`StartRefusal::outcome`) and `main.rs` (the dry-run and success paths). Vocabulary: `started|already-running|refused|failed|dry-run`; `refused` is terminal, `failed` may succeed later, `already-running` is the no-op. Reasons are short codes.

| AgentLife `(outcome, reason)` | Policy section 3 row | Ref (agentlife `1df1b98`) |
|---|---|---|
| `started`, `started` | new session Working or Started: `started` | main.rs success path, `RelaunchOutcome::Relaunched` |
| `started`, `awaiting-dialog` | AwaitingDialog: `started` / `awaiting-dialog` (exit code 2; main.rs also prints a status JSON line `awaiting_confirmation` on that path, distinguish it by its `event` key, not by the presence of `outcome`) | main.rs, `AwaitingConfirmation` |
| `already-running`, `already-running` | role already running | start.rs `StartRefusal::AlreadyRunning` |
| `already-running`, `start-lock-held` | no policy row: another start holds the per-role start lock (that start may still fail afterwards); AgentLife answers `already-running`, exit code as for `already-running` | start.rs `StartLockHeld` |
| `failed`, `spawn-failed` | process ended, never appeared, or spawn error | main.rs `Err(e)` arm; start.rs `Launch` |
| `failed`, `disk-floor` | no policy row: free-disk floor not met; retryable. Emitted directly in `run_start_cmd`, not by `StartRefusal::outcome`, for PM and launcher callers alike | main.rs `disk_gate("start")` |
| `failed`, `held-for-memory` | held for memory | start.rs `HeldForMemory` |
| `failed`, `lock-held` | `restore.lock` held by another run | start.rs `RestoreLockHeld` |
| `failed`, `duplicate-risk` | no policy row: a live or stuck session exists but is not a confirmed lane | start.rs `DuplicateRisk(Live)` |
| `failed`, `process-table-unreadable` | no policy row | start.rs `DuplicateRisk(Unreadable)` |
| `failed`, `start-lock-unavailable` | no policy row | start.rs `StartLockUnavailable` |
| `failed`, `restore-lock-unavailable` / `store-unavailable` / `config-unreadable` | no policy row: the launcher's own gates could not be read | launcher_gate.rs `GateUnavailable` |
| `refused`, `model-not-pinned` | model not Sonnet/Haiku (now `refused`) | start.rs `Model` |
| `refused`, `not-pm` / `pm-role` / `no-signal` / `invalid-role` / `reason` | no policy row: AgentLife-side caller and argument checks (`invalid-argument` above is the nearest) | start.rs `NotPm`, `PmRole`, `NoSignal`, `InvalidRole`, `Reason` |
| `refused`, `no-spec` / `spec-unusable` | no policy row: no usable launch spec for the role | start.rs `NoSpec`, `SpecUnusable` |
| `refused`, `stop-hard` / `stop-unclassified` | consent (rule 7): not a soft stop, `no-grant` | start.rs `launcher_may_start` |
| `refused`, `rate` / `rate-daily` | rate caps (rule 6); `rate-daily` has no policy row | launcher_gate.rs `CapHit::reason` |
| `dry-run`, `dry-run` | none: AgentLife-only. It does not consume budget (`spawned=false`; `is_launcher_spawn` requires `spawned == Some(true)`) and writes a ledger line with `acted=false`. It does run `plan_start` and the disk gate first, so it can also answer `already-running`, `disk-floor`, `stop-hard`, `no-spec` and the other plan refusals. It returns before the start lock and the launcher gates, so it cannot predict `start-lock-held`, `start-lock-unavailable`, `rate`, `rate-daily`, `held-for-memory`, `lock-held`, `restore-lock-unavailable`, `store-unavailable` or `config-unreadable`. Whether the service forwards it to Synapse is the service's decision: it must not | main.rs dry-run path |

Differences from section 3, to be reconciled by the service: the extra `failed`/`refused` reasons above have no policy row (the service should pass the code through verbatim and not invent one); AgentLife enforces its own caps, memory floor, `restore.lock` and stop-class check inside the mechanism only for a launcher caller (main.rs `is_launcher`; start.rs `if launcher { launcher_may_start }`): `stop-hard`, `stop-unclassified`, `rate`, `rate-daily`, `held-for-memory`, `lock-held`, `restore-lock-unavailable`, `store-unavailable` and `config-unreadable` never occur for the PM's own start (`start-lock-unavailable` can: it is not launcher-only). For the launcher, a service-side rule 6 or 7 refusal and an AgentLife one can both occur. Section 3 names `mechanism-timeout` and `invalid-argument`, which AgentLife does not emit (the first is the service's own, the second is its `invalid-role`/`reason` family).

## 4. Rules (the numbers they name are proposals, section 6)

1. **At most one mechanism call per `(signal_id, attempt, epoch)` inside the replay window**, across service
   restarts. Retry timing is Synapse's configured table (today 30 s, 2 min, 10 min; 3 attempts, then
   `unreachable`) and the `launch_wait` re-signal after `started`/`already-running`; the service never
   retries on its own.
2. **Every signal acted on gets a `POST /v1/launch-result`**, refusals included. A signal it cannot process
   answers `failed` with a reason, never silence.
3. **Refusals are terminal in Synapse until the oldest message changes, the role claims, or
   `POST /v1/launch-clear`** (a `launcher_roles` role may call it), and a refused role drops out of
   `/v1/waiting`, so the service will not see it again by polling. A refusal that is only *conditional* is
   therefore re-checked by the service itself, once per poll cycle (at most 30 s), from its store's
   `transient` rows, with these inputs: `refused: rate`: a slot has freed in the store's own ledger;
   `refused: no-grant`: the role's last stop is now classified soft (the input section 6 says is not
   built); `refused: no-claim`: `GET /v1/list` shows the role `online` (the claim signal). When the condition ends the service marks the row `clearing`, calls
   `POST /v1/launch-clear`, and **deletes the row only after that call succeeded**; a failed call (daemon
   down, 5xx, a crash between the steps) leaves the row `clearing` and it is retried every poll cycle, so a
   role is never stranded refused with no row to re-check. Refusals are re-evaluated on a repeat (section 2
   rule 4), so the old row cannot re-impose itself. `invalid-role`, `unknown-role` and `invalid-argument` are
   permanent for that signal.
4. The service never launches Opus, never passes `--resume`, never uses `gh auth switch` or acts as
   `ciresnave`, and never reads or logs a secret or a bearer token.
5. **An authentication failure is a stop, not a loop:** 401/403 from the daemon ends polling with one named
   log line and an alert; a daemon that is down is retried with capped backoff (2 s doubling to 60 s).
6. A compromised sender can at worst cause rate-capped launches of lanes that already exist.

R7. **Online observation and the alarm.** Every poll cycle the service makes ONE read of `GET /v1/list`
   (all roles with `online` and `pending`) and looks at each role that has a spawn row in the last 24 h.
   `online` holds for 90 s after a heartbeat, so a lane that claims and exits between two 30 s cycles is
   still observed. A role seen `online` resets its consecutive-spawn counter (so a
   legitimate launch, claim, exit, launch cycle never trips rule 5). A role with a spawn and an
   `already-running` streak of 5 answers, or a `started` older than 2 x `launch_wait`, with the role never
   seen online, raises one named alarm, `launch-stalled: <role> <reason>`, once per streak: a lane alive and sitting at
   the dialog is a person's job, not a retry. **The sink is the service's own structured log line plus a
   notification to the portfolio PM (AgentLife's notify path)**; Synapse's alert on `unreachable` is a
   different thing and never fires for a lane that is alive (board 131 is still a seam). A streak ends, and
   the alarm re-arms, when the role is seen online. A `started` older than 2 x `launch_wait` and the rule 5
   refusal can fire on one cause; they are reported once, as `launch-stalled`, with `no-claim` as the reason.

**State.** A small durable file, written atomically, with two tables.
*Request rows*, one per `(signal_id, attempt, epoch)`: result, reason, time, whether the mechanism was
called, whether it spawned, and a status in `{answered, transient, clearing}`.
*Role rows*, one per role: `spawns_since_online` (rule 5's counter), `last_seen_online`,
`already_running_streak`, and `alarmed` (the once-per-streak flag). Rules 5 and 7 read and write only role
rows; replay, the rate ledger and the clear loop use only request rows. It is the replay
store and the rate/claim ledger. **Replay window: 5 minutes** (a proposal; long enough for a lost response
and the next poll, short enough that a clear or a daemon restart re-evaluates). Rows are pruned after 24 h.
**A lost or corrupt store is not harmless:** the caps reset and a replay is re-evaluated, so a lane that
started and exited could be started again. The service therefore fails closed (rule 1 of section 2) and
creates a fresh store only on an explicit operator action, not on its own.

## 5. What the service must prove before it is believed

Each as a test with a stand-in daemon and a stand-in mechanism, each seen red first:
(a) the same signal twice inside the window launches once; (b) the same key after the window, and `attempt`
reset by a clear, re-evaluates and launches; (c) `attempt+1` of a `failed` signal launches again;
(d) an unknown role never reaches the mechanism; (e) the 4th spawn for one role in an hour is
`refused: rate`, and the 13th overall likewise, while refusals and replays do not consume budget;
(f) after the window frees a slot, the service sends `launch-clear` for that role; (g) a signal carrying a
`model` field launches with `sonnet`; (g2) a stored refusal is re-evaluated, not replayed, and a clear issued
by the service inside the replay window leads to a launch, not the old refusal; (g3) an `invalid-argument`
refusal stays refused after repeated signals; (h) a restart between two polls does not relaunch inside the window;
(i) a mechanism error becomes a `failed` result, never a missing one, and each mapping row of section 3 is
exercised; (j) a 403 stops polling; (k) no output contains a bearer token; (l) an unreadable store answers
`failed: store-unavailable` and calls nothing; (m) three spawns (`started` or `awaiting-dialog`) with no online observation become `refused: no-claim`
and the role being seen online clears it and resets the counter; (n) records older than 24 h are pruned;
(o) a late mechanism result after `mechanism-timeout` is recorded and the next attempt answers
`already-running`; (p) `already-running` and `held-for-memory` never consume the spawn budget; (q) a failed `launch-clear` leaves
the row `clearing` and is retried, and the role is cleared once the daemon is back; (r) a role that launched,
was seen online and exited three times in a day is launched a fourth time; (s) an `already-running` streak
with no online observation raises exactly one `launch-stalled` alarm; (t) a `started` older than
2 x `launch_wait` with the role never seen online raises it too, once; (u) after the role is seen online the
alarm re-arms and a new streak raises a new one.

## 6. Ruled, not decided, not measured

- **Consent for an unattended launch: RULED (board 216, 2026-10-15).** CireSnave, answering the PM's recommendation
  of a narrow yes, verbatim: *"216: yes"*. The grant, as the PM recorded it:
  - **Covered (launchable on mail):** a role whose last stop was a *soft stop*: an idle or waiting shutdown, a
    task-boundary restart, or a crash.
  - **Not covered (`refused: no-grant`):** a *hard stop* (a person or the PM stopped the lane on purpose), a
    *hold* on the role, and any role whose last stop is *unclassified*. Unknown is refused, never assumed soft.
  - **Conditions, unchanged by the grant:** the launch is the spec's own (section 2, nothing wider), always
    `--model sonnet`, only for signals from allowlisted signed senders, inside the section 2 rate and no-claim
    caps, with every result written to the ledger (section 4 State).
  - **What this spec does not yet define:** how the service learns a role's last-stop class. Nobody has built
    that input (it must come from the mechanism or Synapse, not from the sender's message); until it exists
    every role is unclassified and rule 7 refuses it. The grant is therefore a ruling, not yet a behaviour.
  - AgentLife's M5 grant store is not needed for this grant: the ruling is a standing one, and the open input is the
    last-stop class above.
- **`launch_wait` (120 s)** is Synapse's default and is unmeasured; AgentLife is to report a measured
  lane-start-to-claim time before it is relied on.
- The rate caps (3 per role, 12 overall, per hour), the no-claim bound (3) and the replay window (5 min) are
  proposals, not measurements.
- Inputs this spec needs and nobody has built: the role's last-stop class (soft, hard, hold, unclassified),
  from the mechanism or Synapse, and the `epoch` field from Synapse. (`GET /v1/list` takes any bearer session; Synapse's C3 restricts only
  `/v1/waiting`, `/v1/launch-result` and `/v1/launch-clear`.)
- Whether a launched lane that finds no HANDOFF claims its role at all (the launcher starts it with "there is
  no HANDOFF for `<role>`, so say so and wait for instructions"): not checked.
- Whether Synapse role names and AgentLife role names are the same strings: assumed by rule 3, unverified.
