// SPDX-License-Identifier: MIT OR Apache-2.0
//! The approvals store (plan step #2) and the prompt gate (the brute-force
//! audit, row 1: "approval fatigue").
//!
//! In one directory (by default `%LOCALAPPDATA%\OverMind\user-request`):
//! - `store.key`, `store.key.check`: the HMAC key, DPAPI-protected, and a
//!   value that tells the right key from a wrong one;
//! - `grants.json`: approvals and revocation tombstones;
//! - `attempts.json`: recent prompts and how they ended (or that they are
//!   still pending);
//! - `audit.jsonl`: every save, grant, revocation, gate decision, alert and
//!   integrity finding, hash-chained, with the chain's head ALSO written to a
//!   second file (PM condition (d));
//! - `store.lock`: an OS file lock, held from `open` until the `Store` is
//!   dropped, so one process at a time reads, changes and writes.
//!
//! Every public method that changes something saves before it returns, so
//! nothing depends on the caller remembering to (review I-B). ⚠️ Drop the
//! `Store` before showing a prompt: holding it holds the lock.
//!
//! ⚠️ The audit chain is the store's integrity anchor. On `open`:
//! - the chain must be intact and agree with its head copy;
//! - the files must be the ones the last save recorded;
//! - no integrity problem may be on record since the last repair.
//!
//! Anything else makes the store UNTRUSTWORTHY, and it fails CLOSED: no
//! grant is honoured and the gate refuses. The finding is written to the
//! audit log, so no later save can launder it (review C-B): revoking still
//! works, but only `repair` restores trust, and repair REVOKES EVERY GRANT
//! and closes the gate for an hour. It only removes privilege, so it needs
//! no Hello.
//!
//! ⚠️ Honest limit (WITH-SECRET-DESIGN.md §3): a process running as the same
//! Windows user can read the DPAPI key and rewrite the files, the chain and
//! its head copy consistently. All of this catches accidents and naive
//! edits, not that.

use std::collections::BTreeSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::Instant;

use chrono::{DateTime, Duration, Utc};
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::channel::Outcome;
use crate::request::{Approval, KindId, Requester, Scope};

/// Encrypts the store's key at rest (`dpapi::Dpapi` in production).
pub trait Protector {
    fn protect(&self, plain: &[u8]) -> Result<Vec<u8>, String>;
    fn unprotect(&self, blob: &[u8]) -> Result<Vec<u8>, String>;
}

/// Tells a person something needs their attention. ⚠️ DELIVERY is not built:
/// where alerts go waits on board item 131 (SECURITY_ALERT_EMAIL does not
/// exist yet). Every alert is ALSO written to the audit log by the store,
/// whatever the sink does.
pub trait Alert {
    fn alert(&self, what: &str);
}

/// No delivery; the audit log is the only record.
pub struct AuditOnly;
impl Alert for AuditOnly {
    fn alert(&self, _: &str) {}
}

/// After a denial or a timeout, the same role may not ask about the same
/// subject again for this long.
pub const DENIAL_COOLDOWN: Duration = Duration::minutes(10);
/// At most this many prompts per role per rolling hour.
pub const PROMPTS_PER_HOUR: usize = 6;
/// At most this many prompts per rolling hour in front of the person, from
/// every role together (review I9: the cap protects a person, not a role).
pub const PROMPTS_PER_HOUR_FOR_THE_PERSON: usize = 20;
/// This many denials or timeouts for one role in an hour raise an alert.
pub const DENIALS_BEFORE_ALERT: usize = 3;
/// After a repair, the gate refuses every prompt for this long.
pub const GATE_CLOSED_AFTER_REPAIR: Duration = Duration::hours(1);
/// How long `open` waits for another process's lock before giving up.
pub const LOCK_WAIT: std::time::Duration = std::time::Duration::from_secs(15);

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoredGrant {
    /// 128 random bits, hex.
    pub id: String,
    pub approval: Approval,
}

/// How a prompt ended. A closed set (review I-C): no caller can invent an
/// outcome the gate does not count.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Ended {
    /// The person has not answered yet.
    Pending,
    Approved,
    Denied,
    TimedOut,
    /// The channel could not ask.
    Unavailable,
    /// The channel refused to ask (over the kind's maximum, malformed).
    NotAsked,
    /// Not a prompt: an alert was raised (one per role and reason per hour).
    Alerted,
    /// Not a prompt: the store was repaired, which closes the gate.
    Repaired,
}

impl Ended {
    fn name(self) -> &'static str {
        match self {
            Ended::Pending => "pending",
            Ended::Approved => "approved",
            Ended::Denied => "denied",
            Ended::TimedOut => "timed-out",
            Ended::Unavailable => "unavailable",
            Ended::NotAsked => "not-asked",
            Ended::Alerted => "alerted",
            Ended::Repaired => "repaired",
        }
    }

    /// The person said no, or did not answer.
    fn is_refusal(self) -> bool {
        matches!(self, Ended::Denied | Ended::TimedOut)
    }

    /// Counts toward the hourly caps.
    fn is_prompt(self) -> bool {
        !matches!(self, Ended::Alerted | Ended::Repaired)
    }
}

