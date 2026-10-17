# Launch on message: OverMind's part in replacing claude-peers with Synapse (design, options)

**Status: design only. Nothing here was run, and no code changes.** Written 2026-10-17 for the PM's
[TASK] (OverMind do=design-first). Inputs, all read with `git show origin/main:<path>`, none executed:
Synapse `6b8ad2e` (6.0.0-rc.102): `docs/PILOT_T0_RUNBOOK.md`, `docs/superpowers/specs/2026-10-15-cutover-and-lane-launch.md`
(section C = launch), `...-2026-10-15-daemon-contract-and-pilot.md`, `...-2026-10-10-channel-ack-and-gate.md`,
`crates/synapsed/src/launch.rs` (grep only); AgentLife `2fd0831` (0.16.13): `docs/LAUNCHER-SERVICE.md`,
`docs/LAUNCH-ON-MESSAGE-THREAT.md`, `src/launcher_service.rs` (header and grep only); this repo's
`docs/LANE-LAUNCH-POLICY.md` (the policy).
Mandate, CireSnave 2026-10-15 (CIRESNAVE-EXPECTATIONS 5.1e, verbatim): *"Claude-peers is going away. Synapse
is its replacement."* and *"A message incoming to that lane could then have Synapse tell OverMind which would
use AgentLife to launch the agent so Synapse could complete delivery of the message."*

## 1. The finding that shapes the rest: OverMind subscribes to nothing at runtime

"Synapse tells OverMind" is shorthand. Under the designs already agreed (Synapse C2a, AgentLife
LAUNCHER-SERVICE.md, PM notices 2026-10-15):

