# user-request

Ask a person to approve something, through a channel that can be swapped, and get back a grant the
**approver** chose. CireSnave asked for it on 2026-10-04 (board item 121): *"a separate
"user-request" crate? That way it can implement Windows Hello, SMS requests, push requests, etc. and
code requesting something from a user could use them interchangeably"*.

## What is in it now

- `Request`: what is asked (`kind`, `subject`, `summary`), who asks (`Requester`, taken from the OS
  process table and lane-state files, never from an argument), and why.
- `KindId`: the **closed** set of request kinds. Each kind's maximum grant and scope is compiled in,
  so no lane can define a kind with a larger maximum.
- `Grant`: for a duration, until a date and time, or forever. `Grant::within` checks a grant against
  its kind's maximum. A grant over the maximum is **refused, never clamped**.
- `Channel`: one way to ask. `HelloChannel` (Windows Hello) is built; `SmsChannel` and `PushChannel`
  are designed for and answer `Unavailable` until built.
- `prompt_text`: the text the person approves. Summary and reason are clipped; the requester line
  and the grant line never are.

## Kinds

| kind | maximum grant | scope |
|---|---|---|
| `Secret` (with-secret) | until the next local midnight | this requester only; a lane restart voids it |
| `LaneDialogBypass` (lane-restart) | **FOREVER** | any requester |

### Kinds that can be granted forever

Every kind whose maximum is `Forever` must be listed here, and nowhere else may one be added:

- `LaneDialogBypass`: auto-answering a lane's startup dialog.

A forever grant is described in the prompt as `*** FOREVER (until revoked) ***`.

## How a duration is chosen

Windows Hello is a yes/no dialog with a message: it cannot ask "for how long". So the grant is
chosen **first**, and the message the person approves names it. Hello proves the person was present
and approved that text; it does not prove they read it.

## The store

Everything lives in `%LOCALAPPDATA%\OverMind\user-request\`:

- `store.key` and `store.key.check`: the HMAC key, DPAPI-protected, and a value that tells the right
  key from a wrong one. A store whose key does not check out refuses to save, so it can never
  overwrite the real grants.
- `grants.json` and `attempts.json`: approvals, and recent prompts with how they ended, each
  HMAC-signed. An entry whose signature fails is ignored and counted.
- `audit.jsonl`: every grant, revocation, denial and refusal, append-only. Each line carries the
  SHA-256 of the line before it. The chain's head is also written to
  `C:\Projects\.lane-state\user-request-audit.head`, so a truncated or deleted log is detectable.

### The prompt gate

These limits answer "approval fatigue": a lane re-asking until a mis-click approves it.

- After a denial or a timeout, the same role may not ask about the same subject again for 10 minutes.
- A role may put at most 6 prompts per rolling hour in front of the person.
- Reaching the cap, or 3 refusals within an hour, raises an **alert**.
- The limits are keyed by role, so restarting a lane resets neither.
- ⚠️ Alert **delivery** is not built yet. Where alerts go waits on board item 131; until then they
  are recorded in the audit log only.

### Commands

```
user-request list            active grants, FOREVER ones first and loudly
user-request revoke <id>     revoke one grant (no Hello needed: it only removes privilege)
user-request revoke --all    the panic button
user-request audit verify    check every link of the audit chain and its head copy
```

Every revocation is audited.

## Honest limits

- Like with-secret (WITH-SECRET-DESIGN.md §3), this stops **accidents**, not a deliberate process
  running as the same Windows user. Such a process can read the DPAPI key, forge entries, and
  rewrite both the audit chain and its head copy.
- Coming next, per the approved plan:
  - with-secret's approvals move onto this store, so its prompts pass the gate and `revoke --all`
    covers secrets;
  - an approver-side chooser where FOREVER, or a date over 30 days away, must be typed;
  - durable pending requests with no timeout;
  - grants for lane-launch dialog bypasses.