/// One prompt, from the gate's reservation to its answer.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Attempt {
    /// 128 random bits, hex.
    pub id: String,
    pub at: DateTime<Utc>,
    pub requester: Requester,
    pub kind: KindId,
    /// Normalised (`normal_subject`), so `DB`, `db` and `DB ` share a cooldown.
    pub subject: String,
    pub outcome: Ended,
}

/// What `may_ask` hands back: the pending attempt to resolve once the person
/// has answered.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Reservation {
    pub attempt_id: String,
}

/// What `verify_audit` found.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuditReport {
    /// Lines in the log.
    pub lines: usize,
    /// Chain resets that a later repair acknowledged.
    pub repaired_resets: usize,
}

#[derive(Default, Serialize, Deserialize)]
struct GrantsFile {
    grants: Vec<StoredGrant>,
    /// Ids revoked; kept so an old copy of a grant cannot come back.
    revoked: BTreeSet<String>,
}

#[derive(Serialize, Deserialize)]
struct Header {
    seq: u64,
    mac: String,
}

#[derive(Serialize, Deserialize)]
struct AuditLine {
    prev: String,
    at: DateTime<Utc>,
    event: String,
    detail: String,
}

/// Why the store cannot be trusted right now.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Untrusted {
    /// The key is missing, did not decrypt, or decrypted to the wrong bytes.
    Key(String),
    /// A file or the audit chain is not what it should be, now or at some
    /// point since the last repair.
    Files(String),
}

pub struct Store {
    dir: PathBuf,
    key: Vec<u8>,
    head_copy: Option<PathBuf>,
    seq: u64,
    pub grants: Vec<StoredGrant>,
    pub revoked: BTreeSet<String>,
    pub attempts: Vec<Attempt>,
    /// `None` when the store can be trusted.
    pub untrusted: Option<Untrusted>,
    _lock: StoreLock,
}

/// The files whose presence means a store exists.
const DATA_FILES: [&str; 3] = ["grants.json", "attempts.json", "audit.jsonl"];

impl Store {
    /// Opens the store in `dir`, creating it if it does not exist, and holds
    /// its lock until the `Store` is dropped. `head_copy` is the second
    /// place the audit chain's head is written.
    pub fn open(
        dir: &Path,
        protector: &dyn Protector,
        head_copy: Option<PathBuf>,
    ) -> Result<Self, String> {
        std::fs::create_dir_all(dir).map_err(|e| format!("create {}: {e}", dir.display()))?;
        Self::open_in(dir, protector, head_copy)
    }

    /// Like `open`, but `None` when there is no store yet: read-only
    /// commands never create one (review M2, M5). A store whose key is gone
    /// but whose data is not still exists (review I-F).
    pub fn open_existing(
        dir: &Path,
        protector: &dyn Protector,
        head_copy: Option<PathBuf>,
    ) -> Result<Option<Self>, String> {
        let exists =
            dir.join("store.key").exists() || DATA_FILES.iter().any(|n| dir.join(n).exists());
        if !exists {
            return Ok(None);
        }
        Self::open_in(dir, protector, head_copy).map(Some)
    }

