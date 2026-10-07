// SPDX-License-Identifier: MIT OR Apache-2.0
//! The store, the gate and the audit chain. Each `review` note names the
//! finding of the review of ea50f85 the test pins.

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

/// A protector whose decryption fails outright (a DPAPI error).
struct Broken;
impl Protector for Broken {
    fn protect(&self, p: &[u8]) -> Result<Vec<u8>, String> {
        Ok(p.to_vec())
    }
    fn unprotect(&self, _: &[u8]) -> Result<Vec<u8>, String> {
        Err("DPAPI said no".into())
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

fn mins(m: i64) -> DateTime<Utc> {
    t0() + Duration::minutes(m)
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

/// One full prompt through the gate: reserve, save, resolve, save.
fn prompt(
    dir: &Path,
    r: &Requester,
    subject: &str,
    outcome: &str,
    at: DateTime<Utc>,
) -> Result<(), String> {
    let mut s = open(dir);
    let res = s.may_ask(r, KindId::Secret, subject, at, &AuditOnly)?;
    s.save(at).unwrap();
    drop(s);
    let mut s = open(dir);
    s.resolve(&res, outcome, at, &AuditOnly).unwrap();
    s.save(at).unwrap();
    Ok(())
}

fn audit_text(dir: &Path) -> String {
    std::fs::read_to_string(dir.join("audit.jsonl")).unwrap_or_default()
}

// -- grants -----------------------------------------------------------------

#[test]
fn a_grant_is_found_by_kind_subject_and_scope_until_it_expires() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    let a = who("overmind", "s1");
    s.add(approval(KindId::Secret, "DB", &a, Some(mins(60))));
    assert!(s.find(KindId::Secret, "DB", &a, t0()).is_some());
    assert!(s.find(KindId::Secret, "OTHER", &a, t0()).is_none());
    assert!(
        s.find(KindId::Secret, "DB", &who("overmind", "s2"), t0())
            .is_none(),
        "a restart voids it"
    );
    assert!(s
        .find(KindId::Secret, "DB", &who("fuel", "s1"), t0())
        .is_none());
    assert!(
        s.find(KindId::Secret, "DB", &a, mins(60)).is_none(),
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
    let a = who("o", "s");
    s.add(approval(KindId::Secret, "LIVE", &a, Some(mins(60))));
    s.add(approval(
        KindId::Secret,
        "OLD",
        &a,
        Some(t0() - Duration::seconds(1)),
    ));
    s.save(t0()).unwrap();
    drop(s);
    let s = open(d.path());
    assert_eq!(s.untrusted, None);
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
fn revoke_and_revoke_all_persist_as_tombstones() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    let a = who("o", "s");
    let x = s.add(approval(KindId::Secret, "A", &a, None));
    s.add(approval(KindId::Secret, "B", &a, None));
    s.add(approval(KindId::Secret, "C", &a, None));
    assert!(s.revoke(&x));
    assert!(!s.revoke(&x), "already revoked");
    s.save(t0()).unwrap();
    drop(s);
    let mut s = open(d.path());
    assert_eq!(s.active(t0()).len(), 2);
    assert!(s.revoked.contains(&x));
    assert_eq!(s.revoke_all(), 2);
    s.save(t0()).unwrap();
    drop(s);
    assert!(open(d.path()).active(t0()).is_empty());
}

// -- integrity: the audit chain anchors the files ----------------------------

/// Review C2: a revocation cannot come back, even from a process that had
/// the store open before it (the lock serialises them), nor from an old
/// copy of the file put back (review I4).
#[test]
fn a_revoked_grant_never_comes_back() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    let g = s.add(approval(
        KindId::LaneDialogBypass,
        "trust",
        &who("pm", "s"),
        None,
    ));
    s.save(t0()).unwrap();
    drop(s);
    let before = std::fs::read(d.path().join("grants.json")).unwrap();
    let mut s = open(d.path());
    s.revoke_all();
    s.save(t0()).unwrap();
    drop(s);
    // a lane that records an attempt afterwards does not resurrect it
    prompt(d.path(), &who("fuel", "x"), "S", "approved", t0()).unwrap();
    assert!(open(d.path())
        .find(KindId::LaneDialogBypass, "trust", &who("o", "s"), t0())
        .is_none());
    // nor does putting the old, validly signed file back
    std::fs::write(d.path().join("grants.json"), before).unwrap();
    let s = open(d.path());
    assert!(
        matches!(s.untrusted, Some(Untrusted::Files(_))),
        "{:?}",
        s.untrusted
    );
    assert!(
        s.find(KindId::LaneDialogBypass, "trust", &who("o", "s"), t0())
            .is_none(),
        "{g}"
    );
}

#[test]
fn a_tampered_file_is_quarantined_and_the_store_fails_closed() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    s.add(approval(
        KindId::Secret,
        "DB",
        &who("o", "s"),
        Some(mins(60)),
    ));
    s.save(t0()).unwrap();
    drop(s);
    let p = d.path().join("grants.json");
    let text = std::fs::read_to_string(&p)
        .unwrap()
        .replace("\"DB\"", "\"PROD\"");
    std::fs::write(&p, text).unwrap();
    let mut s = open(d.path());
    assert!(matches!(s.untrusted, Some(Untrusted::Files(_))));
    assert!(s
        .find(KindId::Secret, "PROD", &who("o", "s"), t0())
        .is_none());
    assert!(
        s.may_ask(&who("o", "s"), KindId::Secret, "X", t0(), &AuditOnly)
            .is_err(),
        "gate fails open"
    );
    let kept = std::fs::read_dir(d.path())
        .unwrap()
        .filter_map(|e| e.ok())
        .any(|e| e.file_name().to_string_lossy().contains("rejected"));
    assert!(kept, "the bad file was dropped instead of kept");
}

/// Review I3: deleting the attempts file (or rolling it back) must not reset
/// the gate.
#[test]
fn a_deleted_attempts_file_makes_the_gate_fail_closed() {
    let d = tempdir().unwrap();
    let a = who("o", "s1");
    prompt(d.path(), &a, "DB", "denied", t0()).unwrap();
    std::fs::remove_file(d.path().join("attempts.json")).unwrap();
    let mut s = open(d.path());
    assert!(s
        .may_ask(&a, KindId::Secret, "OTHER", mins(1), &AuditOnly)
        .is_err());
}

#[test]
fn a_store_whose_files_were_untrustworthy_can_still_revoke_and_re_anchors() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    s.add(approval(KindId::Secret, "DB", &who("o", "s"), None));
    s.save(t0()).unwrap();
    drop(s);
    std::fs::remove_file(d.path().join("attempts.json")).unwrap();
    let mut s = open(d.path());
    assert!(s.untrusted.is_some());
    assert_eq!(s.revoke_all(), 1);
    s.save(t0()).unwrap();
    assert_eq!(s.untrusted, None, "the saving handle stays untrusted");
    drop(s);
    let s = open(d.path());
    assert_eq!(s.untrusted, None, "a save re-anchors the files");
    assert!(audit_text(d.path()).contains("re-anchored"));
}

