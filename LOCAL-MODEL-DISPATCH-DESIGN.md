# Local-model dispatch — design (NOT implementation)

**Status: draft, for PM review. No code written, no PR opened, per explicit instruction — the hardware
this targets does not exist yet.** This file is intentionally uncommitted; it exists on disk in the shared
working tree for the PM to read directly.

**Why now:** CireSnave is building a desktop (PCIe switch, several GPUs) to run Fuel + Lightbulb + OverMind
with locally-hosted models specifically to reduce Claude token spend. This document answers whether
OverMind's dispatch path is ready for that, what the minimal change would be, and — the load-bearing
question — whether `checker.py`'s eight failed cloud dispatch attempts (`MEASUREMENTS.md` §33, §35) could
finally get a real capability verdict against a local model instead of a provider-reliability wall.

Every claim below is marked **MEASURED** (read directly from this session, this repo, or an already-run
local-model experiment) or **ASSUMED** (inferred, or genuinely unknown pending hardware that doesn't
exist). Where a claim rests on `MEASUREMENTS.md` §28/§29, that data is from an **RTX 4070 Laptop GPU,
8GB VRAM** — not comparable to CireSnave's new hardware, and flagged as such every time it's cited.

---

## 1. Does the dispatch abstraction already support a local OpenAI-compatible endpoint?

**MEASURED, and the answer is: mostly yes, already built.** `src/overmind/providers.py` already carries a
working `"ollama"` entry in `PROVIDERS` (line 235):

```python
"ollama": Provider(
    key="ollama",
    base_url="http://127.0.0.1:11434/v1",
    secret_name="OLLAMA_API_KEY",   # ignored by Ollama; kept for shape
    prefer=("qwen3", "llama3.2"),
    fallback_models=("qwen3:8b", "llama3.2:3b"),
),
```

`available_providers()` (providers.py:684) special-cases it: `if name == "ollama" or read_secret(prov.secret_name)` —
local providers don't need a cloud API key to be considered available. `ProviderClient.chat()`, `roster()`,
`candidates()`, `resolved_base()` were read in full: no cloud-specific hardcoding found — no assumption of
HTTPS, no cloud-specific auth scheme, no assumption a roster endpoint returns cloud-shaped data. This
**isn't new design intent either**: `DESIGN-PROPOSAL.md` §2.3, written 2026-09-10, is titled *"Providers:
one OpenAI-compatible client, `base_url` is the only switch"* and states plainly: *"Ollama's OpenAI-compatible
endpoint drives the same loop that reaches Groq, Google AI Studio, OpenRouter, NVIDIA NIM and Cloudflare
Workers AI ... four are a pure base-URL + key swap."*

**Consequence for CireSnave's hardware**: adding a second local server (llama.cpp's server, vLLM — both
expose OpenAI-compatible endpoints on a configurable port) is **one more `Provider(...)` table entry**, the
same shape as the existing `ollama` one, pointed at whatever `base_url` the new box serves on. No new
abstraction needed.

**Failure-reporting distinction — the property this design must NOT lose (MEASURED, providers.py:82-116,
:491-493):** `ProviderError`/`RateLimited`/`TimedOut`/`NoUsableModel` are all **transport-classified**,
never behavioral. A local server that isn't running raises `URLError` → generic `ProviderError`, routed
into the same `attempts` accounting as any cloud HTTP error — never silently read as a capability result.
This is the exact distinction that made `checker.py`'s eight-attempt record (§33, §35) legible: six
provider-side failures were reported as provider failures, not as "the model couldn't do it." Whatever
gets added for local dispatch must keep "the local server wasn't running" and "the model couldn't do the
task" as two different, distinguishable outcomes — the abstraction already supports this; a new local
provider entry inherits it for free, but it's worth stating explicitly since it's the property most likely
to erode under pressure to "just make it work."

---

## 2. Minimal change to add a local provider — config, naming, failure reporting

**MEASURED**, and genuinely minimal:

- **Config shape**: one `Provider(...)` entry — `base_url`, a `secret_name` (ignorable, kept for dataclass
  shape consistency), `prefer`/`fallback_models` naming which local model tags to try. Zero code changes
  to `ProviderClient` itself.
