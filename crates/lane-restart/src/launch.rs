// SPDX-License-Identifier: MIT OR Apache-2.0

//! Launching ONE lane, from a description of it rather than from a state
//! file (ARCHITECTURE-LAYERS-SPEC.md section 8b; agentlife's extraction note,
//! seam 1). A restore of a lane that is already dead has a roster entry, not a
//! `LaneState`: `LaunchSpec` is what a launch actually reads.

use crate::relaunch::{
    extra_launch_args, first_unsafe_argument, has_dev_channels_flag, resolve_launch_model,
    valid_identifier, RelaunchError,
};
use crate::state::LaneState;

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
}
