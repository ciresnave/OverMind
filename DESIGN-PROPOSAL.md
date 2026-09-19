# OverMind — design proposal

**Status: PROPOSAL. §2.4b IS NOW BUILT** (`src/overmind/gate.py`, 24 tests). Everything else is unbuilt. Written 2026-09-10 ~00:30Z against
[`MEASUREMENTS.md`](MEASUREMENTS.md), not against the original brief — several parts of that brief
are refuted below, with the measurement that refutes them.

> **Every design claim in this file cites a measurement or is marked `ASSUMPTION`.**
> Where a decision is CireSnave's rather than mine, it says so and stops.

---

## 0. What changed between the brief and this document

| the brief said | measured | consequence |
|---|---|---|
| receiving into a non-Claude agent is "not built anywhere" | §1, §7 — a stock client receives it; the method is an **advertised capability** | the receive half is **a subscription, not a subsystem** |
| a local LLM reads CLI screens because TUIs change | §2 — PTY→emulator→screen works; lifecycle verbs confirmed | keep it, but it is **mechanical**, not a model problem |
| *(mine)* "fuel's load doesn't fit the best local model" | §10.4 — a 3B model holds 112k and passed at 45k load | **withdrawn**; see §10.3 |
| *(implied)* bigger model = more capable | §8.2, §10.4 — 3B passed where 14B failed; 3B window 2.7× the 8B's | **rank by measurement, never by size** |

---

## 1. Shape

```
                        ┌──────────────────────────────────┐
                        │           OverMind               │
                        │  orchestrator + verification      │
                        └───┬───────────┬───────────┬──────┘
             MCP client     │           │           │  PTY driver
        (negotiated bind)   │           │           │  (ConPTY/vt)
                            ▼           ▼           ▼
                      ┌─────────┐  ┌─────────┐  ┌──────────────┐
                      │   FAM   │  │providers│  │ CLI agents   │
                      │ 20 tools│  │OpenAI-  │  │ claude, …    │
                      │ + push  │  │compat   │  │ live sessions│
                      └─────────┘  └─────────┘  └──────────────┘
```

Three edges. **All three are measured working**, none is speculative.

---

## 2. Decisions, each with the measurement behind it

### 2.1 Messaging rides on FAM. Synapse is not a candidate for this edge.

