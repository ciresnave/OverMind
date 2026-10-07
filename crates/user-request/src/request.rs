// SPDX-License-Identifier: MIT OR Apache-2.0
//! What is asked, by whom, and for how long it may be granted.
//!
//! ⚠️ PM condition (a), 2026-10-04: the kinds are COMPILED IN. `KindId` is a
//! closed enum, and each kind's maximum grant is decided here, so no lane can
//! define a kind with a larger maximum. The kinds that can be granted
//! FOREVER are listed in the crate README and must stay listed there.

use chrono::{DateTime, Duration, Local, TimeZone, Utc};
use serde::{Deserialize, Serialize};

/// Who is asking. ⚠️ The caller takes it from the OS process table and
/// lane-state files, never from an argument (with-secret's `identity.rs`).
/// Moved unchanged from with-secret, which re-exports it: its approval cache
/// signs this exact serialised shape.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Requester {
    pub role: String,
    pub session_id: String,
    pub claude_pid: u32,
    pub claude_start_secs: u64,
    pub managed: bool,
}

/// The longest grant a kind allows.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MaxGrant {
    /// No later than the next local midnight (with-secret's ruling for
    /// secrets: "an approval now isn't still valid tomorrow").
    UntilLocalMidnight,
    /// No longer than this from the moment it is granted.
    For(Duration),
    /// Anything, including forever.
    Forever,
}

/// Whether an approval belongs to the asking process only, or to anyone.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scope {
    /// One requester (role, session, process); a lane restart voids it.
    ThisRequester,
    /// Any requester.
    AnyRequester,
}

/// The closed set of request kinds.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum KindId {
    /// with-secret: release one secret to one command.
    Secret,
    /// lane-restart: auto-answer a lane's startup dialog without asking.
    LaneDialogBypass,
}

impl KindId {
    pub fn name(self) -> &'static str {
        match self {
            KindId::Secret => "use a secret",
            KindId::LaneDialogBypass => "auto-answer a lane startup dialog",
        }
    }

    pub fn max(self) -> MaxGrant {
        match self {
            KindId::Secret => MaxGrant::UntilLocalMidnight,
            KindId::LaneDialogBypass => MaxGrant::Forever,
        }
    }

    pub fn scope(self) -> Scope {
        match self {
            KindId::Secret => Scope::ThisRequester,
            KindId::LaneDialogBypass => Scope::AnyRequester,
        }
    }
}

/// What the approver grants.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Grant {
    /// From the moment of approval, for this long (seconds).
    For { secs: i64 },
    /// Until this moment.
    Until(DateTime<Utc>),
    /// Until revoked.
    Forever,
}

impl Grant {
    pub fn for_duration(d: Duration) -> Self {
        Grant::For {
            secs: d.num_seconds(),
        }
    }

    /// When it ends if granted at `now`; `None` for forever.
    pub fn expires_at(&self, now: DateTime<Utc>) -> Option<DateTime<Utc>> {
        match self {
            Grant::For { secs } => Some(now + Duration::seconds(*secs)),
            Grant::Until(t) => Some(*t),
            Grant::Forever => None,
        }
    }

    /// Is it within `max`, granted at `now` (`local` decides midnight)?
    pub fn within<Tz: TimeZone>(&self, max: MaxGrant, now: DateTime<Tz>) -> bool {
        let utc = now.with_timezone(&Utc);
        let end = self.expires_at(utc);
        // a grant that ends before it starts grants nothing
        if end.is_some_and(|e| e <= utc) {
            return false;
        }
        match (max, end) {
            (MaxGrant::Forever, _) => true,
            (_, None) => false,
            (MaxGrant::For(d), Some(e)) => e <= utc + d,
            (MaxGrant::UntilLocalMidnight, Some(e)) => e <= next_local_midnight(now),
        }
    }

    /// One line for the person, never clipped. FOREVER is loud on purpose
    /// (PM condition: Forever grants are shown in a distinct, loud form).
    pub fn describe(&self, now: DateTime<Utc>) -> String {
        let local = |t: DateTime<Utc>| t.with_timezone(&Local).format("%Y-%m-%d %H:%M %Z");
        match self {
            Grant::For { secs } => {
                let (h, m) = (secs / 3600, (secs % 3600) / 60);
                format!(
                    "for {h}h {m:02}m, until {}",
                    local(now + Duration::seconds(*secs))
                )
            }
            Grant::Until(t) => format!("until {}", local(*t)),
            Grant::Forever => "*** FOREVER (until revoked) ***".to_string(),
        }
    }
}

