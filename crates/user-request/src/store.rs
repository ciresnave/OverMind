// SPDX-License-Identifier: MIT OR Apache-2.0
//! The approvals store (plan step #2) and the prompt gate (the brute-force
//! audit, row 1: "approval fatigue").
//!
//! In one directory (by default `%LOCALAPPDATA%\OverMind\user-request`):
//! - `store.key`: the HMAC key, DPAPI-protected;
//! - `grants.json`: approvals, each HMAC-signed;
//! - `attempts.json`: recent prompts and how they ended, each HMAC-signed;
//! - `audit.jsonl`: every grant, revocation, denial and refusal, hash-chained,
//!   with the chain's head ALSO written to a second file (PM condition (d)),
//!   so truncating or rewriting the whole chain is detectable.
//!
//! ⚠️ Honest limit (WITH-SECRET-DESIGN.md §3): a process running as the same
//! Windows user can read the DPAPI key, forge entries and rewrite both the
//! chain and its head copy. The signatures and the chain catch accidents and
//! hand edits, not that.

use std::path::{Path, PathBuf};

use chrono::{DateTime, Duration, Utc};
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::request::{Approval, KindId, Requester, Scope};

/// Encrypts the store's key at rest (`dpapi::Dpapi` in production).
pub trait Protector {
    fn protect(&self, plain: &[u8]) -> Result<Vec<u8>, String>;
    fn unprotect(&self, blob: &[u8]) -> Result<Vec<u8>, String>;
}

/// Tells a person something needs their attention. ⚠️ DELIVERY is not built:
/// where alerts go waits on board item 131 (SECURITY_ALERT_EMAIL does not
/// exist yet). Until then callers pass `AuditOnly`, and the audit log is the
/// only record.
pub trait Alert {
    fn alert(&self, what: &str);
}

/// No delivery; the event is still in the audit log.
pub struct AuditOnly;
impl Alert for AuditOnly {
    fn alert(&self, _: &str) {}
}

/// After a denial or a timeout, the same role may not ask about the same
/// subject again for this long.
pub const DENIAL_COOLDOWN: Duration = Duration::minutes(10);
/// At most this many prompts per role per rolling hour.
pub const PROMPTS_PER_HOUR: usize = 6;
/// This many denials or timeouts for one role in an hour raise an alert.
pub const DENIALS_BEFORE_ALERT: usize = 3;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoredGrant {
    /// 128 random bits, hex.
    pub id: String,
    pub approval: Approval,
}

/// One prompt and how it ended: "approved", "denied", "timed-out" or
/// "unavailable".
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Attempt {
    pub at: DateTime<Utc>,
    pub requester: Requester,
    pub kind: KindId,
    pub subject: String,
    pub outcome: String,
}

#[derive(Serialize, Deserialize)]
struct Signed<T> {
    item: T,
    mac: String,
}

pub struct Store {
    dir: PathBuf,
    key: Vec<u8>,
    key_ok: bool,
    head_copy: Option<PathBuf>,
    pub grants: Vec<StoredGrant>,
    pub attempts: Vec<Attempt>,
    /// Entries ignored on load because their signature did not verify.
    pub rejected: usize,
}

impl Store {
    /// Opens (creating if needed) the store in `dir`. `head_copy` is the
    /// second location the audit chain's head is written to.
    pub fn open(
        dir: &Path,
        protector: &dyn Protector,
        head_copy: Option<PathBuf>,
    ) -> Result<Self, String> {
        std::fs::create_dir_all(dir).map_err(|e| format!("create {}: {e}", dir.display()))?;
        let key_path = dir.join("store.key");
        let check_path = dir.join("store.key.check");
        let key = match std::fs::read(&key_path) {
            Ok(blob) => protector.unprotect(&blob).unwrap_or_default(),
            Err(_) => {
                let mut k = vec![0u8; 32];
                getrandom::fill(&mut k).map_err(|e| format!("random key: {e}"))?;
                write_atomic(&key_path, &protector.protect(&k)?)?;
                write_atomic(&check_path, mac(&k, &KEY_CHECK).as_bytes())?;
                k
            }
        };
        // A key that does not decrypt, or decrypts to the wrong bytes, must
        // never sign anything: everything signed with the right one then
        // reads as rejected, and the store refuses to save over it.
        let key_ok = !key.is_empty()
            && std::fs::read_to_string(&check_path).is_ok_and(|c| c == mac(&key, &KEY_CHECK));
        let (grants, bad_grants) = load_signed::<StoredGrant>(&dir.join("grants.json"), &key);
        let (attempts, bad_attempts) = load_signed::<Attempt>(&dir.join("attempts.json"), &key);
        Ok(Self {
            dir: dir.to_path_buf(),
            key,
            key_ok,
            head_copy,
            grants,
            attempts,
            rejected: bad_grants + bad_attempts,
        })
    }

