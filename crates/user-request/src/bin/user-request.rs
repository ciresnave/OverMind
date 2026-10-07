// SPDX-License-Identifier: MIT OR Apache-2.0
//! `user-request list | revoke <id> | revoke --all | audit verify`.
//!
//! Revoking needs no Windows Hello: it only removes privilege (PM condition
//! (c)). Every revocation is written to the audit log before it is saved.

use std::path::PathBuf;
use std::process::ExitCode;

use chrono::{Local, Utc};
use user_request::dpapi::Dpapi;
use user_request::store::Store;

const ENTROPY: &[u8] = b"overmind.user-request.v1";

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
    let s = Store::open_existing(&dir, &Dpapi { entropy: ENTROPY }, Some(head_copy()))?;
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
    let untrusted = s.trustworthy().is_err();
    let active = s.active(now);
    if active.is_empty() {
        println!("no active grants");
        return Ok(u8::from(untrusted));
    }
    if untrusted {
        println!("*** THE STORE CANNOT BE TRUSTED: none of these is honoured until it is ***");
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
        let n = s.revoke_all();
        s.audit(now, "revoked-all", &format!("{n} grant(s)"))?;
        s.save(now)?;
        println!("revoked {n} grant(s)");
        return Ok(0);
    }
    if !s.revoke(target) {
        return Err(format!("no active grant with id {target}"));
    }
    s.audit(now, "revoked", target)?;
    s.save(now)?;
    println!("revoked {target}");
    Ok(0)
}

fn verify() -> Result<u8, String> {
    let Some(s) = open()? else {
        println!("no store yet: no audit log to verify");
        return Ok(0);
    };
    let n = s.verify_audit()?;
    s.trustworthy()?;
    println!("audit chain intact: {n} line(s), head copy agrees");
    Ok(0)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    let result = match args.as_slice() {
        ["list"] => list(),
        ["revoke", target] => revoke(target),
        ["audit", "verify"] => verify(),
        _ => Err("usage: user-request list | revoke <id> | revoke --all | audit verify".into()),
    };
    match result {
        Ok(code) => ExitCode::from(code),
        Err(e) => {
            eprintln!("user-request: {e}");
            ExitCode::from(1)
        }
    }
}
