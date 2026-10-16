# `with-secret`: should the vault be divided by who requests? Design (NOT implementation)

**Status: draft for the PM. No code written.** The PM's view to test: not two vaults, but ONE store with a
per-entry requester allowlist. This document tests it against the code as it is, and answers the PM's
three questions: how a lane's identity is established today, what an allowlist adds on top of the
per-approval prompt, and how the 17 existing entries migrate.

**Refs.** `consent@57c84ce` (`origin/main`, crates `with-secret` and `user-request`) and
`agentlife@2d4cc51` (`lane-restart`, `lane-state`), read from `origin/main` blobs on 2026-10-16. Machine
facts (ACLs, `vault list`, `access.log`) were read on this box the same day. Where something was read in
code but not executed, the text says so.

## 1. Answer in short

1. **Yes to one store with an allowlist; no to two vaults.** A second vault would be a second DPAPI blob under
   the same Windows user, so it separates nothing an agent can reach (§3.3), and it doubles the
   `vault set`/mask/rotation surface.
2. **The allowlist is a filter before the prompt, not a security boundary.** A lane that is not on the list never
   reaches Windows Hello. That is a real gain (fewer prompts, and a confused lane cannot even ask for another
   project's secret). It does not stop a deliberate same-user process, because a lane's name can be forged
   (§2) and the vault can be decrypted by any process of the same user (`WITH-SECRET-DESIGN.md` §3). Say it
   that way in the docs, never stronger.
3. **The real boundary is the Windows account.** `gh-runner` must be, and stays, a different Windows user.
   Today that account does not exist yet, and two things would let it round the vault anyway: a runner
   that runs as `cires`, and the fact that `with-secret.exe` is writable by every local account (§3.2).

## 2. How a lane's identity is established today

`with-secret` never takes a name from an argument. In `main.rs:108` it calls `identity::resolve`
(`identity.rs:20`) with its own pid, the OS process table and every lane-state file in
`C:/Projects/.lane-state` (`lane-state` `platform.rs:68`):

1. Walk up from `with-secret`'s pid to the **nearest `claude`/`claude.exe` ancestor** (`identity.rs:35-60`; a
   parent that started after its child is a reused pid and stops the walk).
2. Find the lane-state file whose `pid` equals that ancestor's pid (`identity.rs:41`, `states.iter().find`:
   first match, directory order). Its **`role` field** (not the file name) becomes the requester's role, with the
   file's `session_id`. Requester = role + session + claude pid + claude start time, `managed: true`.
3. A `claude` with no such file is role `unmanaged-claude`; no `claude` ancestor is `outside-claude`. Both are
   `managed: false`, and the prompt then says "NOT a registered lane" (`channel.rs:97`).

How a state file gets its `role` (`lane_state_writer.rs:28` `resolve_role`, `:95` `hook_role`): the
`LANE_ROLE` environment variable of the hook process if set, else the file already recording this session, else
the **lower-cased leaf of the working directory**. The PM's own lane needs `LANE_ROLE=pm`, because its cwd
leaf is `Projects`.

**Can a lane forge another's name? Yes, by three routes, none of which needs more than the lane's own Windows
user.** I read each in code; I did **not** run any of them, because two would overwrite a real lane's state
file.

| route | why it works | ref |
|---|---|---|
| Start a nested `claude` with `LANE_ROLE=<other>` | The nested `claude`'s hooks write a state file with that role, and `with-secret` called from inside it resolves to that role. | `lane_state_writer.rs:95-100` |
| Write a state file by hand | `C:/Projects/.lane-state` grants **`Authenticated Users: Modify`** (`icacls`, 2026-10-16, inherited from `C:/Projects`). `resolve` trusts the file's `pid` and `role` fields; nothing signs them. | `identity.rs:41-47`, `icacls` |
| Start `claude` in a directory named like the role, with `LANE_ROLE` unset | Role falls back to the cwd leaf. | `lane_state_writer.rs:28-37` |

So the identity is good against **accident** (a lane cannot name itself into another lane's approval from the
command line, which is what `identity.rs:1-5` claims) and bad against **intent**. The same limit is already
accepted for the Hello prompt: `identity.rs:4` says so, and `WITH-SECRET-DESIGN.md` §3 states it.

Also observed: the role names in use are the state-file names under `C:/Projects/.lane-state`: `agentlife`,
`auth-framework`, `baracuda`, `coderipper`, `fuel`, `humboldt`, `humboldtunifiedkidtracker`, `kiss`,
`lightbulb`, `mlmf`, `overmind`, `persistant`, `pm`, `restarttest`, `safeimage`, `synapse`,
`thinkersfoundation`, `thinkersjournal-community`, `unisql`, `unpopped`, plus strays that are only a worktree's
directory name (`api`, `backend`, `docs`, `frontend`, `web`, `worker`, `shared`, …). The cwd-leaf fallback
therefore mints accidental roles, and an allowlist must use these exact strings (`community` in the PM's example
is not a role; `thinkersjournal-community` is).