    fn open_in(
        dir: &Path,
        protector: &dyn Protector,
        head_copy: Option<PathBuf>,
    ) -> Result<Self, String> {
        let lock = StoreLock::acquire(&dir.join("store.lock"))?;
        let key_path = dir.join("store.key");
        let check_path = dir.join("store.key.check");
        let mut untrusted = None;
        let key = match std::fs::read(&key_path) {
            Ok(blob) => match protector.unprotect(&blob) {
                Ok(k) => k,
                Err(e) => {
                    untrusted = Some(Untrusted::Key(format!("the key did not decrypt: {e}")));
                    Vec::new()
                }
            },
            // a key is created only for a store that has nothing yet (review
            // M2, I-F): a lost key must never look like a fresh install
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                if DATA_FILES.iter().any(|n| dir.join(n).exists()) {
                    untrusted = Some(Untrusted::Key(
                        "store.key is missing but the store has data".into(),
                    ));
                    Vec::new()
                } else {
                    new_key(dir, protector)?
                }
            }
            Err(e) => return Err(format!("read {}: {e}", key_path.display())),
        };
        let mut store = Self {
            dir: dir.to_path_buf(),
            key,
            head_copy,
            seq: 0,
            grants: Vec::new(),
            revoked: BTreeSet::new(),
            attempts: Vec::new(),
            untrusted,
            _lock: lock,
        };
        if store.untrusted.is_none() {
            store.load(&check_path, Utc::now())?;
        }
        Ok(store)
    }

    fn load(&mut self, check_path: &Path, now: DateTime<Utc>) -> Result<(), String> {
        let grants = read_signed(&self.dir.join("grants.json"), &self.key);
        let attempts = read_signed(&self.dir.join("attempts.json"), &self.key);
        // the key check: a match, or (missing check, review I2) files that
        // verify under this key, which proves it is the right one
        let check = std::fs::read_to_string(check_path).ok();
        let verified_any =
            matches!(grants, Signed::Good { .. }) || matches!(attempts, Signed::Good { .. });
        let nothing_yet = grants == Signed::Missing && attempts == Signed::Missing;
        match check {
            Some(c) if c == mac(&self.key, KEY_CHECK.as_bytes()) => {}
            Some(_) => {
                self.untrusted = Some(Untrusted::Key(
                    "the key decrypted to bytes that are not this store's key".into(),
                ));
                return Ok(());
            }
            None if verified_any || nothing_yet => {
                write_atomic(check_path, mac(&self.key, KEY_CHECK.as_bytes()).as_bytes())?;
            }
            None => {
                self.untrusted = Some(Untrusted::Key(
                    "the key check is missing and nothing verifies".into(),
                ));
                return Ok(());
            }
        }
        let mut problems = Vec::new();
        // the chain and its head copy first (review C-A): a whole-directory
        // rollback or wipe leaves files that match their own log
        if let Err(why) = self.check_chain() {
            problems.push(why);
        }
        let expected = self.last_saved();
        for (name, file) in [("grants.json", &grants), ("attempts.json", &attempts)] {
            match (file, expected.as_ref().and_then(|e| e.get(name))) {
                (Signed::Bad, _) => {
                    let to = quarantine(&self.dir.join(name));
                    problems.push(format!("{name} failed its signature (kept as {to})"));
                }
                (Signed::Good { sha, .. }, Some(want)) if sha != want => {
                    problems.push(format!("{name} is not the file the last save recorded"))
                }
                (Signed::Good { .. }, None) => {
                    problems.push(format!("{name} has no save recorded in the audit log"))
                }
                (Signed::Missing, Some(_)) => problems.push(format!("{name} is missing")),
                _ => {}
            }
        }
        // a body that verifies but does not parse (a newer or older schema)
        // is set aside, never a reason open fails: revoking must always work
        // (review I-H)
        if let Signed::Good { seq, body, .. } = grants {
            match serde_json::from_slice::<GrantsFile>(&body) {
                Ok(g) => {
                    self.grants = g.grants;
                    self.revoked = g.revoked;
                    self.seq = seq;
                }
                Err(e) => {
                    let to = quarantine(&self.dir.join("grants.json"));
                    problems.push(format!("grants.json did not parse: {e} (kept as {to})"));
                }
            }
        }
        if let Signed::Good { seq, body, .. } = attempts {
            match serde_json::from_slice::<Vec<Attempt>>(&body) {
                Ok(a) => {
                    self.attempts = a;
                    self.seq = self.seq.max(seq);
                }
                Err(e) => {
                    let to = quarantine(&self.dir.join("attempts.json"));
                    problems.push(format!("attempts.json did not parse: {e} (kept as {to})"));
                }
            }
        }
        let on_record = self.unrepaired_finding();
        if problems.is_empty() && on_record.is_none() {
            return Ok(());
        }
        let mut why = problems.join("; ");
        if !problems.is_empty() && on_record.is_none() {
            // recorded, so no later save can launder it (review C-B); best
            // effort: the store is untrusted in memory either way
            let _ = self.audit(now, "untrusted", &why);
        }
        if let Some(earlier) = on_record {
            if !why.is_empty() {
                why.push_str("; ");
            }
            why.push_str(&format!("not repaired since {earlier}"));
        }
        self.untrusted = Some(Untrusted::Files(why));
        Ok(())
    }

    /// The first integrity finding on record since the last repair.
    fn unrepaired_finding(&self) -> Option<String> {
        let text = std::fs::read_to_string(self.dir.join("audit.jsonl")).unwrap_or_default();
        let lines: Vec<AuditLine> = text
            .lines()
            .filter_map(|l| serde_json::from_str::<AuditLine>(l).ok())
            .collect();
        let start = lines
            .iter()
            .rposition(|a| a.event == "repaired")
            .map_or(0, |i| i + 1);
        lines
            .into_iter()
            .skip(start)
            .find(|a| a.event == "untrusted" || a.event == "chain-reset")
            .map(|a| format!("{} at {}: {}", a.event, a.at, a.detail))
    }

    /// The file hashes the last `saved` audit line recorded.
    fn last_saved(&self) -> Option<std::collections::HashMap<String, String>> {
        let text = std::fs::read_to_string(self.dir.join("audit.jsonl")).ok()?;
        let line = text.lines().rev().find_map(|l| {
            serde_json::from_str::<AuditLine>(l)
                .ok()
                .filter(|a| a.event == "saved")
        })?;
        Some(
            line.detail
                .split_whitespace()
                .filter_map(|kv| kv.split_once('='))
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
        )
    }

    /// `Ok` when grants may be honoured and the gate trusted.
    pub fn trustworthy(&self) -> Result<(), String> {
        match &self.untrusted {
            None => Ok(()),
            Some(Untrusted::Key(why)) | Some(Untrusted::Files(why)) => Err(format!(
                "the store cannot be trusted: {why} (`user-request repair` revokes every \
                 grant and restores it)"
            )),
        }
    }

    /// Writes grants and attempts (expired grants and attempts older than a
    /// day are dropped) and records their hashes in the audit log. Refused
    /// when the key is untrusted, so the real store is never overwritten. A
    /// store whose FILES are untrusted may save (that is how a revocation
    /// lands) but stays untrusted: only `repair` restores trust.
    pub(crate) fn save(&mut self, now: DateTime<Utc>) -> Result<(), String> {
        if let Some(Untrusted::Key(why)) = &self.untrusted {
            return Err(format!("refusing to save: {why}"));
        }
        self.seq += 1;
        let grants = GrantsFile {
            grants: self
                .grants
                .iter()
                .filter(|g| live(g, now))
                .cloned()
                .collect(),
            revoked: self.revoked.clone(),
        };
        let attempts: Vec<&Attempt> = self
            .attempts
            .iter()
            .filter(|a| a.at > now - Duration::days(1))
            .collect();
        let gbody = serde_json::to_vec(&grants).map_err(|e| e.to_string())?;
        let abody = serde_json::to_vec(&attempts).map_err(|e| e.to_string())?;
        let written = write_signed(&self.dir.join("grants.json"), &self.key, self.seq, &gbody)
            .and_then(|g| {
                write_signed(&self.dir.join("attempts.json"), &self.key, self.seq, &abody)
                    .map(|a| (g, a))
            });
        match written {
            Ok((gsha, asha)) => self.audit(
                now,
                "saved",
                &format!("seq={} grants.json={gsha} attempts.json={asha}", self.seq),
            ),
            Err(e) => {
                // so an audited change that did not land says so (review M-a)
                let _ = self.audit(now, "save-failed", &e);
                Err(e)
            }
        }
    }

    /// Stores an approval and returns its id. Refused by an untrustworthy
    /// store.
    pub fn add(&mut self, approval: Approval, now: DateTime<Utc>) -> Result<String, String> {
        self.trustworthy()?;
        let id = random_id();
        let expires = approval
            .expires_at
            .map_or("FOREVER".to_string(), |e| e.to_rfc3339());
        self.audit(
            now,
            "granted",
            &format!(
                "id={id} kind={:?} subject={} role={} expires={expires}",
                approval.kind, approval.subject, approval.requester.role
            ),
        )?;
        self.grants.push(StoredGrant {
            id: id.clone(),
            approval,
        });
        self.save(now)?;
        Ok(id)
    }

    /// A live grant covering this request: same kind and subject, not
    /// expired, not revoked, the same requester when the kind's scope is one
    /// requester - and never from an untrustworthy store.
    pub fn find(
        &self,
        kind: KindId,
        subject: &str,
        requester: &Requester,
        now: DateTime<Utc>,
    ) -> Option<&StoredGrant> {
        if self.trustworthy().is_err() {
            return None;
        }
        self.active(now).into_iter().find(|g| {
            let a = &g.approval;
            a.kind == kind
                && a.subject == subject
                && match kind.scope() {
                    Scope::ThisRequester => a.requester == *requester,
                    Scope::AnyRequester => true,
                }
        })
    }

    /// Every grant that has not expired or been revoked.
    pub fn active(&self, now: DateTime<Utc>) -> Vec<&StoredGrant> {
        self.grants
            .iter()
            .filter(|g| live(g, now) && !self.revoked.contains(&g.id))
            .collect()
    }

    /// Revokes one grant; `false` when there is no such active grant. Works
    /// on an untrustworthy store unless its key is the problem (then its
    /// grants are unknown, and only `repair` helps).
    pub fn revoke(&mut self, id: &str, now: DateTime<Utc>) -> Result<bool, String> {
        self.key_known()?;
        let known = self.grants.iter().any(|g| g.id == id) && !self.revoked.contains(id);
        if !known {
            return Ok(false);
        }
        self.audit(now, "revoked", &format!("id={id}"))?;
        self.revoked.insert(id.to_string());
        self.save(now)?;
        Ok(true)
    }

    /// The panic button: revokes every grant, returns how many.
    pub fn revoke_all(&mut self, now: DateTime<Utc>) -> Result<usize, String> {
        self.key_known()?;
        let n = self.revoke_everything(now)?;
        self.save(now)?;
        Ok(n)
    }

    fn revoke_everything(&mut self, now: DateTime<Utc>) -> Result<usize, String> {
        let ids: Vec<String> = self
            .grants
            .iter()
            .map(|g| g.id.clone())
            .filter(|id| !self.revoked.contains(id))
            .collect();
        let n = ids.len();
        self.audit(now, "revoked-all", &format!("n={n} ids={}", ids.join(",")))?;
        self.revoked.extend(ids);
        Ok(n)
    }

    fn key_known(&self) -> Result<(), String> {
        match &self.untrusted {
            Some(Untrusted::Key(why)) => Err(format!(
                "the store's key cannot be trusted ({why}), so its grants are unknown; \
                 `user-request repair` revokes every grant"
            )),
            _ => Ok(()),
        }
    }

    /// Restores trust by REVOKING EVERY GRANT and closing the gate for
    /// `GATE_CLOSED_AFTER_REPAIR`. A store whose key is the problem gets a
    /// new key, and its old files are set aside. Removes privilege only, so
    /// it needs no Hello. Returns how many grants it revoked.
    pub fn repair(
        &mut self,
        now: DateTime<Utc>,
        protector: &dyn Protector,
    ) -> Result<usize, String> {
        let was = match &self.untrusted {
            None => "nothing (the store was trusted)".to_string(),
            Some(Untrusted::Key(w)) | Some(Untrusted::Files(w)) => w.clone(),
        };
        if matches!(self.untrusted, Some(Untrusted::Key(_))) {
            for name in ["store.key", "grants.json", "attempts.json"] {
                if self.dir.join(name).exists() {
                    quarantine(&self.dir.join(name));
                }
            }
            self.key = new_key(&self.dir, protector)?;
            self.grants.clear();
            self.revoked.clear();
            self.attempts.clear();
            self.seq = 0;
            self.untrusted = Some(Untrusted::Files(was.clone()));
        }
        let n = self.revoke_everything(now)?;
        self.attempts.push(Attempt {
            id: random_id(),
            at: now,
            requester: nobody("*"),
            kind: KindId::Secret,
            subject: "repair".into(),
            outcome: Ended::Repaired,
        });
        self.audit(
            now,
            "repaired",
            &format!(
                "revoked={n} gate-closed-until={} was: {was}",
                (now + GATE_CLOSED_AFTER_REPAIR).to_rfc3339()
            ),
        )?;
        self.untrusted = None;
        self.save(now)?;
        Ok(n)
    }

    /// May `requester` put a prompt about `subject` in front of the person
    /// now? On yes, a PENDING attempt is recorded and saved, and it counts
    /// toward every limit until resolved (review C3). Refused when the store
    /// is untrustworthy (fail closed), for an hour after a repair, in the
    /// cooldown after a denial or timeout of the same role and subject, or
    /// past the role's or the person's hourly cap. Keyed by ROLE, so a
    /// restart resets nothing. Every decision is audited and saved.
    pub fn may_ask(
        &mut self,
        requester: &Requester,
        kind: KindId,
        subject: &str,
        now: DateTime<Utc>,
        alert: &dyn Alert,
    ) -> Result<Reservation, String> {
        let subject = normal_subject(subject);
        let role = requester.role.clone();
        if let Err(why) = self.gate(&role, &subject, now, alert) {
            let recorded = self
                .audit(
                    now,
                    "gate-refused",
                    &format!("role={role} subject={subject}: {why}"),
                )
                .and_then(|()| match self.untrusted {
                    // nothing can be saved under a key that is not trusted
                    Some(Untrusted::Key(_)) => Ok(()),
                    _ => self.save(now),
                });
            return Err(match recorded {
                Ok(()) => why,
                Err(e) => format!("{why} (and recording it failed: {e})"),
            });
        }
        let id = random_id();
        self.attempts.push(Attempt {
            id: id.clone(),
            at: now,
            requester: requester.clone(),
            kind,
            subject: subject.clone(),
            outcome: Ended::Pending,
        });
        self.audit(
            now,
            "gate-allowed",
            &format!("role={role} subject={subject} attempt={id}"),
        )?;
        self.save(now)?;
        Ok(Reservation { attempt_id: id })
    }

    fn gate(
        &mut self,
        role: &str,
        subject: &str,
        now: DateTime<Utc>,
        alert: &dyn Alert,
    ) -> Result<(), String> {
        self.trustworthy()?;
        let within = |a: &Attempt, d: Duration| now < a.at + d;
        if let Some(r) = self
            .attempts
            .iter()
            .filter(|a| a.outcome == Ended::Repaired && within(a, GATE_CLOSED_AFTER_REPAIR))
            .map(|a| a.at)
            .max()
        {
            return Err(format!(
                "the store was repaired at {r}; prompts resume at {}",
                r + GATE_CLOSED_AFTER_REPAIR
            ));
        }
        if self.attempts.iter().any(|a| {
            a.requester.role == role
                && a.subject == subject
                && a.outcome.is_refusal()
                && within(a, DENIAL_COOLDOWN)
        }) {
            return Err(format!(
                "'{role}' was refused about '{subject}' less than {} minutes ago",
                DENIAL_COOLDOWN.num_minutes()
            ));
        }
        let prompts = |only: Option<&str>| {
            self.attempts
                .iter()
                .filter(|a| a.outcome.is_prompt() && within(a, Duration::hours(1)))
                .filter(|a| only.is_none_or(|r| a.requester.role == r))
                .count()
        };
        let (mine, everyone) = (prompts(Some(role)), prompts(None));
        let why = if mine >= PROMPTS_PER_HOUR {
            format!("'{role}' has asked {mine} times in the last hour (cap {PROMPTS_PER_HOUR})")
        } else if everyone >= PROMPTS_PER_HOUR_FOR_THE_PERSON {
            format!(
                "{everyone} prompts reached the person in the last hour \
                 (cap {PROMPTS_PER_HOUR_FOR_THE_PERSON})"
            )
        } else {
            return Ok(());
        };
        self.alert_once(role, "cap", &why, now, alert)?;
        Err(why)
    }

    /// Records how a reserved prompt ended, once (review I-C), and saves.
    /// An approval must be for what was reserved, and is stored as a grant
    /// in the same step; its id is returned. Repeated refusals alert once.
    pub fn resolve(
        &mut self,
        reservation: &Reservation,
        outcome: &Outcome,
        now: DateTime<Utc>,
        alert: &dyn Alert,
    ) -> Result<Option<String>, String> {
        let a = self
            .attempts
            .iter()
            .find(|a| a.id == reservation.attempt_id)
            .ok_or("no such attempt")?;
        if a.outcome != Ended::Pending {
            return Err(format!(
                "attempt {} already ended: {}",
                a.id,
                a.outcome.name()
            ));
        }
        let (role, subject) = (a.requester.role.clone(), a.subject.clone());
        let ended = match outcome {
            Outcome::Approved(ap) => {
                if ap.kind != a.kind
                    || normal_subject(&ap.subject) != a.subject
                    || ap.requester != a.requester
                {
                    return Err("the approval is not for what was reserved".into());
                }
                Ended::Approved
            }
            Outcome::Denied => Ended::Denied,
            Outcome::TimedOut => Ended::TimedOut,
            Outcome::Unavailable(_) => Ended::Unavailable,
            Outcome::Refused(_) => Ended::NotAsked,
        };
        self.audit(
            now,
            "resolved",
            &format!(
                "outcome={} role={role} subject={subject} attempt={}",
                ended.name(),
                reservation.attempt_id
            ),
        )?;
        if let Some(a) = self
            .attempts
            .iter_mut()
            .find(|a| a.id == reservation.attempt_id)
        {
            a.outcome = ended;
        }
        let mut grant = None;
        if let Outcome::Approved(ap) = outcome {
            // `add` saves; it refuses an untrustworthy store, and then the
            // attempt is still recorded
            match self.add(ap.clone(), now) {
                Ok(id) => grant = Some(id),
                Err(e) => {
                    self.save(now)?;
                    return Err(e);
                }
            }
        }
        if ended.is_refusal() {
            let refusals = self
                .attempts
                .iter()
                .filter(|a| {
                    a.requester.role == role
                        && a.outcome.is_refusal()
                        && now < a.at + Duration::hours(1)
                })
                .count();
            if refusals >= DENIALS_BEFORE_ALERT {
                let what = format!("'{role}' was refused {refusals} times within an hour");
                self.alert_once(&role, "refusals", &what, now, alert)?;
            }
        }
        self.save(now)?;
        Ok(grant)
    }

    /// One alert per role and reason per rolling hour (review M1), always
    /// audited, whatever the sink does. The caller saves.
    fn alert_once(
        &mut self,
        role: &str,
        reason: &str,
        what: &str,
        now: DateTime<Utc>,
        alert: &dyn Alert,
    ) -> Result<(), String> {
        let tag = format!("alert:{reason}");
        let recent = self.attempts.iter().any(|a| {
            a.outcome == Ended::Alerted
                && a.requester.role == role
                && a.subject == tag
                && now < a.at + Duration::hours(1)
        });
        if recent {
            return Ok(());
        }
        self.attempts.push(Attempt {
            id: random_id(),
            at: now,
            requester: nobody(role),
            kind: KindId::Secret,
            subject: tag,
            outcome: Ended::Alerted,
        });
        self.audit(now, "ALERT", what)?;
        alert.alert(what);
        Ok(())
    }

    /// Appends one line to the hash-chained audit log and rewrites the head
    /// copy. The chain is checked first (review C1, I5, I6): a chain that is
    /// broken, or shorter than its head copy says (truncated, deleted, a torn
    /// last line), gets an explicit `chain-reset` line naming why, which
    /// keeps the store untrusted until a repair.
    fn audit(&self, at: DateTime<Utc>, event: &str, detail: &str) -> Result<(), String> {
        let path = self.dir.join("audit.jsonl");
        let (count, prev) = match self.check_chain() {
            Ok(head) => head,
            Err(why) => {
                let reset = AuditLine {
                    prev: GENESIS.to_string(),
                    at,
                    event: "chain-reset".into(),
                    detail: why,
                };
                let text =
                    String::from_utf8_lossy(&std::fs::read(&path).unwrap_or_default()).into_owned();
                let line = serde_json::to_string(&reset).map_err(|e| e.to_string())?;
                let sep = if text.is_empty() || text.ends_with('\n') {
                    ""
                } else {
                    "\n"
                };
                append(&path, &format!("{sep}{line}\n"))?;
                let n = String::from_utf8_lossy(&std::fs::read(&path).unwrap_or_default())
                    .lines()
                    .count();
                (n, sha(line.as_bytes()))
            }
        };
        let line = serde_json::to_string(&AuditLine {
            prev,
            at,
            event: event.to_string(),
            detail: detail.to_string(),
        })
        .map_err(|e| e.to_string())?;
        append(&path, &format!("{line}\n"))?;
        if let Some(copy) = &self.head_copy {
            // best effort (review I-D): a revocation must land even where the
            // copy cannot be written; the next open then finds the copy wrong
            // and the store untrusted, which fails closed
            if let Some(parent) = copy.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = write_atomic(
                copy,
                format!("{} {}\n", count + 1, sha(line.as_bytes())).as_bytes(),
            );
        }
        Ok(())
    }

    /// The chain's (length, last hash) if every link and the head copy
    /// agree. A `chain-reset` line starts a new chain.
    fn check_chain(&self) -> Result<(usize, String), String> {
        let path = self.dir.join("audit.jsonl");
        let bytes = match std::fs::read(&path) {
            Ok(b) => b,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(e) => return Err(format!("audit log unreadable: {e}")),
        };
        let text = String::from_utf8(bytes).map_err(|_| "audit log is not UTF-8".to_string())?;
        if !text.is_empty() && !text.ends_with('\n') {
            return Err("the audit log's last line is torn".into());
        }
        // A `chain-reset` line starts a new chain: only lines from the last
        // one on are checked (what came before is the damage it reported).
        let lines: Vec<&str> = text.lines().collect();
        let start = lines
            .iter()
            .rposition(|l| {
                serde_json::from_str::<AuditLine>(l).is_ok_and(|a| a.event == "chain-reset")
            })
            .unwrap_or(0);
        let mut prev = GENESIS.to_string();
        for (i, l) in lines.iter().enumerate().skip(start) {
            let line: AuditLine = serde_json::from_str(l)
                .map_err(|e| format!("audit line {}: unreadable: {e}", i + 1))?;
            if line.prev != prev && !(i == start && line.event == "chain-reset") {
                return Err(format!("audit chain broken at line {}", i + 1));
            }
            prev = sha(l.as_bytes());
        }
        let count = lines.len();
        if let Some(copy) = &self.head_copy {
            match std::fs::read_to_string(copy) {
                Ok(head) if head.trim() == format!("{count} {prev}") => {}
                Ok(head) => {
                    return Err(format!(
                        "audit head copy says '{}', the chain ends at '{count} {prev}': \
                         truncated, rolled back or rewritten",
                        head.trim()
                    ))
                }
                Err(_) if count == 0 => {}
                Err(_) => return Err(format!("audit head copy {} is missing", copy.display())),
            }
        }
        Ok((count, prev))
    }

    /// Checks every link of the chain and the head copy. A `chain-reset`
    /// since the last repair is an error: it is evidence the chain was found
    /// broken. Resets that a repair acknowledged are counted, not failed
    /// (review M-f).
    pub fn verify_audit(&self) -> Result<AuditReport, String> {
        let (lines, _) = self.check_chain()?;
        let text = std::fs::read_to_string(self.dir.join("audit.jsonl")).unwrap_or_default();
        let parsed: Vec<(usize, AuditLine)> = text
            .lines()
            .enumerate()
            .filter_map(|(i, l)| serde_json::from_str::<AuditLine>(l).ok().map(|a| (i, a)))
            .collect();
        let after_repair = parsed
            .iter()
            .rposition(|(_, a)| a.event == "repaired")
            .map_or(0, |i| i + 1);
        let mut repaired_resets = 0;
        for (n, (i, a)) in parsed.iter().enumerate() {
            if a.event != "chain-reset" {
                continue;
            }
            if n < after_repair {
                repaired_resets += 1;
            } else {
                return Err(format!(
                    "the chain was reset at line {}: {}",
                    i + 1,
                    a.detail
                ));
            }
        }
        Ok(AuditReport {
            lines,
            repaired_resets,
        })
    }
}

