# Subagent fork-and-curate, and the auditor — design proposal

**Status: fork-and-curate (§2 item 1, sequential) BUILT in `src/overmind/fork.py` -
see `docs/superpowers/plans/2026-10-01-fork-and-curate.md`. Parallel forks (§4.5, §6
merge semantics), the automatic fork-or-inline policy (§4.1 - data now collected on
every fork's ledger entry), and the auditor (§3) are NOT built.** Written 2026-09-30 by the portfolio PM, from a design
CireSnave worked out with Muse (his name for that instance: Pip) and refined here. Every claim below is
marked **MEASURED** (verified against `origin/main` of the named repo, this session, dated) or **ASSUMPTION**
(inferred, not yet confirmed against OverMind's own code — the OverMind lane should verify or refute these
before planning implementation). Where a decision is CireSnave's, it says so and stops.

**Revised 2026-09-30, reviewed by all three affected repos' own lanes (OverMind ~17:17Z, Lightbulb and Fuel
~17:18-17:21Z) — both open verification questions in §5 are now answered, and every correction below is
folded in at the section it touches, not left as a bolted-on erratum.**

**OverMind review (5 corrections):** (1) OverMind does not actually dispatch CLI agents via PTY today —
that was a false MEASURED claim, corrected in §1, scope is OpenAI-compatible providers only for now; (2)
OverMind reaches local models only over OpenAI-compatible HTTP, so whether local forks are cheap is
Lightbulb's question, not directly OverMind's — §1, §5; (3) 🔴 a curated summary alone conflicts with
OverMind's own core invariant (`agent.py:3-9`: "an actor's report of its work is not the work") — a fork
must also hand back a ledger of what it actually did, not just a narrative — §2, §3; (4) `run_agent` is
synchronous and single-threaded, so an audit can only fire between steps, never mid-generation — §3; (5)
the auditor must layer on top of OverMind's existing cheap stops (`max_steps`, `NO_PROGRESS`), not replace
them — §3.

**Lightbulb review**: confirmed the donor-must-stay-open mechanism is correct and real copy-on-write, found
a genuine (if small) wiring gap — zero callers of the matching logic outside its own tests — and, on a
follow-up ask, resolved the chat-template-determinism question with one non-issue and one real (general,
not fork-specific) hazard: live-date rendering in `strftime_now`. All in §1.

**Fuel review**: confirmed (independently of Lightbulb, different code, different repo — real corroboration)
that donor teardown is not required; found that the parent-continues-and-diverges case is structurally
plausible but unverified at Fuel's own test layer, and recommended a test addition rather than volunteering
to build it; corrected a misattributed quote in Lightbulb's review; flagged a no-internal-locking constraint
relevant only if `§4`'s parallel-forks question goes to genuine thread concurrency. Full detail in its own
section near the end of this document.

---

## 0. The idea, compressed

An LLM forced to produce a final answer under a fixed budget (time, tokens, turns) hallucinates or gives up
early not because timeouts are wrong, but because **time is the limiter on a single, undifferentiated
context** — everything the model generates while exploring stays in the same window it has to reason from
later, and eventually crowds it out or forces premature closure.

The fix: let a parent agent **fork itself** to run a sub-task. The fork inherits the parent's full context
(cheaply — copy-on-write, not a re-send), runs to completion (or is cut off), **curates its own output**
down to what the parent actually needs, hands back only that summary, and is discarded. The parent's own
context never absorbs the fork's intermediate exploration — only the curated result. Critically, **the
curation happens inside the fork (which has full context to judge relevance) but is authored by the same
"mind" as the parent (so it knows what the parent will need)** — resolving the dilemma Pip raised: if the
child curates, it might drop what the parent needed; if the parent curates, it has already paid the context
cost curation was supposed to avoid. Self-forking dissolves this: the fork *is* the parent, briefly, with a
private copy of its own context to spend.

A second piece, layered on top: an **auditor**. When a fork is taking longer than expected, the parent forks
*again* — this time to interrogate the first fork, adversarially, about whether its current path is worth
continuing. If the auditor decides to kill the stuck fork, it interrogates it first and extracts whatever is
worth keeping, so a relaunch doesn't start from zero.

**This is not a novel mechanism in the abstract — it is the exact pattern already running the session that
wrote this document.** Claude Code's own `Agent` tool, called with `subagent_type: "fork"`, does precisely
this: the fork inherits full conversation context, runs in the background, keeps its tool output out of the
parent's context, and returns only a summary when it completes. The idea is proven at the application layer
today, in a product CireSnave already uses. What's proposed here is building the **general-purpose,
portfolio-wide version** of it — available regardless of which model or backend is running the agent, not
just inside one harness.

