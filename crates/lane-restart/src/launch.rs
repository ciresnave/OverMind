// SPDX-License-Identifier: MIT OR Apache-2.0

//! Launching ONE lane, from a description of it rather than from a state
//! file (ARCHITECTURE-LAYERS-SPEC.md section 8b; agentlife's extraction note,
//! seam 1). A restore of a lane that is already dead has a roster entry, not a
//! `LaneState`: `LaunchSpec` is what a launch actually reads.

use crate::facts::SystemFacts;
use crate::relaunch::{
    extra_launch_args, first_unsafe_argument, has_dev_channels_flag, resolve_launch_model,
    valid_identifier, RelaunchError, RelaunchOutcome, StateReader,
};
use crate::state::LaneState;
use std::time::Duration;

/// What a launch needs to know about the lane it starts. Built from a state
/// file for a restart (`From<&LaneState>`), or by hand from a roster entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchSpec {
    pub role: String,
    /// The `--name` the session is started with: the lane's recorded name,
    /// else its role.
    pub name: String,
    pub cwd: String,
    pub permission_mode: Option<String>,
    pub remote_control: bool,
    /// The flags of the ORIGINAL launch; only the allowlisted ones carry over.
    pub launch_args: Option<Vec<String>>,
}

impl From<&LaneState> for LaunchSpec {
    fn from(state: &LaneState) -> Self {
        LaunchSpec {
            role: state.role.clone(),
            name: state.name.clone().unwrap_or_else(|| state.role.clone()),
            cwd: state.cwd.clone(),
            permission_mode: state.permission_mode.clone(),
            remote_control: state.remote_control,
            launch_args: state.launch_args.clone(),
        }
    }
}

/// A launch that passed every check and is ready to spawn.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedLaunch {
    pub argv: Vec<String>,
    /// Whether the argv about to be launched carries the development-channels
    /// flag (the startup dialog a human must confirm).
    pub carries_dev_channels_flag: bool,
}

/// The argv of a launch. The prompt goes FIRST, right after `claude`, and the
/// variadic flags carried over from the original launch are always LAST, so
/// nothing can follow a variadic flag's values (see `relaunch::claude_argv`,
/// which builds the same thing from a state file).
pub fn launch_argv(spec: &LaunchSpec, prompt: &str, model: &str) -> Vec<String> {
    let mut argv = vec!["claude".to_string(), prompt.to_string()];
    argv.push("--name".to_string());
    argv.push(spec.name.clone());
    argv.push("--model".to_string());
    argv.push(model.to_string());
    if let Some(permission_mode) = &spec.permission_mode {
        argv.push("--permission-mode".to_string());
        argv.push(permission_mode.clone());
    }
    if spec.remote_control {
        argv.push("--remote-control".to_string());
    }
    if let Some(launch_args) = &spec.launch_args {
        let (extra, dropped_unknown) = extra_launch_args(launch_args);
        argv.extend(extra);
        if !dropped_unknown.is_empty() {
            eprintln!(
                "lane-restart: dropping unrecognised launch flag(s), not carrying them \
                 over to the relaunch: {}",
                dropped_unknown.join(", ")
            );
        }
    }
    argv
}

/// Every check a launch must pass BEFORE anything is killed or spawned: a
/// plain role and name, a model that is not Opus (unless overridden), and no
/// `;` in the working directory or any argv element (`wt.exe` reads it as a
/// command separator). Returns the argv about to be launched.
pub fn prepare_launch(
    spec: &LaunchSpec,
    policy_default: &str,
    allow_opus: bool,
) -> Result<PreparedLaunch, RelaunchError> {
    if !valid_identifier(&spec.role) {
        return Err(RelaunchError::InvalidIdentifier(format!(
            "role {:?}",
            spec.role
        )));
    }
    if !valid_identifier(&spec.name) {
        return Err(RelaunchError::InvalidIdentifier(format!(
            "name {:?}",
            spec.name
        )));
    }
    let prompt = format!("read {} HANDOFF and continue", spec.role);
    let model = resolve_launch_model(spec.launch_args.as_ref(), policy_default, allow_opus)?;
    let argv = launch_argv(spec, &prompt, &model);
    if let Some(bad) = first_unsafe_argument(
        std::iter::once(spec.cwd.as_str()).chain(argv.iter().map(String::as_str)),
    ) {
        return Err(RelaunchError::UnsafeArgument(bad.to_string()));
    }
    // PM finding, 2026-09-19: judge the argv ABOUT TO BE LAUNCHED (it already
    // went through the allowlist), never the original launch flags.
    let carries_dev_channels_flag = has_dev_channels_flag(&argv);
    Ok(PreparedLaunch {
        argv,
        carries_dev_channels_flag,
    })
}

