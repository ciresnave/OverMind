# Lane launch on message: OverMind's policy

**Status: spec, not code. Nothing here was run.** It fixes the DECISION half of the split ruled by the PM
(2026-10-15): Synapse detects and signals (`ciresnave/synapse` PR #172, section C), **OverMind owns the policy**
(this file), **AgentLife hosts the always-on service** that implements it in Rust under Task Scheduler
(CireSnave 5.1: nothing non-Rust permanent) and owns the mechanism (lane-start by role). The service must
implement exactly the rules below; this repository does not run a poller, and **no poller session runs on a
restarting lane** (a lane restarts at every task boundary and may itself be the role that is down).

## 1. Inputs

From Synapse `GET /v1/waiting?wait=30` (bearer session for role `overmind`, which must be in the daemon's
`launcher_roles`): per role `{role, signal_id = "<role>:<oldest queued global id>", count,
oldest_enqueued_at, attempt}`. Nothing else is trusted from it: no model, no body, no token (and the service
must not read one if it ever appears).

## 2. Decision, in this order (first refusal wins; every refusal is a named reason)

| # | Rule | Outcome |
|---|---|---|
| 1 | `role` matches `[A-Za-z0-9_-]{1,64}` (AgentLife's own check) | else `refused: invalid-role` |
| 2 | `role` is a known lane: present in AgentLife's registry or plan, i.e. it has existed before | else `refused: unknown-role` (a typo or a sender-chosen string is never a launch) |
| 3 | **Dedupe on `(signal_id, attempt)`**, from the durable outcome store (section 4). Seen: answer with the recorded outcome and do **nothing else** | recorded outcome |
| 4 | Rate caps: at most 3 launches per role per hour and 12 across all roles per hour (config, counted from the store) | else `refused: rate` |
| 5 | Model pin: the service passes `--model sonnet` itself, never from the signal; if the mechanism reports any other model it is `failed: model-not-pinned` and nothing starts | `failed: model-not-pinned` |
| 6 | Consent: AgentLife's launcher today runs only after a person approved (`LAUNCHER.md`), and refuses an agent caller. An unattended launch needs a **standing grant** that covers `lane-start <role>`; none = `refused: no-grant`. **Open: see section 6** | else `refused: no-grant` |
| 7 | Call the mechanism once: `lane-start <role>` | map below |

Mapping the mechanism's answer to Synapse's four outcomes (`started | already-running | refused | failed`):
started -> `started`; role already running -> `already-running`; the mechanism refused (memory floor, restore
lock held by someone else, `;`/control character) -> `failed` with its code as the reason **except** a policy
refusal, which is `refused`; spawn error, process ended, never appeared -> `failed: spawn-failed`.

## 3. Rules that are not up for tuning

1. **At most one `lane-start` call per `(signal_id, attempt)`**, ever, across service restarts. Retrying is
   Synapse's job (30 s, 2 min, 10 min; 3 attempts, then `unreachable`); the service never retries by itself.
2. **Every signal acted on gets a `POST /v1/launch-result`**, including refusals. A signal the service cannot
   process (store unreadable, mechanism unreachable) answers `failed` with a reason, never silence.
3. **A refusal is terminal** for that signal id (Synapse's rule): do not re-ask the mechanism on a repeat.
4. The service never launches Opus, never passes `--resume`, never uses `gh auth switch` or acts as
   `ciresnave`, and never reads or logs a secret.
5. **Authentication failure is a stop, not a loop:** 401/403 from the daemon ends polling, writes one named
   log line and alerts; a daemon that is down is retried with capped backoff (2 s doubling to 60 s).
6. The poller has no authority of its own beyond the section 2 policy; a compromised sender can at worst cause
   rate-capped launches of lanes that already exist.

## 4. State

A small durable file (atomic write, survives restart): one row per `(signal_id, attempt)` with outcome, reason,
time and the mechanism's return. It is the dedupe store (rule 3) and the rate-cap ledger (rule 4). Rows older
than 24 h are pruned; losing the file means a possible double `lane-start`, which the mechanism's own
"already running is a no-op" absorbs, so a lost store degrades to extra no-ops, not extra lanes.

## 5. What the service must prove before it is believed

Each as a test with a stand-in daemon and a stand-in mechanism, and each seen red first:
(a) the same signal twice launches once; (b) `attempt+1` of a `failed` signal launches again; (c) an unknown
role never reaches the mechanism; (d) the 4th launch of one role in an hour is `refused: rate`; (e) a signal
carrying a `model` field launches with `sonnet` anyway; (f) a restart between two polls does not relaunch;
(g) a mechanism error becomes a `failed` result, not a missing one; (h) a 403 stops polling; (i) no output
contains a bearer token.

## 6. Not decided, not measured

- **Consent for an unattended launch (CireSnave).** Rule 6 is a hard stop until AgentLife's standing grants
  (its M5) exist and CireSnave decides whether launching a lane because mail arrived may be covered by one.
  Until then the service is a notifier: it answers `refused: no-grant` and a person starts the lane.
- **`launch_wait` (120 s)** is Synapse's default and is unmeasured; AgentLife is to report a measured
  lane-start-to-claim time (Windows Terminal tab, `lane-restart host`, claim) before it is relied on.
- The rate caps (3 / 12 per hour) are proposals, not measurements.
- Whether a launched lane that finds no HANDOFF claims its role at all (AgentLife starts it with "there is no
  HANDOFF for <role>, so say so and wait"): not checked here.