/// A store-wide OS file lock (review I-A): the OS releases it when the
/// handle closes, including when the process dies, so there is no staleness
/// to guess and no other holder's lock to delete. The file itself stays.
struct StoreLock {
    _file: std::fs::File,
}

impl StoreLock {
    fn acquire(path: &Path) -> Result<Self, String> {
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)
            .map_err(|e| format!("lock {}: {e}", path.display()))?;
        let deadline = Instant::now() + LOCK_WAIT;
        loop {
            match file.try_lock() {
                Ok(()) => return Ok(Self { _file: file }),
                Err(std::fs::TryLockError::WouldBlock) => {
                    if Instant::now() >= deadline {
                        return Err(format!(
                            "the store is locked by another process ({})",
                            path.display()
                        ));
                    }
                    std::thread::sleep(std::time::Duration::from_millis(20));
                }
                Err(std::fs::TryLockError::Error(e)) => {
                    return Err(format!("lock {}: {e}", path.display()))
                }
            }
        }
    }
}

fn new_key(dir: &Path, protector: &dyn Protector) -> Result<Vec<u8>, String> {
    let mut k = vec![0u8; 32];
    getrandom::fill(&mut k).map_err(|e| format!("random key: {e}"))?;
    write_atomic(&dir.join("store.key"), &protector.protect(&k)?)?;
    write_atomic(
        &dir.join("store.key.check"),
        mac(&k, KEY_CHECK.as_bytes()).as_bytes(),
    )?;
    Ok(k)
}

