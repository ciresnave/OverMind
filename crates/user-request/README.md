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

- `store.lock`: held by one process at a time, from opening the store to closing it. Concurrent lanes
  never overwrite each other's changes.
- `store.key` and `store.key.check`: the HMAC key, DPAPI-protected, and a value that tells the right
  key from a wrong one. A store whose key does not check out trusts nothing and refuses to save, so
  it can never overwrite the real grants.
- `grants.json`: approvals, plus the ids of revoked ones (tombstones), so an old copy cannot bring a
  revoked grant back.
- `attempts.json`: recent prompts, including ones still pending, and how they ended.
- Both files are HMAC-signed over their exact bytes. A file that fails its signature is moved aside
  (`*.rejected-<time>`), not deleted.
- `audit.jsonl`: every save, grant, revocation, gate decision and alert, append-only. Each line
  carries the SHA-256 of the line before it. The chain's head is also written to
  `C:\Projects\.lane-state\user-request-audit.head`.

### Integrity: the audit chain is the anchor

- Every save records the hashes of the files it wrote. On open, the files are checked against the
  last recorded save.
- A file that was deleted, rolled back, or had an entry removed makes the store **untrustworthy**.
- An untrustworthy store fails **closed**:
  - no grant is honoured;
  - the prompt gate refuses;
  - revoking still works, and the next save re-anchors the files.
- Before every append, the chain itself is checked against its head copy. A chain found truncated,
  deleted, torn or edited gets an explicit `chain-reset` line naming why. `audit verify` reports
  that reset permanently, and later lines still append.

### The prompt gate

These limits answer "approval fatigue": a lane re-asking until a mis-click approves it.

- After a denial or a timeout, the same role may not ask about the same subject again for 10
  minutes. Subjects are compared trimmed and case-folded.
- A role may put at most 6 prompts per rolling hour in front of the person.
- All roles together may put at most 20 per rolling hour in front of the person.
- A prompt that is still pending counts toward both caps, so parallel requests cannot get past them.
- Reaching a cap, or 3 refusals within an hour, raises an **alert**, once per role per hour.
- The limits are keyed by role, so restarting a lane resets neither.
- Every gate decision and every alert is written to the audit log.
- ⚠️ Alert **delivery** is not built yet. Where alerts go waits on board item 131; until then the
  audit log is the only record.

### Commands

```
user-request list            active grants, FOREVER ones first and loudly
user-request revoke <id>     revoke one grant (no Hello needed: it only removes privilege)
user-request revoke --all    the panic button
user-request audit verify    check every link of the audit chain and its head copy
```

- Every revocation is audited before it is saved.
- Each command prints which store it used.
- Read-only commands never create a store.

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
