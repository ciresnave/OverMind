// SPDX-License-Identifier: MIT OR Apache-2.0
//! The chooser window driven through its OWN console, as a person would
//! (review 7): this test process attaches to the child's console, waits for
//! its prompt, then types into it or breaks it.
//!
//! ⚠️ These open REAL console windows on the desktop, so they are ignored by
//! default and never run in a plain `cargo test`. CI runs them explicitly
//! (`--ignored`); run them locally only with the person told first. The
//! fixture says TEST and names an obviously fake subject.
//!
//! In a file of its own: attaching moves this whole process between
//! consoles, so the tests take turns.
#![cfg(windows)]

use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use chrono::{Duration as Span, Utc};
use sysinfo::{Pid, ProcessesToUpdate, System};
use user_request::chooser::{Choice, Chooser};
use user_request::window::WindowChooser;
use user_request::{Grant, KindId, Request, Requester};
use windows::Win32::Foundation::HANDLE;
use windows::Win32::System::Console::{
    AttachConsole, FreeConsole, GetConsoleScreenBufferInfo, ReadConsoleOutputCharacterW,
    WriteConsoleInputW, CONSOLE_SCREEN_BUFFER_INFO, COORD, INPUT_RECORD, INPUT_RECORD_0, KEY_EVENT,
    KEY_EVENT_RECORD, KEY_EVENT_RECORD_0,
};

static ONE_AT_A_TIME: Mutex<()> = Mutex::new(());

fn req() -> Request {
    Request {
        kind: KindId::Secret,
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

/// Start the chooser in a thread; return it and the child's pid.
fn open_window() -> (thread::JoinHandle<Choice>, u32) {
    let me = Pid::from_u32(std::process::id());
    let mut sys = System::new();
    let windows = |sys: &mut System| -> Vec<Pid> {
        sys.refresh_processes(ProcessesToUpdate::All, true);
        sys.processes()
            .values()
            .filter(|p| {
                p.parent() == Some(me) && p.name().to_string_lossy().starts_with("user-request")
            })
            .map(|p| p.pid())
            .collect()
    };
    // an earlier test's window may still be exiting
    let earlier = windows(&mut sys);
    let asking = thread::spawn(|| {
        WindowChooser {
            exe: env!("CARGO_BIN_EXE_user-request").into(),
        }
        .choose(
            &req(),
            &Grant::for_duration(Span::hours(1)),
            Duration::from_secs(30),
        )
    });
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(p) = windows(&mut sys).into_iter().find(|p| !earlier.contains(p)) {
            return (asking, p.as_u32());
        }
        assert!(
            Instant::now() < deadline,
            "the chooser window never started"
        );
        thread::sleep(Duration::from_millis(100));
    }
}

/// Attach to `pid`'s console and wait until its prompt is on screen (it
/// flushes typed-ahead input first, review 3), then run `f` there.
fn in_its_console(pid: u32, f: impl FnOnce()) {
    unsafe {
        let _ = FreeConsole();
        AttachConsole(pid).expect("attach to the chooser's console");
    }
    let out = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open("CONOUT$")
        .unwrap();
    let h = HANDLE(std::os::windows::io::AsRawHandle::as_raw_handle(&out));
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let mut info = CONSOLE_SCREEN_BUFFER_INFO::default();
        unsafe { GetConsoleScreenBufferInfo(h, &mut info).unwrap() };
        let cells = (info.dwSize.X as usize) * (info.dwSize.Y as usize);
        let mut text = vec![0u16; cells];
        let mut read = 0u32;
        unsafe {
            ReadConsoleOutputCharacterW(h, &mut text, COORD { X: 0, Y: 0 }, &mut read).unwrap()
        };
        if String::from_utf16_lossy(&text[..read as usize]).contains("Your choice:") {
            break;
        }
        assert!(Instant::now() < deadline, "the prompt never appeared");
        thread::sleep(Duration::from_millis(100));
    }
    f();
    unsafe {
        let _ = FreeConsole();
    }
}

fn key(c: u16, down: bool) -> INPUT_RECORD {
    INPUT_RECORD {
        EventType: KEY_EVENT as u16,
        Event: INPUT_RECORD_0 {
            KeyEvent: KEY_EVENT_RECORD {
                bKeyDown: down.into(),
                wRepeatCount: 1,
                wVirtualKeyCode: if c == 13 { 0x0D } else { 0 },
                wVirtualScanCode: 0,
                uChar: KEY_EVENT_RECORD_0 { UnicodeChar: c },
                dwControlKeyState: 0,
            },
        },
    }
}

fn type_keys(text: &str) {
    let input = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open("CONIN$")
        .unwrap();
    let h = HANDLE(std::os::windows::io::AsRawHandle::as_raw_handle(&input));
    let records: Vec<INPUT_RECORD> = text
        .encode_utf16()
        .flat_map(|c| [key(c, true), key(c, false)])
        .collect();
    let mut written = 0u32;
    unsafe { WriteConsoleInputW(h, &records, &mut written).unwrap() };
    assert_eq!(written as usize, records.len());
}

/// What the person types in the window is the answer that comes back.
#[test]
#[ignore = "opens a real console window: run with --ignored, the person told first"]
fn what_is_typed_in_the_window_is_the_choice() {
    let _turn = ONE_AT_A_TIME.lock().unwrap_or_else(|e| e.into_inner());
    let before = Utc::now();
    let (asking, pid) = open_window();
    in_its_console(pid, || type_keys("5m\r"));
    let got = asking.join().unwrap();
    let Choice::Chosen(Grant::Until(end)) = got else {
        panic!("{got:?}")
    };
    assert!(end >= before + Span::minutes(5) && end <= Utc::now() + Span::minutes(5));
}

/// Send Ctrl+Break to every process on `pid`'s console, from a throwaway
/// PowerShell that attaches to it (and may die of it): this test process
/// never shares the console it breaks.
fn break_its_console(pid: u32) {
    let script = format!(
        "Add-Type -Namespace W -Name K -MemberDefinition '\
         [DllImport(\"kernel32.dll\")] public static extern bool FreeConsole(); \
         [DllImport(\"kernel32.dll\")] public static extern bool AttachConsole(uint p); \
         [DllImport(\"kernel32.dll\")] public static extern bool GenerateConsoleCtrlEvent(uint e, uint g);'; \
         [void][W.K]::FreeConsole(); \
         if (-not [W.K]::AttachConsole({pid})) {{ exit 3 }}; \
         [void][W.K]::GenerateConsoleCtrlEvent(1, 0); Start-Sleep -Milliseconds 500"
    );
    let status = std::process::Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .unwrap();
    assert_ne!(status.code(), Some(3), "the helper could not attach");
}

/// Review 1: breaking the window is a denial, which starts the gate's
/// 10-minute cooldown; it is not "unavailable". Closing the window (a
/// Windows Terminal tab here) reaches the chooser as CTRL_CLOSE_EVENT,
/// through the same handler; that close itself is not automated.
#[test]
#[ignore = "opens a real console window: run with --ignored, the person told first"]
fn breaking_the_window_is_a_denial() {
    let _turn = ONE_AT_A_TIME.lock().unwrap_or_else(|e| e.into_inner());
    let (asking, pid) = open_window();
    // wait for the prompt: the handler is installed before it is shown
    in_its_console(pid, || {});
    break_its_console(pid);
    assert_eq!(asking.join().unwrap(), Choice::Denied);
}
