// SPDX-License-Identifier: MIT OR Apache-2.0
//! How a request reaches a person. Every channel answers the same way, so
//! callers can use them interchangeably.
//!
//! ⚠️ Windows Hello is a yes/no dialog with a message: it cannot ask "for how
//! long". So the grant is chosen BEFORE the channel is asked (by the
//! approver's chooser; until that lands, by the caller), and the message the
//! person approves NAMES it. Hello proves the person was present and
//! approved that text; it does not prove they read it.

use std::time::Duration;

use chrono::{DateTime, Utc};

use crate::consent::{Consent, ConsentOutcome};
use crate::request::{Grant, Request};

#[derive(Debug, PartialEq)]
pub enum Outcome {
    Approved(Grant),
    Denied,
    TimedOut,
    /// The channel cannot ask right now (no Hello, not implemented, ...).
    Unavailable(String),
    /// Not asked: the grant is over the kind's maximum. Never clamped.
    Refused(String),
}

pub trait Channel {
    /// Ask the person to approve `req` for exactly `grant`.
    fn present(&self, req: &Request, grant: &Grant, now: DateTime<Utc>, wait: Duration) -> Outcome;
}

/// The text the person approves. Summary and reason are clipped; the
/// requester line and the grant line never are (PM condition (f)).
pub fn prompt_text(req: &Request, grant: &Grant, now: DateTime<Utc>) -> String {
    let r = &req.requester;
    let who = if r.managed {
        format!("lane '{}'", r.role)
    } else {
        format!("{} (pid {}) - NOT a registered lane", r.role, r.claude_pid)
    };
    format!(
        "{}: {}
Who: {who}
Grant: {}
What: {}
Why: {}",
        req.kind.name(),
        clip(&req.subject, 200),
        grant.describe(now),
        clip(&req.summary, 300),
        clip(&req.reason, 300),
    )
}

fn clip(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(max).collect::<String>())
    }
}

/// Windows Hello, through any `Consent` (the real one is `hello::HelloConsent`).
pub struct HelloChannel<C: Consent> {
    pub consent: C,
}

impl<C: Consent> Channel for HelloChannel<C> {
    fn present(&self, req: &Request, grant: &Grant, now: DateTime<Utc>, wait: Duration) -> Outcome {
        if !grant.within(req.kind.max(), now.with_timezone(&chrono::Local)) {
            return Outcome::Refused(format!(
                "{} is over the maximum for {:?}",
                grant.describe(now),
                req.kind
            ));
        }
        match self.consent.ask(&prompt_text(req, grant, now), wait) {
            ConsentOutcome::Approved => Outcome::Approved(grant.clone()),
            ConsentOutcome::Denied => Outcome::Denied,
            ConsentOutcome::TimedOut => Outcome::TimedOut,
            ConsentOutcome::Unavailable(why) => Outcome::Unavailable(why),
        }
    }
}

/// Designed for, not built. An SMS or push answer arrives LATER and from
/// another device, so it must carry proof bound to the request (a one-time
/// code or a signature over the request id) and land through the pending
/// store (plan step #4). Until then it cannot ask.
pub struct SmsChannel;
pub struct PushChannel;

impl Channel for SmsChannel {
    fn present(&self, _: &Request, _: &Grant, _: DateTime<Utc>, _: Duration) -> Outcome {
        Outcome::Unavailable("SMS requests are designed for but not built yet".into())
    }
}

impl Channel for PushChannel {
    fn present(&self, _: &Request, _: &Grant, _: DateTime<Utc>, _: Duration) -> Outcome {
        Outcome::Unavailable("push requests are designed for but not built yet".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::request::{KindId, Requester};
    use chrono::{Duration as Span, TimeZone};
    use std::cell::RefCell;

    struct FakeConsent {
        answer: fn() -> ConsentOutcome,
        asked: RefCell<Vec<String>>,
    }
    impl FakeConsent {
        fn new(answer: fn() -> ConsentOutcome) -> Self {
            Self {
                answer,
                asked: RefCell::new(Vec::new()),
            }
        }
    }
    impl Consent for FakeConsent {
        fn ask(&self, prompt: &str, _: Duration) -> ConsentOutcome {
            self.asked.borrow_mut().push(prompt.to_string());
            (self.answer)()
        }
    }

    fn now() -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 10, 7, 16, 0, 0).unwrap()
    }

