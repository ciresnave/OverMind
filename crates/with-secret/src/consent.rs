// SPDX-License-Identifier: MIT OR Apache-2.0
//! What CireSnave is asked, and the seam for asking. Item 81 (c): "every
//! access asks YOU first, naming the secret, the requesting lane, the command
//! and the reason".
//!
//! Pulled forward from plan Task 6 (steps 1-3, verbatim) because `run.rs`
//! (Task 7) consumes these types. The Windows Hello adapter, `hello.rs`,
//! stays in Task 6: it depends on the Task 0 spike.

use chrono::{DateTime, Local, Utc};

use crate::identity::Requester;
use user_request::request::next_local_midnight;

pub struct ConsentRequest {
    pub secret: String,
    pub requester: Requester,
    pub command: String,
    pub reason: String,
    pub expires_at: DateTime<Utc>,
    /// When the person is asked; an end after the next local midnight
    /// following it is shown loudly.
    pub asked_at: DateTime<Utc>,
}

pub use user_request::consent::{Consent, ConsentOutcome};

fn clip(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(max).collect::<String>())
    }
}

pub fn prompt_text(r: &ConsentRequest) -> String {
    let who = if r.requester.managed {
        format!("lane '{}'", r.requester.role)
    } else {
        format!(
            "{} (pid {}) - NOT a registered lane",
            r.requester.role, r.requester.claude_pid
        )
    };
    let until = r.expires_at.with_timezone(&Local).format("%Y-%m-%d %H:%M");
    // board 134: the requester chooses the length, so an approval that
    // outlives today is shown in a loud, distinct form, never by habit
    let longer = r.expires_at > next_local_midnight(r.asked_at.with_timezone(&Local));
    let covers = if longer {
        format!("*** LONGER THAN TODAY: until {until} ***\nApproving covers this lane and this secret until then (or the lane restarts).")
    } else {
        format!("Approving covers this lane and this secret until {until} (or the lane restarts).")
    };
    format!(
        "with-secret: {who} asks to use secret {}.\nCommand: {}\nReason: {}\n{covers}",
        r.secret,
        clip(&r.command, 200),
        clip(&r.reason, 200),
    )
}

pub fn store_prompt_text(action: &str, secret: &str) -> String {
    format!("with-secret: {action} secret {secret} in the vault?")
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn req(cmd: &str) -> ConsentRequest {
        ConsentRequest {
            secret: "TJ_PROD_DATABASE_URL".into(),
            requester: Requester {
                role: "humboldt".into(),
                session_id: "s-1".into(),
                claude_pid: 10,
                claude_start_secs: 100,
                managed: true,
            },
            command: cmd.into(),
            reason: "run the seed migration against prod".into(),
            expires_at: Utc.with_ymd_and_hms(2026, 10, 2, 7, 0, 0).unwrap(),
            asked_at: Utc.with_ymd_and_hms(2026, 10, 2, 6, 0, 0).unwrap(),
        }
    }

    /// Board 134: an approval that outlives today is shown in a loud,
    /// distinct form, like FOREVER, so it is never approved by habit.
    #[test]
    fn an_end_after_today_is_loud_and_one_within_today_is_not() {
        let mut r = req("psql");
        let at = Local
            .with_ymd_and_hms(2026, 10, 2, 9, 0, 0)
            .single()
            .unwrap();
        r.asked_at = at.with_timezone(&Utc);
        r.expires_at = (at + chrono::Duration::hours(2)).with_timezone(&Utc);
        assert!(!prompt_text(&r).contains("LONGER THAN TODAY"));
        r.expires_at = (at + chrono::Duration::hours(20)).with_timezone(&Utc);
        let p = prompt_text(&r);
        assert!(p.contains("*** LONGER THAN TODAY"), "{p}");
        let shown = r
            .expires_at
            .with_timezone(&Local)
            .format("%Y-%m-%d %H:%M")
            .to_string();
        assert!(p.contains(&shown), "{p}");
    }

    #[test]
    fn prompt_names_secret_lane_command_reason_and_expiry() {
        let t = prompt_text(&req("psql -f seed.sql"));
        for part in [
            "TJ_PROD_DATABASE_URL",
            "humboldt",
            "psql -f seed.sql",
            "run the seed migration against prod",
            "until",
        ] {
            assert!(t.contains(part), "missing {part:?} in {t}");
        }
    }

    #[test]
    fn expiry_is_shown_with_its_date_not_as_today() {
        // ⚠️ A midnight expiry rendered as "00:00 today" reads as already past.
        let t = prompt_text(&req("x"));
        assert!(t.contains("2026-10-"), "{t}");
        assert!(!t.contains("today"), "{t}");
    }

    #[test]
    fn an_unmanaged_requester_is_flagged() {
        let mut r = req("x");
        r.requester.managed = false;
        r.requester.role = "unmanaged-claude".into();
        assert!(prompt_text(&r).contains("NOT a registered lane"));
    }

    #[test]
    fn a_long_command_is_truncated_visibly() {
        let t = prompt_text(&req(&"a".repeat(1000)));
        assert!(t.contains('…'));
        assert!(t.chars().count() < 700);
    }
}