// -- the key -----------------------------------------------------------------

#[test]
fn a_key_that_decrypts_to_the_wrong_bytes_trusts_nothing_and_saves_nothing() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    s.add(approval(KindId::Secret, "DB", &who("o", "s"), None));
    s.save(t0()).unwrap();
    drop(s);
    let mut wrong = Store::open(d.path(), &Xor(9), None).unwrap();
    assert!(matches!(wrong.untrusted, Some(Untrusted::Key(_))));
    assert!(wrong
        .find(KindId::Secret, "DB", &who("o", "s"), t0())
        .is_none());
    assert!(wrong.save(t0()).is_err());
    drop(wrong);
    assert_eq!(
        open(d.path()).grants.len(),
        1,
        "the real grants were overwritten"
    );
}

/// Review I3: a DPAPI failure keeps its reason and fails closed.
#[test]
fn a_key_that_does_not_decrypt_says_why_and_fails_closed() {
    let d = tempdir().unwrap();
    drop(open(d.path()));
    let mut s = Store::open(d.path(), &Broken, None).unwrap();
    match &s.untrusted {
        Some(Untrusted::Key(why)) => assert!(why.contains("DPAPI said no"), "{why}"),
        other => panic!("{other:?}"),
    }
    assert!(s
        .may_ask(&who("o", "s"), KindId::Secret, "X", t0(), &AuditOnly)
        .is_err());
}

/// Review I2: a missing check file is rebuilt when the files prove the key.
#[test]
fn a_missing_key_check_is_rebuilt_when_the_files_verify() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    let g = s.add(approval(KindId::Secret, "DB", &who("o", "s"), None));
    s.save(t0()).unwrap();
    drop(s);
    std::fs::remove_file(d.path().join("store.key.check")).unwrap();
    let mut s = open(d.path());
    assert_eq!(s.untrusted, None);
    assert!(s.revoke(&g));
    s.save(t0()).unwrap();
    assert!(d.path().join("store.key.check").exists());
}