## 3. Storage: what the OS actually separates

### 3.1 How `with-secret` stores entries (facts)

- One file, `vault.bin`, is the whole vault: `Vault { secrets: BTreeMap<String, Secret> }` serialised to JSON
  and encrypted as **one** DPAPI blob (`vault.rs:51-77,89,132-160`). A `Secret` is `value`, `env_var`, `access`
  (read/write), `created_at`, `rotate_by` (`vault.rs:51-62`). There is no per-entry blob and no owner field.
- Protection is `CryptProtectData` with `CRYPTPROTECT_UI_FORBIDDEN` only and the fixed entropy
  `overmind.with-secret.v1` (`dpapi.rs:10,47`). **No `CRYPTPROTECT_LOCAL_MACHINE`**, so it is per-user scope:
  the key derives from the Windows user's own credentials, not from the machine. The entropy is "an accident
  guard", per the comment, not a secret.
- Location: `%LOCALAPPDATA%\OverMind\with-secret` (`platform.rs:22-23`). ACL on this box: `SYSTEM`,
  `BUILTIN\Administrators` and `cires`, all Full, inherited from the profile; no `Users` or
  `Authenticated Users` entry (`icacls`, 2026-10-16).
- `masks.bin` (salted hashes for the output-masking hook) is protected the same way; `access.log` is plain JSON
  lines (`audit.rs:12`: time, event, secret, role, session, claude pid, command, reason; never the value).
- Approvals are NOT in the vault: they live in the `user-request` store (`run.rs:189`, `store.find`), keyed by
  kind + secret + requester.

### 3.2 `gh-runner` must have no access: what holds today

- **The account does not exist yet.** `net user` on this box (2026-10-16) lists `Administrator, cires,
  DefaultAccount, Guest, james, kims2, waemi, WDAGUtilityAccount`; the runner script has not been applied.
  Everything below is therefore reasoned from the ACL and the DPAPI scope, **not measured as `gh-runner`**.
- As a standard user, `gh-runner` cannot read `C:\Users\cires\AppData\Local\OverMind\with-secret` (the ACL above),
  and a copy of `vault.bin` would not decrypt under its credentials because the scope is per-user. That is
  documented DPAPI behaviour; I did not test it, because there is no second account to test with.
- `RUNNER-SETUP.md` already requires "a restricted Windows account, not CireSnave's" (`:10`) and "no secrets
  reachable by the account" (`:43`). Two gaps:
  1. §5's fallback, "an interactive runner (`run.cmd`) in a logged-on session of the runner account"
     (`RUNNER-SETUP.md:76-79`), is still `gh-runner`, but §5 also records a measurement taken "as CireSnave's
     account". **Add a rule: a runner never runs as `cires`.** A runner under the owner's account reaches the
     vault by DPAPI with no prompt (`WITH-SECRET-DESIGN.md` §3).
  2. **Everything under `C:/Projects` is writable by every local account.** `icacls` (2026-10-16): `C:/Projects`,
     `C:/Projects/.lane-state`, `C:/Projects/.claude-hooks` and `C:/Projects/.claude-hooks/with-secret.exe` each
     carry `NT AUTHORITY\Authenticated Users:(M)` (Modify), inherited from the drive root. Two consequences
     for a job running as `gh-runner`:
     - **It can replace `with-secret.exe`** (and `lane-restart.exe`, `user-request.exe`) in `.claude-hooks`.
       The next lane that runs it runs the job's code as `cires`, with DPAPI access to the vault. This
       bypasses the whole separation in §3.2, and it is the most important finding of this document. It
       needs no vault access at all.
     - It can write state files in `.lane-state`, and `identity.rs:41` would then mislabel whatever `claude`
       has the pid it names (approvals, `lane-restart`).

     **Recommend: an ACL of SYSTEM, Administrators and `cires` only (Modify for `cires`, read for none else)
     on `C:/Projects/.claude-hooks` and `C:/Projects/.lane-state`, applied before the runner account exists.**
     `C:/Projects` itself holds the repos the runner is meant to check out; its ACL is the PM's call. This
     needs an elevated shell, so it is a PM/CireSnave action. (`C:/Projects/.agentlife` does not exist on
     this box yet; the same rule applies to it when it does.)

### 3.3 Why a second vault does not help