FAM has 20 MCP tools outbound and a channel push inbound, both exercised live from a non-Claude
client (§7). **Synapse has no injection surface at all** — 0 hits for MCP/stdio/JSON-RPC at
`origin/main 6e8d0588` (portfolio roadmap §1, that lane's measurement, not mine). Synapse moves
bytes between processes; it does not put them into an agent.

⚠️ **This is not a judgement on Synapse.** Different job. If OverMind later needs raw
process-to-process transport it is the right tool; for *agent context injection* it is not a
participant.

### 2.2 The receive half is a negotiated subscription — ~10 lines, not a component

The adapter advertises `capabilities.experimental["claude/channel"]` at handshake (§7). Derive the
method as `"notifications/" + key` and register a binding. **Do not hardcode the method name** —
that is the difference between special-casing FAM and negotiating with it.

⚠️ **Two silent failures must be designed around, both measured:**
- The Python SDK **drops unknown notifications at `logger.debug`** (§1). A client that registers
  nothing gets no error, ever.
- **Bindings are fixed at session construction, before the handshake** (§7), so discovery requires
  a **reconnect** — and a discovery connection is **not side-effect-free**: it consumed and
  permanently destroyed undelivered mail (§7.1).

**Therefore: connect once, discover, disconnect, reconnect with bindings — and treat the first
connection as destructive until FAM fixes §7.1.** If FAM fixes it, this collapses to one connect.

### 2.3 Providers: one OpenAI-compatible client, `base_url` is the only switch

Measured (§8, §12): Ollama's OpenAI-compatible endpoint drives the same loop that reaches Groq,
Google AI Studio, OpenRouter, NVIDIA NIM and Cloudflare Workers AI. **All five reach a verified
tool call; four are a pure base-URL + key swap.** This was an assumption in the brief and is now an
observation.

⚠️ **This paragraph previously read "no provider API key is set on this box … local is the only
option currently open." That was true when measured and is now false** — five free-tier keys exist
in `HKCU\Environment` under **non-conventional names** (`*_API_TOKEN`), and a long-running process
does **not** inherit them (§12). **The subject moved; the measurement was not wrong.**

⚠️ **Three provider behaviours the client must handle, all measured (§12, §12.1, §12.2):**
- **Rosters over-report.** NVIDIA entitles **17 of 80**; Google lists models that 404 for new keys.
  **Probe entitlement; do not trust `/models`.**
- **Free availability is per-model and transient.** OpenRouter 429'd two free models and served a
  third. **Fail over across models, never pin one.**
- **The assistant message an API hands you is not always a valid message to hand back.**
  Cloudflare rejects the `content: null` that every other provider returns on a tool-call message.
  **Normalise before echoing.**

### 2.4 🔴 THE LOAD-BEARING INVARIANT: every invocation is verified tool-side

**Measured, twice, in two unrelated subsystems:**
- `granite3.3:8b` announced *"Calling the function to list entities…"* with **zero** tool calls
  recorded. `llama3.1:8b` wrote *"Now, sending the message…"* having never called send (§8.2).
- My own PTY rig reported three green results from three turns that **never submitted** (§3.2).

⚠️ **AN ACTOR'S REPORT OF ITS WORK IS NOT THE WORK.** In both cases the wrong answer *matched
expectation exactly*, which is why it nearly shipped.

**So OverMind must never derive state from an actor's text.** Every tool call is confirmed against
the tool side; every CLI action is confirmed against the reconstructed screen; every "done" is
confirmed against an artifact. **This is not defensive polish — it is the difference between an
orchestrator that works and one that silently does nothing while narrating success.**

**Corollary, measured:** the dominant model failure is **emitting a tool call as JSON in the
message content** (4 of 5 failures, §8.2). The orchestrator must detect a tool-call-shaped blob in
`content` and treat it as a **protocol failure to re-prompt or repair**, not as prose.

### 2.4b 🔴 A RULE THAT MUST HOLD CANNOT BE A RULE IN A PROMPT

**Measured (§11), and it is the sharpest result in the file.** Given a rule the model **provably
holds** — it states it correctly in a separate conversation — compliance is:

| rule | `llama3.2:3b` | `qwen3:8b` |
|---|---|---|
| ordering — *list before send* | 🔴 **0/8** | ✅ 8/8 |
| precondition — *read `origin/main`* | ✅ 8/8 | ✅ 8/8 |
| prohibition — *never merge your own PR* | ✅ 7/8 | 🔴 **1/8** |

⚠️ **`qwen3:8b` said, verbatim: *"I must call `request_gate_review` instead of merging it"* — then
merged, 7 times out of 8.** That is this portfolio's own standing rule, broken silently and almost
always, by the model that looks most capable.

⚠️ **The two models are near-exact mirror images.** Neither dominates; **there is no "more obedient
model" and no single compliance score can describe one.** And **stating a rule and applying it are
decoupled** — so a comprehension check is worthless as a safety gate.

**THEREFORE, for anything irreversible — merge, publish, delete, push — the constraint is enforced
by the harness REFUSING THE CALL, never by text in a system prompt.** Better prompting does not
close a gap between perfect recitation and 12% compliance. A gate that refuses the tool call does.

**And model selection must be per-rule-set**: measure the model against **the actual rules it will
be given**, because capability does not predict which ones it will keep.

### 2.5 CLI puppeteering: PTY → terminal emulator → screen. Keep it.

Confirmed viable (§2). The lifecycle verbs the whole cost argument depends on are real:
**the process survives `/clear`** (pid identical, reproduced), and **`/clear` actually forgets**
(high-entropy token; control proves the same question retrieved it 30 s earlier).

⚠️ **Mechanics that must be in the implementation, all measured (§3):**
- **Type, then send Enter as a SEPARATE write.** One chunk reads as a *paste* and submits nothing.
- **Idle = positive screen signature**, never byte-quiet — quiet fired before the first frame.
- **Probe the keymap; do not derive it** — DECCKM is never set yet only `ESC O B` moves a menu.
- **Read menu selections back before pressing Enter.**
- **Capture the raw stream to a file; analyse offline.** Re-analysis is then free.

⚠️ **The local-LLM screen reader is a FALLBACK, not the primary mechanism.** Idle/working/menu
detection turned out to be **deterministic string and stability checks on the reconstructed
screen**. Reach for a model only where deterministic rules fail. This removes the hardest component
from the critical path — *the opposite conclusion from the brief's, and for a different reason than
the brief's §5 correction assumed.*

### 2.6 Model selection is a measured property, per model, per task

**Measured (§8.2, §10):** `llama3.2:3b` **passed** where `qwen2.5-coder:14b` **failed**, and holds
**112,058** tokens against `qwen3:8b`'s **34,051**. **Neither capability nor window tracks
parameter count.**

**So OverMind ships a capability probe, not a model list**: for each candidate, measure (a) does it
emit real tool calls, (b) its **actual** usable window by needle-with-`prompt_eval_count`, (c) does
it complete a representative task at the intended instruction load. **Re-run on every model or
runtime upgrade.**

⚠️ **Never trust the advertised window.** `qwen3:8b` advertises 40,960 and truncates **silently**
past it — and the truncation **drops the beginning**, which is where a `CLAUDE.md`'s most important
rules live (§10.1).

---

## 3. What is NOT decided, and why

| question | why it is open |
|---|---|
| **Language** | Every rig here is Python; the portfolio is Rust. Rust crates named by the PM (`portable-pty`, `vt100`, `genai`) are **unexercised** — §2's findings must be re-checked there. **A rewrite is a cost with no measurement behind it yet.** |
| **Can a local model do a lane's DAY?** | 🔴 **Not measured.** The task is **two tool calls**; a lane is dozens of turns with growing context (§10.6). |
| **Will it FOLLOW the rules it holds?** | 🔴 First evidence says **not reliably** — `llama3.2:3b` skipped or reordered the required verification step at **every** load including zero (§10.5). ⚠️ **A lane's instructions are almost entirely ordering and precondition rules, so this is the risk that matters most, and it is the least measured.** |
| **`CLAUDE.md` restructure target** | Depends on which model class must read it — **a decision, not a measurement** (§10.6). |

---

## 4. What I would build first, and what I would not

**FIRST — the smallest thing that is useful and cannot silently lie:**
1. The **verification harness** of §2.4, standalone. It is what makes every later result
   trustworthy, and both of tonight's near-miss false results were caught by an early version of it.
2. The **capability probe** of §2.6, over the 12 tool-capable local models. Output: a table of who
   can actually be given work.
3. **One narrow end-to-end lane** — a local model, a small instruction set, FAM tools, tool-side
   verification. Narrow because §10.5 says wide instruction-following is unproven.

**NOT FIRST:** the PTY layer. It is measured working and it is not on the path to *the cost
problem* — running routine work on non-Claude providers is, and that needs §2.3 + §2.4 + §2.6.
⚠️ **The PTY layer only pays off once there is something worth keeping a warm session for.**

---

## 5. ⚠️ For CireSnave — decisions I cannot make

1. **Language.** Python (measured, fast, matches every rig here) vs Rust (portfolio norm,
   unmeasured for this). I have no measurement favouring either for *this* program.
2. **Scope of local-model work.** §10.5 suggests local models suit **narrow jobs with small
   instruction sets** while Claude keeps the wide ones. **That is a policy about how the portfolio
   runs, not a fact I can measure.**
3. **Whether OverMind drives CLI agents at all in v1**, or is purely a provider-side orchestrator.
   Both are supported by what is measured; they are different programs.

---

## 6. Honest statement of what is proven

✅ The pipe: send **and** receive, non-Claude client, live server.
✅ A free local model drives real tools.
✅ A free local model holds a full lane's instruction load.
✅ CLI lifecycle control — create, clear, keep the process.

🔴 **Not proven: that any of this does a lane's work.** Two tool calls is not a day, and the one
model that holds the context does not reliably obey it. **Everything above is capability. None of
it is yet capacity.**


---

## 7. ⚠️ 2026-09-17 addendum — wiring `lanework.py` to a live dispatch channel is a security decision, not an engineering one

**Written before building it, not after finding a problem in it.** README's "What is not" names the
runner's transport as a CLI invocation, and closing that gap looks at first like plumbing:
`dispatch.py` already receives live inbound messages and runs them through `run_agent`; making it
build a `lanework.Task` from the message and call `run_task(..., publish=True)` instead looks like a
few lines.

⚠️ **`dispatch.py` IS BUILT AGAINST FAM, NOT SYNAPSE.** It has zero references to Synapse anywhere in
its code - every mechanism it names (the reply tool, the system-notice shape, the sealed-envelope
shape) is FAM's. It is a different, independent transport from the one §20 measured; nothing here
routes through Synapse or bypasses it, because it was never on that path.

🔴 **IT IS NOT PLUMBING. `Task.check` IS AN ARGV THIS HOST EXECUTES AS A REAL SUBPROCESS**, with
secrets scrubbed from its environment (`lanework.py`'s own docstring) but with no other confinement -
no container, no chroot, full access to this machine outside the git worktree. Today that argv comes
from a file a human or a lane operator writes, which is the entire trust boundary the design has ever
had: `lanework.py`'s own CLI usage assumes whoever wrote `task.json` is trusted. `dispatch.py`'s
design assumes the opposite of its input - "**THE INBOUND CONTENT IS UNTRUSTED**" is stated in its own
docstring - and its own `Dispatch.vouched` property reads a `sender_vouched` flag FAM supplies. ⚠️ **THE
ONE LIVE MEASUREMENT OF IT (§18, not §20) FOUND IT `false`** - a real dispatch through the live FAM
server, where the sender's identity was, in FAM's own terms, "the relay's word," not a
cryptographically verified account key. One observation, not a survey; whether FAM has a path to a
`true` value at all is unmeasured here. Connecting `dispatch.py` to `lanework.py` verbatim means an
unvouched FAM message chooses the command this host runs.

⚠️ **THE EXISTING GATE DOES NOT COVER THIS.** `WorkspaceConfined` governs `write_file` and
`replace_in_file` - the MODEL's six tools inside the worktree. `run_check` is not gated the same way:
it always runs the task's own declared `check`, and the gate has no policy over what that argv *is*.
A dispatch-supplied `Task` reaches `check` before the model ever calls a tool.

**Not decided here, because it is a policy about how much the fabric is allowed to make this host do,
not a fact I can measure:**

1. **Does a dispatched task ever get to supply `check` at all**, or must `check` always come from a
   host-side, pre-registered set of known-safe commands, with the dispatch only selecting one by name?
2. **If `check` may be dispatch-supplied, what proves the sender may be trusted with it** - `vouched`
   alone, given the one measurement of it (§18) came back false? Whether FAM has a working path to a
   verified `true` at all is unmeasured here, and a stronger identity check would need to exist
   *before* this, not be assumed by it.
3. **Does this need a second gate policy**, parallel to `WorkspaceConfined`, that inspects `Task`
   itself (not just tool calls made *during* the run) before `run_task` ever starts a subprocess?

**Until one of these is answered, `dispatch.py` and `lanework.py` stay unconnected.** The gap README
names is real; wiring it the obvious way would open a different one.

### 7.1 Update, same day — question 2 has a real answer, on Synapse's `main`, verified against the code

**CireSnave asked whether Synapse's own engineering work could close this, and pointed me at the
Synapse lane.** Their answer, checked directly against `origin/main` `ee1bf8c` in that repo rather
than taken on their word:

- `src/sender_auth.rs` exists. `canonical_input` builds a length-prefixed byte string from a domain
  tag, `message_id`, `from`/`to`, a microsecond timestamp, the security level, the signer's key id
  and a hash of the encrypted content - confirmed by reading the function. `TrustStore::verify`
  checks a real Ed25519 signature (`UnparsedPublicKey::verify`) against a **pinned** key and returns
  `SenderVerdict::{Verified{key_id}, Unverifiable{reason}, Contradicted{reason}}` - confirmed by
  reading the match arms. `TrustStore` exposes only `pin`/`pin_pem`; there is no discovery and no
  trust-on-first-use path anywhere in the file - confirmed by grep. `TransportManager::receive_messages`
  calls `store.verify(&incoming.message)` for every received message and attaches the verdict -
  confirmed at the exact call site.
- ⚠️ **§20's finding about `auth_integration.rs::verify_message_sender` is UNCHANGED and still true**
  on this same ref: it fetches a profile for the message's *claimed* `from_global_id` and checks
  `trust_level`, never touching `sender_proof` - confirmed by reading it. **Two functions coexist in
  the same codebase, one real and one that looks real and isn't; the fix is to call `TrustStore::verify`,
  never `verify_message_sender`.**

**What Synapse states plainly it will NOT give, by design:** a `Verified` verdict answers *which
pinned key signed this*, never *is this sender allowed to run this command*. That authorization
policy has to live in OverMind, matching this file's own §2.4 invariant that a rule enforced by
asking the sender is not enforced.

**Two gaps Synapse itself calls blocking for command dispatch, both in flight, neither on `main` yet:**
replay (a captured Verified message can be resent and re-verifies - no `message_id`/timestamp check
exists yet) and confidentiality (message bodies on `main` are readable by anyone holding the bytes
until sealing lands). **A `Verified` message today is not yet safe to treat as a one-shot
authorization**, independent of anything OverMind builds.

**Revised target, so this stops being an open question with no shape:** verified sender identity is
usable *today*. Question 1 (may `check` ever be dispatch-supplied, or only selected from a fixed set)
and question 3 (does `Task` need its own gate policy) are unaffected by this and still open. Question
2 becomes: require `SenderVerdict::Verified`, map the verified global id to a role through OverMind's
own config (never through Synapse), and hold `check` to a host-side allowlist regardless of who sent
the message - Synapse's own recommendation, and consistent with never asking the sender to vouch for
what they may do. Wiring should wait for replay suppression and sealing to reach Synapse's `main`
regardless, since a verified-but-replayable, verified-but-readable message is not yet a safe basis for
one.

### 7.2 Update, same day — an account-holder introduction app is planned, and where it does and does not help

**CireSnave told me directly** that he and the Synapse lane have been designing a web app for
introducing account holders (humans) to each other so the services they each control can be properly
authenticated before authorizing anything, with a free public instance planned first on his
ThinkersJournal.com. He asked whether this could help OverMind's model/provider authentication
question. Checked against Synapse's own spec on their local `feat/replay-suppression` branch (§9,
`docs/superpowers/specs/2026-09-17-replay-suppression-design.md`) and their direct answer, not taken
on either source alone:

- **What it mints.** Nothing in the message path itself. Two account holders each sign in with their
  own OIDC provider; the app is the single registered client at each, holds no secrets, and is used
  once, at introduction, exchanging **account public keys**. Both sides' local software shows the
  other's key fingerprint, so a substituted key is visible. The durable artifact is an **agent
  certificate**: the account key signs a statement binding an agent's signing/sealing key
  fingerprints, a label, a validity window, **coarse named permissions**, and a serial - so a receiver
  pins one key per account holder, not per agent. This is **slice f** in Synapse's plan (widened today
  from bare key rotation, because rotation is a certificate re-signing), scheduled after slice e
  (replay, in progress) and before TOFU discovery. The app itself is a separate, unbuilt project - not
  part of Synapse or any one website - designed after slice f lands.