/// The requester recorded on a store-made attempt (an alert, a repair).
fn nobody(role: &str) -> Requester {
    Requester {
        role: role.to_string(),
        session_id: String::new(),
        claude_pid: 0,
        claude_start_secs: 0,
        managed: false,
    }
}

fn live(g: &StoredGrant, now: DateTime<Utc>) -> bool {
    g.approval.expires_at.is_none_or(|e| e > now)
}

/// Subjects are compared trimmed and case-folded (review I9).
pub fn normal_subject(s: &str) -> String {
    s.trim().to_lowercase()
}

fn random_id() -> String {
    let mut id = [0u8; 16];
    getrandom::fill(&mut id).expect("the OS random source works");
    hex(&id)
}

/// What `store.key.check` signs, to tell the right key from a wrong one.
const KEY_CHECK: &str = "user-request store key check";

/// The `prev` of the first audit line.
const GENESIS: &str = "0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Debug, PartialEq)]
enum Signed {
    Missing,
    Bad,
    Good {
        seq: u64,
        body: Vec<u8>,
        sha: String,
    },
}

/// A signed file: a header line `{"seq":N,"mac":"..."}`, a newline, and the
/// body. The MAC covers the sequence number and the body's EXACT bytes, so
/// no re-serialisation can change what was signed (review I8).
fn read_signed(path: &Path, key: &[u8]) -> Signed {
    let Ok(bytes) = std::fs::read(path) else {
        return Signed::Missing;
    };
    let Some(nl) = bytes.iter().position(|&b| b == b'\n') else {
        return Signed::Bad;
    };
    let (head, body) = (&bytes[..nl], &bytes[nl + 1..]);
    let Ok(h) = serde_json::from_slice::<Header>(head) else {
        return Signed::Bad;
    };
    if key.is_empty() || mac(key, &signed_bytes(h.seq, body)) != h.mac {
        return Signed::Bad;
    }
    Signed::Good {
        seq: h.seq,
        body: body.to_vec(),
        sha: sha(&bytes),
    }
}