/// How long a launch is given. `Default` is what lane-restart has always
/// used; a batch restore passes its own.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LivenessTiming {
    /// How long without real progress before the process is checked.
    pub progress_timeout: Duration,
    /// How long in all before giving up (or reporting the dialog as final).
    pub total_timeout: Duration,
    pub poll_interval: Duration,
}

impl Default for LivenessTiming {
    fn default() -> Self {
        LivenessTiming {
            progress_timeout: Duration::from_secs(20),
            // "Configurable" per the PM's own spec; now it is, as a parameter.
            total_timeout: Duration::from_secs(15 * 60),
            poll_interval: Duration::from_secs(1),
        }
    }
}

/// Waits for a launched lane to show real progress: its own state file shows a
/// session that is not `previous_session` (when there is one) and whose last
/// event is past `SessionStart`. Three outcomes, as `relaunch`'s wait always had:
/// `Relaunched`; `AwaitingConfirmation` (alive, no progress, and the launch
/// carries the development-channels flag: `on_awaiting` fires once, polling
/// goes on to the total timeout); or `Err(SessionNeverProcessedPrompt)`.
/// `previous_session` is `None` for a lane that had no session to replace (a
/// restore); `launched_after_secs` is the earliest start time of the new
/// process. `sleep` and `on_awaiting` are injected so the whole protocol is
/// testable without a real wait.
#[allow(clippy::too_many_arguments)]
pub fn wait_for_liveness(
    facts: &dyn SystemFacts,
    state_reader: &dyn StateReader,
    cwd: &str,
    role: &str,
    previous_session: Option<&str>,
    launched_after_secs: u64,
    carries_dev_channels_flag: bool,
    timing: &LivenessTiming,
    sleep: &mut dyn FnMut(Duration),
    on_awaiting: &mut dyn FnMut(),
) -> Result<RelaunchOutcome, RelaunchError> {
    let mut waited = std::time::Duration::ZERO;
    let mut reported_awaiting = false;
    loop {
        if let Some(state) = state_reader.read(role) {
            if previous_session.is_none_or(|old| state.session_id != old)
                && state.updated_by_event != "SessionStart"
            {
                return Ok(RelaunchOutcome::Relaunched);
            }
        }
        if waited >= timing.progress_timeout {
            let alive = facts
                .find_claude_process_in(cwd, launched_after_secs)
                .is_some();
            if !alive {
                return Err(RelaunchError::SessionNeverProcessedPrompt);
            }
            if !reported_awaiting {
                if !carries_dev_channels_flag {
                    // Alive, no progress, and nothing known to explain
                    // the stall - not the dialog case. Keep polling to
                    // timing.total_timeout anyway (a slow session is still a
                    // real possibility), but never report a confirmation
                    // that has no evidence behind it.
                } else {
                    on_awaiting();
                    reported_awaiting = true;
                }
            }
        }
        if waited >= timing.total_timeout {
            return if reported_awaiting {
                Ok(RelaunchOutcome::AwaitingConfirmation)
            } else {
                Err(RelaunchError::SessionNeverProcessedPrompt)
            };
        }
        sleep(timing.poll_interval);
        waited += timing.poll_interval;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::relaunch::claude_argv;

    fn strs(args: &[&str]) -> Vec<String> {
        args.iter().map(|s| s.to_string()).collect()
    }

    fn state(role: &str) -> LaneState {
        LaneState {
            role: role.to_string(),
            session_id: "s".to_string(),
            pid: 1,
            pid_start_secs: None,
            cwd: "C:/x".to_string(),
            name: None,
            model: Some("claude-sonnet-5".to_string()),
            permission_mode: Some("prompting".to_string()),
            remote_control: false,
            busy: false,
            subagents_running: 0,
            no_background_shells: Some(true),
            launch_args: None,
            updated_at: chrono::Utc::now(),
            updated_by_event: "Stop".to_string(),
        }
    }

    fn spec(role: &str) -> LaunchSpec {
        LaunchSpec::from(&state(role))
    }

    #[test]
    fn a_spec_from_a_state_copies_what_a_launch_reads_and_names_the_role_by_default() {
        let mut st = state("overmind");
        st.remote_control = true;
        st.launch_args = Some(strs(&["claude", "--remote-control"]));
        let s = LaunchSpec::from(&st);
        assert_eq!(s.role, "overmind");
        assert_eq!(s.name, "overmind");
        assert_eq!(s.cwd, "C:/x");
        assert_eq!(s.permission_mode.as_deref(), Some("prompting"));
        assert!(s.remote_control);
        assert_eq!(s.launch_args, st.launch_args);
        // control: a recorded name wins over the role
        st.name = Some("PM".into());
        assert_eq!(LaunchSpec::from(&st).name, "PM");
    }

    /// The restart path and the new spec path build the SAME argv, for every
    /// shape of state, so a restore and a restart can never drift apart.
    #[test]
    fn a_spec_builds_the_same_argv_as_the_state_it_came_from() {
        let mut plain = state("overmind");
        plain.permission_mode = None;
        let mut remote = state("pm");
        remote.remote_control = true;
        remote.launch_args = Some(strs(&[
            "claude",
            "--dangerously-load-development-channels",
            "server:claude-peers",
            "--bogus-flag",
            "x",
        ]));
        let mut named = state("overmind");
        named.name = Some("Over".into());
        for st in [state("overmind"), plain, remote, named] {
            let spec = LaunchSpec::from(&st);
            let name = st.name.clone().unwrap_or_else(|| st.role.clone());
            assert_eq!(
                launch_argv(&spec, "p", "sonnet"),
                claude_argv(&name, &st, "p", "sonnet"),
                "{}",
                st.role
            );
        }
    }

    /// A roster entry has no state file: the argv comes from the spec alone.
    #[test]
    fn a_hand_built_spec_launches_without_a_state_file() {
        let spec = LaunchSpec {
            role: "fuel".into(),
            name: "fuel".into(),
            cwd: "C:/Projects/fuel".into(),
            permission_mode: None,
            remote_control: false,
            launch_args: None,
        };
        assert_eq!(
            launch_argv(&spec, "read fuel HANDOFF and continue", "sonnet"),
            strs(&[
                "claude",
                "read fuel HANDOFF and continue",
                "--name",
                "fuel",
                "--model",
                "sonnet",
            ])
        );
    }

    #[test]
    fn a_launch_is_refused_for_an_invalid_role_or_name() {
        assert!(matches!(
            prepare_launch(&spec("a&calc"), "sonnet", false),
            Err(RelaunchError::InvalidIdentifier(_))
        ));
        let mut s = spec("overmind");
        s.name = "a|calc".into();
        assert!(matches!(
            prepare_launch(&s, "sonnet", false),
            Err(RelaunchError::InvalidIdentifier(_))
        ));
    }

    #[test]
    fn a_launch_is_refused_when_any_element_has_a_semicolon() {
        let mut s = spec("overmind");
        s.cwd = "C:/x;calc".into();
        assert!(matches!(
            prepare_launch(&s, "sonnet", false),
            Err(RelaunchError::UnsafeArgument(_))
        ));
        let mut s = spec("overmind");
        s.permission_mode = Some("a;b".into());
        assert!(matches!(
            prepare_launch(&s, "sonnet", false),
            Err(RelaunchError::UnsafeArgument(_))
        ));
    }

    /// CireSnave, 2026-10-08: "I can't afford Opus."
    #[test]
    fn a_launch_on_opus_is_refused_unless_overridden() {
        assert!(matches!(
            prepare_launch(&spec("overmind"), "claude-opus-5", false),
            Err(RelaunchError::OpusRefused(_))
        ));
        let ok = prepare_launch(&spec("overmind"), "claude-opus-5", true).unwrap();
        assert!(ok.argv.contains(&"claude-opus-5".to_string()));
        // an explicit launch --model pin wins over the policy default
        let mut pinned = spec("overmind");
        pinned.launch_args = Some(strs(&["claude", "--model", "haiku"]));
        let p = prepare_launch(&pinned, "claude-opus-5", false).unwrap();
        assert!(p.argv.contains(&"haiku".to_string()));
    }

    #[test]
    fn a_prepared_launch_knows_whether_it_carries_the_dev_channels_flag() {
        let plain = prepare_launch(&spec("overmind"), "sonnet", false).unwrap();
        assert!(!plain.carries_dev_channels_flag);
        assert_eq!(plain.argv[0], "claude");
        let mut s = spec("overmind");
        s.launch_args = Some(strs(&[
            "claude",
            "--dangerously-load-development-channels",
            "server:claude-peers",
        ]));
        let p = prepare_launch(&s, "sonnet", false).unwrap();
        assert!(p.carries_dev_channels_flag);
    }

    // -- LivenessTiming and wait_for_liveness (seam 2) ---------------------------

    struct Reader(Vec<Option<LaneState>>, std::cell::RefCell<usize>);
    impl StateReader for Reader {
        fn read(&self, _role: &str) -> Option<LaneState> {
            let mut calls = self.1.borrow_mut();
            let idx = (*calls).min(self.0.len() - 1);
            *calls += 1;
            self.0[idx].clone()
        }
    }

    fn reader(states: Vec<Option<LaneState>>) -> Reader {
        Reader(states, std::cell::RefCell::new(0))
    }

    fn at(session: &str, event: &str) -> Option<LaneState> {
        let mut st = state("overmind");
        st.session_id = session.into();
        st.updated_by_event = event.into();
        Some(st)
    }

    /// Only `find_claude_process_in` is ever asked.
    struct Alive(bool);
    impl SystemFacts for Alive {
        fn is_alive_claude_process(&self, _: u32) -> bool {
            unreachable!()
        }
        fn cwd_of(&self, _: u32) -> Option<std::path::PathBuf> {
            unreachable!()
        }
        fn has_live_shell_descendant(&self, _: u32) -> Result<bool, crate::facts::ShellCheckError> {
            unreachable!()
        }
        fn transcript_is_recent(&self, _: &str, _: &str, _: Duration) -> bool {
            unreachable!()
        }
        fn now(&self) -> chrono::DateTime<chrono::Utc> {
            unreachable!()
        }
        fn process_identity(&self, _: u32) -> Option<crate::facts::ProcessIdentity> {
            unreachable!()
        }
        fn kill_verified(
            &self,
            _: u32,
            _: &crate::facts::ProcessIdentity,
        ) -> Result<(), crate::facts::KillError> {
            unreachable!()
        }
        fn find_claude_process_in(&self, _: &str, _: u64) -> Option<u32> {
            self.0.then_some(1)
        }
        fn process_table(
            &self,
        ) -> Result<Vec<crate::facts::ProcEntry>, crate::facts::ShellCheckError> {
            unreachable!()
        }
    }

    fn short() -> LivenessTiming {
        LivenessTiming {
            progress_timeout: Duration::from_secs(2),
            total_timeout: Duration::from_secs(5),
            poll_interval: Duration::from_secs(1),
        }
    }

    #[test]
    fn the_default_timing_is_what_lane_restart_has_always_used() {
        let t = LivenessTiming::default();
        assert_eq!(t.progress_timeout, Duration::from_secs(20));
        assert_eq!(t.total_timeout, Duration::from_secs(15 * 60));
        assert_eq!(t.poll_interval, Duration::from_secs(1));
    }

    /// A batch restore wants a short wait: the parameters really are used.
    #[test]
    fn a_custom_timing_decides_when_the_dialog_is_reported_and_when_to_stop() {
        let r = reader(vec![at("new", "SessionStart")]);
        let mut slept = Vec::new();
        let mut awaiting = 0;
        let out = wait_for_liveness(
            &Alive(true),
            &r,
            "C:/x",
            "overmind",
            Some("old"),
            0,
            true,
            &short(),
            &mut |d| slept.push(d),
            &mut || awaiting += 1,
        );
        assert_eq!(out.unwrap(), RelaunchOutcome::AwaitingConfirmation);
        assert_eq!(slept, vec![Duration::from_secs(1); 5], "5 s in 1 s steps");
        assert_eq!(awaiting, 1, "reported once");
    }

    #[test]
    fn a_dead_process_is_found_at_the_custom_progress_timeout() {
        let r = reader(vec![None]);
        let mut slept = 0;
        let out = wait_for_liveness(
            &Alive(false),
            &r,
            "C:/x",
            "overmind",
            Some("old"),
            0,
            false,
            &short(),
            &mut |_| slept += 1,
            &mut || {},
        );
        assert!(matches!(
            out,
            Err(RelaunchError::SessionNeverProcessedPrompt)
        ));
        assert_eq!(slept, 2, "gave up at the 2 s progress timeout, not at 5 s");
    }

    /// A restore has no previous session: any session that got past
    /// `SessionStart` is the fresh one.
    #[test]
    fn with_no_previous_session_any_progress_counts() {
        let none = wait_for_liveness(
            &Alive(true),
            &reader(vec![at("whatever", "UserPromptSubmit")]),
            "C:/x",
            "overmind",
            None,
            0,
            false,
            &short(),
            &mut |_| {},
            &mut || {},
        );
        assert_eq!(none.unwrap(), RelaunchOutcome::Relaunched);
        // control: still only SessionStart is not yet progress
        let mut slept = 0;
        let stuck = wait_for_liveness(
            &Alive(false),
            &reader(vec![at("whatever", "SessionStart")]),
            "C:/x",
            "overmind",
            None,
            0,
            false,
            &short(),
            &mut |_| slept += 1,
            &mut || {},
        );
        assert!(stuck.is_err());
        assert_eq!(slept, 2);
    }

    /// With a previous session, its own later events are not the new session.
    #[test]
    fn the_previous_sessions_own_events_do_not_count() {
        let mut slept = 0;
        let out = wait_for_liveness(
            &Alive(false),
            &reader(vec![at("old", "UserPromptSubmit")]),
            "C:/x",
            "overmind",
            Some("old"),
            0,
            false,
            &short(),
            &mut |_| slept += 1,
            &mut || {},
        );
        assert!(out.is_err());
        assert_eq!(slept, 2);
    }
}
