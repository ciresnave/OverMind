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
| 5 | No more than 3 consecutive spawns (`started`, `awaiting-dialog`) for this role without the role coming online | else `refused: no-claim` (cleared when the role is seen online, section 4 rule 3) |
| 6 | Rate caps: **spawns** in the last hour (rows with result `started` or reason `spawn-failed`), per role (3) and overall (12), counted from the store | else `refused: rate` |
| 7 | Consent: an unattended launch needs a standing grant covering "start `<role>`" (section 6) | else `refused: no-grant` |
| 8 | Call the mechanism once, passing the role and `--model sonnet` | map as in section 3 |

Rows are written for every result including refusals at 2, 3, 5, 6, 7, so a repeat answers the same way.
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
| model not Sonnet/Haiku (`ModelNotPinned`) | `failed` | `model-not-pinned` (a configuration bug: three attempts then `unreachable`, which is the right alarm) |
| `;` / control character / invalid argument | `refused` | `invalid-argument` (deterministic; retrying cannot help) |
| no answer within `launch_wait` minus 10 s | `failed` | `mechanism-timeout` |

The model pin is **enforced by the mechanism**, which refuses Opus or an unknown model before anything
spawns; the service's part is to pass `sonnet` and never a value from a signal. `model-not-pinned` is a
defensive row: with `sonnet` always passed it is reachable only if the mechanism's own configuration
disagrees, which is exactly the case it exists to make loud. If the mechanism reports Haiku it is accepted.
A late result after `mechanism-timeout` is still recorded; the next attempt then finds the lane running and
answers `already-running`.

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
   `refused: no-grant`: AgentLife's grant store shows a covering grant (an input AgentLife must expose;
   section 6); `refused: no-claim`: `GET /v1/list` shows the role `online` (the claim signal; needs the same
   bearer session to read it). When the condition ends the service **deletes the transient row and then**
   calls `POST /v1/launch-clear`, so the re-signalled request is re-evaluated, not answered from the old
   row. `invalid-role`, `unknown-role` and `invalid-argument` are permanent for that signal.
4. The service never launches Opus, never passes `--resume`, never uses `gh auth switch` or acts as
   `ciresnave`, and never reads or logs a secret or a bearer token.
5. **An authentication failure is a stop, not a loop:** 401/403 from the daemon ends polling with one named
   log line and an alert; a daemon that is down is retried with capped backoff (2 s doubling to 60 s).
6. A compromised sender can at worst cause rate-capped launches of lanes that already exist.

**State.** A small durable file, written atomically: one row per `(signal_id, attempt, epoch)` with result,
reason, time, whether the mechanism was called, whether it spawned, and whether the refusal is `transient`. It is the replay
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
`model` field launches with `sonnet`; (g2) a stored refusal is re-evaluated, not replayed, and a clear issued
by the service inside the replay window leads to a launch, not the old refusal; (g3) an `invalid-argument`
refusal stays refused after repeated signals; (h) a restart between two polls does not relaunch inside the window;
(i) a mechanism error becomes a `failed` result, never a missing one, and each mapping row of section 3 is
exercised; (j) a 403 stops polling; (k) no output contains a bearer token; (l) an unreadable store answers
`failed: store-unavailable` and calls nothing; (m) three `started` with no claim becomes `refused: no-claim`
and the role being seen online clears it and resets the counter; (n) records older than 24 h are pruned;
(o) a late mechanism result after `mechanism-timeout` is recorded and the next attempt answers
`already-running`; (p) `already-running` and `held-for-memory` never consume the spawn budget.

## 6. Not decided, not measured

- **Consent for an unattended launch (CireSnave).** Rule 7 is a hard stop until AgentLife's standing grants
  (its M5) exist and CireSnave decides that launching a lane because mail arrived may be covered by one. Until
  then the service is a notifier: `refused: no-grant`, and a person starts the lane.
- **`launch_wait` (120 s)** is Synapse's default and is unmeasured; AgentLife is to report a measured
  lane-start-to-claim time before it is relied on.
- The rate caps (3 per role, 12 overall, per hour), the no-claim bound (3) and the replay window (5 min) are
  proposals, not measurements.
- Inputs this spec needs and nobody has built: a grant-store read from AgentLife (for `no-grant` clearing),
  the `epoch` field from Synapse, and a role's `launcher_roles` session being allowed `GET /v1/list`.
- Whether a launched lane that finds no HANDOFF claims its role at all (the launcher starts it with "there is
  no HANDOFF for `<role>`, so say so and wait for instructions"): not checked.
- Whether Synapse role names and AgentLife role names are the same strings: assumed by rule 3, unverified.