/// Review I2, the other half: a missing check file is NOT rebuilt under a
/// key the files do not verify under - that key would quarantine the real
/// files and anchor itself.
#[test]
fn a_missing_key_check_is_not_rebuilt_under_the_wrong_key() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    s.add(approval(KindId::Secret, "DB", &who("o", "s"), None));
    s.save(t0()).unwrap();
    drop(s);
    std::fs::remove_file(d.path().join("store.key.check")).unwrap();
    let wrong = Store::open(d.path(), &Xor(9), None).unwrap();
    assert!(
        matches!(wrong.untrusted, Some(Untrusted::Key(_))),
        "{:?}",
        wrong.untrusted
    );
    drop(wrong);
    assert!(!d.path().join("store.key.check").exists());
    let s = open(d.path());
    assert_eq!(s.untrusted, None, "the real files were touched");
    assert_eq!(s.grants.len(), 1);
}

/// Files that verify but that no save in the audit log recorded (the log
/// was deleted, or the files were copied in) are not trusted.
#[test]
fn files_no_save_recorded_are_not_trusted() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    s.add(approval(KindId::Secret, "DB", &who("o", "s"), None));
    s.save(t0()).unwrap();
    drop(s);
    std::fs::remove_file(d.path().join("audit.jsonl")).unwrap();
    std::fs::remove_file(d.path().join("head.copy")).unwrap();
    let s = open(d.path());
    match &s.untrusted {
        Some(Untrusted::Files(why)) => assert!(why.contains("no save recorded"), "{why}"),
        other => panic!("{other:?}"),
    }
    assert!(s.find(KindId::Secret, "DB", &who("o", "s"), t0()).is_none());
}

/// The MAC covers the sequence number: an edited header fails its
/// signature (and is quarantined), not just the hash check.
#[test]
fn an_edited_sequence_number_fails_the_signature() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    s.save(t0()).unwrap();
    s.save(t0()).unwrap();
    drop(s);
    let p = d.path().join("grants.json");
    let text = std::fs::read_to_string(&p).unwrap();
    assert_eq!(text.matches("{\"seq\":2,").count(), 1, "{text}");
    std::fs::write(&p, text.replace("{\"seq\":2,", "{\"seq\":9,")).unwrap();
    let s = open(d.path());
    match &s.untrusted {
        Some(Untrusted::Files(why)) => assert!(why.contains("failed its signature"), "{why}"),
        other => panic!("{other:?}"),
    }
}

/// Review M2: only a key that does not exist is created; a read-only open
/// never creates a store.
#[test]
fn a_read_only_open_never_creates_a_store() {
    let d = tempdir().unwrap();
    let dir = d.path().join("none");
    assert!(Store::open_existing(&dir, &Xor(7), None).unwrap().is_none());
    assert!(!dir.exists());
}

// -- the gate (audit row 1) ---------------------------------------------------

#[test]
fn after_a_denial_the_same_role_and_subject_cool_down() {
    let d = tempdir().unwrap();
    let a = who("o", "s1");
    prompt(d.path(), &a, "DB", "denied", t0()).unwrap();
    let mut s = open(d.path());
    let restarted = who("o", "s2");
    assert!(
        s.may_ask(&restarted, KindId::Secret, " db ", mins(9), &AuditOnly)
            .is_err(),
        "restart or case reset it"
    );
    assert!(s
        .may_ask(&a, KindId::Secret, "OTHER", mins(1), &AuditOnly)
        .is_ok());
    assert!(s
        .may_ask(&who("fuel", "x"), KindId::Secret, "DB", mins(1), &AuditOnly)
        .is_ok());
    assert!(s
        .may_ask(&a, KindId::Secret, "DB", t0() + DENIAL_COOLDOWN, &AuditOnly)
        .is_ok());
}

#[test]
fn a_timeout_cools_down_too_but_an_approval_does_not() {
    let d = tempdir().unwrap();
    let a = who("o", "s1");
    prompt(d.path(), &a, "X", "timed-out", t0()).unwrap();
    prompt(d.path(), &a, "Y", "approved", t0()).unwrap();
    let mut s = open(d.path());
    assert!(s
        .may_ask(&a, KindId::Secret, "X", mins(1), &AuditOnly)
        .is_err());
    assert!(s
        .may_ask(&a, KindId::Secret, "Y", mins(1), &AuditOnly)
        .is_ok());
}

/// Review C3: a pending prompt counts, so parallel asks cannot overrun.
#[test]
fn pending_prompts_count_toward_the_cap() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    let a = who("o", "s1");
    for i in 0..PROMPTS_PER_HOUR {
        s.may_ask(&a, KindId::Secret, &format!("S{i}"), t0(), &AuditOnly)
            .unwrap();
    }
    assert!(s
        .may_ask(&a, KindId::Secret, "NEW", t0(), &AuditOnly)
        .is_err());
}

