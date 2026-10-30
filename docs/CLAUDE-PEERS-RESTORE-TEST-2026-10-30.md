# claude-peers broker restore test (scratch), 2026-10-30

Requested by the PM on CireSnave's word (2026-10-30: test the switch to Synapse and back to
claude-peers before the soak); closes the "nobody has verified a restore" line in Synapse
`docs/CUTOVER_READINESS_2026-10-15.md` section 4 (rollback, "After phase C"), item 6 of its
checklist. Needed before Phase C, not before the soak.

## What was run (once)

`python probe/peers_restore.py` from a Git Bash shell on this box. It never touches 7899.

1. Scratch data dir `%TEMP%\peers-restore-XXXXXXXX\` (broker DB `peers.db`, two session cwds
   `sessA`, `sessB`).
2. **Broker start command** (the one the readiness doc says is OverMind's):
   `bun C:\Users\cires\claude-peers-mcp\broker.ts` with
   `CLAUDE_PEERS_PORT=7897 CLAUDE_PEERS_DB=<scratch>\peers.db` (the two env vars `broker.ts`,
   `server.ts` and `cli.ts` read; defaults are 7899 and `~/.claude-peers.db`).
3. Two real claude-peers MCP servers, `bun C:\Users\cires\claude-peers-mcp\server.ts`, one per scratch
   cwd, same env, driven over stdio JSON-RPC (`initialize`, then `tools/call send_message`). They
   registered with the scratch broker (`/list-peers` showed both, with their pids and cwds).
4. A sent "ping from A" to B; B's server emitted `notifications/claude/channel` with that content
   and `from_id`/`from_cwd` of A. B sent "pong from B"; A's server emitted the channel notification.
5. Teardown: both servers' stdin closed and terminated, `POST /shutdown` to 7897, 7897 confirmed down.

## Result

**PASS.** Both directions delivered, push-style, through a broker started cold from its start command
on a fresh DB. The live broker on 7899 had 22 peers before the run and 22 after (read through
`/health` only); it was neither stopped nor sent anything.

## Versions

claude-peers-mcp commit `c1210c6` ("fix(broker): index the poll query in schema setup, add bounded
retention"), bun 1.3.14, `@modelcontextprotocol/sdk` 1.27.1, Windows 11 10.0.26300, Python for the driver.

## NOT verified (so the rollback is not fully proven)

- No real Claude Code session was launched with `--dangerously-load-development-channels server:claude-peers`.
  The two "sessions" are the real MCP servers driven by a script, so the broker, the server, the
  registration and the channel notification are tested; Claude Code's own dev-channels dialog and the
  handler record are not. The handler record (the dev-channels approval, fetched from the user repo per
  #94/ciresnave#1) was not exercised: the readiness doc's "OverMind's handler record" remains unverified.
- The broker's auto-start (`server.ts` `ensureBroker` spawning `broker.ts` detached) was not exercised:
  the broker was started explicitly first.
- Not tried: a restore onto the LIVE database (`~/.claude-peers.db`), a broker that was stopped with
  messages undelivered, the 90-day retention path, or 7899 itself. Doing those means touching the live broker.
