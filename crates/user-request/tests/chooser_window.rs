// SPDX-License-Identifier: MIT OR Apache-2.0
//! The chooser window, through the real `user-request` binary.
//!
//! ⚠️ These open REAL console windows on the desktop, so they are ignored by
//! default and never run in a plain `cargo test`. CI runs them explicitly
//! (`--ignored`); run them locally only with the person told first. Every
//! fixture says TEST and names an obviously fake subject (PM, 2026-10-07,
//! after a test window naming a real secret's name was left open for hours).
#![cfg(windows)]

use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use sysinfo::{Pid, ProcessesToUpdate, System};
use user_request::chooser::{Choice, Chooser};
use user_request::window::WindowChooser;
use user_request::{Grant, KindId, Request, Requester};

fn req(kind: KindId) -> Request {
    Request {
        kind,
        subject: "TEST_FIXTURE_DO_NOT_APPROVE".into(),
        summary: "TEST: a test fixture; nothing will run".into(),
        requester: Requester {
            role: "TEST-FIXTURE".into(),
            session_id: "s".into(),
            claude_pid: 7,
            claude_start_secs: 1,
            managed: true,
        },
        reason: "TEST: do not approve".into(),
    }
}

fn window() -> WindowChooser {
    WindowChooser {
        exe: env!("CARGO_BIN_EXE_user-request").into(),
    }
}

/// The round trip works: the child reads the request from its pipe and
/// answers on its pipe. A proposal over the maximum is refused without
/// asking, so no one has to type.
#[test]
#[ignore = "opens a real console window: run with --ignored, the person told first"]
fn the_window_answers_on_its_pipe() {
    let got = window().choose(
        &req(KindId::Secret),
        &Grant::Forever,
        Duration::from_secs(20),
    );
    assert!(
        matches!(&got, Choice::Refused(why) if why.contains("over the maximum")),
        "{got:?}"
    );
}

/// PM condition (1): the person's typing is read from the window's own
/// console. With nothing typed there, the chooser waits for the person
/// until the wait runs out; it does not read its closed stdin pipe as an
/// answer (which would end at once, as a denial).
#[test]
#[ignore = "opens a real console window: run with --ignored, the person told first"]
fn the_window_waits_for_its_own_console_not_its_stdin() {
    let start = Instant::now();
    let got = window().choose(
        &req(KindId::LaneDialogBypass),
        &Grant::Forever,
        Duration::from_secs(3),
    );
    assert_eq!(got, Choice::TimedOut);
    assert!(start.elapsed() >= Duration::from_secs(3));
}

/// A binary that does not serve the window gives no answer: unavailable,
/// never a choice.
#[test]
#[ignore = "opens a real console window: run with --ignored, the person told first"]
fn a_binary_that_does_not_serve_the_window_is_unavailable() {
    let ch = WindowChooser {
        exe: std::path::PathBuf::from(r"C:\Windows\System32\whoami.exe"),
    };
    let got = ch.choose(
        &req(KindId::Secret),
        &Grant::Forever,
        Duration::from_secs(20),
    );
    assert!(matches!(got, Choice::Unavailable(_)), "{got:?}");
}

/// Not a test on its own: the requester that
/// `a_killed_requester_takes_its_window_with_it` starts and kills.
#[test]
#[ignore = "opens a real console window: run with --ignored, the person told first"]
fn requester_for_the_kill_test() {
    if std::env::var_os("USER_REQUEST_TEST_REQUESTER").is_none() {
        return;
    }
    window().choose(
        &req(KindId::Secret),
        &Grant::for_duration(chrono::Duration::hours(1)),
        Duration::from_secs(120),
    );
}

fn children_named(sys: &mut System, parent: u32, prefix: &str) -> Vec<Pid> {
    sys.refresh_processes(ProcessesToUpdate::All, true);
    sys.processes()
        .values()
        .filter(|p| {
            p.parent() == Some(Pid::from_u32(parent))
                && p.name().to_string_lossy().starts_with(prefix)
        })
        .map(|p| p.pid())
        .collect()
}

/// Issue #118 (PM, 2026-10-07): a window whose requester is killed must
/// close with it, never wait for hours for an answer that goes nowhere. A
/// window that outlives this test is killed by the test, so a failing run
/// does not leave one open either.
#[test]
#[ignore = "opens a real console window: run with --ignored, the person told first"]
fn a_killed_requester_takes_its_window_with_it() {
    let mut requester = Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "requester_for_the_kill_test",
            "--ignored",
            "--test-threads=1",
        ])
        .env("USER_REQUEST_TEST_REQUESTER", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let mut sys = System::new();
    let deadline = Instant::now() + Duration::from_secs(20);
    let window_pid = loop {
        if let Some(p) = children_named(&mut sys, requester.id(), "user-request").pop() {
            break p;
        }
        if Instant::now() >= deadline {
            let _ = requester.kill();
            panic!("the requester never opened its window");
        }
        std::thread::sleep(Duration::from_millis(100));
    };
    requester.kill().unwrap();
    requester.wait().unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    let closed = loop {
        sys.refresh_processes(ProcessesToUpdate::Some(&[window_pid]), true);
        if sys.process(window_pid).is_none() {
            break true;
        }
        if Instant::now() >= deadline {
            break false;
        }
        std::thread::sleep(Duration::from_millis(100));
    };
    if !closed {
        if let Some(p) = sys.process(window_pid) {
            p.kill();
        }
    }
    assert!(closed, "the window outlived its killed requester");
}
