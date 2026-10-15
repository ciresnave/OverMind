# OverMind roadmap

Written 2026-10-14 at OverMind `origin/main` = `a198c29` (0.13.3), on the PM's TASK (no roadmap was a defect).
Every row says **RULED** (with who ruled it and where) or **UNRULED** (nobody with authority has decided; the
lane does not act). Versions are the PM's, allocated at gate time. This file records state; the portfolio-wide
list is `PORTFOLIO-ROADMAP.md` in `C:\Projects`.

## 1. In flight

| item | state | ruled? | source |
|---|---|---|---|
| **restarttest soak (board 175)**: one message every 10 minutes into the restarttest session, 3 days | DONE 2026-10-15T18:52Z, 72.5 h: 435 sent / 435 seen, max shows 3 (watcher, ids 1-434), 0 tool_use, 0 compactions, daemon unchanged. NOT clean: 1 `auth_failure` line (the synapse lane's own curl, their statement); compaction bound untested; weekly-limit delta unmeasured. Report: `docs/SOAK-RESTARTTEST-END-REPORT.md` | RULED, CireSnave 2026-10-12: "1) Yes. 2) 1 message every 10 minutes.  3) Restart it." | docs; synapse lane owns the daemon |
| Follow-ups after the soak | token rate reported; weekly-limit delta and behaviour across a compaction (3x show bound, id dedupe) still unmeasured | OPEN (needs a banner reading and a run that compacts) | docs |
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
| 7a `licence-tools`: `tools/spdx.py`, `tools/preflight.py`, `tools/gate_fleet.py`, their two tests and the fleet workflow | **DONE**: repo `ThinkersJournal/licence-tools` 0.1.0 (`3ce26593`); removed from OverMind in 0.13.6 | RULED (PM 2026-10-14; name, 0.1.0, move) |
| 7b Create `ciresnave/measurements` and move `MEASUREMENTS.md` and the `probe/` files that do not import `overmind` | not started | Same: direction RULED, move/name/version UNRULED |
| The six probes that import `overmind` (`daily_limits`, `free_capacity`, `ledger_context`, `local_throughput`, `p1_bench`, `paper_summaries`) | **stay** | RULED (ruling 4, 2026-10-08) |
| `MEASUREMENTS.md` is cited by section number from 13 files in `src/overmind` and `tests`; the name and section numbers must not change | constraint on 7b | RULED by the roadmap's own wording; how the citations are repointed is UNRULED |
| PM duties: remove the two `rust` required contexts; make OverMind's licence check a required check | measured 2026-10-14 at `branches/main`, enforcement `non_admins`: 7 required contexts (`gate boundary is intact` and the six `tests (...)`); the two `rust` contexts are gone; **`Every source file declares the licence` is NOT required**, nor is the fleet job | RULED (PM duty); the licence-check half is OPEN, the PM's to do |
| Port the non-Rust pieces to Rust (CireSnave's caveat, 2026-10-08, board 143) | PM ruled 2026-10-14 11:48Z "leave in place (not Rust code, not ours to move without a roadmap decision)" | RULED by the PM (not CireSnave) |

## 4. OverMind's own work (standing charge, CireSnave 2026-10-12)

| item | state | ruled? |
|---|---|---|
| Open PRs including Dependabot | none open at `a198c29` before this file's PR (#141) was opened; `gh pr list --state open` returned `[]` then | RULED (standing charge) |
| Issue #110 and the `git grep -c` over-deny | queued, not started | waits on a PM TASK |
| Dispatch infrastructure: `check_profile`, model pinning, per-model `max_tokens`, per-model timeouts, tool_calls consumption proof | landed (PRs #83, #84, #90, #92) | RULED, merged |
| Local-model dispatch design and the Lightbulb OpenAI-compat checklist (#91) | design docs merged; no implementation scheduled here | UNRULED (needs the PM to schedule) |
| Claims checker, paid-provider cost-vs-quality comparison, Synapse sender-proof signing field | parked or pending in the lane's notes; **not re-verified at this ref** | UNRULED |

## 5. Ties to `PORTFOLIO-ROADMAP.md`

- "OverMind split: APPROVED" (section 3 above).
- "Session restart tool": assigned to OverMind, then moved to agentlife with step 5; the soak (section 1) is its live acceptance run.
- FAM/Synapse channel injection: PORTFOLIO-ROADMAP says "OverMind owns" the client-side subscription work; **no OverMind task is open for it** and it is not scheduled here: UNRULED.
- Cheap-model routing ("OverMind routes by cost per successful task") feeding the dashboard and claims work: depends on the items in section 4.
