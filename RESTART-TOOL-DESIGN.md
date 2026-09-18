# Session restart tool — design spec

**Status: SPEC, settled — not yet built.** Written per the task from CireSnave via the PM,
2026-09-18: pulled ahead of the throttle ("it is another project that will help us avoid the
condition that required the throttle in the first place"). Location decided: a Cargo workspace crate
in this repo, sharing OverMind's version number, with a Rust CI job (fmt, clippy, test) branch
protection requires, and SPDX `MIT OR Apache-2.0` headers.

**Every `DECISION NEEDED` point from the first draft is now answered by the PM (2026-09-18,
CireSnave sees and can override)** - marked `DECIDED` in place below. Everything else is grounded in
either the task's own stated requirements or Claude Code's documented behaviour, verified directly
against `code.claude.com/docs` in this session — not assumed, and in one case correcting an initial
wrong assumption (below).

## 0. The one correction that reshaped this design

The natural first idea — poll `claude agents --json` for each lane's `status` (`busy`/`waiting`/
`idle`) — **does not work**. Verified directly against `agent-view.md`:

> *"Interactive sessions you have open in other terminals don't appear until you background them."*

Every lane in this portfolio (OverMind, Synapse, the PM) runs **interactive**, not backgrounded. That
command's entire field table (`id`, `state`, `pid`, `status`, `waitingFor`) only populates for
sessions started as background sessions. There is no external, polling-based way to ask an
interactive Claude Code process "are you busy right now" at all.

**So the design below does not rely on polling for state.** It relies on each lane maintaining its
own state, event-driven, via documented hooks - and an external reader (this tool, or the PM) only
ever reads what a lane already wrote about itself.

## 1. What each lane self-reports: `C:/Projects/.lane-state/<role>.json` (location per §7)

A small JSON file, one per lane, written by a hook running **inside** that lane's own Claude Code
process - so it is always describing that process's own, current-moment truth, never a stale
snapshot fetched from outside.

```jsonc
{
  "role": "overmind",                    // this lane's role name - matches its HANDOFF file
  "session_id": "3f9a...-uuid",          // from transcript_path's filename, read in the hook
  "pid": 48213,
  "cwd": "C:/Projects/OverMind",
  "name": "overmind",                    // session name, if set (--name / /rename)
  "model": "claude-sonnet-5",
  "permission_mode": "prompting",
  "remote_control": true,
  "busy": false,                         // see the event mapping below
  "subagents_running": 0,                // incremented on SubagentStart, decremented on SubagentStop
  "background_bash_unknown": true,       // see §1a - this is a KNOWN GAP, always true today
  "updated_at": "2026-09-18T09:14:03Z",
  "updated_by_event": "Stop"
}
```

**Hook wiring** (all documented, verified against `hooks.md`):

| hook event | effect on the state file |
|---|---|
| `SessionStart` | write the file fresh: `pid`, `cwd`, `session_id` (parsed from `transcript_path`), `name`, `model`, `permission_mode`, `remote_control`, `busy: false` |
| `UserPromptSubmit`, `PreToolUse` | `busy: true` |
| `Stop` | `busy: false` |
| `SubagentStart` | `subagents_running += 1` |
| `SubagentStop` | `subagents_running -= 1` (floor at 0 - see §1a) |
| `PostModelSwitch` | update `model` |
| `SessionEnd` | delete the file, or mark `"exited": true` - **not** leave a stale "idle" record lying around after the process is gone |

### 1a. Known, unresolved gap: background Bash commands

Verified against `hooks.md`: **there is no documented hook or query for whether a `run_in_background`
Bash command is still running.** `PostToolUse` fires when the tool CALL resolves, which for a
backgrounded command is immediately (it returns a task handle), not when the background process
itself finishes. `SubagentStart`/`SubagentStop` give a reliable *count* for subagents; there is no
equivalent pair of events for background shells.

`background_bash_unknown: true` is always set, honestly, rather than pretending this is tracked.

**DECIDED (PM, 2026-09-18):** use both signals, and refuse if EITHER shows activity.

1. The self-asserted `no_background_shells: true` claim, written into HANDOFF by the lane itself -
   the honesty-burden mechanism proposed above.
