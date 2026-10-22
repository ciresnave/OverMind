# Synapse cutover, OverMind's items G5 and G6: state and plan

Written 2026-10-22 by the overmind lane for the PM's TASK (pilot report due 21:10Z). Design only; no code, no pilot-home changes.
Measured at: synapse `origin/main` docs (`CUTOVER_READINESS_2026-10-15.md`), agentlife `origin/main` 5cedfdc (lane-restart 0.29.x),
`ciresnave/ciresnave` default branch approvals listing read 2026-10-22. Nothing here was run against a daemon.

## 1. Finding: most of this is already built, by agentlife; two gaps remain

The lane-restart source moved to agentlife (`crates/lane-restart`); OverMind has no copy at `origin/main`. So both items are
agentlife's code, and the work here is coordination plus whatever is missing.

### G6 (notify as `lane-restart@<account>`)
| Fact | Evidence |
|---|---|
| `notify_transport: synapse` exists (default `broker`) | agentlife `notify.rs` `Transport`; `approvals.rs` `Config.notify_transport` |
| With it, an unmatched-dialog [ASK] is written to `.lane-state/notify-outbox/` (fixed `message_id`, temp+rename) | `notify::enqueue`, `host.rs` ~1045 |
| `agentlife launcher` drains the outbox and sends via the Synapse daemon **as role `agentlife-launcher`** | `launcher_service.rs:351` `drain_outbox`; CHANGELOG "M8 phase 1" (#123) |
| Not run: a real send/claim against a daemon | same CHANGELOG entry says so |
| `~/.overmind/lane-restart.json` today has **no** `notify_transport`, so this box still uses the claude-peers broker | file read 2026-10-22: only `approvals` is set |

**Deviation from the plan.** The Synapse plan (M8) and cutover doc say role `lane-restart@<account>`. agentlife built a relay
under its own existing role instead (no per-host claims). Consequence to decide, not assume: a reply to an [ASK] goes to
`agentlife-launcher`; nothing in `launcher_service.rs` that I found fetches replies. The ASK flow is one-way unless a lane/PM
sends to the *lane* instead. Whether one-way is acceptable is the question for the PM and the agentlife lane.

### G5 (dialog approval for `server:synapse`)
| Fact | Evidence |
|---|---|
| Records exist: `synapse-dev-channel-pilot` (Channels `server:synapse`), `claude-peers-synapse-dev-channels-pilot` (`server:claude-peers, server:synapse`), both scoped to roles synapse, agentlife, overmind, **expire 2026-10-31**; `synapse-dev-channel-restarttest` (role restarttest, expires 2026-11-09) | read from the approvals repo, 2026-10-22 |
| Matching is exact on the Channels value (no prefix match); unit tests cover record x screen combinations and the real 2026-10-09 two-server capture | agentlife `handlers.rs` ~1288-1380 |
| **No record covers all lanes.** The pilot quote's own text says it is a pilot, not the cutover | the records' `approving` field |
| A cutover record needs CireSnave's verbatim yes | G5 row, synapse readiness doc; PM asks |

## 2. What is still missing

1. **A test that uses the real records.** Existing tests build a record from `EXAMPLE_JSON` with the Channels value swapped. They
   never load the actual files from the approvals repo, so a typo in a real record, a wrong `scope.roles`, or an expiry already
   past would pass every test. Proposed (agentlife PR, coordinated): vendor copies of the four synapse records under
   `tests/fixtures/approvals/` (pinned to a commit, with a drift check against the repo in a non-gating job, since the repo is
   CireSnave's), and assert for each: `parse_and_validate` ok; `is_active(role, now)` true for every role in scope and false
   for an out-of-scope role; false after `expires_at`; matches the real capture of exactly its channel set and **none** of the
   other sets; refuses a screen that mentions a secret. Mutation check: change one character of the Channels value, one anchor,
   and one role, and each must fail a test.
2. **A real one-server capture.** The only real dialog fixture with synapse is the two-server one (2026-10-09). The
   `server:synapse`-only screen is built by string replacement. The restarttest lane ran with it; its capture, if one was kept,
   is in `.lane-state/unhandled-prompts/`. Scanned 2026-10-22: 21 captures, none with a one-server `Channels: server:synapse` line (control: the
   host logs of restarttest do mention it, so the dialog was seen and answered, which leaves no capture). The first pilot relaunch
   that stops at it produces one; the fixture is replaced then.
3. **The cutover record (draft only).** Channels `server:synapse`, `roles: ["*"]`, same anchors and action as the pilot, expiry
   per CireSnave. It needs his verbatim quote; I draft the file and the PM relays the ask. I do not merge into his repo.
4. **G6 decisions** (below), then flipping `notify_transport` in the user config, which is the PM's call (shared config).

## 3. Decisions for the PM (recommendation first)

| # | Question | Recommend | Why |
|---|---|---|---|
| D1 | Keep the `agentlife-launcher` relay as the G6 implementation, instead of a separate `lane-restart@<account>` role? | Keep it, and amend the plan/readiness doc to say so | Built and unit-tested; one claim, no per-host claims. A second role adds a daemon client for no reply benefit unless replies are wanted |
| D2 | Must an [ASK] reply reach lane-restart? | No: replies go to the PM's own session; the ASK carries the capture path | The ASK flow's job is to tell the PM; nothing in lane-restart acts on an answer |
| D3 | Before cutover, one real send/claim through the outbox against a daemon | Yes, on a scratch home, by agentlife with the synapse lane's recipe | CHANGELOG says not run; a path never exercised is not "done" |
| D4 | Replace the pilot records with one all-roles record at cutover | Yes, after CireSnave's quote; keep the pilot ones until phase C has held a week (readiness doc section 4) | Rollback keeps working |

## 4. Plan and ownership

| Step | Owner | Output |
|---|---|---|
| Send agentlife this note and ask them to confirm section 1 facts and own item 1 | overmind | message, no code |
| Draft cutover record JSON | overmind | in this repo's docs, for the PM to relay |
| Real-record test (item 1) | agentlife PR; overmind writes the test spec and reviews | red-first mutation list as in item 1 |
| D3 scratch-home send test | agentlife + synapse lane | evidence line in the PR |
| Flip `notify_transport` | PM | one config edit, after D3 |

## 5. Not done / not claimed
- No daemon contact, no relaunch, no read of the pilot home.
- Section 1's "nothing fetches replies" is from reading `launcher_service.rs` by search, not a full read; agentlife should confirm.
- The record contents are from a read of the repo's default branch at one time; they can change.
