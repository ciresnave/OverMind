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