---

## 1. The two pieces have different homes, and that split is the main design decision

**Correctness (orchestration policy)** — when to fork, what "hand back a summary" means, how the parent
decides a fork's summary is sufficient, when to spawn an auditor, what the auditor's verdict authorizes —
is **portable across models and backends**. It does not need cache-sharing, engine internals, or anything
model-specific. This is OverMind's job, and the natural home already exists: `run_agent` (`agent.py:158`)
owns the tool loop, keeps `messages` as a list, and already injects harness notes between steps. A fork is
"copy `messages`, run the loop from them." An audit is "append a user turn at the next step boundary." Both
are small extensions of an existing mechanism, not new architecture — **MEASURED, OverMind's own review,
2026-09-30**.

**`[OverMind review, correction 1]` MEASURED, and the original version of this sentence was wrong: OverMind
does NOT dispatch CLI agents via PTY today.** `src/overmind` has zero PTY drivers — every `pty` hit there is
a comment or the string "empty." PTY exists only in `probe/` (11 files, a different subsystem), and
`DESIGN-PROPOSAL.md` §2.5 describes PTY-driven CLI dispatch as a **design decision**, not shipped code. So,
as of today, **this whole feature applies to OpenAI-compatible providers only** (`src/overmind/providers.py`,
cloud and local/Ollama through one client — this part was correctly measured). For CLI agents there is
currently no tool-call boundary OverMind can inject at, because OverMind isn't driving their tool loop at
all. If PTY-driven CLI dispatch ships later, extending fork/audit to it is separate scope, not assumed here.

**Efficiency (the actual copy-on-write)** — the fork not re-paying the full prefill cost of the parent's
context — is **not** something an orchestrator can give you from outside. A real LLM's "context" is a KV
cache, not process memory; true copy-on-write means the fork and the parent share the *same computed
attention state* for the shared prefix, diverging only where the fork generates something new. That is an
**inference-engine** concern.

**MEASURED, and this is the headline finding of this document: that engine-level primitive already exists
and is already on `origin/main` of Fuel.** `fuel-core/src/kv_block_pool.rs` and
`fuel-inference/src/multi_session.rs` both carry `register_prefix`/`release_prefix`/`splice_prefix` and
`PagedSessionScheduler::add_session_sharing_prefix` (verified via `git grep` against `origin/main`,
2026-09-30). Per Fuel's own plan doc
(`fuel/docs/superpowers/plans/2026-07-31-prefix-sharing-serving.md`, status block: "ALL TASKS IMPLEMENTED +
VERIFIED"), this is described as letting "a new paged decode session reuse an already-computed KV prefix …
computing the prefix ONCE and reading it from many concurrent sessions," via a **transactional, refcounted**
splice that Fuel enforces as an invariant, not a per-consumer convention.

**This is rung-1 — same absolute token positions — and rung-1 is exactly what forking needs, not rung-2.**
Fuel's plan explicitly scopes rung-1 to "the shared-system-prompt case, prefix at position 0" and defers
rung-2 (RoPE delta-rotation for a prefix reused at a *different* position than it was computed at) as a
separate, later op. A conversation fork never needs rung-2: the parent has generated tokens `0..N`; the fork
shares exactly those tokens at exactly those same absolute positions `0..N` and continues generating from
position `N`, precisely like the parent would have. There is no position shift. **ASSUMPTION, to be
confirmed with Fuel, not verified by the PM**: that this reasoning is correct and no Fuel-side change is
needed to support fork-then-diverge specifically (as opposed to the shared-system-prompt case it was built
for) — the mechanism looks identical, but Fuel's own lane should confirm before OverMind builds against it.

**`[OverMind review, correction 2]` MEASURED: OverMind cannot call Fuel's `add_session_sharing_prefix`
directly — it reaches local models only over OpenAI-compatible HTTP, through `providers.py`.** That means
whether a local fork is actually cheap is decided entirely by what's on the other side of that HTTP call —
i.e., **Lightbulb's question, not OverMind's**. Fuel's rung-1 mechanics (§5, question 1) matter to Lightbulb,
which is the thing that would call Fuel directly; they don't matter to OverMind's own implementation, which
only ever sees Lightbulb's (or any other provider's) OpenAI-compatible surface.

