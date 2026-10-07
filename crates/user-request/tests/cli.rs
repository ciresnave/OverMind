// SPDX-License-Identifier: MIT OR Apache-2.0
//! The `user-request` binary against a throwaway store. Windows-only: the
//! binary's store is DPAPI-protected.
#![cfg(windows)]

use std::path::Path;
use std::process::Command;

use chrono::{Duration, Utc};
use user_request::dpapi::Dpapi;
use user_request::request::{Approval, KindId, Requester};
use user_request::store::Store;

const ENTROPY: &[u8] = b"overmind.user-request.v1";

fn run(dir: &Path, args: &[&str]) -> (i32, String, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_user-request"))
        .args(args)
        .env("USER_REQUEST_DIR", dir)
        .env("USER_REQUEST_HEAD", dir.join("head.copy"))
        .output()
        .unwrap();
    (
        out.status.code().unwrap(),
        String::from_utf8_lossy(&out.stdout).into_owned(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
    )
}

fn seed(dir: &Path) -> (String, String) {
    let mut s = Store::open(
        dir,
        &Dpapi { entropy: ENTROPY },
        Some(dir.join("head.copy")),
    )
    .unwrap();
    let who = Requester {
        role: "overmind".into(),
        session_id: "s".into(),
        claude_pid: 1,
        claude_start_secs: 1,
        managed: true,
    };
    let now = Utc::now();
    let mk = |kind, subject: &str, expires_at| Approval {
        kind,
        subject: subject.into(),
        requester: who.clone(),
        approved_at: now,
        expires_at,
    };
    let forever = s.add(mk(KindId::LaneDialogBypass, "trust-dialog", None));
    let timed = s.add(mk(KindId::Secret, "DB", Some(now + Duration::hours(1))));
    s.save(now).unwrap();
    (forever, timed)
}

#[test]
fn list_shows_forever_grants_first_and_loudly() {
    let d = tempfile::tempdir().unwrap();
    let (forever, timed) = seed(d.path());
    let (code, out, _) = run(d.path(), &["list"]);
    assert_eq!(code, 0);
    let (f, t) = (out.find(&forever).unwrap(), out.find(&timed).unwrap());
    assert!(out.starts_with("*** FOREVER GRANTS"), "{out}");
    assert!(f < t, "{out}");
}

#[test]
fn revoke_removes_one_and_the_audit_chain_records_it() {
    let d = tempfile::tempdir().unwrap();
    let (forever, timed) = seed(d.path());
    assert_eq!(run(d.path(), &["revoke", &forever]).0, 0);
    let (_, out, _) = run(d.path(), &["list"]);
    assert!(!out.contains(&forever) && out.contains(&timed), "{out}");
    let (code, out, err) = run(d.path(), &["audit", "verify"]);
    assert_eq!(code, 0, "{err}");
    assert!(out.contains("1 line"), "{out}");
}

#[test]
fn revoke_all_is_the_panic_button() {
    let d = tempfile::tempdir().unwrap();
    seed(d.path());
    let (code, out, _) = run(d.path(), &["revoke", "--all"]);
    assert_eq!((code, out.trim()), (0, "revoked 2 grant(s)"));
    assert!(run(d.path(), &["list"]).1.contains("no active grants"));
}

#[test]
fn an_unknown_id_and_bad_usage_fail() {
    let d = tempfile::tempdir().unwrap();
    seed(d.path());
    assert_eq!(run(d.path(), &["revoke", "nope"]).0, 1);
    assert_eq!(run(d.path(), &["frobnicate"]).0, 1);
}

#[test]
fn a_truncated_audit_log_fails_verify() {
    let d = tempfile::tempdir().unwrap();
    let (forever, timed) = seed(d.path());
    run(d.path(), &["revoke", &forever]);
    run(d.path(), &["revoke", &timed]);
    let p = d.path().join("audit.jsonl");
    let first = std::fs::read_to_string(&p)
        .unwrap()
        .lines()
        .next()
        .unwrap()
        .to_string();
    std::fs::write(&p, first + "\n").unwrap();
    let (code, _, err) = run(d.path(), &["audit", "verify"]);
    assert_eq!(code, 1);
    assert!(err.contains("head copy"), "{err}");
}
