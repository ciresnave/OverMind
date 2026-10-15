# restarttest soak, end report (board 175)

Window: 2026-10-12T18:25Z to 2026-10-15T18:52Z (72.5 h). One message every 10 minutes from role
`soak-probe` to role `restarttest` (a Sonnet session under `lane-restart host`), against synapsed
6.0.0-rc.33 at 127.0.0.1:7931. CireSnave 2026-10-12: "1) Yes. 2) 1 message every 10 minutes.  3) Restart it."

## Result: no failure observed, and NOT a clean soak

| Measure | Reading |
|---|---|
| Sent / unique / seen in the transcript | 435 / 435 / 435; none missing, none seen but unsent (`sent.log` against the session transcript) |
| Show bound | max shows of any one id was 3 (the watcher's count) |
| Model actions | 0 `tool_use` blocks in the transcript |
| Compactions | 0 |
| Daemon | pid 28280, `started_at` 2026-10-12T18:18:41Z unchanged through the window |
| Session | claude pid 46932 alive to the end; no stuck dialog seen |
| Usage to 18:49Z | cache_read 170.6M (about 2.35M per hour), cache_create 197k, output 54.3k (about 750 per hour), 1303 turns |

## Caveats, as stated to the PM

1. **The "0 events" criterion was not met literally.** `security-events.jsonl` holds one line:
   `auth_failure`, surface `synapsed/bearer`, detail `unauthorized`, 2026-10-14T20:44:08Z. The synapse
   lane says it was its own unauthenticated curl. That is their statement; it was not independently
   confirmed. What was verified: exactly one line, the daemon unchanged, the watcher healthy. The PM ruled
   continue-and-record.
2. **The compaction bound is untested.** No compaction occurred, so whether the 3x show bound and the id
   dedupe hold across one is unanswered by this run.
3. **The weekly-limit delta is unmeasured.** No weekly-limit banner reading was taken for the window.
4. The token figures above are from the watcher's reading to 18:49Z, not the final transcript.

## Stop

Sender (canary pid 26264), `lane-restart host` (pid 36200) and the session (claude pid 46932) were
stopped on PM instruction at 2026-10-15 after the window closed, by pid, after checking their command
lines. synapsed (synapse lane's) and the hourly monitor (pid 2840) were left running. Logs are kept in
`%LOCALAPPDATA%\synapse-restarttest` (`sent.log`, `monitor.log`, `security-events.jsonl`).