- **What it does for a model provider.** Less than the analogy first suggests. OpenAI, Anthropic and
  Google will not participate in this scheme, so nothing here can cryptographically vouch that a
  remote endpoint *is* a given provider - that is what TLS and the provider's own API key already do,
  and a certificate from this scheme adds nothing to it. What it *can* authenticate is **the local
  side**: an OverMind adapter process for provider X becomes an agent under an account holder's
  account key, carrying a certificate saying "this key may act as a model-provider adapter for X, with
  these permissions." A peer authorizing that adapter to run inference is then trusting a **known
  account holder's own agent**, not a claim about the remote provider. Provider attestation proper is
  out of scope for this scheme; provider credentials stay in their own TLS/API-key channel, outside
  the fabric.
- **Status.** Decided in outline 2026-09-17, nothing built. Order: slice e (replay) is in progress now;
  slice f (account keys + agent certificates + rotation/revocation) next; then TOFU discovery; then the
  introduction app itself, its own project and repo. Treat this as **future work**, not something to
  design `dispatch.py`/`lanework.py` wiring against yet - when slice f lands, it replaces §7.1's
  hand-maintained "map verified id to a role in OverMind's own config" with "this agent's certificate,
  signed by a pinned account key, carries permission P," without changing anything else in §7.1's
  plan.

