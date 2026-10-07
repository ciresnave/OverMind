// SPDX-License-Identifier: MIT OR Apache-2.0
//! `user-request list | revoke <id> | revoke --all | repair | audit verify`.
//!
//! Revoking and repairing need no Windows Hello: they only remove privilege
//! (PM condition (c)). Every change is audited by the store before it is
//! saved.

use std::path::PathBuf;
use std::process::ExitCode;

use chrono::{Local, Utc};
use user_request::dpapi::Dpapi;
use user_request::store::{Store, Untrusted, GATE_CLOSED_AFTER_REPAIR};

const ENTROPY: &[u8] = b"overmind.user-request.v1";
const PROTECTOR: Dpapi = Dpapi { entropy: ENTROPY };

/// ⚠️ The overrides exist for this crate's tests and only in debug builds
/// (review M4): a leaked variable must never point the panic button at
/// another store.
fn env_override(name: &str) -> Option<PathBuf> {
    if cfg!(debug_assertions) {
        std::env::var_os(name).map(PathBuf::from)
    } else {
        None
    }
}

fn dir() -> Result<PathBuf, String> {
    if let Some(d) = env_override("USER_REQUEST_DIR") {
        return Ok(d);
    }
    let base = std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is not set")?;
    Ok(PathBuf::from(base).join("OverMind").join("user-request"))
}

fn head_copy() -> PathBuf {
    env_override("USER_REQUEST_HEAD")
        .unwrap_or_else(|| PathBuf::from("C:/Projects/.lane-state/user-request-audit.head"))
}

/// The store, or `None` when there is none yet. Says which store it is and
/// whether it can be trusted.
fn open() -> Result<Option<Store>, String> {
    let dir = dir()?;
    eprintln!("user-request: store {}", dir.display());
    let s = Store::open_existing(&dir, &PROTECTOR, Some(head_copy()))?;
    if let Some(Err(why)) = s.as_ref().map(Store::trustworthy) {
        eprintln!("user-request: ⚠️ {why}");
    }
    Ok(s)
}

fn list() -> Result<u8, String> {
    let now = Utc::now();
    let Some(s) = open()? else {
        println!("no store yet: nothing has been granted");
        return Ok(0);
    };
    // review M-b: under an untrusted key nothing was read, which is not
    // the same as nothing granted
    if let Some(Untrusted::Key(_)) = s.untrusted {
        println!("*** GRANTS UNKNOWN: the store's key cannot be trusted ***");
        println!("`user-request repair` revokes every grant and restores the store");
        return Ok(1);
    }
    let untrusted = s.trustworthy().is_err();
    let active = s.active(now);
    if untrusted {
        println!("*** THE STORE CANNOT BE TRUSTED: none of these is honoured until it is ***");
    }
    if active.is_empty() {
        println!("no active grants");
        return Ok(u8::from(untrusted));
    }
    // FOREVER grants first, loud (PM condition (a))
    let (forever, timed): (Vec<_>, Vec<_>) = active
        .into_iter()
        .partition(|g| g.approval.expires_at.is_none());
    if !forever.is_empty() {
        println!("*** FOREVER GRANTS (until revoked) ***");
        for g in &forever {
            let a = &g.approval;
            println!(
                "  {}  {:?}  {}  by '{}'",
                g.id, a.kind, a.subject, a.requester.role
            );
        }
    }
    for g in &timed {
        let a = &g.approval;
        let until = a
            .expires_at
            .map(|e| {
                e.with_timezone(&Local)
                    .format("%Y-%m-%d %H:%M %Z")
                    .to_string()
            })
            .unwrap_or_default();
        println!(
            "  {}  {:?}  {}  by '{}'  until {until}",
            g.id, a.kind, a.subject, a.requester.role
        );
    }
    Ok(u8::from(untrusted))
}

fn revoke(target: &str) -> Result<u8, String> {
    let now = Utc::now();
    let Some(mut s) = open()? else {
        return Err("no store yet: nothing to revoke".into());
    };
    if target == "--all" {
        let n = s.revoke_all(now)?;
        println!("revoked {n} grant(s)");
        return Ok(0);
    }
    if !s.revoke(target, now)? {
        return Err(format!("no active grant with id {target}"));
    }
    println!("revoked {target}");
    Ok(0)
}

fn repair() -> Result<u8, String> {
    let now = Utc::now();
    let Some(mut s) = open()? else {
        println!("no store yet: nothing to repair");
        return Ok(0);
    };
    let n = s.repair(now, &PROTECTOR)?;
    let resume = (now + GATE_CLOSED_AFTER_REPAIR)
        .with_timezone(&Local)
        .format("%H:%M %Z");
    println!("repaired: revoked {n} grant(s); prompts are refused until {resume}");
    Ok(0)
}

fn verify() -> Result<u8, String> {
    let Some(s) = open()? else {
        println!("no store yet: no audit log to verify");
        return Ok(0);
    };
    let report = s.verify_audit()?;
    s.trustworthy()?;
    println!(
        "audit chain intact: {} line(s), head copy agrees",
        report.lines
    );
    if report.repaired_resets > 0 {
        println!(
            "({} earlier chain reset(s), acknowledged by a repair)",
            report.repaired_resets
        );
    }
    Ok(0)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    let result = match args.as_slice() {
        ["list"] => list(),
        ["revoke", target] => revoke(target),
        ["repair"] => repair(),
        ["audit", "verify"] => verify(),
        _ => Err(
            "usage: user-request list | revoke <id> | revoke --all | repair | audit verify".into(),
        ),
    };
    match result {
        Ok(code) => ExitCode::from(code),
        Err(e) => {
            eprintln!("user-request: {e}");
            ExitCode::from(1)
        }
    }
}
