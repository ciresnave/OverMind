# OverMind

A multi-agent orchestrating harness for running routine agent work on **non-Claude** providers.

**Status: doing real lane work, on free-tier providers only, at small daily volume.** The first
non-Claude-written pull request merged 2026-09-17 (OverMind#31); see [`MEASUREMENTS.md`](MEASUREMENTS.md)
§26 onward for what a lane task actually does, and §11, §13, §16 for the compliance findings the
gate exists to answer. [`DESIGN-PROPOSAL.md`](DESIGN-PROPOSAL.md) is the pre-lanework design and predates
the shape below by a week; read it for the FAM/MCP/PTY edges, not for how a lane task runs today.

## What is built

- **`src/overmind/lanework.py`** — the runner. `python -m overmind.lanework task.json [--publish]`
  gives a model six tools (list/read/search/write/replace/run the declared check) in a fresh git
  worktree, refuses writes to CI/tool config whatever the task declares, and lets an existing file
  change only by exact replacement — a local model once replaced 626 lines of this file with a
  7-line fragment through a whole-file write. **The harness runs the check itself after the model
  stops** and computes the verdict from the tree and the exit code; the model's own claim is only
  compared against that, never trusted. Commits, pushes and opens the PR only on `PASS`.
- **`src/overmind/providers.py`** — one OpenAI-shaped client over eight backends (seven hosted free
  tiers + Ollama), with model failover, rate-limit handling, message normalisation, and
  `RoutedClient` to try several providers in order.
- **`src/overmind/quota.py`** — a per-user book of daily and monthly free-tier allowances, learned
  from each provider's own refusal body (not guessed), so a spent model or a spent account is not
  asked again until the provider's own reset.
- **`src/overmind/gate.py`** — the mechanical gate underneath the runner. Every tool invocation
  passes through it; it refuses rather than warns, and cannot be satisfied by anything the model
  writes.
- **`src/overmind/agent.py`** — the loop `lanework.py` drives. Every effect goes through the gate;
  the ledger is the only record consulted afterwards.
- **`src/overmind/mcp_tools.py`** — MCP as an alternative tool source, so an agent can be a
  participant in a fabric rather than a gated script. ⚠️ The gate stays at tool execution; `gate.py`
  is untouched by it. Not currently used by `lanework.py`, which has its own six tools.
- **`src/overmind/dispatch.py`** — being dispatched to over a live channel. An inbound message
  becomes a task, runs through the gate, and the answer goes back. ⚠️ Inbound content is
  **untrusted** — another agent's text becoming this model's instructions — and the gate is what
  makes accepting it safe. `lanework.py`'s v0 transport is a CLI invocation, not this channel.

Why it exists, in one measured sentence: **a model that states a prohibition perfectly violates it
4 to 7 times out of 8 when its reasoning is disabled** (`MEASUREMENTS.md` §11, §16) — and
reasoning-off is the cheap setting a cost-driven orchestrator would choose. ⚠️ **With reasoning
ON the same model complies 5 of 5**, so compliance is per-model × per-rule × per-inference-setting.
**A rule that must hold cannot be a rule in a prompt.**

```
python -m unittest discover -s tests -v
```

## What is established

- **A free-tier model can complete a real lane task, unattended, and merge.**
  [OverMind#31](https://github.com/ciresnave/OverMind/pull/31) was written by Google
  `gemini-3.5-flash-lite`, published by the runner only after its own check passed, and merged
  2026-09-17 with no hand edit to the code (§26 is the dry run that preceded it, not this PR).
- **One provider fixes real regressions reliably; most don't, and the harness catches the
  difference.** Four planted bugs, harness-verified controls both ways: Google's cheapest model
  fixed 4/4; several others fixed 0/4 while **claiming** success on a still-failing check — caught
  every time, because the verdict comes from the tree, never the model's report (§27).
- **A screened survey of free models is not a capability measurement.** Nine models that all passed
  a one-request tool-call check split sharply on two real bugs — four fixed both, five fixed none
  (§30); two providers verified only by a tool call went on to score 0/4 and "0/4 but one real fix
  thrown away by an infrastructure hiccup" on the full benchmark (§31).
- **Free-tier capacity is small and shaped differently per provider**, now tracked rather than
  rediscovered every run: Google gives each model its own ~20 requests/day (§27); OpenRouter's free
  tier is 50/day (§23); Hugging Face's is a small **monthly** credit, not a daily one (§31); Groq's
  per-minute token cap rules it out for multi-step tasks entirely (§23, §27).
- A non-Claude MCP client sends **and** receives through FAM's live server (§7).
- A free local model holds a full ~45k-token instruction load and still completes the task (§10.4);
  local models are not currently used — free hosted tiers only, by direction (§29).
- CLI lifecycle control works: the process survives `/clear`, and `/clear` actually forgets (§2).

## What is not

- **Tested so far: single-file Python fixes with the failing test named.** Nothing here says
  anything yet about multi-file changes, other languages, or a task without a pointed check.
- **One run per model per task, mostly.** A model's fix rate is a point, not yet a rate.
- **The transport is a CLI invocation.** `dispatch.py`'s live channel and `mcp_tools.py` exist but
  are not wired into `lanework.py`; a lane or a human still starts each task by hand.
- **Capability is not capacity, and capacity is not stable.** A provider's daily allowance can be
  spent by measuring it, and a "clean" run minutes apart can land on an exhausted account (§31).