fn write_signed(path: &Path, key: &[u8], seq: u64, body: &[u8]) -> Result<String, String> {
    let mut bytes = serde_json::to_vec(&Header {
        seq,
        mac: mac(key, &signed_bytes(seq, body)),
    })
    .map_err(|e| e.to_string())?;
    bytes.push(b'\n');
    bytes.extend_from_slice(body);
    write_atomic(path, &bytes)?;
    Ok(sha(&bytes))
}

fn signed_bytes(seq: u64, body: &[u8]) -> Vec<u8> {
    let mut v = format!("seq:{seq}\n").into_bytes();
    v.extend_from_slice(body);
    v
}

/// Moves a file aside instead of dropping it (review I8); returns its new
/// name, which is unique, so two files set aside in the same millisecond
/// never overwrite each other.
fn quarantine(path: &Path) -> String {
    let to = path.with_extension(format!(
        "rejected-{}-{}",
        Utc::now().format("%Y%m%dT%H%M%S%.3f"),
        &random_id()[..8]
    ));
    let _ = std::fs::rename(path, &to);
    to.display().to_string()
}

fn append(path: &Path, text: &str) -> Result<(), String> {
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| format!("open {}: {e}", path.display()))?;
    f.write_all(text.as_bytes()).map_err(|e| e.to_string())
}

/// A uniquely named temp file and a rename, so neither a reader nor another
/// writer ever sees half a file (review I1: shared temp names collided).
fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let tmp = path.with_extension(format!("tmp-{}", random_id()));
    std::fs::write(&tmp, bytes).map_err(|e| format!("write {}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("rename to {}: {e}", path.display())
    })
}

fn mac(key: &[u8], bytes: &[u8]) -> String {
    let mut m = <Hmac<Sha256> as KeyInit>::new_from_slice(key).expect("HMAC takes any key length");
    m.update(bytes);
    hex(&m.finalize().into_bytes())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn sha(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes))
}

#[cfg(test)]
mod tests;
