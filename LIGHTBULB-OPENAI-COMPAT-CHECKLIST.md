# What OverMind requires of an OpenAI-compatible endpoint — a scoreable checklist

**Status: draft, for PM review before it goes to the Lightbulb lane. Not committed, not a PR.** Every item
below is read directly out of `src/overmind/providers.py`, `agent.py`, and `gate.py` (this repo, current
`main`) — not out of `LOCAL-MODEL-DISPATCH-DESIGN.md` or any other prior document, per explicit instruction:
a design doc is a cached claim like any other artifact, and the only trustworthy source for "what does
OverMind's code actually require" is the code itself.

**Purpose:** CireSnave asked, verbatim: *"OverMind is designed to sit atop OpenAI compatible APIs. Can
Lightbulb provide an OpenAI compatible API? If so, why would OverMind need to treat Lightbulb any
different from a remote provider?"* This is the measurable form of that question — the Lightbulb lane
scores itself against each row (`✅ have it` / `❌ don't have it` / `⚠️ partial`), and the honest count is the
real answer, not an impression from reading crates.

**How to read MUST vs OPTIONAL:** MUST means OverMind's dispatch path will not function at all without it.
OPTIONAL means OverMind degrades gracefully without it — a missing optional capability changes *behavior*
(less retry resilience, less token accounting, etc.), never *whether dispatch works at all*.

---

## 1. HTTP surface

| # | Requirement | MUST / OPTIONAL | Source |
|---|---|---|---|
| 1.1 | `POST {base_url}/chat/completions` returns a JSON body | **MUST** | providers.py:657 |
| 1.2 | `GET {base_url}/models` (a roster endpoint) | **OPTIONAL** — `models_path` can be `None`; OverMind falls back to a fixed model-id list instead. Cloudflare already runs this way in production. | providers.py:171, :255, :559 |
| 1.3 | HTTPS | **NOT REQUIRED AT ALL** — no scheme check anywhere in the client. OverMind's own existing `ollama` provider entry already runs over plain `http://127.0.0.1:11434/v1`. | providers.py:261 (existing entry) |
| 1.4 | Server-Sent Events / streaming responses | **NOT USED, AT ALL** — zero references to streaming in the client. Every request is a single blocking call awaiting one complete JSON response. | providers.py (grepped, zero hits) |

## 2. Chat completions — request body

Every request sends exactly these fields, nothing else:

| # | Field | MUST / OPTIONAL |
|---|---|---|
| 2.1 | `model` (string) | **MUST** be accepted |
| 2.2 | `messages` (array, OpenAI role/content shape) | **MUST** be accepted |
| 2.3 | `temperature` (float) | **MUST** be accepted (always sent, default `0.0`) |
| 2.4 | `max_tokens` (int) | **MUST** be accepted (always sent — see §5, this is now per-model configurable on OverMind's side) |
| 2.5 | `tools` (OpenAI `tools:[{type:"function",function:{...}}]` array) | **OPTIONAL to require** — only sent `if tools:`, omitted entirely on turns/tasks with none |

**Nothing else is ever in the payload.** No `stream`, no `top_p`, no `stop`, no vendor-specific fields.

## 3. Chat completions — response body

| # | Field OverMind reads | MUST / OPTIONAL | Notes |
|---|---|---|---|
| 3.1 | `choices[0].message` | **MUST** be present, non-empty | An empty `choices` array is handled as a named failure ("no choices returned"), not a crash — but dispatch can't proceed without it. |
| 3.2 | `choices[0].message.content` | **OPTIONAL** (falls back to `""`) | |
| 3.3 | `choices[0].message.tool_calls` | **OPTIONAL** (falls back to `[]`) | Required only on turns where the model needs to call a tool. |
| 3.4 | `choices[0].finish_reason` | **OPTIONAL**, but recommended | If present and equal to `"length"`, `"max_tokens"`, or `"max_output_tokens"` (case-insensitive), OverMind correctly reports a truncated reply as a truncation, not a silent/empty answer. If absent, OverMind cannot make this distinction and a truncated reply may misread as "the model had nothing to say." |
| 3.5 | `usage.prompt_tokens` / `completion_tokens` / `total_tokens` | **OPTIONAL** | If `usage` is missing or not an object, OverMind records `reported=False` rather than crashing or guessing zero. Token accounting is simply unavailable, not broken. |

## 4. Auth and headers

| # | Requirement | MUST / OPTIONAL |
|---|---|---|
| 4.1 | Accept an `Authorization: Bearer <token>` header without erroring | **MUST tolerate it, need not validate it** — OverMind always sends this header, even to providers (like the existing `ollama` entry) that ignore it entirely. A local server just needs to not reject the request for having an auth header it doesn't check. |
| 4.2 | Accept `Content-Type: application/json`, `Accept: application/json`, a custom `User-Agent` | **MUST tolerate**, need not require |

## 5. Error handling

| # | Behavior | MUST / OPTIONAL |
|---|---|---|
| 5.1 | Return HTTP 429 on rate-limit, with a body OverMind can text-scan for a "daily" signal | **OPTIONAL** — a local server realistically never needs to 429 at all (it's not a shared multi-tenant quota system); if it never does, OverMind's 429/retry path simply never triggers, which is fine. |
| 5.2 | Return HTTP 402 on an account-wide exhaustion | **OPTIONAL**, same reasoning — irrelevant for a local server with no billing concept. |
| 5.3 | Any other error status (500, connection refused, timeout) | **MUST just be a real HTTP error or a real connection failure** — OverMind already handles every other status code and every connection-level failure generically (marks that model bad, moves to the next candidate). Nothing local-specific needed here. |

**No retry/backoff is required of the server.** OverMind's own client does the retrying (429 only, up to 2
attempts with exponential backoff); everything else fails over to the next candidate model immediately,
with no expectation the server retries anything itself.

## 6. Tool-calling — the one place a partial implementation shows

| # | Capability | MUST / OPTIONAL | What happens if it's missing |
|---|---|---|---|
| 6.1 | Native OpenAI-shaped `tool_calls` in the response, when the model decides to call a tool | **Strongly preferred, not hard-required** | OverMind's agent loop already has a fallback: `detect_smuggled_tool_call()` regex-scans plain-text `content` for tool-call-shaped JSON a model wrote there instead of a real `tool_calls` entry — built specifically because local models have already been measured doing this. On the FIRST occurrence in a run, the model gets one re-prompt ("emit it as a real tool call, or answer without one"). On a SECOND occurrence in the same run, the harness stops the task with `PROTOCOL_FAILURE` rather than continuing to nudge. |
| 6.2 | `content: null` on an assistant message that made a tool call, echoed back on the next turn | **MUST tolerate `content: null` coming FROM the server**, but OverMind coerces it to `""` before ever sending it back — a server only needs to accept `content: ""`, never `content: null`, on what OverMind sends it. | N/A — this is a compatibility fix OverMind already applies universally (originally for Cloudflare), so Lightbulb gets it for free. |

**Scoreable framing for 6.1**: if Lightbulb's OpenAI-compat layer can emit real `tool_calls` for at least
some models, that's the clean path. If it can only pass through models that write tool-call JSON as plain
text (or doesn't reshape it at all), OverMind still functions — but every task that needs a tool call
costs one extra re-prompt turn the first time, and a model that does this twice in one run fails the task
outright. **This is the one row where "technically OpenAI-compatible" and "works well in OverMind" can
diverge**, and it's worth Lightbulb testing directly rather than assuming from the transport layer alone.

## 7. Concurrency

| # | Requirement | MUST / OPTIONAL |
|---|---|---|
| 7.1 | Handle concurrent requests | **NOT REQUIRED** — OverMind's dispatch path is strictly sequential everywhere: one candidate model tried after another, one retry attempt after another, never in parallel. A server that can only serve one request at a time is not a mismatch with anything OverMind's client does today. |

## 8. Timeout — now fully configurable, zero code needed for Lightbulb specifically

Both `max_tokens` and per-request `timeout` are data-driven per provider/model as of `main` (PRs #83, #90)
— a `Provider` table entry for Lightbulb (`base_url`, optional `default_timeout_s`/`model_timeout_s` for a
longer local-inference timeout, optional `default_max_tokens`/`model_max_tokens` for a larger output
budget) is the **entire** integration surface on OverMind's side. Today's fallback (180s timeout, 16384
max_tokens) applies unless Lightbulb's entry sets its own.

## 9. ⚠️ Correction to the headline claim below — one line, not zero

**"One more `Provider(...)` table entry" is not quite the whole integration cost, measured precisely:**

```
providers.py:745    if name == "ollama" or read_secret(prov.secret_name):
```

`available_providers()` only returns providers whose secret is actually **readable** — with `"ollama"`
hardcoded as the sole exemption. **A Lightbulb `Provider` entry with no `LIGHTBULB_API_KEY` set in the
environment would be silently ABSENT from the candidate list — not a crash, an absence**, which is worse to
diagnose than an error. This is the only hardcoded provider-name comparison anywhere in the client
(positive control: `"ollama"` appears exactly 3 times in `providers.py` — the table entry, this exemption,
and nowhere else — so the query that found this finds what's actually there, not nothing).

**Two ways to close it, in order of recommendation:**

1. **Recommended**: add `local: bool = False` to `Provider`, change line 745 to
   `if prov.local or read_secret(prov.secret_name):`, and retrofit the existing `ollama` entry to set
   `local=True` instead of relying on its name matching a string literal. One-line change at the call site,
   one field added to the dataclass, and a third local provider (or a fourth) costs nothing — no more
   hardcoded names to grow.
2. **Not recommended, but zero code**: set a dummy `LIGHTBULB_API_KEY` (any non-empty value) in the
   environment, since `read_secret()` only checks for presence, never validity. Works today, no PR needed,
   but leaves the same trap for the next local provider that isn't named `"ollama"`.

**So, precisely: the integration is one `Provider(...)` entry PLUS either the one-line generalisation in
(1) or a dummy environment variable in (2) — not "zero special-case code" outright.** The claim below is
corrected accordingly, not softened.

---

## The direct answer to CireSnave's question, as measured

**If Lightbulb's endpoint satisfies §1-§5 and §7 (all either MUST-and-trivial or already OPTIONAL), OverMind
needs one `Provider(...)` table entry plus the one-line fix in §9** — not code specific to Lightbulb's own
behavior, a generic gap in how ANY local provider is recognized as available, already latent for a
hypothetical second local provider before Lightbulb was ever in the picture. The entry itself is identical
in kind to the `ollama` entry that already exists and already runs over plain HTTP with an ignored auth
header. **The only place a real behavioral difference could show up is §6 (tool-calling shape)** — not
because OverMind requires anything special, but because a partial or non-native tool-calling
implementation triggers an already-built fallback path with real behavioral cost (one re-prompt, then a
hard stop on a second miss) rather than a silent failure.

**What this checklist does NOT answer**, because it's a different question than "what does OverMind's code
require": whether Lightbulb, running on the new hardware, can actually SERVE a real model end-to-end at
all — `LOCAL-MODEL-DISPATCH-DESIGN.md` §5 already names two measured gaps in that stack (GGUF config
derivation, `dequant_bytes_to_f32` refusing Q6_K) found by running a real checkpoint, not by reading the
code. This document is "what OverMind needs if given a working endpoint"; whether Lightbulb currently
produces a working endpoint is Lightbulb's own question to score, not OverMind's to assume.