    fn req(kind: KindId, summary: &str, reason: &str, managed: bool) -> Request {
        Request {
            kind,
            subject: "subject".into(),
            summary: summary.into(),
            requester: Requester {
                role: "overmind".into(),
                session_id: "s".into(),
                claude_pid: 42,
                claude_start_secs: 1,
                managed,
            },
            reason: reason.into(),
        }
    }

    const WAIT: Duration = Duration::from_secs(1);

    #[test]
    fn an_approval_returns_exactly_the_grant_that_was_shown() {
        let ch = HelloChannel {
            consent: FakeConsent::new(|| ConsentOutcome::Approved),
        };
        let g = Grant::for_duration(Span::hours(1));
        let r = req(KindId::LaneDialogBypass, "dialog", "because", true);
        assert_eq!(
            ch.present(&r, &g, now(), WAIT),
            Outcome::Approved(g.clone())
        );
        assert!(ch.consent.asked.borrow()[0].contains(&g.describe(now())));
    }

    #[test]
    fn every_other_answer_maps_through() {
        let r = req(KindId::LaneDialogBypass, "d", "r", true);
        let g = Grant::Forever;
        for (answer, want) in [
            (
                (|| ConsentOutcome::Denied) as fn() -> ConsentOutcome,
                Outcome::Denied,
            ),
            (|| ConsentOutcome::TimedOut, Outcome::TimedOut),
            (
                || ConsentOutcome::Unavailable("x".into()),
                Outcome::Unavailable("x".into()),
            ),
        ] {
            let ch = HelloChannel {
                consent: FakeConsent::new(answer),
            };
            assert_eq!(ch.present(&r, &g, now(), WAIT), want);
        }
    }

    #[test]
    fn a_grant_over_the_kinds_maximum_is_refused_without_asking() {
        let ch = HelloChannel {
            consent: FakeConsent::new(|| ConsentOutcome::Approved),
        };
        let r = req(KindId::Secret, "TJ_PROD_DATABASE_URL", "migration", true);
        assert!(matches!(
            ch.present(&r, &Grant::Forever, now(), WAIT),
            Outcome::Refused(_)
        ));
        assert!(
            ch.consent.asked.borrow().is_empty(),
            "the person was asked anyway"
        );
    }

    #[test]
    fn the_requester_and_grant_lines_are_never_clipped_but_the_rest_is() {
        let long = "x".repeat(5000);
        let r = req(KindId::LaneDialogBypass, &long, &long, true);
        let g = Grant::Forever;
        let text = prompt_text(&r, &g, now());
        assert!(text.contains("lane 'overmind'"), "{text}");
        assert!(text.contains(&g.describe(now())), "{text}");
        assert!(
            text.len() < 1500,
            "summary and reason were not clipped: {}",
            text.len()
        );
        assert!(text.contains('…'));
    }

    #[test]
    fn an_unregistered_requester_is_named_as_such() {
        let r = req(KindId::LaneDialogBypass, "d", "r", false);
        let text = prompt_text(&r, &Grant::Forever, now());
        assert!(
            text.contains("pid 42") && text.contains("NOT a registered lane"),
            "{text}"
        );
    }

    #[test]
    fn the_prompt_names_the_kind_and_the_subject() {
        let r = req(KindId::Secret, "summary", "reason", true);
        let text = prompt_text(&r, &Grant::for_duration(Span::minutes(5)), now());
        assert!(
            text.contains(KindId::Secret.name()) && text.contains("subject"),
            "{text}"
        );
    }

    #[test]
    fn sms_and_push_are_designed_for_but_cannot_ask_yet() {
        let r = req(KindId::LaneDialogBypass, "d", "r", true);
        for ch in [&SmsChannel as &dyn Channel, &PushChannel] {
            assert!(matches!(
                ch.present(&r, &Grant::Forever, now(), WAIT),
                Outcome::Unavailable(_)
            ));
        }
    }
}
