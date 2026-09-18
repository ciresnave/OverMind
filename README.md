# OverMind

A multi-agent orchestrating harness for running routine agent work on **non-Claude** providers.

**Status: measurement complete, one component built.** See
[`MEASUREMENTS.md`](MEASUREMENTS.md) for what is established (with instrument, ref and scope limit
on every claim) and [`DESIGN-PROPOSAL.md`](DESIGN-PROPOSAL.md) for the design those measurements
imply.

## What is built

- **`src/overmind/gate.py`** — the mechanical gate. Every tool invocation passes through it; it
  refuses rather than warns, and cannot be satisfied by anything the model writes.
- **`src/overmind/providers.py`** — one OpenAI-shaped client over six backends (five hosted free
  tiers + Ollama), with model failover, rate-limit handling and message normalisation.
- **`src/overmind/agent.py`** — the loop. Every effect goes through the gate; the ledger is the
  only record consulted afterwards.
- **`src/overmind/mcp_tools.py`** — MCP as the tool source, so an agent is a participant in the
  fabric rather than a gated script. ⚠️ The gate stays at tool execution; `gate.py` is untouched
  by it.
- **`src/overmind/dispatch.py`** — being dispatched to. An inbound message becomes a task, runs
  through the gate, and the answer goes back. ⚠️ Inbound content is **untrusted** — another
  agent's text becoming this model's instructions — and the gate is what makes accepting it safe.
- **`src/overmind/lanework.py`** — It runs one piece of a lane's work with a non-Claude model in a
  fresh git worktree. The harness runs the acceptance check itself, and opens a pull request only
  if that check passes.
- **`src/overmind/repo_probe.py`** / **`src/overmind/dispatch_mcp.py`** — the unattended path into
  `lanework.py`: an MCP tool (`dispatch_lane_task`) any agent can call with a repo and a prompt.
  ⚠️ The acceptance check is **never** caller-supplied — it's inferred from a fixed, host-owned
  table keyed by the repo's own marker files (`Cargo.toml` → `cargo test`, ...), never read out of
  the repo's own CI config. Everything else (CI config text, an in-repo standards file, a caller's
  extra requirements or a `https://` URL to them) is safe to fold in richly, because none of it
  becomes a subprocess argv — only informational text the model reads.

Why it exists, in one measured sentence: **a model that states a prohibition perfectly violates it
4 to 7 times out of 8 when its reasoning is disabled** (`MEASUREMENTS.md` §11, §16) — and
reasoning-off is the cheap setting a cost-driven orchestrator would choose. ⚠️ **With reasoning
ON the same model complies 5 of 5**, so compliance is per-model × per-rule × per-inference-setting.
**A rule that must hold cannot be a rule in a prompt.**

```
python -m unittest discover -s tests -v
```

## What is established

- A non-Claude MCP client sends **and** receives through FAM's live server (§7).
- All five free-tier providers reach a verified tool call (§12).
- A free local model holds a full ~45k-token instruction load and still completes the task (§10.4).
- CLI lifecycle control works: the process survives `/clear`, and `/clear` actually forgets (§2).
- A free-tier model can be **sent** a task and return an answer, with no human in the loop (§18).

## What is not

**None of it has been shown to do a lane's work.** Two tool calls is not a day, and obedience is
per-model × per-rule with no predictor. Capability is not capacity.
