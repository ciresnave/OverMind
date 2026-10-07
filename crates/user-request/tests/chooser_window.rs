// SPDX-License-Identifier: MIT OR Apache-2.0
//! The chooser window, through the real `user-request` binary. ⚠️ Each test
//! opens a console window for a few seconds.
#![cfg(windows)]

use std::time::{Duration, Instant};

use user_request::chooser::{Choice, Chooser};
use user_request::window::WindowChooser;
use user_request::{Grant, KindId, Request, Requester};

fn req(kind: KindId) -> Request {
    Request {
        kind,
        subject: "trust-dialog".into(),
        summary: "auto-answer".into(),
        requester: Requester {
            role: "overmind".into(),
            session_id: "s".into(),
            claude_pid: 7,
            claude_start_secs: 1,
            managed: true,
        },
        reason: "test".into(),
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
