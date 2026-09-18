// SPDX-License-Identifier: MIT OR Apache-2.0

//! CLI entry point. All the actual decision logic lives in the library
//! (`authorize.rs`, `facts.rs`, `state.rs`, `log.rs`) so it can be unit
//! tested without a real process. This file only: parses argv, wires the
//! real `SystemFacts`, calls `authorize::decide`, and - only if it comes
//! back `Ok` with `will_act: true` - performs the kill and the relaunch.

use lane_restart::authorize::{self, Target};
use lane_restart::facts::SysinfoFacts;
use lane_restart::log;
use std::path::PathBuf;
use std::process::ExitCode;

/// `C:/Projects/.lane-state` - RESTART-TOOL-DESIGN.md §7. Not configurable
/// via CLI on purpose: a caller-supplied state directory would defeat the
/// whole point of a fixed, portfolio-wide location every lane and the PM
/// agree on.
fn state_dir() -> PathBuf {
    PathBuf::from("C:/Projects/.lane-state")
}

fn log_path() -> PathBuf {
    state_dir().join("restart.log")
}

fn claude_config_dir() -> PathBuf {
    std::env::var_os("CLAUDE_CONFIG_DIR")
        .map(PathBuf::from)
        .or_else(|| dirs_home().map(|h| h.join(".claude")))
        .expect("could not resolve the Claude Code config directory")
}

fn dirs_home() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
}

#[derive(Debug)]
struct Args {
    role: String,
    target_self: bool,
    dry_run: bool,
    confirmed: bool,
}

#[derive(Debug, PartialEq, Eq)]
enum ArgError {
    MissingRole,
    RoleRequired,
    Unknown(String),
}

impl std::fmt::Display for ArgError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ArgError::MissingRole => write!(f, "--role requires a value"),
            ArgError::RoleRequired => write!(f, "--role <name> is required"),
            ArgError::Unknown(a) => write!(f, "unrecognised argument: {a}"),
        }
    }
}

/// ⚠️ `--self` DEFAULTS TO FALSE. Omitting it means "restart a different
/// lane" - the SAFER default is the one that requires `--yes` to act for
/// real, not the one that acts unconditionally. A caller who wants to
/// restart their own session must say so explicitly.
fn parse_args<I: IntoIterator<Item = String>>(argv: I) -> Result<Args, ArgError> {
    let mut role: Option<String> = None;
    let mut target_self = false;
    let mut dry_run = false;
    let mut confirmed = false;
    let mut iter = argv.into_iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--role" => role = Some(iter.next().ok_or(ArgError::MissingRole)?),
            "--self" => target_self = true,
            "--dry-run" => dry_run = true,
            "--yes" => confirmed = true,
            other => return Err(ArgError::Unknown(other.to_string())),
        }
    }
    Ok(Args {
        role: role.ok_or(ArgError::RoleRequired)?,
        target_self,
        dry_run,
        confirmed,
    })
}

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = match parse_args(argv) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("lane-restart: {e}");
            return ExitCode::FAILURE;
        }
    };

    let target = if args.target_self {
        Target::Myself {
            role: args.role.clone(),
        }
    } else {
        Target::Other {
            role: args.role.clone(),
        }
    };
    let requested_by = if args.target_self { "self" } else { "pm" };

    let facts = SysinfoFacts::new(claude_config_dir());
    let req = authorize::Request {
        target,
        confirmed: args.confirmed,
        dry_run: args.dry_run,
    };

    match authorize::decide(&req, &facts, &state_dir()) {
        Err(refusal) => {
            eprintln!("lane-restart: refused - {refusal}");
            log_outcome(
                requested_by,
                &args.role,
                &format!("refused: {refusal}"),
                false,
            );
            ExitCode::FAILURE
        }
        Ok(plan) if !plan.will_act => {
            println!(
                "lane-restart: DRY RUN - would kill pid {} and relaunch \
                 `claude --resume {} --name {} \"read {} HANDOFF and continue\"` in {}",
                plan.state.pid,
                plan.state.session_id,
                plan.state.name.as_deref().unwrap_or(&plan.state.role),
                plan.state.role,
                plan.state.cwd
            );
            log_outcome(requested_by, &args.role, "dry run - no action taken", false);
            ExitCode::SUCCESS
        }
        Ok(plan) => {
            eprintln!(
                "lane-restart: killing pid {} and relaunching...",
                plan.state.pid
            );
            match facts::kill_and_relaunch(&facts, &plan.state) {
                Ok(()) => {
                    log_outcome(
                        requested_by,
                        &args.role,
                        &format!(
                            "killed pid {} and relaunched via claude --resume {}",
                            plan.state.pid, plan.state.session_id
                        ),
                        true,
                    );
                    ExitCode::SUCCESS
                }
                Err(e) => {
                    eprintln!("lane-restart: {e}");
                    log_outcome(
                        requested_by,
                        &args.role,
                        &format!("action failed: {e}"),
                        false,
                    );
                    ExitCode::FAILURE
                }
            }
        }
    }
}

fn log_outcome(requested_by: &str, role: &str, outcome: &str, acted: bool) {
    let entry = log::Entry {
        timestamp: chrono::Utc::now(),
        requested_by,
        role,
        outcome: outcome.to_string(),
        acted,
    };
    if let Err(e) = log::append(&log_path(), &entry) {
        eprintln!("lane-restart: WARNING - could not write to the restart log: {e}");
    }
}

/// Kept as its own module so `main`'s match arms stay readable - this is
/// glue over `SysinfoFacts::kill` and a real process spawn, deliberately
/// NOT unit tested (see `facts.rs`'s own docs on why).
mod facts {
    use lane_restart::facts::SystemFacts;
    use lane_restart::state::LaneState;

    pub fn kill_and_relaunch(facts: &dyn SystemFacts, state: &LaneState) -> Result<(), String> {
        facts.kill(state.pid)?;
        // Give the OS a moment to finish tearing the process down before a
        // new `claude` process claims the same session lock.
        std::thread::sleep(std::time::Duration::from_millis(500));

        let name = state.name.as_deref().unwrap_or(&state.role);
        let prompt = format!("read {} HANDOFF and continue", state.role);

        // Windows: a visible new terminal window, per RESTART-TOOL-DESIGN.md §5.
        #[cfg(windows)]
        {
            std::process::Command::new("cmd")
                .args(["/C", "start", "", "claude"])
                .args(["--resume", &state.session_id])
                .args(["--name", name])
                .arg(&prompt)
                .current_dir(&state.cwd)
                .spawn()
                .map_err(|e| format!("could not launch the relaunch command: {e}"))?;
        }
        #[cfg(not(windows))]
        {
            std::process::Command::new("claude")
                .args(["--resume", &state.session_id])
                .args(["--name", name])
                .arg(&prompt)
                .current_dir(&state.cwd)
                .spawn()
                .map_err(|e| format!("could not launch the relaunch command: {e}"))?;
        }
        Ok(())
    }
}