    /// Writes grants and attempts; expired grants and attempts older than a
    /// day are dropped.
    pub fn save(&self, now: DateTime<Utc>) -> Result<(), String> {
        if !self.key_ok {
            return Err("the store's key did not decrypt to the key it was created with;                         refusing to save over the real store"
                .into());
        }
        let grants: Vec<_> = self.active(now).into_iter().cloned().collect();
        let attempts: Vec<_> = self
            .attempts
            .iter()
            .filter(|a| a.at > now - Duration::days(1))
            .cloned()
            .collect();
        save_signed(&self.dir.join("grants.json"), &self.key, &grants)?;
        save_signed(&self.dir.join("attempts.json"), &self.key, &attempts)
    }

    /// Stores an approval and returns its id.
    pub fn add(&mut self, approval: Approval) -> String {
        let mut id = [0u8; 16];
        getrandom::fill(&mut id).expect("the OS random source works");
        let id = hex(&id);
        self.grants.push(StoredGrant {
            id: id.clone(),
            approval,
        });
        id
    }

    /// A live grant covering this request: same kind and subject, not
    /// expired, and the same requester when the kind's scope is one
    /// requester.
    pub fn find(
        &self,
        kind: KindId,
        subject: &str,
        requester: &Requester,
        now: DateTime<Utc>,
    ) -> Option<&StoredGrant> {
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

    /// Every grant that has not expired.
    pub fn active(&self, now: DateTime<Utc>) -> Vec<&StoredGrant> {
        self.grants
            .iter()
            .filter(|g| g.approval.expires_at.is_none_or(|e| e > now))
            .collect()
    }

    pub fn revoke(&mut self, id: &str) -> bool {
        let before = self.grants.len();
        self.grants.retain(|g| g.id != id);
        self.grants.len() != before
    }

    /// The panic button: revokes every grant, returns how many.
    pub fn revoke_all(&mut self) -> usize {
        std::mem::take(&mut self.grants).len()
    }

    /// May `requester` put a prompt about `subject` in front of the person
    /// now? Refused during the cooldown after a denial or timeout of the
    /// same role and subject, and past the hourly cap for the role; a cap
    /// refusal also alerts. Keyed by ROLE, so a restart (a new session)
    /// does not reset either limit.
    pub fn may_ask(
        &self,
        requester: &Requester,
        subject: &str,
        now: DateTime<Utc>,
        alert: &dyn Alert,
    ) -> Result<(), String> {
        let role = &requester.role;
        let cooling = self.attempts.iter().any(|a| {
            a.requester.role == *role
                && a.subject == subject
                && is_refusal(&a.outcome)
                && now < a.at + DENIAL_COOLDOWN
        });
        if cooling {
            return Err(format!(
                "'{role}' was refused about '{subject}' less than {} minutes ago",
                DENIAL_COOLDOWN.num_minutes()
            ));
        }
        let this_hour = self
            .attempts
            .iter()
            .filter(|a| a.requester.role == *role && now < a.at + Duration::hours(1))
            .count();
        if this_hour >= PROMPTS_PER_HOUR {
            let why = format!(
                "'{role}' has asked {this_hour} times in the last hour (cap {PROMPTS_PER_HOUR})"
            );
            alert.alert(&why);
            return Err(why);
        }
        Ok(())
    }

    /// Records how a prompt ended; repeated denials or timeouts alert.
    pub fn record(&mut self, attempt: Attempt, alert: &dyn Alert) {
        let (role, at, refused) = (
            attempt.requester.role.clone(),
            attempt.at,
            is_refusal(&attempt.outcome),
        );
        self.attempts.push(attempt);
        if !refused {
            return;
        }
        let refusals = self
            .attempts
            .iter()
            .filter(|a| {
                a.requester.role == role && is_refusal(&a.outcome) && at < a.at + Duration::hours(1)
            })
            .count();
        if refusals == DENIALS_BEFORE_ALERT {
            alert.alert(&format!(
                "'{role}' was refused {refusals} times within an hour"
            ));
        }
    }

    /// Appends one line to the hash-chained audit log and rewrites the
    /// head copy.
    pub fn audit(&self, at: DateTime<Utc>, event: &str, detail: &str) -> Result<(), String> {
        let path = self.dir.join("audit.jsonl");
        let text = std::fs::read_to_string(&path).unwrap_or_default();
        let (count, prev) = text
            .lines()
            .fold((0usize, GENESIS.to_string()), |(n, _), l| {
                (n + 1, sha(l.as_bytes()))
            });
        let line = serde_json::to_string(&AuditLine {
            prev,
            at,
            event: event.to_string(),
            detail: detail.to_string(),
        })
        .map_err(|e| e.to_string())?;
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(|e| format!("open {}: {e}", path.display()))?;
        use std::io::Write;
        writeln!(f, "{line}").map_err(|e| e.to_string())?;
        if let Some(copy) = &self.head_copy {
            write_atomic(
                copy,
                format!("{} {}\n", count + 1, sha(line.as_bytes())).as_bytes(),
            )?;
        }
        Ok(())
    }

    /// Checks every link of the chain and the head copy; returns how many
    /// lines verified, or where it breaks.
    pub fn verify_audit(&self) -> Result<usize, String> {
        let path = self.dir.join("audit.jsonl");
        let text = std::fs::read_to_string(&path).unwrap_or_default();
        let mut prev = GENESIS.to_string();
        let mut count = 0;
        for (i, l) in text.lines().enumerate() {
            let line: AuditLine = serde_json::from_str(l)
                .map_err(|e| format!("audit line {}: unreadable: {e}", i + 1))?;
            if line.prev != prev {
                return Err(format!("audit chain broken at line {}", i + 1));
            }
            prev = sha(l.as_bytes());
            count += 1;
        }
        if let Some(copy) = &self.head_copy {
            let head = std::fs::read_to_string(copy)
                .map_err(|_| format!("audit head copy {} is missing", copy.display()))?;
            if head.trim() != format!("{count} {prev}") {
                return Err(format!(
                    "audit head copy says '{}', the chain ends at '{count} {prev}': truncated or rewritten",
                    head.trim()
                ));
            }
        } else if count == 0 {
            return Err("the audit log is empty".into());
        }
        Ok(count)
    }
}

fn is_refusal(outcome: &str) -> bool {
    matches!(outcome, "denied" | "timed-out")
}

/// What `store.key.check` signs, to tell the right key from a wrong one.
const KEY_CHECK: &str = "user-request store key check";

/// The `prev` of the first audit line.
const GENESIS: &str = "0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Serialize, Deserialize)]
struct AuditLine {
    prev: String,
    at: DateTime<Utc>,
    event: String,
    detail: String,
}

fn load_signed<T: Serialize + for<'de> Deserialize<'de>>(
    path: &Path,
    key: &[u8],
) -> (Vec<T>, usize) {
    let signed: Vec<Signed<T>> = std::fs::read(path)
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default();
    let total = signed.len();
    let good: Vec<T> = signed
        .into_iter()
        .filter(|s| !key.is_empty() && mac(key, &s.item) == s.mac)
        .map(|s| s.item)
        .collect();
    let bad = total - good.len();
    (good, bad)
}

fn save_signed<T: Serialize>(path: &Path, key: &[u8], items: &[T]) -> Result<(), String> {
    let signed: Vec<Signed<&T>> = items
        .iter()
        .map(|i| Signed {
            item: i,
            mac: mac(key, i),
        })
        .collect();
    write_atomic(
        path,
        &serde_json::to_vec_pretty(&signed).map_err(|e| e.to_string())?,
    )
}

/// Temp file and rename, so a reader never sees half a file.
fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, bytes).map_err(|e| format!("write {}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| format!("rename to {}: {e}", path.display()))
}

fn mac<T: Serialize>(key: &[u8], item: &T) -> String {
    let mut m = <Hmac<Sha256> as KeyInit>::new_from_slice(key).expect("HMAC takes any key length");
    m.update(&serde_json::to_vec(item).expect("serialisable"));
    hex(&m.finalize().into_bytes())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn sha(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;
    use std::cell::RefCell;
    use tempfile::tempdir;

    /// Reversible and keyed, so "another key" is testable.
    struct Xor(u8);
    impl Protector for Xor {
        fn protect(&self, p: &[u8]) -> Result<Vec<u8>, String> {
            Ok(p.iter().map(|b| b ^ self.0).collect())
        }
        fn unprotect(&self, b: &[u8]) -> Result<Vec<u8>, String> {
            Ok(b.iter().map(|x| x ^ self.0).collect())
        }
    }

    #[derive(Default)]
    struct Alerts(RefCell<Vec<String>>);
    impl Alert for Alerts {
        fn alert(&self, what: &str) {
            self.0.borrow_mut().push(what.to_string());
        }
    }

    fn t0() -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 10, 7, 16, 0, 0).unwrap()
    }

    fn who(role: &str, session: &str) -> Requester {
        Requester {
            role: role.into(),
            session_id: session.into(),
            claude_pid: 42,
            claude_start_secs: 1,
            managed: true,
        }
    }

    fn approval(
        kind: KindId,
        subject: &str,
        r: &Requester,
        expires: Option<DateTime<Utc>>,
    ) -> Approval {
        Approval {
            kind,
            subject: subject.into(),
            requester: r.clone(),
            approved_at: t0(),
            expires_at: expires,
        }
    }

    fn open(dir: &Path) -> Store {
        Store::open(dir, &Xor(7), Some(dir.join("head.copy"))).unwrap()
    }

    fn attempt(r: &Requester, subject: &str, outcome: &str, at: DateTime<Utc>) -> Attempt {
        Attempt {
            at,
            requester: r.clone(),
            kind: KindId::Secret,
            subject: subject.into(),
            outcome: outcome.into(),
        }
    }

    // -- grants -------------------------------------------------------------

    #[test]
    fn a_grant_is_found_by_kind_subject_and_scope_until_it_expires() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        let a = who("overmind", "s1");
        s.add(approval(
            KindId::Secret,
            "DB",
            &a,
            Some(t0() + Duration::hours(1)),
        ));
        assert!(s.find(KindId::Secret, "DB", &a, t0()).is_some());
        assert!(s.find(KindId::Secret, "OTHER", &a, t0()).is_none());
        assert!(
            s.find(KindId::Secret, "DB", &who("overmind", "s2"), t0())
                .is_none(),
            "a restart voids it"
        );
        assert!(
            s.find(KindId::Secret, "DB", &who("fuel", "s1"), t0())
                .is_none(),
            "another lane"
        );
        assert!(
            s.find(KindId::Secret, "DB", &a, t0() + Duration::hours(1))
                .is_none(),
            "expired"
        );
    }

    #[test]
    fn an_any_requester_grant_covers_every_lane_and_forever_never_expires() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        s.add(approval(
            KindId::LaneDialogBypass,
            "trust",
            &who("pm", "s"),
            None,
        ));
        let later = t0() + Duration::days(3650);
        assert!(s
            .find(KindId::LaneDialogBypass, "trust", &who("fuel", "x"), later)
            .is_some());
    }

    #[test]
    fn grants_survive_a_save_and_expired_ones_are_dropped() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        let a = who("overmind", "s1");
        s.add(approval(
            KindId::Secret,
            "LIVE",
            &a,
            Some(t0() + Duration::hours(1)),
        ));
        s.add(approval(
            KindId::Secret,
            "OLD",
            &a,
            Some(t0() - Duration::seconds(1)),
        ));
        s.save(t0()).unwrap();
        let s = open(d.path());
        assert_eq!(s.rejected, 0);
        let subjects: Vec<_> = s
            .grants
            .iter()
            .map(|g| g.approval.subject.as_str())
            .collect();
        assert_eq!(subjects, vec!["LIVE"]);
    }

    #[test]
    fn ids_are_128_random_bits() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        let a = who("o", "s");
        let x = s.add(approval(KindId::Secret, "A", &a, None));
        let y = s.add(approval(KindId::Secret, "A", &a, None));
        assert_eq!(x.len(), 32);
        assert!(x.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(x, y);
    }

    #[test]
    fn a_tampered_grant_is_ignored_and_counted() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        s.add(approval(
            KindId::Secret,
            "DB",
            &who("o", "s"),
            Some(t0() + Duration::hours(1)),
        ));
        s.save(t0()).unwrap();
        let p = d.path().join("grants.json");
        let text = std::fs::read_to_string(&p)
            .unwrap()
            .replace("\"DB\"", "\"PROD\"");
        std::fs::write(&p, text).unwrap();
        let s = open(d.path());
        assert_eq!((s.grants.len(), s.rejected), (0, 1));
    }

    #[test]
    fn a_key_that_does_not_decrypt_reads_nothing() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        s.add(approval(KindId::Secret, "DB", &who("o", "s"), None));
        s.save(t0()).unwrap();
        let s = Store::open(d.path(), &Xor(9), None).unwrap();
        assert_eq!((s.grants.len(), s.rejected), (0, 1));
    }

    /// A store that could not decrypt its key must not overwrite the
    /// files signed with the real one.
    #[test]
    fn a_store_with_an_undecryptable_key_refuses_to_save() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        s.add(approval(KindId::Secret, "DB", &who("o", "s"), None));
        s.save(t0()).unwrap();
        let wrong = Store::open(d.path(), &Xor(9), None).unwrap();
        assert!(wrong.save(t0()).is_err());
        assert_eq!(
            open(d.path()).grants.len(),
            1,
            "the real grants were overwritten"
        );
    }

    #[test]
    fn revoke_and_revoke_all_persist() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        let a = who("o", "s");
        let x = s.add(approval(KindId::Secret, "A", &a, None));
        s.add(approval(KindId::Secret, "B", &a, None));
        s.add(approval(KindId::Secret, "C", &a, None));
        assert!(s.revoke(&x));
        assert!(!s.revoke(&x), "already gone");
        s.save(t0()).unwrap();
        let mut s = open(d.path());
        assert_eq!(s.grants.len(), 2);
        assert_eq!(s.revoke_all(), 2);
        s.save(t0()).unwrap();
        assert!(open(d.path()).grants.is_empty());
    }

    #[test]
    fn active_lists_only_unexpired_grants() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        let a = who("o", "s");
        s.add(approval(
            KindId::Secret,
            "A",
            &a,
            Some(t0() + Duration::minutes(1)),
        ));
        s.add(approval(KindId::Secret, "B", &a, Some(t0())));
        s.add(approval(KindId::LaneDialogBypass, "C", &a, None));
        let mut subjects: Vec<_> = s
            .active(t0())
            .iter()
            .map(|g| g.approval.subject.clone())
            .collect();
        subjects.sort();
        assert_eq!(subjects, vec!["A", "C"]);
    }

    // -- the prompt gate (audit row 1) ---------------------------------------

    #[test]
    fn after_a_denial_the_same_role_and_subject_cool_down() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        let a = who("o", "s1");
        s.record(attempt(&a, "DB", "denied", t0()), &AuditOnly);
        let restarted = who("o", "s2");
        assert!(
            s.may_ask(&restarted, "DB", t0() + Duration::minutes(9), &AuditOnly)
                .is_err(),
            "a restart reset it"
        );
        assert!(s
            .may_ask(&a, "OTHER", t0() + Duration::minutes(1), &AuditOnly)
            .is_ok());
        assert!(s
            .may_ask(
                &who("fuel", "x"),
                "DB",
                t0() + Duration::minutes(1),
                &AuditOnly
            )
            .is_ok());
        assert!(s
            .may_ask(&a, "DB", t0() + DENIAL_COOLDOWN, &AuditOnly)
            .is_ok());
    }

    #[test]
    fn a_timeout_cools_down_too_but_an_approval_does_not() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        let a = who("o", "s1");
        s.record(attempt(&a, "X", "timed-out", t0()), &AuditOnly);
        s.record(attempt(&a, "Y", "approved", t0()), &AuditOnly);
        assert!(s
            .may_ask(&a, "X", t0() + Duration::minutes(1), &AuditOnly)
            .is_err());
        assert!(s
            .may_ask(&a, "Y", t0() + Duration::minutes(1), &AuditOnly)
            .is_ok());
    }

    #[test]
    fn a_role_is_capped_per_rolling_hour_and_the_cap_alerts() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        let a = who("o", "s1");
        for i in 0..PROMPTS_PER_HOUR {
            s.record(
                attempt(
                    &a,
                    &format!("S{i}"),
                    "approved",
                    t0() + Duration::minutes(i as i64),
                ),
                &AuditOnly,
            );
        }
        let alerts = Alerts::default();
        let at = t0() + Duration::minutes(30);
        assert!(s.may_ask(&a, "NEW", at, &alerts).is_err());
        assert_eq!(alerts.0.borrow().len(), 1, "the cap must alert");
        assert!(
            s.may_ask(&who("fuel", "x"), "NEW", at, &AuditOnly).is_ok(),
            "another role"
        );
        assert!(
            s.may_ask(&a, "NEW", t0() + Duration::minutes(60), &AuditOnly)
                .is_ok(),
            "the first one aged out"
        );
    }

    #[test]
    fn repeated_denials_alert() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        let a = who("o", "s1");
        let alerts = Alerts::default();
        for i in 0..DENIALS_BEFORE_ALERT {
            assert!(alerts.0.borrow().is_empty(), "alerted early at {i}");
            s.record(
                attempt(
                    &a,
                    &format!("S{i}"),
                    "denied",
                    t0() + Duration::minutes(i as i64),
                ),
                &alerts,
            );
        }
        assert_eq!(alerts.0.borrow().len(), 1);
    }

    #[test]
    fn attempts_survive_a_restart_of_the_tool_and_a_forged_one_is_ignored() {
        let d = tempdir().unwrap();
        let mut s = open(d.path());
        let a = who("o", "s1");
        s.record(attempt(&a, "DB", "denied", t0()), &AuditOnly);
        s.save(t0()).unwrap();
        let s2 = open(d.path());
        assert!(s2
            .may_ask(&a, "DB", t0() + Duration::minutes(1), &AuditOnly)
            .is_err());
        let p = d.path().join("attempts.json");
        let text = std::fs::read_to_string(&p)
            .unwrap()
            .replace("denied", "approved");
        std::fs::write(&p, text).unwrap();
        let s3 = open(d.path());
        assert_eq!(s3.rejected, 1);
    }

    // -- the audit chain (audit row 13, PM condition (d)) --------------------

    fn three_events(d: &Path) -> Store {
        let s = open(d);
        for (i, e) in ["granted", "denied", "revoked"].iter().enumerate() {
            s.audit(
                t0() + Duration::minutes(i as i64),
                e,
                &format!("detail {i}"),
            )
            .unwrap();
        }
        s
    }

    #[test]
    fn an_intact_chain_verifies() {
        let d = tempdir().unwrap();
        assert_eq!(three_events(d.path()).verify_audit(), Ok(3));
    }

    #[test]
    fn an_edited_line_breaks_the_chain_at_the_next_line() {
        let d = tempdir().unwrap();
        let s = three_events(d.path());
        let p = d.path().join("audit.jsonl");
        let text = std::fs::read_to_string(&p)
            .unwrap()
            .replace("detail 1", "detail X");
        std::fs::write(&p, text).unwrap();
        let err = s.verify_audit().unwrap_err();
        assert!(err.contains("line 3"), "{err}");
    }

    #[test]
    fn truncating_the_chain_disagrees_with_the_head_copy() {
        let d = tempdir().unwrap();
        let s = three_events(d.path());
        let p = d.path().join("audit.jsonl");
        let text = std::fs::read_to_string(&p).unwrap();
        let kept: Vec<&str> = text.lines().take(2).collect();
        std::fs::write(&p, kept.join("\n") + "\n").unwrap();
        assert!(s.verify_audit().unwrap_err().contains("head copy"));
    }

    #[test]
    fn deleting_the_whole_chain_is_detected() {
        let d = tempdir().unwrap();
        let s = three_events(d.path());
        std::fs::remove_file(d.path().join("audit.jsonl")).unwrap();
        assert!(s.verify_audit().is_err());
    }

    #[test]
    fn a_missing_head_copy_is_reported() {
        let d = tempdir().unwrap();
        let s = three_events(d.path());
        std::fs::remove_file(d.path().join("head.copy")).unwrap();
        assert!(s.verify_audit().unwrap_err().contains("head copy"));
    }
}
