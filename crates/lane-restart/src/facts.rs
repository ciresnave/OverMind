// SPDX-License-Identifier: MIT OR Apache-2.0

//! What the OS actually says, behind one trait — so `authorize.rs`'s decision
//! logic can be tested against a fake without ever touching a real process.
//!
//! ⚠️ `SysinfoFacts` (the real implementation) is NOT unit-tested here - it
//! cannot be, without a real process to observe, which this crate must never
//! spin up just to test itself. It is exercised by `--dry-run` against real
//! lanes before `--yes` is ever used for real. Everything this module's
//! CALLERS do with what it returns - the actual refusal logic - is fully
//! tested in `authorize.rs`.

use chrono::{DateTime, Utc};
use std::path::PathBuf;
use std::time::Duration;
use sysinfo::{Pid, System};

/// Image names (lowercased, no extension) counted as "a live shell" when
/// found as a descendant of the target PID — RESTART-TOOL-DESIGN.md §1a.
const SHELL_IMAGE_NAMES: &[&str] = &["bash", "pwsh", "powershell", "cmd", "sh", "zsh", "fish"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ShellCheckError {
    /// The process table could not be enumerated cleanly. RESTART-TOOL-DESIGN.md
    /// §1a: this counts as UNSAFE, the same as finding a shell - never as "no
    /// shell found."
    EnumerationFailed(String),
}

pub trait SystemFacts {
    /// Is `pid` alive right now, and is its own image name `claude` (or
    /// `claude.exe`)? A dead PID, or a live one that isn't Claude Code
    /// itself (PID reuse), both return `false` - see RESTART-TOOL-DESIGN.md §2.1.
    fn is_alive_claude_process(&self, pid: u32) -> bool;

    /// The live process's own working directory, if it could be read.
    fn cwd_of(&self, pid: u32) -> Option<PathBuf>;

    /// §1a's second, independent signal: walk `pid`'s descendants looking
    /// for a live shell. `Ok(true)` = found one, `Ok(false)` = none found
    /// among processes that WERE enumerable, `Err` = the walk itself failed
    /// (treated as unsafe by the caller, same as `Ok(true)`).
    fn has_live_shell_descendant(&self, pid: u32) -> Result<bool, ShellCheckError>;

    /// §2.3: does a transcript for `session_id` exist under `cwd`'s project
    /// storage, with an mtime recent enough (within `max_age`) to plausibly
    /// belong to the CURRENT run rather than a stale leftover from a PID
    /// that got reused?
    fn transcript_is_recent(&self, cwd: &str, session_id: &str, max_age: Duration) -> bool;

    fn now(&self) -> DateTime<Utc>;

    /// Sends the real kill. Only ever called after `authorize::decide`
    /// returns `Ok`, and only in non-dry-run mode.
    fn kill(&self, pid: u32) -> Result<(), String>;
}

pub struct SysinfoFacts {
    claude_config_dir: PathBuf,
}

impl SysinfoFacts {
    pub fn new(claude_config_dir: PathBuf) -> Self {
        Self { claude_config_dir }
    }

    fn project_dir_name(cwd: &str) -> String {
        // ⚠️ MATCHES sessions.md: "your working directory path with
        // non-alphanumeric characters replaced by -". Not a guess - quoted
        // directly from the verified documentation this spec cites.
        cwd.chars()
            .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
            .collect()
    }
}

impl SystemFacts for SysinfoFacts {
    fn is_alive_claude_process(&self, pid: u32) -> bool {
        let mut sys = System::new();
        sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);
        match sys.process(Pid::from_u32(pid)) {
            Some(p) => {
                let name = p.name().to_string_lossy().to_lowercase();
                name == "claude" || name == "claude.exe"
            }
            None => false,
        }
    }

    fn cwd_of(&self, pid: u32) -> Option<PathBuf> {
        let mut sys = System::new();
        sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);
        sys.process(Pid::from_u32(pid))
            .and_then(|p| p.cwd())
            .map(|p| p.to_path_buf())
    }

    fn has_live_shell_descendant(&self, pid: u32) -> Result<bool, ShellCheckError> {
        let mut sys = System::new();
        sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);
        let target = Pid::from_u32(pid);
        let mut stack = vec![target];
        let mut seen = std::collections::HashSet::new();
        while let Some(current) = stack.pop() {
            if !seen.insert(current) {
                continue;
            }
            for (child_pid, process) in sys.processes() {
                if process.parent() != Some(current) {
                    continue;
                }
                let name = process.name().to_string_lossy().to_lowercase();
                let base = name.trim_end_matches(".exe");
                if SHELL_IMAGE_NAMES.contains(&base) {
                    return Ok(true);
                }
                stack.push(*child_pid);
            }
        }
        Ok(false)
    }

    fn transcript_is_recent(&self, cwd: &str, session_id: &str, max_age: Duration) -> bool {
        let project = Self::project_dir_name(cwd);
        let path = self
            .claude_config_dir
            .join("projects")
            .join(project)
            .join(format!("{session_id}.jsonl"));
        let Ok(meta) = std::fs::metadata(&path) else {
            return false;
        };
        let Ok(modified) = meta.modified() else {
            return false;
        };
        match modified.elapsed() {
            Ok(age) => age <= max_age,
            Err(_) => false, // mtime in the future - refuse rather than trust it
        }
    }

    fn now(&self) -> DateTime<Utc> {
        Utc::now()
    }

    fn kill(&self, pid: u32) -> Result<(), String> {
        let mut sys = System::new();
        sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);
        match sys.process(Pid::from_u32(pid)) {
            Some(p) => {
                if p.kill() {
                    Ok(())
                } else {
                    Err(format!("kill signal to pid {pid} was not accepted"))
                }
            }
            None => Err(format!("pid {pid} is not running")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn project_dir_name_matches_the_documented_rule() {
        // sessions.md: "working directory path with non-alphanumeric
        // characters replaced by -"
        assert_eq!(
            SysinfoFacts::project_dir_name("C:/Projects/OverMind"),
            "C--Projects-OverMind"
        );
    }
}