#[test]
fn a_role_is_capped_per_rolling_hour_and_alerted_once() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    let a = who("o", "s1");
    for i in 0..PROMPTS_PER_HOUR {
        s.may_ask(
            &a,
            KindId::Secret,
            &format!("S{i}"),
            mins(i as i64),
            &AuditOnly,
        )
        .unwrap();
    }
    let alerts = Alerts::default();
    for m in 30..35 {
        assert!(s
            .may_ask(&a, KindId::Secret, "NEW", mins(m), &alerts)
            .is_err());
    }
    assert_eq!(
        alerts.0.borrow().len(),
        1,
        "review M1: one alert, not one per refusal"
    );
    assert!(s
        .may_ask(
            &who("fuel", "x"),
            KindId::Secret,
            "NEW",
            mins(30),
            &AuditOnly
        )
        .is_ok());
    assert!(
        s.may_ask(&a, KindId::Secret, "NEW", mins(60), &AuditOnly)
            .is_ok(),
        "the first one aged out"
    );
}

/// Review I9: the cap protects a person, not just each role.
#[test]
fn the_person_is_capped_across_every_role() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    for i in 0..PROMPTS_PER_HOUR_FOR_THE_PERSON {
        s.may_ask(
            &who(&format!("lane{i}"), "s"),
            KindId::Secret,
            "S",
            t0(),
            &AuditOnly,
        )
        .unwrap();
    }
    assert!(s
        .may_ask(&who("fresh", "s"), KindId::Secret, "S", t0(), &AuditOnly)
        .is_err());
}

#[test]
fn repeated_refusals_alert_once_within_the_hour() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    let a = who("o", "s1");
    let alerts = Alerts::default();
    for i in 0..DENIALS_BEFORE_ALERT + 1 {
        let r = s
            .may_ask(
                &a,
                KindId::Secret,
                &format!("S{i}"),
                mins(i as i64),
                &AuditOnly,
            )
            .unwrap();
        s.resolve(&r, "denied", mins(i as i64), &alerts).unwrap();
        let want = usize::from(i + 1 >= DENIALS_BEFORE_ALERT);
        assert_eq!(alerts.0.borrow().len(), want, "after {} refusals", i + 1);
    }
}

/// Once the first alert's hour has passed, refusals still piling up alert
/// again, even past the threshold (4 in the window, not exactly 3).
#[test]
fn a_role_still_being_refused_is_alerted_again_an_hour_later() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    let a = who("o", "s1");
    let alerts = Alerts::default();
    for (i, m) in [0, 1, 2, 30, 40, 50, 63].into_iter().enumerate() {
        let r = s
            .may_ask(&a, KindId::Secret, &format!("S{i}"), mins(m), &AuditOnly)
            .unwrap();
        s.resolve(&r, "denied", mins(m), &alerts).unwrap();
    }
    // at minute 63 the window holds 30, 40, 50 and 63: four refusals
    assert_eq!(alerts.0.borrow().len(), 2, "{:?}", alerts.0.borrow());
}

#[test]
fn refusals_older_than_an_hour_do_not_count_toward_the_alert() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    let a = who("o", "s1");
    let alerts = Alerts::default();
    for (i, m) in [0, 1, 70].into_iter().enumerate() {
        let r = s
            .may_ask(&a, KindId::Secret, &format!("S{i}"), mins(m), &AuditOnly)
            .unwrap();
        s.resolve(&r, "denied", mins(m), &alerts).unwrap();
    }
    assert!(alerts.0.borrow().is_empty());
}

/// Review I7: every gate decision and alert is in the audit log.
#[test]
fn gate_decisions_and_alerts_are_audited() {
    let d = tempdir().unwrap();
    let a = who("o", "s1");
    prompt(d.path(), &a, "DB", "denied", t0()).unwrap();
    let mut s = open(d.path());
    let _ = s.may_ask(&a, KindId::Secret, "DB", mins(1), &AuditOnly);
    for i in 0..3 {
        let r = s
            .may_ask(&a, KindId::Secret, &format!("Z{i}"), mins(2), &AuditOnly)
            .unwrap();
        s.resolve(&r, "denied", mins(2), &AuditOnly).unwrap();
    }
    let log = audit_text(d.path());
    for want in [
        "\"gate-allowed\"",
        "\"gate-refused\"",
        "\"denied\"",
        "\"ALERT\"",
    ] {
        assert!(log.contains(want), "{want} missing from:\n{log}");
    }
}