Both vaults would be DPAPI blobs readable by the same `cires` user, which every lane runs as
(`WITH-SECRET-DESIGN.md` §2.1 and §3). Splitting by requester at the file level adds nothing a lane cannot undo;
splitting by Windows user (one account per lane) would, but is a different and much larger project (an
interactive session and a Hello prompt per account, plus the lanes' tools and credentials). Not proposed.

## 4. What an allowlist adds on top of the per-approval prompt

**The flow today** (`run.rs:153-250`): dump check, vault lookup (`:167`), grant maximum, **`store.find`**
(`:189`: an existing grant for this requester is used without any prompt), the store's gate `may_ask`
(`:198`: 10 minutes after a refusal, 6 prompts per lane per hour, 20 per hour for the person;
`store.rs:101-106`), then Hello (`:215`). The prompt shows Who, Wants, Duration and the reason
(`channel.rs:91-115`).

**What the allowlist changes, with the check placed where it must go:**

| | today | with an allowlist |
|---|---|---|
| A lane asks for another project's secret | A Hello prompt appears. CireSnave must know that this lane should not have it and say no (and the 10-minute cooldown applies per lane). | Refused in `authorize` before `store.find`/`may_ask`/Hello. No prompt, an audit line `not-allowed`. |
| Prompt fatigue | Bounded by the gate (6/lane/hour), but each wrong ask is still a prompt. | Wrong asks cost nothing. The prompt is for a lane that is allowed to ask. |
| Removing a lane from an entry | Needs `with-secret revoke` as well, or its existing grant keeps working until it expires. | If the check runs **before** `store.find` (place it right after `run.rs:167`), removal takes effect at once. |
| Who decides which lane may hold which secret | CireSnave, in his head, at each prompt. | CireSnave once, in a Hello-approved change; the log records every refusal. |
| A confused or compromised lane probing secrets | Looks like ordinary prompts. | Repeated `not-allowed` events for one role are a clear signal (new audit kind; the PM can alert on a count). |

**What it does not add:**

- No protection from a deliberate same-user process: it can forge a role (§2), edit `access` via DPAPI, or read
  the vault directly. The allowlist is only enforced by `with-secret.exe` itself.
- A forged role does not make the Hello prompt more trustworthy. It already says `Who: lane 'synapse'` for a
  forged `synapse`. With an allowlist the forger also becomes *eligible* to ask, so the approver's check is
  unchanged but the forger is no longer stopped early. The approver is still the only barrier to a forged
  identity, as now.
- The prompt line "Allowed lanes: …" is informational. It lets the approver notice an over-wide list; it
  does not prove the requester is who it says.

**Design, concretely**

- **Data:** `Secret.lanes: Vec<String>` with `#[serde(default)]` (`vault.rs:51`). Stored inside the same DPAPI
  blob, so a list change is one atomic `vault.bin` write. Role strings are matched exactly, case-sensitively
  against the lower-case role. `unmanaged-claude` and `outside-claude` are **never** matched by default; a list
  may name them explicitly, and `vault list` prints them in a warning colour.
- **Check:** in `authorize`, after the vault lookup: not on the list → log `not-allowed` (role, secret, claude
  pid) and return a message naming the entry and "ask the PM to add the lane". No store access, so no
  prompt and no gate slot spent.
- **Default deny** for new entries: `vault set` without `--lanes` is refused; `--lanes none` makes an
  entry nobody can request (parked). Absent `lanes` in a **legacy** entry means "none" after cutover (§5).
- **CLI:** `vault set NAME --env VAR --access read|write --lanes a,b,c`; **and** `vault allow NAME --lanes a,b,c`
  (replace) / `--add` / `--remove`, because changing the list through `vault set` means re-entering the
  secret (`main.rs:268`, `read_value_from_console`) and renews the mask salts. `vault allow` touches neither.
  Both are Hello-gated like `vault set` (`main.rs:184`, `require_hello`, text from `consent.rs`), so a lane
  cannot widen its own list through the CLI. A role that matches no state file under `.lane-state` is a
  warning ("typo? currently no such lane"), not an error: a lane may not have started yet.
- **Prompt:** one added line, `Allowed lanes: a, b, c` after `Wants`, in `channel.rs:91`. The test at
  `run.rs:553` pins the prompt at five lines and changes to six; Who, Wants and Duration stay unclipped
  (`channel.rs:88-90`).
- **Audit:** `Event` already records requester and entry (`audit.rs:12`). Add the kind `not-allowed` to the
  existing set (`granted | used | denied | timed-out | unavailable | refused | gate-refused`).
- **Version skew:** `Secret` has no `deny_unknown_fields`, so an older `with-secret.exe` that rewrites the vault
  (`vault set`/`remove`) drops `lanes`. The `.prev` binaries in `C:\Projects\.claude-hooks` would do that. After
  cutover, such an entry reads as "none": fail-closed, loud, recoverable with `vault allow`.

## 5. Migration of the 17 existing entries

`with-secret vault list` (2026-10-16; names and metadata only) shows 17 entries, all created 2026-10-15,
none with `rotate_by`, 16 `Write` and one `Read`. `access.log` has 34 rows between 2026-10-01 and 2026-10-16
and only three roles in it (`overmind`, `pm`, `outside-claude`), so **13 of the 17 entries have no logged use at
all**. The "proposed lanes" column is therefore mostly a proposal from the name, which the PM must confirm;
"basis" says what is evidence and what is a guess.

| entry | access | proposed lanes | basis |
|---|---|---|---|
| `GROQ_CLOUD_API_TOKEN` | write | `overmind` | log: overmind granted+used |
| `LLAMA_DESKTOP_API_KEY` | write | `overmind`, `pm` | log: both granted+used |
| `GOOGLE_AI_STUDIO_API_TOKEN` | write | `overmind` | name: free-tier provider probes (`probe/provider_probe.py`); no use logged |
| `MISTRAL_API_TOKEN` | write | `overmind` | same |
| `NVIDIA_NIM_API_TOKEN` | write | `overmind` | same |
| `OPENROUTER_API_TOKEN` | write | `overmind` | same |
| `HUGGINGFACE_API_TOKEN` | write | `overmind` | name; **ask** whether `mlmf` or `llm-bench` need it |
| `CLOUDFLARE_WORKERS_AI_API_TOKEN` | write | `overmind` | name |
| `CIVITAI_API_TOKEN` | write | `overmind` | name; **ask** who generates images (`safeimage`?) |
| `TJ_PROD_DATABASE_URL` | write | `pm` | log: pm granted+used (12 uses); `outside-claude` also granted+used (see below) |
| `TJ_PROD_DATABASE_URL_RO` | **read** | `pm`, `thinkersjournal-community`, `thinkersfoundation` | log: pm; the other two are a guess from the project, and the read-only entry is the safe one to share |
| `CLOUDFLARE_BUILDS_API_TOKEN` | write | `thinkersfoundation`, `pm` | name; `thinkersfoundation` is waiting on "token, zones, Pages" per its status |
| `CACHE_PURGE_TOKEN` | write | `thinkersfoundation`, `thinkersjournal-community` | name |
| `R2_ACCESS_KEY_ID` | write | `thinkersjournal-community` | name; **ask** |
| `R2_SECRET_ACCESS_KEY` | write | `thinkersjournal-community` | name; must always equal the line above |
| `CODECOV_TOKEN` | write | `pm` | name; CI normally uses a repo secret, so **ask** whether a lane needs it at all |
| `FLY_API_TOKEN` | write | `pm` | name; **ask** which lane deploys to Fly |

Notes for the PM:

- **`outside-claude` used `TJ_PROD_DATABASE_URL` twice** (and `TEST_THROWAWAY`, since removed from the vault).
  Under the new rule that role is denied unless an entry names it. Decide whether CireSnave's own shell keeps
  access (then list `outside-claude` on that entry and accept that any non-`claude` process of his user
  qualifies) or he uses a lane.
- A pair such as `R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY` should have the same list; `vault allow` takes several
  names in one call (`vault allow --file lanes.json`) so they cannot drift.
- **Cutover, one prompt:** build the new `with-secret.exe`; keep the old binary at `*.prev`; run one
  `vault allow --file` (a single Hello prompt: "set the lane lists of 17 secrets?") with the table the PM
  decided; then `vault list` shows each list. Between install and that call, every request is refused with a
  message that says why (fail-closed). With the log showing 34 events in 15 days, the gap costs little. The
  alternative (a transition release where an absent list means "any lane" and `vault list` warns) is
  fail-open while it lasts and I do not recommend it.
- Owners are the PM's decision, as asked; this is a proposal and the guesses are marked.

## 6. Open points for the PM

1. Approve the "filter, not boundary" wording of §1 for the user-facing docs.
2. Who decides the table in §5, and the `outside-claude` question.
3. The two OS actions in §3.2: "a runner never runs as `cires`" in `RUNNER-SETUP.md`, and the ACL on
   `.claude-hooks` and `.lane-state` (**before** `gh-runner` is created; elevated shell).
4. Where the implementation goes: `with-secret` and `user-request` are in the `consent` repo, so the code PR is
   that lane's. This document lives in OverMind only because the design task came to this lane.

## 7. Not measured

- Any behaviour as `gh-runner` (no such account).
- The three forgery routes (read, not executed).
- Whether an Administrator on this box (`james`, `kims2`, `waemi` were not checked for group membership) can
  recover `cires`'s DPAPI key; Microsoft's documented behaviour is that they cannot without the credentials,
  and I did not test it.
