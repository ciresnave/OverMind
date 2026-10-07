// SPDX-License-Identifier: MIT OR Apache-2.0
//! The chooser in its own console window (PM ruling 2026-10-07, option A).
//!
//! The requesting process starts a child of itself in a NEW console
//! (`CREATE_NEW_CONSOLE`); the binary must call `serve_if_child` first thing
//! in `main`.
//! - The request and the proposal reach the child on a pipe (its stdin),
//!   which is then closed.
//! - The person's typing is read ONLY from that window's own console input,
//!   `CONIN$` (PM condition (1)): never from the requester's stdin, arguments
//!   or environment.
//! - The child's answer comes back on a pipe (its stdout): one JSON `Choice`.
//!
//! ⚠️ Honest limit (PM condition (4)): this stops accidents and a lane that
//! merely echoes text. A hostile process running as the same user can still
//! drive the window (`SendInput`, `AttachConsole`).

use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::chooser::{Choice, Chooser};
use crate::request::{Grant, Request};

/// The argument that makes a binary serve the chooser window.
pub const CHILD_ARG: &str = "--user-request-chooser-window";

/// What the child is asked, on its stdin.
#[derive(Serialize, Deserialize)]
struct Ask {
    request: Request,
    proposed: Grant,
}

/// The chooser in a new console window, served by `exe`.
pub struct WindowChooser {
    pub exe: PathBuf,
}

impl WindowChooser {
    /// This binary, which must call `serve_if_child` first thing in `main`.
    pub fn this_exe() -> Result<Self, String> {
        std::env::current_exe()
            .map(|exe| Self { exe })
            .map_err(|e| format!("cannot find this program to open the chooser window: {e}"))
    }
}

impl Chooser for WindowChooser {
    #[cfg(windows)]
    fn choose(&self, req: &Request, proposed: &Grant, wait: Duration) -> Choice {
        use std::io::{Read, Write};
        use std::os::windows::process::CommandExt;
        use std::process::{Command, Stdio};
        use std::time::Instant;
        // winbase.h
        const CREATE_NEW_CONSOLE: u32 = 0x0000_0010;

        let ask = match serde_json::to_vec(&Ask {
            request: req.clone(),
            proposed: proposed.clone(),
        }) {
            Ok(a) => a,
            Err(e) => return Choice::Unavailable(format!("cannot send the request: {e}")),
        };
        let mut child = match Command::new(&self.exe)
            .arg(CHILD_ARG)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .creation_flags(CREATE_NEW_CONSOLE)
            .spawn()
        {
            Ok(c) => c,
            Err(e) => return Choice::Unavailable(format!("cannot open the chooser window: {e}")),
        };
        // written, then closed: the child reads its stdin to the end and
        // never again
        let sent = child
            .stdin
            .take()
            .map(|mut i| i.write_all(&ask))
            .unwrap_or_else(|| Err(std::io::ErrorKind::BrokenPipe.into()));
        if let Err(e) = sent {
            let _ = child.kill();
            let _ = child.wait();
            return Choice::Unavailable(format!("cannot send the request: {e}"));
        }
        let mut out = child.stdout.take().expect("stdout is piped");
        let answer = std::thread::spawn(move || {
            let mut s = String::new();
            let _ = out.read_to_string(&mut s);
            s
        });
        let deadline = Instant::now() + wait;
        loop {
            match child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(100))
                }
                Ok(None) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Choice::TimedOut;
                }
                Err(e) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Choice::Unavailable(format!("the chooser window failed: {e}"));
                }
            }
        }
        let text = answer.join().unwrap_or_default();
        serde_json::from_str(text.trim()).unwrap_or_else(|_| {
            Choice::Unavailable("the chooser window closed without an answer".into())
        })
    }

    #[cfg(not(windows))]
    fn choose(&self, _: &Request, _: &Grant, _: Duration) -> Choice {
        Choice::Unavailable("the chooser window exists only on Windows".into())
    }
}

/// If this process was started as a chooser window, serve it and return
/// the exit code; otherwise `None`.
pub fn serve_if_child() -> Option<ExitCode> {
    if std::env::args_os().nth(1).as_deref() != Some(std::ffi::OsStr::new(CHILD_ARG)) {
        return None;
    }
    let choice = serve().unwrap_or_else(Choice::Unavailable);
    match serde_json::to_string(&choice) {
        Ok(json) => {
            println!("{json}");
            Some(ExitCode::SUCCESS)
        }
        Err(_) => Some(ExitCode::FAILURE),
    }
}

fn serve() -> Result<Choice, String> {
    use std::io::Read;
    let mut raw = String::new();
    std::io::stdin()
        .read_to_string(&mut raw)
        .map_err(|e| format!("cannot read the request: {e}"))?;
    let ask: Ask = serde_json::from_str(&raw).map_err(|e| format!("a malformed request: {e}"))?;
    let mut io = console::WindowIo::open()?;
    Ok(crate::chooser::run(
        &mut io,
        &ask.request,
        &ask.proposed,
        chrono::Local::now(),
    ))
}

#[cfg(windows)]
mod console {
    use std::fs::{File, OpenOptions};
    use std::io::{BufRead, BufReader, Write};

    use crate::chooser::ConsoleIo;

    /// This window's own console: `CONIN$` and `CONOUT$`, never stdin.
    pub struct WindowIo {
        input: BufReader<File>,
        output: File,
    }

    impl WindowIo {
        pub fn open() -> Result<Self, String> {
            use windows::core::HSTRING;
            use windows::Win32::System::Console::{SetConsoleOutputCP, SetConsoleTitleW};
            // best effort: UTF-8 for the prompt's text, and a title that says
            // what the window is for
            unsafe {
                let _ = SetConsoleOutputCP(65001);
                let _ = SetConsoleTitleW(&HSTRING::from("user-request: choose how long to grant"));
            }
            let input = OpenOptions::new()
                .read(true)
                .write(true)
                .open("CONIN$")
                .map_err(|e| format!("the chooser window has no console input: {e}"))?;
            let output = OpenOptions::new()
                .write(true)
                .open("CONOUT$")
                .map_err(|e| format!("the chooser window has no console output: {e}"))?;
            Ok(Self {
                input: BufReader::new(input),
                output,
            })
        }
    }

    impl ConsoleIo for WindowIo {
        fn say(&mut self, text: &str) {
            let _ = writeln!(self.output, "{text}");
        }

        fn line(&mut self, prompt: &str) -> Option<String> {
            let _ = write!(self.output, "{prompt}");
            let _ = self.output.flush();
            let mut s = String::new();
            match self.input.read_line(&mut s) {
                Ok(0) | Err(_) => None,
                Ok(_) => Some(s.trim_end_matches(['\r', '\n']).to_string()),
            }
        }
    }
}

#[cfg(not(windows))]
mod console {
    use crate::chooser::ConsoleIo;

    pub struct WindowIo;

    impl WindowIo {
        pub fn open() -> Result<Self, String> {
            Err("the chooser window exists only on Windows".into())
        }
    }

    impl ConsoleIo for WindowIo {
        fn say(&mut self, _: &str) {}
        fn line(&mut self, _: &str) -> Option<String> {
            None
        }
    }
}