2. **Independently**, the tool walks the target `claude` process's own child-process tree (via its
   PID, using the OS process list, no hook or Claude Code cooperation needed) looking for live shells
   - `bash`, `pwsh`, `cmd`, and their own children. A backgrounded Bash command is a real OS child
   process even though no Claude Code hook fires for it, so the process table can see it regardless.

**Unknown counts as unsafe** in both directions: an absent `no_background_shells` claim refuses the
restart, and a child-process walk that can't enumerate cleanly (permissions, a transient OS error)
refuses rather than proceeding on the claim alone. Neither signal is trusted by itself - matches this
portfolio's own "no absence without a positive control" discipline: the claim alone could be wrong,
and the process walk alone could miss a shell spawned through something that doesn't appear as a
direct child (a detached process, a service) - together they cover more than either does alone.

## 2. Process identification — "never kill by name alone"

Verified: Claude Code does not document a PID-reuse guard, and `pid` alone is not sufficient (OS PIDs
recycle). The positive-identification tuple this tool checks, all four, right before sending any
signal:

1. **PID** is running, and is a `claude` process (checked via the OS process list's own recorded
   command/image name for that PID - not assumed from the state file).
2. **`cwd`** of that live PID matches the state file's `cwd`.
3. **`session_id`** recorded in the state file corresponds to a transcript file that exists on disk
   under that `cwd`'s project directory (`~/.claude/projects/<project>/<session-id>.jsonl`,
   documented in `sessions.md`) and whose mtime is recent enough to be plausibly this run, not a
   leftover from a much older process that reused the PID.
4. **`updated_at`** on the state file is recent (a tool-configurable staleness bound, e.g. 2 minutes) -
   a stale file is refused, not trusted, because the lane may have crashed without a `SessionEnd`
   hook ever firing.

Any mismatch on any of the four: refuse, log why, do not kill.

## 3. Authorization: who may restart whom

- **A lane may request its own restart at any time it chooses** (it just wrote its own HANDOFF, so it
  is, by construction, at a point it considers safe to leave). No `busy`/`subagents_running` check is
  needed for a *self*-restart - the lane is the same process making the request, so there is no
  staleness window between "I checked" and "I act."
