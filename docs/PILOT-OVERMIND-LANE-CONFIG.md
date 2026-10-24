<!-- SPDX-License-Identifier: MIT OR Apache-2.0 -->
# Synapse pilot: the `overmind` lane's config (proposal, nothing run)

Written 2026-10-16 from `synapse` origin/main `519bd52` (`docs/PILOT_T0_RUNBOOK.md`, section 3) and `agentlife@e609da5`
(`crates/lane-restart`), both read from `origin/main` blobs. **Nothing here was run, installed or written
to `C:\Projects\.lane-state`.** The PM applies it at pilot T-0; this file is the exact content for the
`overmind` role so no one has to re-derive it.

Pilot facts, from the runbook: adapter `synapse-claude-channel` (not `synapse-mcp-server`), role = the lane
name (`overmind`), pilot home `C:\Users\cires\AppData\Local\synapse-pilot`, address `127.0.0.1:7920`, cwd
`C:\Projects\OverMind`.

## 1. `C:\Projects\.lane-state\overmind.mcp.json` (new file)

The server name MUST be `synapse`. `SYNAPSE_HOME` in `env` is required: `--home` does not reach a daemon
the adapter auto-starts (runbook section 3a).

```json
{ "mcpServers": { "synapse": {
  "command": "C:/Users/cires/.cargo/bin/synapse-claude-channel.exe",
  "args": ["--role", "overmind", "--home", "C:/Users/cires/AppData/Local/synapse-pilot"],
  "env": { "SYNAPSE_HOME": "C:/Users/cires/AppData/Local/synapse-pilot", "SYNAPSE_ADDR": "127.0.0.1:7920" } } } }
```

## 2. `C:\Projects\.lane-state\overmind.launch.json`: only `launch_args` changes

Today (read 2026-10-16): `["--dangerously-load-development-channels","server:claude-peers"]`. Becomes:

```json
"launch_args": ["--dangerously-load-development-channels","server:claude-peers","server:synapse",
                "--mcp-config","C:\\Projects\\.lane-state\\overmind.mcp.json"]
```

Every other field (`model: sonnet`, `permission_mode: auto`, `cwd`, `handoff_path`) is untouched.
The variadic channels flag ends at `--mcp-config`, so the second channel does not swallow it.

## 3. What the code says about the runbook's one unverified point

The runbook asks whether `lane-restart` re-reads `launch_args` or overwrites it from the live process.
Read in `agentlife@e609da5` (code only, not executed). **Answer: a restart launches from the state file
`<role>.json`, not from the spec, and then overwrites the spec. Editing `overmind.launch.json` alone does
nothing for a restart.**

- Both flags are on the carry-over allowlist: `ALLOWED_LAUNCH_ARG_FLAGS` in `relaunch.rs` holds
  `--dangerously-load-development-channels` (variadic) and `--mcp-config` (one value).
  `RESTART-TOOL-DESIGN.md` line 241 still says `--mcp-config` is "not carried over", while its line 348
  lists it on the allowlist; the code agrees with line 348. Line 241 is stale.
- Two files hold launch args. `<role>.json` (state) records the **original launch's full argv**
  (executable first; `extra_launch_args` skips element 0). `<role>.launch.json` (spec) holds only the
  allowlisted extras, no executable.
- The restart path (`kill_and_relaunch_persisting`, `relaunch.rs` ~735-782) starts with
  `LaunchSpec::from(state)`, which takes `launch_args` from the state file, builds the spec from it, writes
  it to `<role>.launch.json`, and launches from that. A hand edit of the spec is overwritten. The live file
  agrees: `overmind.launch.json` has `"written_by": "restart"`. Only `lane-restart start` (a lane that is not
  running) launches from an existing spec.
- A real `SessionStart` records the live process's argv into the state (the "always fresh" rule in
  `lane_state_writer.rs`); a later non-start event does not overwrite a state that already has
  `launch_args`.

So the **hand launch below is required, not just a safe order**: it is what puts the new flags in the
state file, after which a normal restart carries them and rewrites the spec to match. Editing the spec
first is optional (it only matters for a stop-then-`start`). Read `overmind.json` and
`overmind.launch.json` back after the first hand launch to confirm both hold the new flags. Not traced:
any restart caller other than `kill_and_relaunch_persisting`.

First launch (hand, in `C:\Projects\OverMind`, Sonnet only):

```
claude --model sonnet --mcp-config C:\Projects\.lane-state\overmind.mcp.json --dangerously-load-development-channels server:claude-peers server:synapse
```

Answer the "I am using this for local development" dialog only if its text matches the G5 record; an
unfamiliar dialog means STOP.

## 4. Still blocking (not mine)

- **G5** (2026-10-24: `claude-peers-synapse-dev-channels-pilot.json` now exists in `ciresnave/ciresnave` `.overmind/lane-restart/approvals`; who merged it, and CireSnave's yes, were not checked): CireSnave's yes and OverMind's approval record for the channel string
  `server:claude-peers, server:synapse`. Without it every relaunch stops at a dialog nobody answers.
- The pilot **account name** (`<ACCT>`), and installing `synapsed`, `synapsectl`,
  `synapse-claude-channel` at the runbook's version. The runbook's header says it was written against
  `6.0.0-rc.94` but installs `rc.93`; I did not resolve which is meant.

## 5. Rollback

Because a restart rebuilds from the state file, restoring the spec and restarting reverts nothing. Rollback
is: hand-launch once with the old flags
(`claude --model sonnet --dangerously-load-development-channels server:claude-peers`) so `SessionStart`
re-records them, confirm `overmind.json` and `overmind.launch.json` show the two-element form, delete
`overmind.mcp.json`, then restart normally and confirm a prompt is reached. Nothing on claude-peers is ever
switched off, so no path needs rebuilding. (The runbook's own rollback, section 5 step 1, has the same gap:
it says to restore `launch_args` and restart.)