| Step | Who really does it | Role in Synapse | Where it lives |
|---|---|---|---|
| detect "mail waits, role has no live session"; signal | `synapsed` | n/a | Synapse |
| **subscribe**: long-poll `GET /v1/waiting?wait=30` | the `agentlife launcher` service, a Task Scheduler task, **not a lane** | `agentlife-launcher` (in the daemon's `launcher_roles`) | AgentLife |
| **decide**: rules 1-7 of the policy | the same service, executing OverMind's spec | same | policy text: this repo |
| mechanism: start one lane by role, `--model sonnet` | `lane-restart start`, launcher caller | n/a | AgentLife |

The `overmind` lane is an ordinary lane with an ordinary role. A lane restarts at every task boundary and may be
the role that is down, so it cannot host a subscriber, and it must never claim `agentlife-launcher` (one session
per (home, role): it would knock the service off). **So OverMind's deliverable is the policy, the approval
records and lane config the policy needs, and the measures; it runs no poller.** This corrects two lines of
`LANE-LAUNCH-POLICY.md` section 1 (fixed in this PR): the bearer role is `agentlife-launcher`, not `overmind`,
and Synapse's `epoch` is built in `launch.rs`, no longer "requested".

## 2. The message path end to end

```
sender --send--> synapsed mailbox (redb, durable)         role R has queued mail, online=false
synapsed --GET /v1/waiting--> agentlife launcher          signal {role, signal_id, count, oldest_enqueued_at, attempt, epoch}
launcher: policy rules 1-7  --lane-restart start R --model sonnet--> new claude session for R
launcher --POST /v1/launch-result--> synapsed             {signal_id, epoch, attempt, role, outcome, reason}
R's synapse-claude-channel adapter claims role R; synapsed releases the mailbox to it; adapter pushes
  notifications/claude/channel (bounded attempts, then self-ack); R's model acts once per meta.message_id
```

### Message shapes (and what must never cross)

| Shape | Fields | Never in it |
|---|---|---|
| **Mail** (sender to R). Channel event `<channel source="synapse">`, text starts `UNTRUSTED message from`, `meta` = `{sender, message_id}` plus optional `sender_summary`, `sender_cwd` (present in the adapter at `6b8ad2e`) | body, sender identity, message id | n/a: the only shape with a body |
| **Launch signal** (`/v1/waiting`) | `role`, `global_id` (`role@account`), `signal_id` = `<role>:<oldest queued enqueue time>`, `count`, `oldest_enqueued_at`, `attempt`, `epoch` | body, sender, model, token, key |
| **Launch result** (`/v1/launch-result`) | `signal_id`, `epoch`, `attempt`, `role`, `outcome` in `started \| already-running \| refused \| failed`, `reason` short code | anything else is `malformed` |
| **Mechanism answer** (`lane-restart start --json`) | `outcome` + `reason` (vocabulary: policy 3a) | n/a |

The launcher sees *that* mail waits and *how much*, never *what*: a compromised sender can at worst cause
rate-capped launches of lanes that already exist, on Sonnet (policy rule 6; THREAT doc). The dedupe key is
`(signal_id, epoch, attempt)` at Synapse and `(signal_id, attempt, epoch, generation)` in AgentLife's store;
the policy's rule 4 and section 4 already use the first, so they need no change.

## 3. What each party must supply

**Synapse:**
1. `claimed_at` (session start) per role in `/v1/list` or the signal (not seen in either; an internal `claimed_at` exists at `synapsed/src/lib.rs:1651`, whether it is exposed I did not trace). Without it AgentLife can only record
   `observed_online_at` (an upper bound, error = one 30 s poll) and `launch_wait` (120 s) stays unmeasured.
2. A statement that `POST /v1/launch-result` is idempotent for the same triple (AgentLife asked; it treats 409 as success meanwhile).
3. A readiness gate or equivalent for the first push after a claim (F4 below): the gate was deferred in rc.32 and is unproven.
4. `launcher_roles` set to `agentlife-launcher` only when phase 2 (section 6) starts; empty (feature off) until then.

**AgentLife** (from LAUNCHER-SERVICE.md and the source header at `2fd0831`; I ran none of it):
1. The service is **notifier-only today**: its mechanism is `NoMechanism` and rule 7 answers `refused: no-grant`. Wiring the real mechanism (`lane-restart start`, launcher path: soft-stop reader 0.15.12, caps/memory floor/`restore.lock` 0.15.13) into the service is build steps 2-3, not done.
2. **Its notify path still posts to the claude-peers broker** (`lane_restart::notify::notify_role`, `broker_addr()`). With claude-peers going away the notifier-only fallback goes silent at cutover. That is cutover step 3 (M8: `lane-restart@<account>` on the daemon API) and must land before the broker is stopped.
3. A lane that shuts down while waiting must use `lane-restart stop --self` (writes `stop_kind: soft`, the only class rule 7 may launch). A PM or person stop is `hard` and is never woken: intended.

**Lane config and approvals** (the PM, CireSnave, each lane):
1. A lane's launch spec must carry the synapse flags (`--mcp-config`, `server:synapse`). A restart rebuilds from the state file, so one hand launch per lane is required (PILOT-OVERMIND-LANE-CONFIG.md section 3). A lane never hand-launched with them is launched by the service **without** a synapse channel and never claims its role (F6).
2. **G5**: the dev-channels approval record for `server:claude-peers, server:synapse` (CireSnave's yes). Without it every unattended launch stops at a dialog nobody answers (the service raises `launch-stalled`). The folder-trust dialog is a second, separate dialog: the 16 trust records I hold uncommitted are the same kind of prerequisite for an unattended launch in a fresh folder.
3. The HANDOFF convention (portfolio CLAUDE.md section 9: begin the first unblocked task, check the forge yourself) plus the one line Synapse's delivery statement already calls for: **on start, call `fetch`**.

## 4. Failure modes

| # | Failure | What happens | Mitigation / owner |
|---|---|---|---|
| F1 | Signal lost (daemon restart, service restart, lost response) | nothing lost: the signal is level-triggered from the durable mailbox. After a daemon restart presence resets, so launch signals are suppressed for one online window (90 s) | Synapse design; measure latency |
| F2 | Duplicate launch of one role | same triple twice: the replay window answers the recorded result. A new `epoch` or `generation` is a new request, but `lane-restart start` returns `already-running` (per-role start lock, 0.15.11); caps 3 per role and 12 overall per hour | policy rules 4-6; pilot counts duplicates (L3) |
| F3 | Launched lane never claims (dialog, crash, bad flags) | `launching`, then `no-claim` re-signal; 3 spawns without an online sighting become `refused: no-claim`; a `started` older than 2 x `launch_wait` raises `launch-stalled` to the PM | policy rule 5 and R7; the alarm sink is the PM notify path, which is claude-peers today (3, AgentLife item 2) |
| F4 | **Launched lane claims, but the first pushes are dropped and the adapter then self-acks** | the failure specific to launch-on-message: the launch succeeded and the mail is lost. This is the c7 case (queued while down, pushed before Claude Code registered the channel; FAIL n=1 on rc.29). The rc.33 bound (>= 2 own pushes, daemon `attempts` >= 3, about 30 s apart) covers most of it; the residual is "every push in the bound dropped" | lane runs `fetch` at start (the mailbox is truth); pilot measures it (L2); Synapse gate if L2 > 0 |
| F5 | Latency is longer than "a second" | worst case = up to 90 s online window + 5 s grace + up to 30 s poll + start-to-claim. Not measured | pilot L1; promise nothing sub-minute |
| F6 | Launched lane has no synapse channel | the spec still holds only claude-peers flags | one hand launch per lane; read back `<role>.json` |
| F7 | Mail to a hard-stopped, held or unclassified lane | `refused: no-grant`; the sender sees `launch: refused` in `/v1/list`; mail stays queued until a manual start | by design (board 216); no sender-side error |
| F8 | Launch storm or ping-pong (A's launch makes A message B, B's launch messages A) | bounded by the hourly caps and the daily cap, `refused: rate` | caps are proposals; pilot records the count |
| F9 | Cost: every launch is a cold Sonnet session | tokens per launch, not per message; several messages to one role ride one launch | pilot L5; no pass bar |
| F10 | Service, store or daemon down | the feature is off, not best-effort: mail waits; a lost or corrupt store fails closed and needs an operator `--init-store` | by design |
| F11 | Role-name drift between Synapse and AgentLife | policy rule 3 assumes identical strings; unverified | the pilot's three roles use `synapse`, `agentlife`, `overmind`; write the table down |
| F12 | Notifier path dies at cutover | see section 3, AgentLife item 2 | order: M8 before the broker stops |

## 5. Options and recommendation

**D1. Where the decision runs.**
(a) In the AgentLife launcher service, executing OverMind's spec (current design). (b) A separate OverMind
service (Python, permanent): contradicts CireSnave's "nothing non-Rust permanent" (portfolio notice 2026-10-15)
and adds a second always-on process. (c) Inside Synapse: contradicts the agreed split (Synapse never decides).
**Recommend (a).** OverMind keeps the policy file, the numbers and the measures.

**D2. How to ship it.**
(a) Notifier first (as built), then real launches. (b) Straight to launches. **Recommend (a):** the notifier
needs only the M8 notify move and the Synapse items; real launches (rule 7 open) additionally need the
AgentLife mechanism wiring and Synapse's `claimed_at`.

**D3. When to measure.**
(a) Fold launch cases into the running 24 h pilot. (b) A separate phase 2 on the same three lanes after the
pilot report passes. **Recommend (b):** the pilot's stop rule (any lost id) would be confused by deliberately
down lanes, and `launcher_roles` must stay empty until phase 2.

**D4. The F4 residual loss.**
(a) Accept and measure. (b) Ask Synapse for the readiness gate now. (c) Make `fetch`-on-start the lane's
duty. **Recommend (c) plus (a):** (c) costs one onboarding line and makes the mailbox, not push, the guarantee
(Synapse's own delivery statement, item 4); ask for (b) only if L2 is nonzero.

**D5. Consent scope.** Already ruled (board 216, CireSnave *"216: yes"*, as the PM recorded it): soft stops
only, Sonnet only, allowlisted signed senders, capped. Not reopened here.

## 6. What the pilot (phase 2) measures

Subject: the three pilot lanes, `launcher_roles = agentlife-launcher`, real mechanism wired, G5 record present.
Each case: stop the lane with `lane-restart stop --self`, then send from another lane with id `pilot-<role>-L<n>`.

| # | Measure | How | Proposed pass |
|---|---|---|---|
| L1 | Send-to-shown latency for a down lane | sender log time vs transcript event time; split by `claimed_at` once Synapse has it | recorded p50/p95/max; no bar until N >= 5 (PM ruling for `launch_wait`) |
| L2 | **Loss by id on launched lanes** | sender ids minus ids in the receiving transcript (only the transcript decides) | 0 |
| L3 | Duplicate launches | `restart.log` spawn rows per signal and per role; `already-running` answers | 0 sessions beyond one per down-interval |
| L4 | Refusals by reason | launcher ledger | each explained; `no-grant` for a hard-stopped lane shown once as a positive control |
| L5 | Cost per launch | turns and cache-read for the cold start vs a warm lane's baseline | recorded |
| L6 | Dialog stalls and `launch-stalled` alarms | PM notifications | 0 unexplained |
| L7 | Shows per id on a launched lane | events per `message_id` | max 3, one action |
| L8 | Daemon restart while a signal is open | restart `synapsed` once; count relaunches | exactly one launch |

Stop and revert (empty `launcher_roles`): any L2 loss, a launch of a hard-stopped or unknown role, two
sessions of one role, or CireSnave says stop.

## 7. Not decided, not measured

`launch_wait` (120 s), the caps (3 / 12 per hour, 8 per day), the no-claim bound (3) and the replay window
(5 min) are proposals. The 90 s online window and 5 s grace are Synapse's. Whether the service's
`lane-restart start` call works against a real lane end to end is unrun. I grepped Synapse's `launch.rs` for
`epoch` and did not read its body. Everything about AgentLife's wiring comes from its design doc and header
comments at `2fd0831`.
