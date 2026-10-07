// SPDX-License-Identifier: MIT OR Apache-2.0
//! `user-request list | revoke <id> | revoke --all | audit verify`.
//!
//! Revoking needs no Windows Hello: it only removes privilege (PM condition
//! (c)). Every revocation is written to the audit log.

use std::path::PathBuf;
use std::process::ExitCode;

use chrono::{Local, Utc};
use user_request::dpapi::Dpapi;
use user_request::store::Store;

const ENTROPY: &[u8] = b"overmind.user-request.v1";

/// ⚠️ TEST-ONLY overrides: pointing them elsewhere reaches an empty store,
/// not anyone else's grants.
fn dir() -> Result<PathBuf, String> {
    if let Some(d) = std::env::var_os("USER_REQUEST_DIR") {
        return Ok(d.into());
    }
    let base = std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is not set")?;
    Ok(PathBuf::from(base).join("OverMind").join("user-request"))
}

fn head_copy() -> PathBuf {
    std::env::var_os("USER_REQUEST_HEAD")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("C:/Projects/.lane-state/user-request-audit.head"))
}

fn open() -> Result<Store, String> {
    let s = Store::open(&dir()?, &Dpapi { entropy: ENTROPY }, Some(head_copy()))?;
    if s.rejected > 0 {
        eprintln!(
            "user-request: ignored {} entr(y/ies) whose signature did not verify",
            s.rejected
        );
    }
    Ok(s)
}

fn list() -> Result<u8, String> {
    let now = Utc::now();
    let s = open()?;
    let active = s.active(now);
    if active.is_empty() {
        println!("no active grants");
        return Ok(0);
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
    Ok(0)
}

fn revoke(target: &str) -> Result<u8, String> {
    let now = Utc::now();
    let mut s = open()?;
    if target == "--all" {
        let n = s.revoke_all();
        s.save(now)?;
        s.audit(now, "revoked-all", &format!("{n} grant(s)"))?;
        println!("revoked {n} grant(s)");
        return Ok(0);
    }
    if !s.revoke(target) {
        return Err(format!("no grant with id {target}"));
    }
    s.save(now)?;
    s.audit(now, "revoked", target)?;
    println!("revoked {target}");
    Ok(0)
}

fn verify() -> Result<u8, String> {
    let n = open()?.verify_audit()?;
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