/// The next local midnight after `now`, in UTC.
pub fn next_local_midnight<Tz: TimeZone>(now: DateTime<Tz>) -> DateTime<Utc> {
    let tomorrow = now
        .date_naive()
        .succ_opt()
        .expect("a date after today exists");
    let midnight = tomorrow.and_hms_opt(0, 0, 0).expect("midnight exists");
    // On a DST-gap day midnight may not exist locally; take the earliest
    // valid instant after it rather than guessing a later one.
    now.timezone()
        .from_local_datetime(&midnight)
        .earliest()
        .unwrap_or_else(|| now.timezone().from_utc_datetime(&midnight))
        .with_timezone(&Utc)
}

/// One request to one person.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Request {
    pub kind: KindId,
    /// What it is about: a secret's name, a dialog handler's id, a plan hash.
    pub subject: String,
    /// What the person is shown about it (clipped in the prompt).
    pub summary: String,
    pub requester: Requester,
    /// Why, in the requester's words (clipped in the prompt).
    pub reason: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::FixedOffset;

    /// 2026-10-07 h:m in Phoenix (UTC-7, no DST), host time zone irrelevant.
    fn at(h: u32, m: u32) -> DateTime<FixedOffset> {
        FixedOffset::west_opt(7 * 3600)
            .unwrap()
            .with_ymd_and_hms(2026, 10, 7, h, m, 0)
            .unwrap()
    }

    #[test]
    fn next_local_midnight_is_in_the_given_time_zone() {
        assert_eq!(
            next_local_midnight(at(18, 0)),
            Utc.with_ymd_and_hms(2026, 10, 8, 7, 0, 0).unwrap()
        );
    }

    #[test]
    fn a_secret_may_be_granted_until_midnight_but_never_beyond() {
        let now = at(18, 0);
        let max = KindId::Secret.max();
        assert!(Grant::Until(next_local_midnight(now)).within(max, now));
        assert!(Grant::for_duration(Duration::hours(6)).within(max, now));
        assert!(!Grant::for_duration(Duration::hours(6) + Duration::seconds(1)).within(max, now));
        assert!(!Grant::Forever.within(max, now));
    }

    #[test]
    fn a_dialog_bypass_may_be_granted_forever() {
        assert!(Grant::Forever.within(KindId::LaneDialogBypass.max(), at(9, 0)));
    }

    #[test]
    fn a_for_maximum_is_measured_from_the_moment_of_granting() {
        let now = at(9, 0);
        let max = MaxGrant::For(Duration::hours(1));
        let utc = now.with_timezone(&Utc);
        assert!(Grant::for_duration(Duration::hours(1)).within(max, now));
        assert!(!Grant::for_duration(Duration::hours(1) + Duration::seconds(1)).within(max, now));
        assert!(Grant::Until(utc + Duration::hours(1)).within(max, now));
        assert!(!Grant::Until(utc + Duration::hours(2)).within(max, now));
        assert!(!Grant::Forever.within(max, now));
    }

    #[test]
    fn a_grant_that_ends_before_it_starts_is_never_within() {
        let now = at(9, 0);
        let past = now.with_timezone(&Utc) - Duration::minutes(1);
        assert!(!Grant::Until(past).within(MaxGrant::Forever, now));
        assert!(!Grant::For { secs: 0 }.within(MaxGrant::Forever, now));
    }

    #[test]
    fn expiry_of_each_grant() {
        let now = at(9, 0).with_timezone(&Utc);
        assert_eq!(
            Grant::for_duration(Duration::hours(1)).expires_at(now),
            Some(now + Duration::hours(1))
        );
        assert_eq!(Grant::Until(now).expires_at(now), Some(now));
        assert_eq!(Grant::Forever.expires_at(now), None);
    }

    #[test]
    fn forever_is_described_loudly_and_nothing_else_is() {
        let now = at(9, 0).with_timezone(&Utc);
        assert!(Grant::Forever.describe(now).contains("FOREVER"));
        let hour = Grant::for_duration(Duration::hours(1)).describe(now);
        assert!(
            hour.contains("until") && !hour.contains("FOREVER"),
            "{hour}"
        );
    }
}