- **The PM may request a restart of another lane only when that lane's own state file says
  `busy: false`, `subagents_running: 0`, and (per §1a's resolution) `no_background_shells: true`
  was set when its HANDOFF was written**, AND the four-part identification in §2 passes at the moment
  of the kill, not just at the moment the PM decided. **The PM's own belief that a lane is idle
  (from `ListAgents`, or anywhere else) is never sufficient by itself** - the tool re-checks the
  state file itself, fresh, immediately before acting.
- **No lane may restart a DIFFERENT lane.** Only self-restart, or PM-restart-of-idle-lane. Written
  down explicitly because the task asked for exactly this to be written down.

## 4. HANDOFF file format — one per role

`<role>/HANDOFF.md`, written by the lane itself immediately before requesting its own restart (or, on
the PM's side, before a PM-initiated restart is even considered eligible - see §3, `no_background_shells`).

```markdown
# HANDOFF — overmind
Written: 2026-09-18T09:14:03Z
Session ending: 3f9a...-uuid

## Where things stand
<free text - what's in flight, what's queued, what's blocked and on what>

## Open PRs / branches
<list>

## Board items waiting on someone else
<list, with who>

## Anything the next session must NOT re-derive from scratch
<the load-bearing facts that took real work to establish this session>

## Role-specific
<optional - free content a role's own CLAUDE.md/role doc may require>
```

**DECIDED (PM, 2026-09-18):** the shared skeleton above, plus an optional `## Role-specific` section
with free content. The PM's own section is just a pointer to `C:/Projects/PM-HANDOFF.md`, which stays
the PM's real, full handoff document - this file doesn't replace it. Nothing else is mandated per
role for now.

## 5. Relaunch mechanics

**REVISED (PM finding, 2026-09-18): NEVER `--resume`.** The first draft of this section proposed
`claude --resume <session_id>`, verified against `sessions.md` as automatically restoring the model
and permission mode. That's true, but it restores something else along with them: the WHOLE prior
transcript, reloaded into context. That is exactly the per-turn cost a restart tool exists to cut -
the whole point of restarting is a small, fresh context, not the old one reloaded under a new PID.

**The actual design: a genuinely fresh session, with continuity through HANDOFF alone.**

- **`state.session_id` is used ONLY by `authorize::decide`'s identity check (§2)** - proving the pid
  being killed really is the recorded lane, via a matching, recent transcript. It plays no part in
  the relaunch command at all.
- **Model and permission mode are NOT auto-restored on a fresh session** (that restoration is a
  property of `--resume`/`--continue`, which this design no longer uses) - so the tool passes them
  explicitly: `--model <state.model> --permission-mode <state.permission_mode>`, both recorded in the
  state file by the lane's own hooks (§1).
- **Remote Control does not "reconnect" either** - a fresh session has no prior RC record to
  reconnect to; `remote-control.md`'s auto-reconnect-on-resume behaviour doesn't apply here, since
  there is no resume. Instead: `--remote-control` is passed at launch when `state.remote_control` was
  true, which starts a NEW RC session (documented as a real launch flag) - the property "RC was on"
  carries over; the specific prior RC session's identity does not, and cannot, for a fresh session.
- **`--mcp-config`, `--settings`, `--plugin-dir`, `--fallback-model`, `--add-dir`** are not carried
  over by this tool at all (still not recorded in §1's schema) - documented here as NOT carried over,
  rather than silently dropped without saying so, per this file's own §0 discipline.
- **The launch command:** `claude --name <name> --model <state.model> --permission-mode
  <state.permission_mode> [--remote-control] "read <role> HANDOFF and continue"`, run in the recorded
  `cwd`. **No shell in the loop** - PM finding, 2026-09-18: an earlier draft's `cmd /C start "" claude
  ...` put `role`/`name` (state-file content, not this host's own choice) through cmd.exe's own
  parser, which a Rust `Command`'s argv-quoting does not protect against. Fixed two ways, not one:
  `role` and `name` are validated against `^[A-Za-z0-9_-]{1,64}$` and refused otherwise (confirmed
  live: `a&calc` as a name reaches `cmd.exe` and would execute `calc`), AND the launch now spawns
  `claude.exe` directly with `CREATE_NEW_CONSOLE` (Windows) - a real argv array Windows'
  `CreateProcess` never hands to a shell for re-parsing, giving the same visible new window without
  cmd.exe touching any of it.

## 6. Bulletproof requirements (from the task, restated as testable properties)

1. **Positive identification** — §2's four-part check, every time, no exceptions, including for
   self-restart (a lane still shouldn't be able to accidentally target a different process's PID
   through a bug in its own state file). **Extended (PM finding, 2026-09-18):** identification at
   `decide()` time is not enough on its own - the pid could be recycled in the window between
   deciding and the actual kill. The process's start time (and exe path, when both sides have one)
   is recorded at `decide()` time and RE-READ, fresh, immediately before the real kill signal;
   any mismatch refuses rather than kills.
2. **Dry-run mode** — `--dry-run` performs every check, prints exactly what it would do (kill PID X,
   wait, launch a fresh session in cwd Y), and does neither. **DECIDED (PM, 2026-09-18):**
   self-restart is real by default (the agent chose that boundary itself, restarting only its own
   process). Restarting a DIFFERENT lane is dry-run unless `--yes` is passed - and even with `--yes`,
   it still refuses unless that lane is idle (§1a's two-signal check) and its state file is fresh
   (§2's four-part identification).
3. **A log of every kill and launch** — append-only, at `C:/Projects/.lane-state/restart.log` (§7),
   every entry: timestamp, who requested it (self or PM), the full identification tuple checked, the
   verdict (killed+launched / refused+why), and the exact command line used to relaunch. Never
   overwritten, never rotated silently - this is exactly the kind of artifact this portfolio's own
   evidence culture depends on existing.

## 7. Where lane state and the log live

**DECIDED (PM, 2026-09-18):** fixed, portfolio-wide, not per-lane:

- `C:/Projects/.lane-state/<role>.json` - one file per lane, matching §1's schema.
- `C:/Projects/.lane-state/restart.log` - the one append-only log for every lane's kills and
  launches (§6.3), not split per role, so a single read shows the whole portfolio's restart history.

**Kept out of every git repo**, including this one's own `.portfolio-history.git` (the PM is adding
`.lane-state/` to its excludes) - this is live, per-process runtime state, not a durable record
anyone should be committing.

## 8. What this spec still doesn't decide

- **Exact Windows process-kill mechanism** (`taskkill`, `TerminateProcess` via a crate, sending
  Ctrl-C then escalating) - an implementation detail, not a design question, deferred to the build.

## 9. What's needed before building starts

All four `DECISION NEEDED` points from the earlier draft are now answered (§1a, §4, §6.2, §7). Next:
fold these into the crate skeleton and CI (already in progress), then build the kill/launch logic
against this now-settled spec.

**Update, 2026-09-18: the crate is built (OverMind#53).** What's left, per the PM: *"Before the tool
restarts any real lane, the hooks that write `.lane-state` must exist on every lane... don't install
them yourself: settings are CireSnave's."* §10 is that proposal - not installed anywhere, and not
authored to be installed by this lane.

## 10. PROPOSAL, not installed — the hooks that write `.lane-state/<role>.json`

**CireSnave's or the PM's to install, in `settings.json`.** Nothing in this section has been applied
anywhere. Verified against `hooks.md`'s documented common input fields and settings shape before
writing this, not assumed - and that check surfaced two real gaps, honestly flagged below rather than
worked around with a guess.

### 10.1 Two gaps in what a hook can know, found while designing this

- ⚠️ **No hook receives the running Claude Code process's own PID.** The common input fields
  (`session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, ...) do not include
  one, and `lane-restart` needs it (§2's identity check signs against `pid`). The script below derives
  it itself: a hook runs as a CHILD process of the `claude` process, so `(Get-CimInstance
  Win32_Process -Filter "ProcessId=$PID").ParentProcessId` gets it, with a sanity check that the
  parent's own image name really is `claude.exe`.
- ⚠️ **No hook receives the current model name either**, at `SessionStart` or otherwise, except
  `PostModelSwitch`'s own event (whose payload isn't in the DOCUMENTED common-fields table, so this
  proposal doesn't assume its shape without checking that separately). Consequence: `model` in the
  state file starts **unset** for a session that never explicitly switches models, until proven
  otherwise. **Flagged, not worked around** - a wrong guess here would feed a wrong `--model` into a
  future relaunch.

### 10.2 Role: derived from `cwd`, not configured separately

`cwd` IS a common field. Proposal: the role is the lowercased leaf directory name of `cwd`
(`C:/Projects/OverMind` → `overmind`). No new environment variable, no per-lane settings needed beyond
the hooks themselves - every lane already runs from its own, distinctly-named project directory. If a
lane's directory name doesn't match the role name the PM/CireSnave already use for it elsewhere,
that's a naming mismatch to resolve by renaming, not a reason to add a second source of truth for
"which role is this."

### 10.3 The script (one file, all events dispatch through it)

`C:/Projects/.claude-hooks/lane-state.ps1` (PowerShell - `"shell": "powershell"` is a documented hook
option, matching this box's own primary shell; a fixed path, not `${CLAUDE_PROJECT_DIR}` - see §10.4):

```powershell
param()
$input_json = [Console]::In.ReadToEnd() | ConvertFrom-Json
$event = $input_json.hook_event_name
$cwd = $input_json.cwd
$role = (Split-Path $cwd -Leaf).ToLower()
$stateDir = "C:/Projects/.lane-state"
$statePath = Join-Path $stateDir "$role.json"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

function Get-ClaudePid {
    $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId
    $parentProc = Get-CimInstance Win32_Process -Filter "ProcessId=$parent"
    if ($parentProc.Name -notmatch '^claude(\.exe)?$') {
        throw "hook's parent process is '$($parentProc.Name)', not claude - refusing to record a wrong pid"
    }
    return $parent
}

$existing = if (Test-Path $statePath) { Get-Content $statePath -Raw | ConvertFrom-Json } else { $null }

switch ($event) {
    "SessionStart" {
        $state = @{
            role = $role
            session_id = $input_json.session_id
            pid = (Get-ClaudePid)
            cwd = $cwd
            name = $null            # not in common fields; left for a future revision if needed
            model = $(if ($existing) { $existing.model } else { $null })
            permission_mode = $input_json.permission_mode
            remote_control = $(if ($existing) { $existing.remote_control } else { $false })
            busy = $false
            subagents_running = 0
            no_background_shells = $null
            updated_at = (Get-Date -AsUTC).ToString("o")
            updated_by_event = $event
        }
    }
    default {
        if (-not $existing) { exit 0 }   # no SessionStart seen yet - nothing to update
        $state = $existing | ConvertTo-Json | ConvertFrom-Json  # clone
        $state.updated_at = (Get-Date -AsUTC).ToString("o")
        $state.updated_by_event = $event
        switch ($event) {
            "UserPromptSubmit" { $state.busy = $true }
            "PreToolUse"       { $state.busy = $true }
            "Stop"             { $state.busy = $false }
            "SubagentStart"    { $state.subagents_running += 1 }
            "SubagentStop"     { $state.subagents_running = [Math]::Max(0, $state.subagents_running - 1) }
            "PostModelSwitch"  { $state.model = $input_json.model }  # field name unverified - see §10.1
            "SessionEnd"       { Remove-Item $statePath -ErrorAction SilentlyContinue; exit 0 }
        }
    }
}

$state | ConvertTo-Json | Set-Content -Path $statePath -Encoding utf8
```

⚠️ **`no_background_shells` is never set to `true` by this script.** Per RESTART-TOOL-DESIGN.md §1a,
that claim is the lane's own assertion, written when it writes its own HANDOFF - a deliberate manual
step in whatever a lane's own restart-request procedure is, not something a lifecycle hook can
honestly assert on the lane's behalf.

### 10.4 The `settings.json` block (not applied)

**Recommended scope: user-level `~/.claude/settings.json`**, not per-project - every lane already runs
under this one user account, and a user-level entry covers every project directory without editing
each lane's own repo. Per-project would need the identical block added to every lane's project
settings separately, for no benefit this design needs. Still CireSnave's call.

⚠️ **The script's own path is a fixed, absolute one - deliberately NOT `${CLAUDE_PROJECT_DIR}`.** That
placeholder resolves per-project ("project root where session started"), which would mean placing an
identical copy of the script under every lane's own repo just to get one user-level settings edit to
reach all of them - defeating the point. One script, once, at a location no project's own directory
structure affects:

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "shell": "powershell",
      "command": "C:/Projects/.claude-hooks/lane-state.ps1" }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "shell": "powershell",
      "command": "C:/Projects/.claude-hooks/lane-state.ps1" }] }],
    "PreToolUse": [{ "hooks": [{ "type": "command", "shell": "powershell",
      "command": "C:/Projects/.claude-hooks/lane-state.ps1" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "shell": "powershell",
      "command": "C:/Projects/.claude-hooks/lane-state.ps1" }] }],
    "SubagentStart": [{ "hooks": [{ "type": "command", "shell": "powershell",
      "command": "C:/Projects/.claude-hooks/lane-state.ps1" }] }],
    "SubagentStop": [{ "hooks": [{ "type": "command", "shell": "powershell",
      "command": "C:/Projects/.claude-hooks/lane-state.ps1" }] }],
    "PostModelSwitch": [{ "hooks": [{ "type": "command", "shell": "powershell",
      "command": "C:/Projects/.claude-hooks/lane-state.ps1" }] }],
    "SessionEnd": [{ "hooks": [{ "type": "command", "shell": "powershell",
      "command": "C:/Projects/.claude-hooks/lane-state.ps1" }] }]
  }
}
```

`C:/Projects/.claude-hooks/` is proposed as a sibling of `.lane-state/` for the same reason: portfolio-
wide runtime tooling, not part of any one project, kept out of every git repo.

### 10.5 Not proposed here

- Installing any of the above. This section is the design; CireSnave/the PM decide whether, when, and
  exactly how.
- A fix for the two gaps in §10.1 - they're recorded as known, current limits of what a hook can
  report, not solved by guessing at data hooks don't document providing.