// -- concurrency (review C2, C3, I1, I5) ---------------------------------------

#[test]
fn parallel_writers_lose_nothing_and_the_chain_holds() {
    let d = tempdir().unwrap();
    drop(open(d.path()));
    let dir = d.path().to_path_buf();
    let threads: Vec<_> = (0..8)
        .map(|i| {
            let dir = dir.clone();
            std::thread::spawn(move || {
                prompt(&dir, &who(&format!("lane{i}"), "s"), "S", "approved", t0())
            })
        })
        .collect();
    for t in threads {
        t.join().unwrap().unwrap();
    }
    let s = open(&dir);
    assert_eq!(s.untrusted, None);
    assert_eq!(
        s.attempts
            .iter()
            .filter(|a| a.outcome == "approved")
            .count(),
        8
    );
    assert!(s.verify_audit().is_ok(), "{:?}", s.verify_audit());
}

#[test]
fn parallel_first_opens_agree_on_one_key() {
    let d = tempdir().unwrap();
    let dir = d.path().join("new");
    let threads: Vec<_> = (0..6)
        .map(|_| {
            let dir = dir.clone();
            std::thread::spawn(move || {
                let mut s = Store::open(&dir, &Xor(7), Some(dir.join("head.copy"))).unwrap();
                assert_eq!(s.untrusted, None);
                s.add(approval(KindId::Secret, "S", &who("o", "s"), None));
                s.save(t0()).unwrap();
            })
        })
        .collect();
    for t in threads {
        t.join().unwrap();
    }
    let s = open(&dir);
    assert_eq!(s.untrusted, None);
    assert_eq!(s.grants.len(), 6);
}

// -- the audit chain (row 13, PM condition (d)) -------------------------------

fn three_events(d: &Path) -> Store {
    let s = open(d);
    for (i, e) in ["granted", "denied", "revoked"].iter().enumerate() {
        s.audit(mins(i as i64), e, &format!("detail {i}")).unwrap();
    }
    s
}

#[test]
fn an_intact_chain_verifies_and_a_fresh_store_has_nothing_to_report() {
    let d = tempdir().unwrap();
    assert_eq!(open(d.path()).verify_audit(), Ok(0));
    let e = tempdir().unwrap();
    assert_eq!(three_events(e.path()).verify_audit(), Ok(3));
}

#[test]
fn an_edited_line_breaks_the_chain() {
    let d = tempdir().unwrap();
    let s = three_events(d.path());
    let p = d.path().join("audit.jsonl");
    let text = std::fs::read_to_string(&p)
        .unwrap()
        .replace("detail 1", "detail X");
    std::fs::write(&p, text).unwrap();
    assert!(s.verify_audit().unwrap_err().contains("line 3"));
}

/// Review C1: truncation, deletion and a torn line stay visible after the
/// next append.
#[test]
fn a_truncated_deleted_or_torn_log_stays_visible_after_the_next_append() {
    for damage in ["truncate", "delete", "tear", "newline"] {
        let d = tempdir().unwrap();
        let s = three_events(d.path());
        let p = d.path().join("audit.jsonl");
        let text = std::fs::read_to_string(&p).unwrap();
        match damage {
            "truncate" => {
                std::fs::write(&p, text.lines().next().unwrap().to_string() + "\n").unwrap()
            }
            "delete" => std::fs::remove_file(&p).unwrap(),
            // the last line whole but its newline lost: the next line must
            // not be glued onto it
            "newline" => std::fs::write(&p, &text[..text.len() - 1]).unwrap(),
            _ => std::fs::write(&p, &text[..text.len() - 10]).unwrap(),
        }
        s.audit(mins(9), "revoked", "after the damage").unwrap();
        let err = s.verify_audit().unwrap_err();
        assert!(err.contains("reset"), "{damage}: {err}");
        // and the chain keeps working
        s.audit(mins(10), "granted", "later").unwrap();
        assert!(s.verify_audit().unwrap_err().contains("reset"), "{damage}");
    }
}

#[test]
fn a_missing_head_copy_is_reported() {
    let d = tempdir().unwrap();
    let s = three_events(d.path());
    std::fs::remove_file(d.path().join("head.copy")).unwrap();
    assert!(s.verify_audit().unwrap_err().contains("head copy"));
}

#[test]
fn every_save_is_anchored_in_the_chain() {
    let d = tempdir().unwrap();
    let mut s = open(d.path());
    s.save(t0()).unwrap();
    s.save(t0()).unwrap();
    assert_eq!(audit_text(d.path()).matches("\"saved\"").count(), 2);
    assert!(s.verify_audit().is_ok());
}