### 7.3 Update, same day — a DIFFERENT unattended channel was decided and built: an MCP tool, not `dispatch.py`

**CireSnave chose a different path into `lanework.py` than the one this whole section has been
analysing.** Not `dispatch.py`/FAM, and not waiting on Synapse's slices - an MCP tool
(`dispatch_lane_task`, `src/overmind/dispatch_mcp.py`) that any agent (Claude, GPT, or another
provider entirely) calls directly with a repo and a prompt. Built and shipped (OverMind#48), on the
same reasoning this section already established, applied to the actual design he asked for:

- **Question 1 answered, for this channel:** `check` is never caller-supplied. It comes from
  `repo_probe.py`'s fixed, host-owned table, keyed by a marker file present in the target repo
  (`Cargo.toml` → `cargo test`, `pyproject.toml`/`setup.py` → `python -m pytest`, `package.json` →
  `npm test`, `go.mod` → `go test ./...`). A repo with none of those markers gets no check at all
  (`NoCheckInferred`) - refused, not guessed.
- ⚠️ **Reading a repo's own CI config to CHOOSE the check would have reopened exactly the hole this
  section warned about** - that config is content the repo's own author controls, and an arbitrary
  dispatched repo is precisely the "don't otherwise trust it" case. CireSnave's own distinction,
  stated plainly when asked: CI config, an in-repo standards file, and a caller's own extra
  requirements (text or a `https://`-only URL to them) are all safe to fold in richly, because none
  of them becomes the check - they only become more TEXT in the goal, read by a model still confined
  by `WorkspaceConfined` and still verified against the host-chosen check regardless of what that
  text said.
- **Question 3 answered, narrowly, for this channel:** `dispatch_mcp.py` never operates on a
  caller-named path directly - every repo is `git clone`d fresh into a scratch directory before
  anything else happens, so this tool cannot `fetch`/`worktree add` against a checkout another lane
  is using. That is `Task`'s own validation for this entry point; it does not answer question 3 in
  general for a future `dispatch.py` wiring, which stays exactly as open as §7's original text left
  it.
- **`capabilities` is accepted and recorded, not yet routed on** - there is no per-capability
  measured data (the P1 bench is all one capability, tool-calling code edits), and pretending it
  changed provider selection today would itself be the §2.4 failure this whole file exists to name.

**`dispatch.py`/FAM remains exactly where §7 and §7.1 left it - unconnected to `lanework.py`,
questions 1 (for THAT channel) and 3 (in general) still open.** This section is about a second,
independent unattended path that reaches the same runner more narrowly, not a resolution of the
FAM question.

### 7.4 ⚠️ PROPOSED, not built - an explicit "docs-only / no executable check" mode, needs CireSnave

**PM finding, 2026-09-19, motivated by a real limitation §7.3's own `repo_probe.py` now surfaces
correctly rather than silently:** `infer_check` refuses (`NoCheckInferred`) whenever a repo has no
marker file `CHECK_TABLE` recognises, OR a marker's own content doesn't validate (a real fix, not this
proposal - see `repo_probe.py`'s own module docstring). That refusal is *correct* for code work: an
unverified change is not one this tool should ever auto-publish. But it also means `dispatch_lane_task`
cannot be used at all for a legitimate class of task this tool's own design already anticipates -
**docs-only work, where there genuinely is no executable check** (a README fix, a comment, a markdown
file with no test suite backing it at all, not even indirectly).

**What's proposed, in outline, not code:**

- An EXPLICIT, caller-chosen mode (not a fallback `infer_check` reaches for on its own) - the caller
  states up front "this is docs-only, there is no check," rather than the tool ever guessing that for
  itself. Guessing "no check needed" from a repo's shape would reopen exactly the hole §7's own
  reasoning has been closing all along: a caller (or an arbitrary repo) choosing whether verification
  happens at all.
- In this mode, the run **never auto-publishes**, regardless of what the model produced or how
  confident anything looks. The absence of an executable check means the absence of PASS/FAIL
  evidence, not a green light.
- The result instead **always routes to a human or the PM gate** for review before anything reaches a
  real branch or PR - the same "queue for review, never self-merge" discipline this portfolio's own
  `CLAUDE.md` already applies to every PR a lane opens, extended to cover a dispatch run that has no
  automated verification behind it at all.

**Why this needs CireSnave and isn't built here:** it changes WHAT THIS TOOL IS ALLOWED TO DO -
today, `dispatch_lane_task` refuses outright rather than publish anything unverified; this proposal
would let it produce and surface an unverified result, gated on a human seeing it before anything
happens with it. That is a real expansion of the tool's own risk surface (an unverified diff exists
and is shown to someone, even if it's never auto-merged), not an implementation detail - exactly the
class of decision §0/§2.4's own "narrating success" discipline requires a real ruling for, not an
agent's own judgment call. **Not implemented, not scoped further, until CireSnave decides whether this
mode should exist at all.**
