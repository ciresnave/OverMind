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
reports one of the section 3 results. LAUNCHER.md today executes plans through `restore`, after a person's
approval. Everything below that says "the mechanism" means that entry point.

## 1. Inputs

From Synapse `GET /v1/waiting?wait=30` (bearer session for role `overmind`, which must be in the daemon's
`launcher_roles`): per role `{role, signal_id = "<role>:<oldest queued global id>", count,
oldest_enqueued_at, attempt}`. Nothing else is trusted: no model, no body, no token (the service must not
read one if it ever appears). Synapse resets `attempt` on a `launch-clear` and on a daemon restart, and the
`signal_id` stays the same while the oldest message stays queued, so the same `(signal_id, attempt)` can be a
genuinely new request. **Requested of Synapse:** an `epoch` field (a counter bumped by a clear or restart) in
the signal. Until it exists, section 4's time window is what separates a replay from a new request.

## 2. Decision, in this order (first refusal wins; every refusal has a named reason)

| # | Rule | Result |
|---|---|---|
| 1 | The store is readable and writable (section 4) | else `failed: store-unavailable` (fail closed) |
| 2 | `role` matches `[A-Za-z0-9_-]{1,64}` | else `refused: invalid-role` |
| 3 | `role` is a known lane: it has existed in AgentLife's registry or plan under the same name | else `refused: unknown-role` (a sender-chosen string is never a launch) |
| 4 | **Replay check**: a stored row with the same `(signal_id, attempt, epoch)` younger than the replay window (section 4) | answer the recorded result; do nothing else; count nothing |
| 5 | No more than 3 consecutive `started` results for this role without the role claiming | else `refused: no-claim` (cleared when the role claims) |
| 6 | Rate caps: mechanism calls in the last hour, per role (3) and overall (12), counted from the store | else `refused: rate` |
| 7 | Consent: an unattended launch needs a standing grant covering "start `<role>`" (section 6) | else `refused: no-grant` |
| 8 | Call the mechanism once, passing the role and `--model sonnet` | map as in section 3 |

Rows are written for every result including refusals at 2, 3, 5, 6, 7, so a repeat answers the same way.
**Counting:** only a row whose mechanism call was made (rule 8) counts toward rules 5 and 6; refusals and
replays never consume budget.

## 3. The mechanism's answer to Synapse's four outcomes

| Mechanism reports | Result to Synapse | Reason |
|---|---|---|
| a new session Working or Started | `started` | `started` |
| new session AwaitingDialog (alive, dialog not answered) | `started` | `awaiting-dialog` |
| the role is already running | `already-running` | `already-running` |
| held for memory | `failed` | `held-for-memory` |
| `restore.lock` held by another run | `failed` | `lock-held` |
| process ended, never appeared, or spawn error | `failed` | `spawn-failed` |
| model not Sonnet/Haiku (`ModelNotPinned`) | `failed` | `model-not-pinned` (a configuration bug: three attempts then `unreachable`, which is the right alarm) |
| `;` / control character / invalid argument | `refused` | `invalid-argument` (deterministic; retrying cannot help) |
| no answer within `launch_wait` minus 10 s | `failed` | `mechanism-timeout` |

The model pin is **enforced by the mechanism**, which refuses Opus or an unknown model before anything
spawns; the service's part is to pass `sonnet` and never a value from a signal. The mechanism may legitimately
report Haiku for a lane configured for it; that is accepted.

## 4. Rules that are not up for tuning

1. **At most one mechanism call per `(signal_id, attempt, epoch)` inside the replay window**, across service
   restarts. Retry timing is Synapse's configured table (today 30 s, 2 min, 10 min; 3 attempts, then
   `unreachable`) and the `launch_wait` re-signal after `started`/`already-running`; the service never
   retries on its own.
2. **Every signal acted on gets a `POST /v1/launch-result`**, refusals included. A signal it cannot process
   answers `failed` with a reason, never silence.
3. **Refusals are terminal in Synapse until the oldest message changes, the role claims, or
   `POST /v1/launch-clear`** (a `launcher_roles` role may call it). So a refusal that is only
   *conditional* must be cleared by the service when its condition ends: `refused: rate` when the window
   frees a slot, `refused: no-grant` when a grant is issued, `refused: no-claim` is cleared by the claim
   itself. The service keeps these in its store as `transient` and clears them; it does not rely on
   Synapse's retry. `invalid-role`, `unknown-role` and `invalid-argument` are permanent for that signal.
4. The service never launches Opus, never passes `--resume`, never uses `gh auth switch` or acts as
   `ciresnave`, and never reads or logs a secret or a bearer token.
5. **An authentication failure is a stop, not a loop:** 401/403 from the daemon ends polling with one named
   log line and an alert; a daemon that is down is retried with capped backoff (2 s doubling to 60 s).
6. A compromised sender can at worst cause rate-capped launches of lanes that already exist.

**State.** A small durable file, written atomically: one row per `(signal_id, attempt, epoch)` with result,
reason, time, whether the mechanism was called, and whether the refusal is `transient`. It is the replay
store and the rate/claim ledger. **Replay window: 5 minutes** (a proposal; long enough for a lost response
and the next poll, short enough that a clear or a daemon restart re-evaluates). Rows are pruned after 24 h.
**A lost or corrupt store is not harmless:** the caps reset and a replay is re-evaluated, so a lane that
started and exited could be started again. The service therefore fails closed (rule 1 of section 2) and
creates a fresh store only on an explicit operator action, not on its own.

## 5. What the service must prove before it is believed

Each as a test with a stand-in daemon and a stand-in mechanism, each seen red first:
(a) the same signal twice inside the window launches once; (b) the same key after the window, and `attempt`
reset by a clear, re-evaluates and launches; (c) `attempt+1` of a `failed` signal launches again;
(d) an unknown role never reaches the mechanism; (e) the 4th mechanism call for one role in an hour is
`refused: rate`, and the 13th overall likewise, while refusals and replays do not consume budget;
(f) after the window frees a slot, the service sends `launch-clear` for that role; (g) a signal carrying a
`model` field launches with `sonnet`; (h) a restart between two polls does not relaunch inside the window;
(i) a mechanism error becomes a `failed` result, never a missing one, and each mapping row of section 3 is
exercised; (j) a 403 stops polling; (k) no output contains a bearer token; (l) an unreadable store answers
`failed: store-unavailable` and calls nothing; (m) three `started` with no claim becomes `refused: no-claim`
and a claim clears it; (n) records older than 24 h are pruned.

## 6. Not decided, not measured

- **Consent for an unattended launch (CireSnave).** Rule 7 is a hard stop until AgentLife's standing grants
  (its M5) exist and CireSnave decides that launching a lane because mail arrived may be covered by one. Until
  then the service is a notifier: `refused: no-grant`, and a person starts the lane.
- **`launch_wait` (120 s)** is Synapse's default and is unmeasured; AgentLife is to report a measured
  lane-start-to-claim time before it is relied on.
- The rate caps (3 per role, 12 overall, per hour), the no-claim bound (3) and the replay window (5 min) are
  proposals, not measurements.
- Whether a launched lane that finds no HANDOFF claims its role at all (the launcher starts it with "there is
  no HANDOFF for `<role>`, so say so and wait for instructions"): not checked.
- Whether Synapse role names and AgentLife role names are the same strings: assumed by rule 3, unverified.