- **`max_tokens` is already data-driven per provider/model (MEASURED, providers.py:163-172, built this
  session, PR #83)**: `Provider.model_max_tokens: Mapping[str, int]` and `Provider.default_max_tokens: int`,
  resolved by `resolve_max_tokens()`. A local provider needing a larger output budget than the cloud
  default (16384) needs **zero code change** — set `default_max_tokens` on the local `Provider` entry, or
  a `model_max_tokens` entry for one specific local model tag.
- **✅ FIXED since this document was first drafted (MEASURED, PR #90, merged 2026-09-24)**: `build_client()`
  used to hard-code `timeout=180.0` for every `ProviderClient` it built, for every provider — a literal
  float, not a `Provider` field, the same shape of gap `max_tokens` had before #83, and already measured
  biting `nvidia/z-ai/glm-5.3` in this session (§32, §35). `timeout` now resolves the same 3-tier way
  `max_tokens` does (`Provider.model_timeout_s`/`default_timeout_s`, via `resolve_timeout()`), authorized
  and scoped by the PM as the one piece of real code work this design identified. Every existing provider
  still resolves to today's 180.0 by default; a local provider needing a longer per-request timeout than a
  cloud API round-trip needs **zero further code change** — just set `default_timeout_s` on its `Provider`
  entry once real hardware numbers exist (§5 still applies to *what value* to set, not to whether the
  mechanism exists).
- **Naming**: a model is named exactly as Ollama (or vLLM/llama.cpp) names it — whatever tag the local
  server's own `/v1/models` (or a fixed `fallback_models` list, for servers with no roster endpoint) reports.
  No new naming convention needed; `canonical_model()` (providers.py) already handles the one measured
  quirk (a `models/` prefix mismatch) and that logic is provider-agnostic.

---

## 3. What would `checker.py` need to run against a local model?

**This is the section that matters most — it's the one that could finally produce the capability verdict
eight cloud attempts could not (MEASUREMENTS.md §33, §35).**

**MEASURED, this session's ledger**: full-size `checker.py` dispatch ≈27.5k prompt tokens, reaching at
most 16 real steps before a provider failure (attempt 6, `gemini-3.5-flash-lite`). The scoped-down
single-function version (attempt 8) was ≈18k prompt tokens and still failed provider-side (HTTP 503) —
task size was shown NOT to be the constraint on the cloud path; both sizes hit the identical failure mode.

**MEASURED, `src/overmind`**: `Task.check_timeout_s: int = 900` (lanework.py:132), `Task.max_steps: int = 20`.
**No `context_window` field or concept exists anywhere in `src/`** (`grep -r context_window src/` → zero
hits) — OverMind's own code has no notion of context window at all.

**MEASURED, `MEASUREMENTS.md` §28/§29 (RTX 4070 Laptop GPU, 8GB VRAM — NOT the new hardware)**: context was
capped at 16,384 tokens via an Ollama Modelfile tag (`PARAMETER num_ctx 16384`), separate from the output
`max_tokens` cap (8,192 there). **Context window is controlled entirely Ollama-side, per model tag — not
by anything in OverMind's own code.** §29 additionally measured: *"Long runs reported up to ~171k prompt
tokens over 16 steps ... the 16k cap probably truncated late steps"* — i.e. even on a task far smaller than
`checker.py`, a full multi-step lane run's ACCUMULATED transcript pressure on that hardware was already
straining a 16k context window, well before `checker.py`'s single-shot 18-27.5k prompt is even considered.

**MEASURED, `DESIGN-PROPOSAL.md` §3**: *"Can a local model do a lane's DAY? 🔴 Not measured. The task is
two tool calls; a lane is dozens of turns with growing context."* This remains true after this session's
work — nothing measured here or in §28/§29 resolves it. `checker.py` is a multi-step lane task (read
existing files, write `checker.py`, write `tests/test_claims_checker.py`, run the check, iterate on
failures) at the larger end of what's been attempted, not the smaller end.

**What this implies, stated plainly**: `checker.py` on a local model would need (a) a context window
comfortably larger than its own ~18-27.5k prompt size **plus** room for transcript growth across up to 20
steps (§29's own number — 171k prompt tokens over 16 steps on a smaller task — suggests the safe target is
well into six figures of context, not 16-32k), and (b) `max_tokens` set generously enough that a thinking
model doesn't truncate before answering (the exact failure `providers.DEFAULT_MAX_TOKENS=16384` already
guards against for cloud models, per §33/§35 — the same principle applies locally, just against a
provider-specific ceiling instead of a cloud one).

**ASSUMED, cannot be measured from this box**: what context window / model size CireSnave's new hardware
(PCIe switch, multiple GPUs, unbuilt) can actually serve. §28/§29's 16k figure is a single 8GB-VRAM laptop
GPU's practical ceiling, not a floor or a target for a multi-GPU desktop — no inference should be drawn
from it about what the new hardware will support, only that "does the new box comfortably clear the
context §29 already showed was tight" is the first thing to check once it exists.

---

## 4. Quota tracking and local models

**MEASURED, `src/overmind/quota.py` + `lanework.py:613`**: `build_client()` attaches the **same shared
user-level `QuotaBook` to every `ProviderClient` it builds, ollama included** — no exemption exists today.
Every quota-book call site (`blocked()`, `candidates()`, `chat()`) is null-safe if `quota=None` is passed
instead, so a local provider client with no quota book already works with zero code changes.

**As currently wired**, dispatching through `build_client()` against `ollama` would silently accumulate a
meaningless "requests used" counter under `ollama/<model>` in the shared `quota.json` forever — harmless
(the cap stays unset, so it never blocks), but semantically wrong, since quota's whole purpose is a cloud
daily-allowance concept that doesn't apply to a local, compute-bound model. **Open design question, not
decided here**: either (a) exempt local providers from quota tracking entirely (pass `quota=None` when
`task.provider_keys()` resolves to a known-local provider), or (b) repurpose the same bookkeeping for a
different local-relevant signal (e.g. concurrent-request limiting, if the new hardware can only usefully
serve one dispatch at a time) rather than leaving it as a silently-meaningless counter.

---

## 5. What CANNOT be decided until the hardware exists

Stated explicitly, per instruction — this is deliberately the least resolved section, not a gap in the
research:

- **Context window and model size the new hardware can actually serve.** Nothing measured on this box or
  in prior local-model work (§28/§29, 8GB laptop GPU) is comparable. This gates the answer to §3 entirely.
- **Per-request timeout VALUE the new hardware needs.** The mechanism is no longer the open question — §2's
  gap is fixed (PR #90, merged) and `Provider.default_timeout_s`/`model_timeout_s` are ready to receive a
  real number. The number itself is still unmeasurable without the actual hardware's real first-token
  latency on a multi-thousand-token prompt.
- **⚠️ Whether Lightbulb can serve ANY real model checkpoint end-to-end at all — a separate uncertainty
  from context-window size or model reliability below, not a variant of either.** PM update, 2026-09-24: the
  fuel/Lightbulb stack has two measured gaps between "fuel has GGUF support" and "a real GGUF checkpoint
  loads" — no config derivation from GGUF metadata, and `dequant_bytes_to_f32` refusing Q6_K even though
  `k_quants.rs` implements it one layer down. This second gap was found by RUNNING a real checkpoint, after
  two lanes had read the crates and concluded it worked — reading the code said "capable," running it said
  otherwise. **"A local model will be available to dispatch against" is itself a claim about a stack with
  unmeasured joins, not a given this document can assume.** Everything in §1-§4 above (the provider
  abstraction, the config shape, `checker.py`'s context-size needs) describes what OverMind would do ONCE a
  model is actually being served — it says nothing about whether Lightbulb currently gets that far on real
  hardware with a real checkpoint, and that has to be demonstrated, not inferred from either crate reading
  or wishful extrapolation from the code existing.
- **Whether the new hardware can serve more than one dispatch concurrently**, which bears directly on
  whether local dispatch needs its own concurrency/quota-like gate (§4) or can safely reuse nothing at all.
- **Whether a local model reliably FOLLOWS the harness's own rules across a full multi-step lane task.**
  `DESIGN-PROPOSAL.md` §3 already measured a real, distinct risk here on prior hardware: *"Will it FOLLOW
  the rules it holds? 🔴 First evidence says not reliably — `llama3.2:3b` skipped or reordered the required
  verification step at every load including zero."* This is a capability question separate from raw
  context/speed, and CANNOT be assumed to improve just because the new hardware is faster or bigger — it
  needs its own measurement once real dispatch is possible.
- **Whether `checker.py` specifically is the right FIRST local-dispatch task to attempt**, versus something
  smaller that isolates capability from capacity more cleanly (per `DESIGN-PROPOSAL.md` §6's own framing:
  *"Everything above is capability. None of it is yet capacity."* `checker.py` conflates both at once — a
  smaller lane task might produce a cleaner first local capability verdict before spending a run on the
  full `checker.py` surface again).

---

## `claims/piece2-parked` — does this change its status?

**Not changed by this document, per explicit instruction.** But recorded, since the park was dated
specifically so it wouldn't silently outlive its blocker (`MEASUREMENTS.md` §35, dated 2026-09-24): **if
local dispatch becomes real and produces a PASS verdict on `checker.py`, that ends the park** — for a
reason nobody could predict when it was dated (a hardware-driven unblock, not a retry-driven one). The
dated park is exactly the record that should catch that when it happens; this document doesn't move that
date, it just names the condition that would.

---

*No code was written for this document. No PR was opened. This file itself is uncommitted, sitting in the
shared working tree for review — delete, commit, or ask me to revise as needed.*
