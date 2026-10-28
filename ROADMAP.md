# OverMind roadmap

Written 2026-10-14 at OverMind `origin/main` = `a198c29` (0.13.3) (rows added or corrected since carry their own ref; section 6 is at `f604d8d`), on the PM's TASK (no roadmap was a defect).
Every row says **RULED** (with who ruled it and where) or **UNRULED** (nobody with authority has decided; the
lane does not act). Versions are the PM's, allocated at gate time. This file records state; the portfolio-wide
list is `PORTFOLIO-ROADMAP.md` in `C:\Projects`.

## 1. In flight

| item | state | ruled? | source |
|---|---|---|---|
| **restarttest soak (board 175)**: one message every 10 minutes into the restarttest session, 3 days | DONE 2026-10-15T18:52Z, 72.5 h: 435 sent / 435 seen, max shows 3 (watcher, ids 1-434), 0 tool_use, 0 compactions, daemon unchanged. NOT clean: 1 `auth_failure` line (the synapse lane's own curl, their statement); compaction bound untested; weekly-limit delta unmeasured. Report: `docs/SOAK-RESTARTTEST-END-REPORT.md` | RULED, CireSnave 2026-10-12: "1) Yes. 2) 1 message every 10 minutes.  3) Restart it." | docs; synapse lane owns the daemon |
| Follow-ups after the soak | token rate reported; weekly-limit delta and behaviour across a compaction (3x show bound, id dedupe) still unmeasured | RULED (PM ask); still open: needs a banner reading and a run that compacts | docs |
| restarttest step 6 | waits on CireSnave's go | UNRULED (his) | HANDOFF |

## 2. Fleet gate coverage (the `spdx_gate.py` copies)

| item | state | ruled? |
|---|---|---|
| The fleet comparison of the `spdx_gate.py` copies | **MOVED** with 0.13.6 to `ThinkersJournal/licence-tools` (`ci.yml` job `fleet`, six deployments, every PR + daily). Was DONE here at #140 `a198c29` (five deployments) | RULED (PM 2026-10-14) |
| A change to the gate in a *different* repo is caught up to a day later (daily schedule), not on that repo's PR | known limit, stated in the workflow header; closing it means publishing the gate as a package each repo pins | UNRULED. Tied to the licence-tooling move below |
| `gate_fleet.py`'s second trigger: "any `DIVERGENT` entry needs a second reason" | not fired; four entries, each with one reason, at `a198c29` | n/a |

## 3. The OverMind split (PORTFOLIO-ROADMAP "OverMind split: APPROVED", CireSnave 2026-10-08, board 143)

| step | state | ruled? |
|---|---|---|
| 5 lane-restart / lane-state to agentlife | DONE (0.12.0) | RULED |
| 6 user-request + with-secret to `ciresnave/consent` | DONE: repo created, history kept, deleted here in 0.13.0. `user-request` 0.13.0 is published; **`with-secret` is unpublished** | RULED. Publishing `with-secret` needs CireSnave's per-crate yes (board 173-A) **and** an independent Sonnet review: UNRULED |
| 6b **Finish step 6: nothing vault-related left in OverMind** (owner: overmind lane; dated 2026-10-24; CireSnave 2026-10-23, verbatim: "As long as the vault move is planned, I'm fine with that.  I just don't want it left in OverMind indefinitely.") | The code already lives in `ciresnave/consent`. Residue here: `WITH-SECRET-REQUESTER-ALLOWLIST-DESIGN.md` and the files citing it, plus `with-secret` callers in `probe/` and `src/overmind/providers.py`, which are users and stay. Order: (1) vault redaction-mislabel investigation and fix in `consent` (PM TASK 2026-10-24; blocks nothing below but goes first); (2) move the allowlist design doc to `consent/docs/` and repoint its references here; (3) one sweep that no `with-secret`/`user-request` implementation or design text remains; (4) `with-secret` publish stays separate and waits on board 173-A. Steps 2-3 are blocked only by (1) being reported. **Steps 1-3 DONE 2026-10-24** (1: consent#19; 2: consent#20 + OverMind 0.14.13; 3: sweep found only callers and ARCHITECTURE-LAYERS-SPEC.md, which describes the move). | RULED (PM TASK 2026-10-24); step 4 UNRULED (CireSnave's per-crate yes) |
| 7a `licence-tools`: `tools/spdx.py`, `tools/preflight.py`, `tools/gate_fleet.py`, their two tests and the fleet workflow | **DONE**: repo `ThinkersJournal/licence-tools` 0.1.0 (`3ce26593`); removed from OverMind in 0.13.6 | RULED (PM 2026-10-14; name, 0.1.0, move) |
| 7b Create `ciresnave/measurements` and move `MEASUREMENTS.md` and the `probe/` files that do not import `overmind` | not started | Same: direction RULED, move/name/version UNRULED |
| The six probes that import `overmind` (`daily_limits`, `free_capacity`, `ledger_context`, `local_throughput`, `p1_bench`, `paper_summaries`) | **stay** | RULED (ruling 4, 2026-10-08) |
| `MEASUREMENTS.md` is cited by section number from 13 files in `src/overmind` and `tests`; the name and section numbers must not change | constraint on 7b | RULED by the roadmap's own wording; how the citations are repointed is UNRULED |
| PM duties: remove the two `rust` required contexts; make OverMind's licence check a required check | measured 2026-10-14 at `branches/main`, enforcement `non_admins`: 7 required contexts (`gate boundary is intact` and the six `tests (...)`); the two `rust` contexts are gone; **`Every source file declares the licence` is NOT required**, nor is the fleet job | RULED (PM duty); the licence-check half is OPEN, the PM's to do |
| Port the non-Rust pieces to Rust (CireSnave's caveat, 2026-10-08, board 143) | PM ruled 2026-10-14 11:48Z "leave in place (not Rust code, not ours to move without a roadmap decision)" | RULED by the PM (not CireSnave) |

## 4. OverMind's own work (standing charge, CireSnave 2026-10-12)

| item | state | ruled? |
|---|---|---|
| Open PRs including Dependabot | none open at `a198c29` before this file's PR (#141) was opened; `gh pr list --state open` returned `[]` then. **At `f604d8d` (2026-10-24) one is open: #161, a Dependabot `npm_and_yarn` bump for `probe/`**: OverMind's to dispose of under this charge | RULED (standing charge) |
| Issue #110 (lane-restart: record a launch name only when it is a safe identifier) | DONE: merged as agentlife#115 (0.16.5), 2026-10-16T18:20Z, read back with `gh pr view`. The `git grep -c` over-deny this row used to name has no record (PM, 2026-10-17); row dropped | RULED, merged |
| Dispatch infrastructure: `check_profile`, model pinning, per-model `max_tokens`, per-model timeouts, tool_calls consumption proof | landed (PRs #83, #84, #90, #92) | RULED, merged |
| Local-model dispatch design and the Lightbulb OpenAI-compat checklist (#91) | design docs merged; no implementation scheduled here | UNRULED (needs the PM to schedule) |
| Claims checker, paid-provider cost-vs-quality comparison, Synapse sender-proof signing field | parked or pending in the lane's notes; **not re-verified at this ref** | UNRULED |
| Launch-on-message design (`docs/LAUNCH-ON-MESSAGE-DESIGN.md`, #153) | design only; OverMind runs no subscriber, the AgentLife launcher service executes `LANE-LAUNCH-POLICY.md`. Decisions D1a, D2a (notifier first), D3 = separate phase 2 after the 24h pilot passes, D4 (lane fetch-on-start, no readiness gate yet) | RULED (PM ASK-ANSWER 2026-10-17, relayed; D3 clarified by content) |
| **M8, owner agentlife:** `lane-restart` `notify.rs` and the launcher's PM-notify path move from the claude-peers broker to role `lane-restart@<account>` on the Synapse daemon API | **phases 1-3 MERGED 2026-10-17** (agentlife#123 notify via Synapse, outbox + launcher relay, dual backend; #124 `down` + restore probe; #126 `status --peers`), then agentlife#159/#160 (real approval records; outbox run against a real scratch daemon). **Remaining: the cutover flip of `notify_transport` and stopping the broker**; the flip must precede the broker stop or the notifier-only launcher goes silent. The built relay role is `agentlife-launcher`, not `lane-restart@<account>`: see row 6.C2 | RULED (PM 2026-10-17: owner agentlife; PM adds the portfolio row) |
| CodeRipper `licence` finding: `LICENSE-MIT` names a person as the holder (advisory portfolio check, #158, 2026-10-24) | WAIVED in `.coderipper.toml`; `LICENSE-MIT` unchanged | RULED (CireSnave 2026-10-24, board 272: person-named holder is correct until the entities exist). Revisit when they do |
| CodeRipper `SEC-002` (gitleaks generic-api-key) at `probe/pty_drive.py`, `probe/pty_lifecycle.py`, `probe/pty_v4.py` (#158, 2026-10-24) | WAIVED, 3 entries with reasons. `pty_drive.py` `SECRET` is an 18-character alphabetic canary phrase the probe types to test `/clear`, checked by shape not printed; the other two flagged lines are log-file path opens (false positives). Not verified locally (gitleaks absent here): CI is the check | RULED (PM TASK 2026-10-24) |

## 5. Ties to `PORTFOLIO-ROADMAP.md`

- "OverMind split: APPROVED" (section 3 above).
- "Session restart tool": assigned to OverMind, then moved to agentlife with step 5; the soak (section 1) is its live acceptance run.
- FAM/Synapse channel injection: PORTFOLIO-ROADMAP says "OverMind owns" the client-side subscription work; **no OverMind task is open for it** and it is not scheduled here: UNRULED.
- Cheap-model routing ("OverMind routes by cost per successful task") feeding the dashboard and claims work: depends on the items in section 4.

## 6. Spec-to-roadmap audit (PM TASK 2026-10-24, CireSnave's idea)

Read at `origin/main` = `f604d8d` (0.14.13): every doc below, in full, by five read-only Sonnet readers; the claims that
change a row here were re-checked by the lane (`gh pr list`, `git grep`, `git ls-remote`, the forge). Docs read (18):
`ARCHITECTURE-LAYERS-SPEC.md`, `DESIGN-PROPOSAL.md`, `USER-REPO-APP-FOLDERS-SPEC.md`, `RESTART-TOOL-DESIGN.md`,
`SUBAGENT-FORK-AND-AUDITOR-DESIGN.md`, `LOCAL-MODEL-DISPATCH-DESIGN.md`, `LIGHTBULB-OPENAI-COMPAT-CHECKLIST.md`,
`MEASUREMENTS.md`, `README.md`, `CHANGELOG.md`, `research/ai-to-ai-communication-papers.md`, `docs/LANE-LAUNCH-POLICY.md`,
`docs/LAUNCH-ON-MESSAGE-DESIGN.md`, `docs/PILOT-OVERMIND-LANE-CONFIG.md`, `docs/SOAK-RESTARTTEST-END-REPORT.md`,
`docs/SYNAPSE-CUTOVER-G5-G6-DESIGN.md`, `docs/superpowers/plans/2026-10-01-fork-and-curate.md`,
`docs/superpowers/plans/2026-10-08-phase0-import-direction.md`. Nothing in this section is started by the PR that added it.
Where a row says **CS call**, the answer is CireSnave's and travels through the PM; nobody acts on it before then.
Absence checks, with controls: ROADMAP had 0 hits for `auditor|fork` (control: it has `M8`); `src` has 0 hits for `auditor` outside
`fork.py` (control: `fork` hits); no `SessionConfig`/`plugin_api`/`addaudithook` in `src` or `tests` (control: the same search finds `fork`).

### 6.A Harness, handler, routing (`ARCHITECTURE-LAYERS-SPEC.md`, `DESIGN-PROPOSAL.md`)

| id | item | state | ruled? |
|---|---|---|---|
| A1 | Enforced import direction: the test, 18 frozen edges | **DONE** (#129, `tests/test_import_direction.py`). The spec's "nothing like it exists" is stale (row 6.H1) | RULED, merged |
| A2 | Later phases edit `ALLOWED` toward the layer table in the spec's section 1 | open, rides with A4 | UNRULED (PM to schedule) |
| A3 | **Phase 3: harness and plug-in API** (hooks, `SessionConfig`, `LoadedManifest`, versioning) and the chokepoint tests (static scan, audit hook, rogue fixtures, mutations). The spec's "assumed: the harness can mark all its own I/O as harness-owned" is unmeasured | **open; plan written** (`docs/superpowers/plans/2026-10-28-phase3-harness.md`, PRs P3.1-P3.7); nothing built | RULED by CireSnave 2026-10-25, relayed by the PM: *"A3: Build the harness/plug-in API."* This overrides the PM's earlier "defer" ruling |
| A4 | **Phase 4: handler.** OpenAI shapes out of `agent.py`/`providers.py` (`normalise_for_echo`); `detect_smuggled_tool_call` to the handler; a public harness API so `fork.py` stops importing private `agent._tool_result_message`; rename one of the two `Ledger`s (`gate.Ledger`, `ledger.py`); `reconcile` (`outcome.py`) has no caller; `dispatch_mcp._run` near-duplicates lanework's | open (callers still at `gate.py:45`, `fork.py:23`, `gate.py:324`; the last two items not re-verified) | UNRULED (PM to schedule) |
| A5 | **Phase 5: routing.** Model cards, a results ledger the router reads, a quota read view, and the **spend book**. The spec: paid providers need CireSnave's explicit approval | open; `ledger.py` is collection-only | UNRULED. **CS call**: spend-book budget and which paid providers may ever be used (ties to 6.D5) |
| A6 | README rewritten for the orchestrator; one-line pointers to the new home of each piece OverMind gave up | open, unverified | UNRULED (PM) |
| A7 | Port the non-Rust pieces to Rust | the section 3 row stands (PM ruled "leave in place"). The spec records CireSnave's words as "add to our eventual roadmap"; no port row existed | RULED by the PM only. Not a new call: the existing row stands unless the PM wants to ask |
| A8 | DESIGN-PROPOSAL 7.1: require `SenderVerdict::Verified`, map the sender id to a role in OverMind config, wait for replay suppression and sealing | held: **Synapse** (replay, sealing); #154 hardened the vouch flag only | UNRULED. See also D7 |
| A9 | DESIGN-PROPOSAL 7.2: agent-certificate/introduction app (Synapse slice f) | held: **Synapse lane** | n/a (their roadmap) |
| A10 | DESIGN-PROPOSAL section 3: restructure `CLAUDE.md` for a local-model lane; the doc itself calls it "a decision" | open; ruled to do, except the D5 free-tier stop | RULED by the PM 2026-10-25 while CireSnave sleeps (relayed, not his words; he can overrule): do it. There is no `CLAUDE.md` in this repo; the target is the portfolio file `C:\Projects\CLAUDE.md`, which the PM owns, so the deliverable is a draft for the PM, not an edit. The 16k target is E4 |

### 6.B Fork, curate, auditor (`SUBAGENT-FORK-AND-AUDITOR-DESIGN.md`, the fork-and-curate plan)

| id | item | state | ruled? |
|---|---|---|---|
| B1 | fork-and-curate: ledger lineage and `absorb`, `run_agent(history=)`, `curate_now`, `ForkCost`, `run_fork`, the wiring, status line; plus the seven deferred minors | **DONE**: #98 (spec + plan), #101, #103 (`fork.py`, `gate.py`, `agent.py`) | RULED, merged |
| B2 | **The auditor**: fires on elapsed time/turns only, never activity-gated; re-check backoff at least doubling; no separate kill-log, a killed fork's ledger entry is marked killed-with-salvage; audit cost gated behind the cheapest correct pre-filter | open, nothing built | Sequencing RULED by CireSnave 2026-10-01 per the design doc ("build fork-and-curate first"), so this is the sanctioned next step; scheduling UNRULED (PM) |
| B3 | **Parallel forks** (async dispatch on one thread, no OS threads) and the merge-mode taxonomy (paused single writer vs parallel multi-writer; "completion is not mergeability"). The doc says merge semantics must be designed before the build | open; async-in-principle RULED, design and go UNRULED | RULED by the PM 2026-10-25 while CireSnave sleeps (relayed, not his words; he can overrule): **no-go until the auditor (B2) ships; the row stays** |
| B4 | Automatic cost-based fork-or-inline policy ("the threshold is cost") | held: needs measured `ForkCost` data (collected on the ledger); the plan's own ruling says do not build before data | policy RULED, build UNRULED |
| B5 | Stage 1 fork contracts: snapshot/lineage ids, capability intersection (`parent ∩ task_policy ∩ project_sandbox ∩ resource_budget − denials`) enforced on five surfaces; "break into its own task sequence" | open; the fork gate today = parent policies + extra policies + `ForbidTools(fork)` only | UNRULED (needs a plan; more than one PR) |
| B6 | PTY-driven CLI-agent forking | held by CireSnave: "Stick with OpenAI-compatible providers at first." | RULED (hold). Reopens only if PTY dispatch ships |
| B7 | **Read the Broadridge patent (US20250252293A1) claim by claim "before this ships".** `fork.py` shipped in #101; no record of a read exists in the repo (not searched elsewhere) | open gate, status unknown | **CS call**: was the read done? If not, who does it |
| B8 | Fuel and Lightbulb pieces (donor-keeps-decoding test; wire `record/lookup/splice_prefix`; `strftime_now` hazard; `BlockPrefixIndex` rewire if a parent must end before its fork) | held: **Fuel and Lightbulb lanes**, in their own repos | n/a here |
| B9 | Extend MEASUREMENTS cost tracking to "fork vs tokens added inline" | open; `ForkCost` logs it per fork, no MEASUREMENTS section | UNRULED (low) |

### 6.C Restart tool, pilot, Synapse cutover, launch (`RESTART-TOOL-DESIGN.md`, `docs/`)

| id | item | state | ruled? |
|---|---|---|---|
| C1 | restarttest soak follow-ups | already in section 1 | n/a |
| C2 | **G5/G6 cutover** (`docs/SYNAPSE-CUTOVER-G5-G6-DESIGN.md`, #157): (a) cutover approval record `server:synapse, roles [*]` needs CireSnave's verbatim yes and an expiry; (b) **pilot records `claude-peers-synapse-dev-channels-pilot.json` etc. expire 2026-10-31** (per the doc; the files exist in `ciresnave/ciresnave` `.overmind/lane-restart/approvals`); (c) D1: keep the `agentlife-launcher` relay as the G6 implementation, instead of role `lane-restart@<account>`? (d) D2: must an `[ASK]` reply reach lane-restart (recommend no); (e) flip `notify_transport` (PM; the doc's blocker D3 is done, agentlife#160) | (a),(b) held for CireSnave; (c),(d) open PM decisions; (e) unblocked, PM's | (a),(b) **CS call**; (c)-(e) PM. **2026-10-28, PM:** (c) keep the `agentlife-launcher` relay as G6; (d) an `[ASK]` reply need not reach lane-restart. (b) no longer matters: `ciresnave/ciresnave#14` merged (Synapse-only approvals for every lane, no expiry), so the 10-31 pilot expiry is moot; (a) not re-read here |
| C3 | Pilot lane config (`docs/PILOT-OVERMIND-LANE-CONFIG.md`): apply `overmind.mcp.json` and the `launch_args` change at T-0; the runbook header says rc.94 and installs rc.93, unresolved; the doc's "G5 still blocking" line is stale (the record exists; who merged it was not checked) | held: **PM at T-0** | PM |
| C4 | Launch-on-message phase 2, and per-lane hand launches with the synapse flags (about 20 lanes) | held until the 24h pilot passes (D3, section 4); the roll-out is a PM or CireSnave action | **CS call** only if he wants to approve the roll-out; recommend the PM runs it under D3 |
| C5 | `LANE-LAUNCH-POLICY.md` section 6 open numbers: caps 3/12 per hour, no-claim 3, replay window 5 min are "proposals"; `launch_wait` 120 s unmeasured; no sink for `refused: model-not-pinned`; role-name equality between Synapse and AgentLife unverified; whether a launched lane with no HANDOFF claims its role unchecked; unattended-launch consent was "216: yes" (board 216) | held: **AgentLife service**, Synapse `claimed_at`; numbers need ratifying | caps: PM ratifies. Consent: RULED (board 216) |
| C6 | `lane-restart start` end to end against a real lane is unrun (LAUNCH-ON-MESSAGE section 7) | open; AgentLife's code, OverMind's design | UNRULED (PM) |
| C7 | Restart-tool leftovers: known limit (PM restart of a lane that wandered cwd is refused); the PM restarting itself has no one to ask (section 12.6); the fullscreen-renderer note; `remote_control` false when only `remoteControlAtStartup` sets it; the model shape in the state file "NOT CONFIRMED until a real capture"; the PM records a SHA-256 for `lane-restart.exe` | open, observations in `RESTART-TOOL-DESIGN.md`; owner agentlife for code | UNRULED. **CS call** only for a fallback when the PM restarts itself; recommend none |
| C8 | Soak cleanup: `synapsed` and the hourly monitor were left running, logs kept in `%LOCALAPPDATA%\synapse-restarttest`; the final token figures are to 18:49Z only | open | UNRULED (PM / synapse lane) |

### 6.D Local models, providers, claims (`LOCAL-MODEL-DISPATCH-DESIGN.md`, checklist, `MEASUREMENTS.md`)

| id | item | state | ruled? |
|---|---|---|---|
| D1 | Local dispatch infrastructure: `Provider.local`, `rig` provider, per-model timeouts, `probe/rig_probe.py` | **DONE** (#151, #90) | RULED, merged |
| D2 | Should local providers be exempt from quota tracking, or the book repurposed for concurrency? (`lanework.py` attaches one `QuotaBook` to every client; `providers.py` records every request) | open design question | UNRULED (PM) |
| D3 | Rig measurements: context window, timeout value, concurrency, multi-checkpoint serving; set `default_timeout_s`/`default_max_tokens` from `rig_probe.py` output | held: **hardware** (the rig), Lightbulb and Fuel lanes | n/a |
| D4 | Hand the Lightbulb OpenAI-compat checklist to the Lightbulb lane to score itself; test `tool_calls` directly; a `lightbulb` Provider entry (`rig` may cover it) | open; I found no evidence the hand-off happened | UNRULED (PM routes) |
| D5 | **Is the free-tier-only stop (CireSnave 2026-09-17: "For right now, we're solely working toward using free tiers only.") still in force?** It gates paid-provider comparison, local-model dispatch, the `CLAUDE.md` restructure (A10) and A5 | held on that answer | **CS call**. Options: still in force / lifted for local only / lifted. Recommend: lifted for local only (the rig exists) |
| D6 | Claims checker: `checker.py` is absent from `origin/main`; `claims/piece2-parked` exists at `cea1195` (`git ls-remote`); parked after 8 free-tier attempts, MEASUREMENTS section 35. Is `checker.py` the right first local task? Spend on a paid non-Claude model for it? | open; free-tier retry ruled first; every provider call needs a Windows Hello approval per `with-secret` use, and CireSnave is asleep | RULED by the PM 2026-10-25 while CireSnave sleeps (relayed, not his words; he can overrule): free-tier retry first (gemini-3.5-flash-lite). Paid model and first-local-task options not taken |
| D7 | Synapse sender-proof signing field (MEASUREMENTS section 20); Synapse#19 (inventory) merged, no signing PR found (one of three searches done: unknown, not "none") | held: **Synapse** protocol addition | UNRULED |
| D8 | Paid-provider cost-vs-quality comparison (CireSnave asked) | held until free-tier work settles | UNRULED; follows D5 |
| D9 | `dispatch.py` stays unconnected to `lanework.py` until DESIGN-PROPOSAL 7 questions 1-3 are answered (policy). Open scope questions in its section 5: local-model scope; drive CLI agents in v1 | held | RULED by the PM 2026-10-25 while CireSnave sleeps (relayed, not his words; he can overrule) for the `CLAUDE.md` half (with A10); the free-tier stop (D5) and the `dispatch.py`/`lanework.py` wiring questions stay CireSnave's. **The dispatch-wiring half is answered 2026-10-28** by CireSnave (verbatim in `DESIGN-PROPOSAL.md` 7.5): agent-generated commands run only in a sandbox (AntSpawn); anything with effects outside a sandbox is picked by name from a pre-registered list. Until a sandbox runner is callable, `dispatch.py` stays unconnected for agent-generated argvs; the refusal is plan step P3.5 and the sandbox seam is P3.7 |
| D10 | `README`: FAM live channel not wired into `lanework.py` | already in section 5 (UNRULED; FAM is being decommissioned) | n/a |

### 6.E Measurements and research (`MEASUREMENTS.md`, `research/`, `CHANGELOG.md`)

| id | item | state | ruled? |
|---|---|---|---|
| E1 | Daily-cap measurement by exhaustion for the four providers with no published cap, and `flash-lite`'s own cap; reading the providers' documentation is also not done | docs read 2026-10-24 (MEASUREMENTS 37): Cloudflare 10,000 neurons/day and OpenRouter 50 (under $10 credits) or 1000 (at least $10) requests/day are published; Groq, Gemini, NVIDIA publish no number we could fetch, so UNKNOWN. No exhaustion run | RULED by the PM 2026-10-25 while CireSnave sleeps (relayed, not his words; he can overrule): read docs only. DONE |
| E2 | Unmeasured, low stakes, free-tier lane work: input cost after a clear (the "context tax" is arithmetic); a long local session; hosted multi-turn rerun (Groq `qwen3.6-27b`, Cloudflare `llama-3.3-70b`); Google rules rerun (N=5, rate-limited out); P1 on each provider's default model; OpenRouter `nemotron-3-ultra-550b` (daily quota blocked); Mistral `codestral-2508` 0/4; OpenRouter/HF/Mistral untried for the checker task | open | UNRULED (PM schedules) |
| E3 | `model_timeout_s` is set for no model in `src` or `probe`: the mechanism (#90) exists but the glm-5.3 case that motivated it is unresolved | open | UNRULED |
| E4 | Lane instruction size target (16k or 41k tokens?) "is a decision, not a measurement" | ruled | RULED by the PM 2026-10-25 while CireSnave sleeps (relayed, not his words; he can overrule): **16k tokens** |
| E5 | Is a `codex` lane in scope? (nothing measured since 2026-09-09) | ruled | RULED by the PM 2026-10-25 while CireSnave sleeps (relayed, not his words; he can overrule): **no codex lane** |
| E6 | Nested `with-secret A -- with-secret B` not measured; needs two Windows Hello approvals | ruled | RULED by the PM 2026-10-25 while CireSnave sleeps (relayed, not his words; he can overrule): **skip**; not measured |
| E7 | `research/ai-to-ai-communication-papers.md` was written for the synapse lane's efficient AI-to-AI communication design: no row consumes it; reviewer notes unapplied (7 of 54 gains flagged; #3 journal version unchecked) | pointer sent to the synapse lane 2026-10-25 | RULED by the PM 2026-10-25 while CireSnave sleeps (relayed, not his words; he can overrule): leave to the synapse lane. DONE |
| E8 | FAM message-path defects (fam#65 open, `fam_send_message` schema) | held: FAM lane, message-path fixes only per the FAM scope rule | RULED (portfolio rule) |

### 6.F The user-repo app-folders spec (`USER-REPO-APP-FOLDERS-SPEC.md`)

| id | item | state | ruled? |
|---|---|---|---|
| F1 | Spec status DRAFT 0.1: licence of the text (CC0 or MIT OR Apache-2.0: "his call"), its name and home (the architecture spec says it goes with lane-restart, now agentlife), and whether it gets its own repo | ruled; moving to agentlife (agentlife#175 opened); OverMind removes its copy once agentlife merges it | RULED by the PM 2026-10-25 while CireSnave sleeps (relayed, not his words; he can overrule): licence `MIT OR Apache-2.0`, home agentlife |
| F2 | lane-restart does not follow `links.json` yet (`git grep links.json` outside the spec: 0) | open; owner agentlife | UNRULED |

### 6.H Docs that disagree with the code at `f604d8d` (each reported to the PM as a FINDING first; fix in a follow-up PR, docs only)

| id | doc and the stale statement | contradicted by | state |
|---|---|---|---|
| H1 | `ARCHITECTURE-LAYERS-SPEC.md` section 1 "nothing like it exists yet"; sections 4-5 describe `crates/`, `tools/` and the gate-fleet workflow as OverMind's; steps 7a and 4 not annotated done | #129; #136, #137, #143 | open |
| H2 | `DESIGN-PROPOSAL.md` 7.4 "PROPOSED, not built" and header "everything else is unbuilt"; "24 tests" | `dispatch_mcp.py:150,204` (`docs_only`, with the ruling quoted at :214); `src` has `fork`, `lanework`, `dispatch`, `dispatch_mcp`, `providers`; `tests/test_gate.py` has 49 tests | open |
| H3 | `RESTART-TOOL-DESIGN.md` line 3 "not yet built", line 241 "`--mcp-config`... not carried over", line 693 "still nothing installed", the 11.1 hold, 12.11; the file lives here though the code is in agentlife | its own sections 5, 9, 11.2; agentlife `relaunch.rs` | open. Whether the file should move to agentlife: CS-free, PM decides |
| H4 | `LOCAL-MODEL-DISPATCH-DESIGN.md` and the Lightbulb checklist: "uncommitted, no PR"; line numbers and the `ollama` hard-code predate #151 | merged #91; `providers.py:310,835` | open |
| H5 | `MEASUREMENTS.md`: section 34 says pytest/pnpm SKIPPED (OverMind#87); sections 27, 30, 32 run a fourth P1 task; the header says the repo is "deliberately empty"; section 5 says fam#44 unbuilt | `.github/workflows/ci.yml` installs both (#89, #87 closed); `probe/p1_bench.py:64` dropped it in 0.13.6; `src/overmind` has 12+ modules; fam#44 closed | open. A retraction must reach as far as the claim: addenda, not rewrites |
| H6 | `README.md`: "eight backends" | `PROVIDERS` has 9 (`rig`) | open |
| H7 | `docs/LANE-LAUNCH-POLICY.md` cites `docs/LAUNCHER.md` (moved to agentlife); `docs/PILOT-OVERMIND-LANE-CONFIG.md` G5 line; the fork-and-curate plan's Task 6 text, `ForkCost` finalisation and test names | no such file here; the approval record exists; #103 changed the behaviour | open |
| H8 | This file's header ref `a198c29` | rows citing 2026-10-24 events | corrected in this PR (header note); the rows keep their own refs |

### 6.Z Counts (this PR)

Docs read: 18. Rows added: 55 (A 10, B 9, C 8, D 10, E 8, F 2, H 8; some only record that a doc item is already a row, done, or another repo's), plus 3 corrections to existing text (the open-PRs row, the M8 row, the header). Rows needing a CireSnave call at the audit: A3, A5, A10, B3, B7, C2(a,b), D5, D6, D9, E1, E4, E5, E6, E7, F1 (15, of which E5, E6, E7 are low stakes). **Now (2026-10-25, PM rulings, relayed):** 9 ruled in full (A3, B3, D6 as to order, E1, E4, E5, E6, E7, F1); A10 and D9 ruled only for the `CLAUDE.md` half, with the D5 free-tier stop and the `dispatch.py`/`lanework.py` wiring still CireSnave's; still his, board 286: A5, B7, C2(a,b), D5.