Lightbulb already has its *own* layer on top of Fuel's primitive — `src/model_fuel/policies.rs` implements
`PrefixMatch` (a content-addressed prefix index: `lookup(&mut self, tokens: &[u32]) -> Option<PrefixMatch>`)
and `splice_prefix`, described in its own doc comment as distinct from Fuel's pool-level `splice_prefix`
because it "takes a `PrefixMatch` with stricter policy" — i.e., Lightbulb already matches an incoming token
sequence against previously-seen prefixes and reuses the cached KV automatically, by content, not by an
explicit session handle.

**`[Lightbulb review, 2026-09-30] CONFIRMED, and the direction was backwards from my original phrasing**:
the donor session is REQUIRED to stay open while referenced, not merely permitted to — Lightbulb's own doc
comment: "Donor lifetime is this index's responsibility. A donor must stay open (never discarded) while it
is referenced," enforced by `is_donor()`/`donor_refs`, and `splice_prefix` re-checks `donor_blocks` against
the live pool before splicing (a shrunk/discarded donor is caught as an error, never silent corruption).
Underneath, Fuel's own `kv_block_pool.rs` confirms this is real copy-on-write — `splice()` bumps refcounts on
shared physical blocks, `cow_break()` gives a session a private copy the instant it needs to WRITE a shared
block, and decode only ever appends new blocks (never mutates an already-full shared one), so a donor that
keeps decoding after being spliced from is safe — built for exactly this fork pattern. *(Correction, Fuel
review below: the phrase "parallel trains of thought" does not appear anywhere in Fuel's own repo —
`git grep` returns 0 hits against a working positive control. Wherever that phrase was read, it isn't a
Fuel source; removed here so it doesn't get cited as one downstream.)*

**The real remaining gap, per Lightbulb: wiring, not zero work.** `BlockPrefixIndex`/`record`/`lookup`/
`splice_prefix` has **zero callers anywhere in Lightbulb's src tree outside its own tests** (grepped the
whole tree). The matching logic is correct and ready; nothing in the serving path today calls `.record()` at
a live point, or `.lookup()`/`splice_prefix()` to actually spawn a fork. That wiring is small but real, not
"zero new Lightbulb code" as this doc originally speculated.

**Design note for the implementation plan, not yet acted on**: Fuel has shipped a *newer* primitive since
Lightbulb's wrapper was written — `PrefixId`/`register_prefix`/`splice_prefix_from` (Fuel's registry-owned
prefix, §1 above), which lets the REGISTRY control a shared prefix's lifetime instead of the live donor
session — explicitly so a donor can be discarded while a consumer's reference stays valid, decoupling
prefix lifetime from donor teardown. Lightbulb's `BlockPrefixIndex` does **not** use this yet; it splices
directly off the live donor `SessionHandle`, which is the actual source of the "donor must stay open"
requirement above. For this design's primary pattern — the parent keeps running past the fork point, forks
are always shorter-lived than their parent — donor-stays-open is fine as-is. It would only become a problem
if a *parent* session itself needs to end while a fork it spawned is still running independently; if that
turns out to matter, Lightbulb's wrapper should be rewired against `register_prefix`/`PrefixId` rather than
raw donor splicing, since Fuel already built the primitive for it.

**`[OverMind review]` One addition to the Lightbulb question**, also owed to the OpenAI-HTTP boundary: the
match OverMind's fork request would trigger is against the **re-rendered chat template text**, not the
parent's raw generated KV state directly — OverMind sends messages, Lightbulb renders them through a chat
template, and it's the rendered text whose prefix gets matched. That prefix is identical between the
parent's next request and the fork's request **only if the template renders the same message history
deterministically** — which a thinking-stripping template (one that removes prior `<think>` blocks from
history before re-rendering) may not do consistently. Lightbulb's lane should account for this when
answering §5's question, not just whether the KV-level matching itself works.

**`[Lightbulb review, answering the above, 2026-09-30]` CHECKED, mixed result — one non-issue, one real
hazard, neither fork-specific.** Background fact that shapes the whole question: Lightbulb has **no
incremental/cached tokenization today** — `build_prompt()` (`src/api/openai/chat.rs:519-590`) calls
`template.render(raw)` fresh on *every* request, re-rendering the entire history from scratch each time, so
"does the parent's next request match the fork's request" reduces to "does `render()` produce byte-identical
output given byte-identical input," full stop.

- **Thinking-stripping: no divergence found.** It's implemented entirely inside checkpoint-supplied Jinja
  templates (e.g. Qwen3's, via `.split`/`.strip`/`.lstrip`/`.rstrip` on a `loop.last`-style check), so it's
  content/position-driven, not session-state-driven — and every shared message (everything before the fork
  point) has at least one later message appended after it in BOTH the parent's continuation and the fork's,
  so it's never "last" in either render. The stripping decision lines up identically both times.
- **Real hazard, and it's general, not fork-specific**: `render()` registers a `strftime_now(fmt)` Jinja
  function (`chat_template.rs:213-231`) that calls `chrono::Local::now()` **live at render time** — used by
  Llama-3.x templates to interpolate the current date into the system message. Two renders of an
  *identical* message list on different calendar days (or straddling local midnight) produce different
  text, hence different tokens, for what should be the same prefix. This would silently defeat content-
  addressed prefix matching generally — not just for a fork, any repeat request for the same conversation
  across a day boundary — but it bears directly here: a parent's next-turn request and a fork's request
  issued moments apart around midnight could fail to match a prefix that's semantically identical.
  **Recommended fix** (Lightbulb's own lane, for its implementation plan when tasked): either stop
  re-rendering full history from scratch per request — hold/reuse already-tokenized prefix tokens past a
  stable point — or pin the date at session/conversation start rather than re-evaluating it live on every
  render, if exact byte-for-byte prefix stability is required for the fork/splice mechanism to fire
  reliably.

**For remote models (Claude via API, or any CLI-driven agent OverMind doesn't run inference for itself)**:
there is no server-side fork available, so a fork there means resending the shared prefix as an independent
call. This is more expensive than the local rung-1 case but not as expensive as it sounds — most providers,
Anthropic included, apply their own prompt caching to repeated prefixes, so a meaningful fraction of the
efficiency win still happens, just at the provider's discretion rather than under Fuel's guarantee. This
path needs no new capability; OverMind already dispatches to these providers uniformly.

**Bottom line on scope**: this is very possibly an **OverMind-only build** for the orchestration logic,
consuming primitives that already exist end-to-end for local models and degrade gracefully for remote ones.
That should be confirmed, not assumed — see §5.

---

## 2. What OverMind needs to add (the actual gap)

1. **A `fork(session, task)` orchestration primitive.** `run_agent` already owns exactly the state a fork
   needs to copy — its `messages` list — so implementation-wise this is "copy `messages`, start a new
   `run_agent` loop from the copy," not new architecture (OverMind review). Given a running agent session
   (local, over an OpenAI-compatible provider, or remote), start a child that:
   - shares the parent's context up to the fork point (via whatever the provider does with a resent prefix —
     for a local provider that's Lightbulb's `PrefixMatch`, see §1's correction; for remote, provider-side
     prompt caching where available),
   - runs the given sub-task to completion or to a stop condition,
   - **always** produces a curated summary on request, not only at natural completion (see §3 — this is the
     property the auditor depends on),
   - is torn down after handing back its result; **the parent never ingests the fork's raw transcript, but
     always ingests the fork's ledger** — see the correction immediately below, this is not optional.

   **`[OverMind review, correction 3]` 🔴 A curated summary ALONE conflicts with OverMind's own core
   invariant** (`agent.py:3-9`: *"an actor's report of its work is not the work"*). A fork's narrative
   summary is exactly the kind of testimony that invariant exists to distrust — it's the fork's own account
   of what it did, and nothing stops that account from being wrong or self-serving, same as any other
   agent's self-report. **The fix: a fork must hand back its ledger — a structured record of every gated
   effect it actually executed (file writes, commands run, external calls made) — alongside its narrative
   summary, and the parent must be able to verify what happened from the ledger, never only from the
   summary.** This applies equally to the auditor's "interim summary before kill" (§3) — an interrogated
   fork's account of its own progress is testimony too, and the ledger-so-far must travel with it. Restated
   from §0/§1's looser framing: "the parent never ingests raw output" should read **"never ingests the raw
   transcript; always ingests the ledger."**

2. **A decision policy for when to fork vs. run inline.** Not designed here — this is a judgment call about
   cost/latency tradeoffs that belongs with whoever owns OverMind's dispatch cost model (`MEASUREMENTS.md`
   already tracks per-provider cost; extending it to "cost of a fork vs. cost of the tokens a sub-task would
   otherwise add to the parent's own context" is in scope for implementation planning, not this doc).

3. **The auditor**, layered on (1): see §3.

**What OverMind does NOT need to add**, per the findings above: a KV-cache-sharing mechanism (Fuel has it),
a content-matching cache lookup for local models (Lightbulb has it — confirmed, see §1). **Lightbulb does
have small remaining work of its own**, confirmed by its own lane: wiring `record`/`lookup`/`splice_prefix`
into the actual serving path, since nothing calls them outside tests today. OverMind's job is the
orchestration policy and the provider-agnostic interface; Lightbulb's job (separate from OverMind's
implementation, tracked in Lightbulb's own plan when tasked) is making its already-correct matching logic
reachable from a real request.

---

## 3. The auditor

**Trigger: elapsed time or turns since the fork started, full stop — never gated on activity level.**
CireSnave's explicit correction during design: a sub-agent that's busy — furiously generating and calling
tools — can be exactly as stuck as one that's gone quiet, specifically when it's looping or chasing an
irrelevant rabbit hole. An activity-based gate (e.g. "only audit if no new tool calls recently") would skip
the auditor precisely when it's most needed, because rabbit-holing *looks* maximally active on any such
proxy. The only correct trigger is pure elapsed time/turns, independent of how much motion the fork is
generating. Any further cost containment (see §4) must come from somewhere other than an activity gate.

**Mechanism: reuse the resume-with-message primitive, not a new interrupt facility.** A sub-agent structured
as a tool-use loop (call a tool, get a result, decide next step) has a natural injection point at every tool
boundary — the auditor doesn't need to pause mid-generation, only inject its question at the fork's next
turn and read the response before the fork's own next action. This is architecturally the same primitive as
(1) in §2 — an auditor is a fork-and-curate, triggered early and adversarially, talking *to* another fork
instead of running independently.

**`[OverMind review, correction 4]` MEASURED, and this bounds the mechanism above more tightly: `run_agent`
is synchronous and single-threaded, with no background concurrency.** A time/turn-count trigger can only be
checked *between* steps — that's natural and matches the mechanism above — but **one long `chat()` call
cannot be audited mid-generation**; `resolve_timeout` (`providers.py:304`) is the only cutoff available
inside a single call, and it's a hard timeout, not an interrogation. Practical consequence: the auditor can
only act at step boundaries of a fork that has returned control to the loop at least once. **Running forks
in parallel** (so a fork can be audited while genuinely concurrent with the parent, rather than the parent
blocking on it) **would be new concurrency work, not reuse of the existing loop** — out of scope for a first
build unless CireSnave wants it in scope from the start (see §4).

**On kill: interrogate before terminating, so a relaunch doesn't start from zero.** Every fork must support
producing an interim summary on demand (not only at natural completion) — this is a design constraint on §2
item 1, not a separate mechanism. If the auditor decides the fork's path isn't worth continuing, it asks for
that interim summary before tearing the fork down, and hands it to the parent alongside the kill decision.

**Two risks, not reasons to skip this, but design constraints to build in from the start:**
- **The auditor is also an LLM, and "is this rabbit hole worth it" is a judgment call it can get wrong.**
  An overzealous auditor could kill genuinely-close-to-finish work. Auditor kill decisions should be logged
  somewhere reviewable — the same discipline this portfolio already applies to PM decisions on
  `CIRESNAVE-DECISIONS.md` — not silent.
- **Cost.** Spawning a full LLM auditor conversation on every "taking a while" fork adds tokens on top of
  the fork's own cost, which matters given the portfolio's weekly token-budget pressure (see
  `PORTFOLIO-EVIDENCE.md` / `CIRESNAVE-EXPECTATIONS.md` §9's routing guidance). Gate the *auditor itself*
  behind the cheapest possible pre-filter that is still correct given the trigger constraint above — e.g.
  back off how often the auditor re-checks after a first "keep going" verdict, rather than skipping checks
  based on activity (which would reintroduce the exact bug just fixed).

**`[OverMind review, correction 5]` MEASURED: the LLM auditor must sit ON TOP of OverMind's existing cheap
stops, not replace them.** `run_agent` already has `max_steps` (a hard turn limit) and `NO_PROGRESS`
(consecutive identical tool calls) — both end some rabbit holes deterministically, at zero LLM cost. Neither
is an activity gate on the auditor (they don't decide whether to *invoke* the auditor based on how active the
fork looks — they're independent, unconditional stops that fire on their own criteria), so they don't
conflict with the elapsed-time-only trigger above. Keep them as the cheap floor; reserve the LLM auditor for
the cases they can't catch — a genuine rabbit hole that varies its calls enough not to trip `NO_PROGRESS` but
still isn't converging.

---

## 4. CireSnave's rulings (2026-10-01) — all six answered, verbatim

**1. Fork decision policy — cost/latency threshold.** *"Unless a task is deemed time sensitive, latency
doesn't matter. As such, the threshold is cost. If it is cheaper to run one way than the other, do so."*
**Implementation implication**: the fork-vs-inline decision is a cost comparison, not a latency
comparison, except when a task is explicitly flagged time-sensitive (in which case latency governs
instead). Needs a per-task time-sensitivity flag/signal in `run_agent`'s task framing, and a cost model
for "fork and curate" vs. "run inline" to compare against.

**2. Auditor re-check backoff cadence.** *"It should be a settable default so that the user or an agent
working on their behalf can fine tune the delays to best suit their desired outcomes. Initially, I would
say at least double the time of the previous delay before checking... but that's a guess I'm pulling out
of the air without much to back it."* **Implementation implication**: exponential backoff (≥2x) as the
shipped default, exposed as a configurable value, not hardcoded. Treat the 2x figure as a starting point
to tune from measurement, not a validated constant.

**3. Kill-log.** *"I'm not 100% sure the auditor even needs a kill log. If the parent agent has the
findings and the knowledge that the sub-agent was killed, is that logged enough?"* **Resolution**: yes —
this matches §2/§3's own ledger requirement (a killed fork still hands back whatever the auditor
extracted before the kill, through the same ledger path a normal fork's return uses). No separate
kill-log artifact; the parent's own ledger entry for that fork, marked killed-with-salvage, is the record.

**4. Sequencing.** *"Build fork-and-curate first as it will help us even when building the auditor."*
**Confirmed** — matches this design's own proposed ordering (§0, §3). No override.

**5. Parallel forks / concurrency.** *"Why would we need concurrency across threads when we can already
have concurrency across models, model pools, and the like within a single thread?"* **This is correct,
and it's already what this design's own Fuel-review note above concluded independently**: OverMind-level
"parallel" forks don't need OS thread/process concurrency at all — each fork makes its own async HTTP
request to a model backend (local pool or remote API), and multiple in-flight requests on one thread is
real concurrency from the orchestrator's perspective, because the actual compute happens in the backend,
not in `run_agent`'s own loop. **Build async-dispatch concurrent forks** (one thread, many in-flight
model calls via async I/O), **not OS-thread concurrency**. Fuel's no-internal-locking constraint on
`KvBlockPool`/`PagedSessionScheduler` only bites if something runs two OS threads mutating one Fuel pool
instance directly — async-dispatch-on-one-thread never does that, so the constraint doesn't apply to the
scope just approved.

**6. PTY-driven CLI-agent forking.** *"Stick with OpenAI-compatible providers at first."* **Confirmed** —
matches the already-corrected scope (§1, correction 1). PTY-driven CLI-agent forking stays out of scope
until it's separately tasked.

All six questions this design raised are now answered. Nothing remains blocking an implementation plan.

---

## 5. Before implementation planning: two things need verification, not assumption

1. ~~Ask Fuel's lane to confirm...~~ **ANSWERED 2026-09-30 by Fuel's own lane (full detail in the `[Fuel
   review]` section below), MIXED**: the no-teardown-required half is **CONFIRMED** by direct code read,
   independently matching Lightbulb's finding (different code, different repo — real corroboration). The
   parent-continues-and-diverges half is **STRUCTURALLY PLAUSIBLE but UNVERIFIED** — no existing Fuel test
   exercises a donor continuing to decode while a sharer spliced from it also decodes; the one test with two
   independent continuations tears the donor down first. Fuel recommends adding that test before treating
   this as load-bearing for an implementation plan, and is not volunteering to build it unprompted. **No
   Fuel-side code change is expected either way** — this is a test-coverage gap, not a design gap.
2. ~~Ask Lightbulb's lane to confirm...~~ **ANSWERED 2026-09-30 by Lightbulb's own lane, see §1: the
   matching logic is correct and already requires (not merely tolerates) a still-open donor, but has zero
   callers in the serving path — real wiring work remains, tracked for Lightbulb's own implementation plan.**
   Chat-template rendering determinism (§1, correction 2's addition) is **also answered**, same section:
   thinking-stripping is a non-issue (content-driven, not session-state-driven); `strftime_now`'s live
   date-rendering is a real, general (non-fork-specific) hazard that should be fixed as part of Lightbulb's
   own implementation work, not specific to this feature.

**Both questions are now answered. Remaining concrete items before implementation, none blocking the
design itself:**
- Fuel: add a test exercising "donor keeps decoding while a sharer spliced from it also decodes" — the
  parent-continues-and-diverges case is structurally plausible but unverified at Fuel's own test layer
  (§ `[Fuel review]` below).
- Lightbulb: wire `record`/`lookup`/`splice_prefix` into the actual serving path (currently zero callers
  outside tests), and fix (or at least account for) `strftime_now`'s live-date hazard so prefix matching is
  stable across a render-time boundary.
- These are small, scoped, and owned by the repos that found them — not reasons to hold OverMind's own
  implementation plan, which doesn't depend on either being fixed first (OverMind never calls Fuel directly,
  and a Lightbulb-side gap degrades gracefully to "the fork just isn't cheap yet," not "the fork is wrong").

This becomes a **writing-plans**-skill implementation plan scoped almost entirely to OverMind's own
orchestration layer (`src/overmind/`) — the PM does not have enough grounding in OverMind's codebase to
write that plan's bite-sized, file-and-line-level tasks honestly, so that step should be done by whoever
holds OverMind's own context, against this document as the spec.

---

## 6. Addendum 2026-10-01: a larger external brief, folded in

CireSnave was independently handed a longer design document, "Forked-Agent Orchestration: Architecture
Discussion Brief," covering the same territory as this doc but going further in several places. The PM
reviewed it against this design and against ground truth verified in this same session; CireSnave then
asked the PM to get patent/IP due-diligence done and push every concept — plus the gaps found while doing
so — into the correct project roadmaps. This section is that fold-in for OverMind's piece. Fuel's and
Lightbulb's pieces are in their own `ROADMAP.md` files, cross-referenced here rather than duplicated.

**What the bigger brief adds that this design left thin, worth adopting:**

- **Merge-mode taxonomy.** This design (§0-§3) assumed sequential fork-then-return (parent waits, single
  writer, cheap merge). The brief correctly separates **paused-single-writer** (what this design already
  covers) from **parallel multi-writer** merges, and treats "completion ≠ mergeability" as a first-class
  concern: a fork can finish its task correctly and still return something that conflicts with newer parent
  state if the parent's own context changed while the fork was running. **This design does not yet handle
  that case** — it's only reachable once §4's parallel-forks question is answered yes, but the merge
  semantics should be designed before that, not after.
- **Capability inheritance as subtractive intersection.** Formalize as
  `effective_capabilities(child) = capabilities(parent) ∩ task_policy ∩ project_sandbox ∩ resource_budget
  − explicit_denials`. Matches the portfolio's existing "a capability is not a permission" principle
  (`CLAUDE.md` §8) but gives it an actual composition rule for forks specifically — worth adopting as the
  concrete authority model once OverMind builds capability-scoped forks.
- **Lineage as structured data, not narrative.** The brief's framing ("lineage turns accountability into
  data": every fork records its ancestor chain, base snapshot, granted capability, and governing task) is
  a cleaner articulation of the exact fix OverMind's own review already forced into §2/§3 of this design
  (a fork must hand back a ledger of real effects, not just a curated summary). Worth adopting this
  phrasing/schema directly rather than re-deriving it.
- **Auditor loop-detection signals.** §3 of this design nailed the auditor's *trigger* (elapsed time only,
  never activity-gated) but left "what does the auditor actually check once it fires" unspecified. The
  brief's list is a concrete answer: repeated tool-call patterns with no artifact delta, stable uncertainty
  across turns, budget burn without new evidence, child-creation without consolidation. Adopt as the
  auditor's check procedure; does not change the trigger rule.

**Scope flag, not adopted as-is:** the brief's own "Stage 1: Fork contracts" bundles snapshot/lineage IDs,
full capability enforcement across five side-effect surfaces (filesystem, messages, credentials, budgets,
child creation), and auditor-trigger-plus-salvage into one stage. That's bigger than a first increment.
Treat it as a milestone broken into its own task sequence (same shape as CodeRipper's plan), not a single
PR, whenever CireSnave or OverMind tasks an implementation plan.

**IP due-diligence (2026-10-01, web research only — not a legal clearance):** the fork/collapse-with-
capability-inheritance pattern is architecturally close to one issued patent, Broadridge Financial
Solutions' **US20250252293A1** ("Systems and Methods of Large Language Model Driven Orchestration of
Task-Specific Machine Learning Software Agents") — covers LLM-orchestrated sub-agents generally, likely
does not read on subtractive capability inheritance or paused/parallel merge-contract semantics
specifically, but close enough that it should get a direct claim-by-claim read before this ships, not just
this summary. No litigation found in the LLM-agent-orchestration space. Lower risk than the KV-cache pieces
(see Fuel's and Lightbulb's roadmaps) — this is well-trodden process-fork/actor-supervision territory
applied to LLM agents, not a novel mechanism in itself.

---

## `[Fuel review, 2026-09-30T17:18Z]` — answer to §5 item 1

**Read directly, at `origin/main`: `fuel-core/src/kv_block_pool.rs` (pool mechanism) and
`fuel-inference/src/multi_session.rs` (scheduler wrapper), plus every test whose name mentions `prefix`/
`shar` in both files — not from the plan doc's status block.** Landed after Lightbulb's own review above, so
this is read as independent corroboration where it agrees (separately derived: different code, different
repo — worth noting per this portfolio's own evidence discipline) rather than restated.

**One correction to Lightbulb's review, since it's attributed to Fuel specifically and will get relayed
further**: *"Fuel's own doc calls this the 'parallel trains of thought' mechanism"* — **that phrase does not
appear anywhere in Fuel's repo** (`git grep -in "parallel trains of thought" origin/main` → 0 hits; positive
control, `git grep -c "prefix sharing"` on the same ref → 3 hits, so the query itself works). Whatever
Lightbulb read that phrase in, it isn't Fuel's docs — flagging so it doesn't get cited as a Fuel source
downstream.

**Confirms Lightbulb's core finding independently: `register_prefix` does not require the donor torn
down, and a donor that keeps decoding after being registered/spliced from is safe by construction.**
Neither `KvBlockPool::register_prefix` nor the scheduler's `PagedSessionScheduler::register_prefix` reads
session phase/`suspended`/finished state — the only precondition is a snapshot check that the donor already
has `prefix_blocks * block_size` tokens fully filled. The safety argument matches Lightbulb's: Fuel's KV
cache is append-only (a filled block is never rewritten, only referenced), so `register_prefix`'s refcount
bump over the donor's *already-filled* blocks can never be invalidated by anything the donor generates
afterward — that lands in a fresh block beyond the shared range, guarded by the same CoW-break logic
Lightbulb cites. `prefix_owner_keeps_blocks_alive_after_donor_discarded`
(`fuel-core/src/kv_block_pool.rs:1588`) independently confirms discard is optional, not required: it
registers, THEN discards, and checks refcount drops 2→1 with nothing freed.

**The one thing neither review has flagged: no test in Fuel's own suite exercises the donor CONTINUING to
decode concurrently with a sharer spliced from it.** The only test with two independent continuations off
one prefix — `two_sharers_of_one_prefix_decode_independently`
(`fuel-inference/src/multi_session.rs:2773`) — calls `sched.reap_finished()` on the donor **before**
creating either sharer; the donor there has `max_new=1` and is already finished by that point. So the
donor-keeps-running case is STRUCTURALLY PLAUSIBLE (same reasoning Lightbulb gives, and I reach it
independently) but UNVERIFIED at Fuel's own scheduler layer — no test has watched it pass. Recommend adding
one before this assumption is load-bearing for an implementation plan: register a prefix, keep stepping the
donor while a sharer spliced from it also decodes, assert both diverge correctly. Small, mechanical addition
to the existing test's shape minus the `reap_finished()` call — flagging as a gap for whoever picks up
Fuel-side work, not volunteering to do it unprompted in this session.

**One addition bearing on §4's "parallel forks" open question: `KvBlockPool`/`PagedSessionScheduler` have
no internal locking** — every mutating method takes `&mut self`, no `Arc`/`Mutex`/`RwLock` anywhere in
`kv_block_pool.rs`'s own definition. Fuel's model is one scheduler stepping every session (donor + every
sharer) in a single loop; "the donor keeps running while a child diverges" in Fuel's world means the
donor's session object stays open and keeps getting ticked by that same loop, never a second thread
mutating the pool concurrently. If §4's "parallel forks" question gets decided yes at genuine
thread/process concurrency against one pool instance, that needs external synchronization Fuel doesn't
supply today — doesn't block the design as written (single-loop cooperative scheduling is exactly what the
existing tests exercise), but worth having on record before that question is decided.

**Summary: §5 item 1's second half (no teardown required) is CONFIRMED by direct code read, independently
matching Lightbulb's finding. The first half (parent-continues-and-diverges) is STRUCTURALLY PLAUSIBLE but
UNVERIFIED at Fuel's own test layer — recommend the test addition above before treating it as given. No
Fuel-side code change is expected. One citation in Lightbulb's review needs correcting (the misattributed
quote, above).**
