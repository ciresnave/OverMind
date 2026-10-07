// SPDX-License-Identifier: MIT OR Apache-2.0
//! The approver's chooser: the PERSON picks how long a grant lasts, before
//! Windows Hello approves text that names it (plan step #3, PM ruling
//! 2026-10-07: a separate console window).
//!
//! - The requester proposes a grant; the person may accept it with Enter,
//!   pick a preset, type a duration or a date, or refuse.
//! - FOREVER, or an end more than 30 days away, must be TYPED (PM condition
//!   (b)): the word `FOREVER` or the end's date. Enter never accepts one.
//! - A choice over the kind's maximum is refused, never clamped.
//!
//! ⚠️ Honest limit: this stops accidents and a lane that merely echoes text.
//! It does NOT stop a hostile same-user process driving the window
//! (SendInput, AttachConsole).

use std::time::{Duration, Instant};

use chrono::{DateTime, Duration as Span, NaiveDate, NaiveDateTime, TimeZone, Utc};
use serde::{Deserialize, Serialize};

use crate::channel::{prompt_text, Channel, HelloChannel, Outcome};
use crate::consent::Consent;
use crate::request::{next_local_midnight, Grant, MaxGrant, Request};

/// An end further away than this must be typed (PM condition (b)).
pub const TYPED_BEYOND_DAYS: i64 = 30;

/// Entries the person may get wrong before the chooser gives up (denied).
pub const MAX_TRIES: usize = 3;

/// Where the person reads and types. In production: the chooser window's
/// own console (`CONIN$`/`CONOUT$`), never the requester's stdin.
pub trait ConsoleIo {
    fn say(&mut self, text: &str);
    /// One line typed by the person, without its line ending; `None` when
    /// the input is closed.
    fn line(&mut self, prompt: &str) -> Option<String>;
}

/// What the person chose.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum Choice {
    Chosen(Grant),
    Denied,
    TimedOut,
    Unavailable(String),
    /// Not asked: the proposal is over the kind's maximum, or malformed.
    Refused(String),
}

/// One way to let the person choose.
pub trait Chooser {
    fn choose(&self, req: &Request, proposed: &Grant, wait: Duration) -> Choice;
}

/// Must `g`, shown at `now`, be typed? FOREVER, or an end more than
/// `TYPED_BEYOND_DAYS` away.
pub fn must_be_typed(g: &Grant, now: DateTime<Utc>) -> bool {
    match g.end_at(now) {
        Ok(None) => true,
        Ok(Some(end)) => now
            .checked_add_signed(Span::days(TYPED_BEYOND_DAYS))
            .is_none_or(|limit| end > limit),
        // never within any maximum, so refused before this matters
        Err(_) => false,
    }
}

/// One entry the person typed.
enum Entry {
    Refuse,
    /// The grant, and whether the entry WAS the literal that must be typed
    /// (`FOREVER`, or a date).
    Pick(Grant, bool),
}

fn parse<Tz: TimeZone>(typed: &str, proposed: &Grant, now: &DateTime<Tz>) -> Result<Entry, String> {
    let t = typed.trim();
    if t.eq_ignore_ascii_case("n") || t.eq_ignore_ascii_case("no") {
        return Ok(Entry::Refuse);
    }
    match t {
        "" => return Ok(Entry::Pick(proposed.clone(), false)),
        "FOREVER" => return Ok(Entry::Pick(Grant::Forever, true)),
        "today" => {
            return Ok(Entry::Pick(
                Grant::Until(next_local_midnight(now.clone())),
                false,
            ))
        }
        _ => {}
    }
    if let Some(unit) = t.chars().last().and_then(|u| match u {
        'm' => Some(60),
        'h' => Some(3600),
        'd' => Some(86_400),
        _ => None,
    }) {
        let n = &t[..t.len() - 1];
        if !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()) {
            let secs = n
                .parse::<i64>()
                .ok()
                .and_then(|n| n.checked_mul(unit))
                .ok_or_else(|| format!("{t} is too long to represent"))?;
            return Ok(Entry::Pick(Grant::For { secs }, false));
        }
    }
    let naive = NaiveDateTime::parse_from_str(t, "%Y-%m-%d %H:%M")
        .ok()
        .or_else(|| {
            NaiveDate::parse_from_str(t, "%Y-%m-%d")
                .ok()
                .and_then(|d| d.and_hms_opt(0, 0, 0))
        })
        .ok_or_else(|| format!("'{}' is not understood", crate::channel::clean(t)))?;
    // an hour that repeats (clocks going back) means its earlier instant:
    // short, never long
    let end = now
        .timezone()
        .from_local_datetime(&naive)
        .earliest()
        .ok_or_else(|| format!("{t} does not exist in this time zone"))?;
    Ok(Entry::Pick(Grant::Until(end.with_timezone(&Utc)), true))
}

fn menu(forever: bool) -> String {
    let mut lines = vec![
        "",
        "Choose how long to grant it:",
        "  Enter             the proposed grant above",
        "  5m, 1h, today     a short grant",
        "  <n>m, <n>h, <n>d  that long from now",
        "  YYYY-MM-DD        until 00:00 that day (or YYYY-MM-DD HH:MM)",
    ];
    if forever {
        lines.push("  FOREVER           until revoked");
    }
    lines.push("  n                 refuse");
    format!(
        "{}\nFOREVER, or an end more than {TYPED_BEYOND_DAYS} days away, must be typed.\n\
         Windows Hello then asks you to approve the grant you chose.",
        lines.join("\n")
    )
}

/// Ask the person through `io`. `clock` is read when the window opens and
/// again at every entry; its time zone decides what a typed date means.
pub fn run<Tz: TimeZone>(
    io: &mut dyn ConsoleIo,
    req: &Request,
    proposed: &Grant,
    clock: impl Fn() -> DateTime<Tz>,
) -> Choice
where
    Tz::Offset: std::fmt::Display,
{
    let now = clock();
    let utc = now.with_timezone(&Utc);
    let max = req.kind.max();
    if !proposed.within(max, now.clone()) {
        return Choice::Refused(format!(
            "the proposed grant, {}, is over the maximum for {:?}",
            proposed.describe(utc),
            req.kind
        ));
    }
    io.say(&prompt_text(req, proposed, utc));
    io.say(&menu(max == MaxGrant::Forever));
    for _ in 0..MAX_TRIES {
        let Some(typed) = io.line("Your choice: ") else {
            return Choice::Denied;
        };
        // review 2: everything about this entry is measured when it is made
        let now = clock();
        let utc = now.with_timezone(&Utc);
        let (grant, was_typed) = match parse(&typed, proposed, &now) {
            Ok(Entry::Refuse) => return Choice::Denied,
            Ok(Entry::Pick(g, t)) => (g, t),
            Err(why) => {
                io.say(&format!("Not accepted: {why}."));
                continue;
            }
        };
        // PM condition (2): refused, never shortened to fit
        if !grant.within(max, now.clone()) {
            io.say(&format!(
                "Refused: {} is over the maximum for this request, or already over. \
                 Nothing was shortened to fit.",
                grant.describe(utc)
            ));
            continue;
        }
        // PM condition (b): Enter, a preset or a duration never accepts these
        if must_be_typed(&grant, utc) && !was_typed {
            let want = match grant.end_at(utc) {
                Ok(Some(end)) => end
                    .with_timezone(&now.timezone())
                    .format("%Y-%m-%d")
                    .to_string(),
                _ => "FOREVER".to_string(),
            };
            io.say(&format!(
                "{} must be typed: type {want} to confirm.",
                grant.describe(utc)
            ));
            match io.line(&format!("type {want}: ")) {
                None => return Choice::Denied,
                Some(t) if t.trim() == want => {}
                Some(_) => {
                    io.say("Not confirmed.");
                    continue;
                }
            }
        }
        // review 2: a length becomes the absolute end it has as chosen, so
        // Hello cannot stretch it and a date typed back is the end granted
        return Choice::Chosen(match (&grant, grant.end_at(utc)) {
            (Grant::For { .. }, Ok(Some(end))) => Grant::Until(end),
            _ => grant,
        });
    }
    io.say("No choice was made: refused.");
    Choice::Denied
}

/// The chooser, then Windows Hello on the grant the person CHOSE.
pub struct ChooserChannel<Ch: Chooser, C: Consent> {
    pub chooser: Ch,
    pub hello: HelloChannel<C>,
}

impl<Ch: Chooser, C: Consent> Channel for ChooserChannel<Ch, C> {
    fn present(&self, req: &Request, proposed: &Grant, wait: Duration) -> Outcome {
        let start = Instant::now();
        match self.chooser.choose(req, proposed, wait) {
            // the chooser and Hello share one wait
            Choice::Chosen(g) => match wait.checked_sub(start.elapsed()) {
                Some(left) if !left.is_zero() => self.hello.present(req, &g, left),
                _ => Outcome::TimedOut,
            },
            Choice::Denied => Outcome::Denied,
            Choice::TimedOut => Outcome::TimedOut,
            Choice::Unavailable(why) => Outcome::Unavailable(why),
            Choice::Refused(why) => Outcome::Refused(why),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::consent::ConsentOutcome;
    use crate::request::{KindId, Requester};
    use chrono::FixedOffset;
    use std::cell::RefCell;
    use std::collections::VecDeque;

    /// 2026-10-07 h:m in Phoenix (UTC-7, no DST).
    fn at(h: u32, m: u32) -> DateTime<FixedOffset> {
        FixedOffset::west_opt(7 * 3600)
            .unwrap()
            .with_ymd_and_hms(2026, 10, 7, h, m, 0)
            .unwrap()
    }

    fn req(kind: KindId) -> Request {
        Request {
            kind,
            subject: "TJ_DB".into(),
            summary: "run: psql".into(),
            requester: Requester {
                role: "overmind".into(),
                session_id: "s".into(),
                claude_pid: 7,
                claude_start_secs: 1,
                managed: true,
            },
            reason: "migrate".into(),
        }
    }

    /// The person's typing, scripted; records what they were shown.
    struct Script {
        typed: VecDeque<&'static str>,
        shown: String,
        asked: usize,
    }

    fn script(lines: &[&'static str]) -> Script {
        Script {
            typed: lines.iter().copied().collect(),
            shown: String::new(),
            asked: 0,
        }
    }

    impl ConsoleIo for Script {
        fn say(&mut self, text: &str) {
            self.shown.push_str(text);
            self.shown.push('\n');
        }
        fn line(&mut self, prompt: &str) -> Option<String> {
            self.asked += 1;
            self.shown.push_str(prompt);
            self.shown.push('\n');
            self.typed.pop_front().map(str::to_string)
        }
    }

    fn hour() -> Grant {
        Grant::for_duration(Span::hours(1))
    }

    fn utc(t: DateTime<FixedOffset>) -> DateTime<Utc> {
        t.with_timezone(&Utc)
    }

    #[test]
    fn enter_accepts_a_short_proposal() {
        let mut io = script(&[""]);
        assert_eq!(
            run(&mut io, &req(KindId::Secret), &hour(), || at(9, 0)),
            Choice::Chosen(Grant::Until(utc(at(10, 0))))
        );
        // the person was shown who asks and the proposal
        assert!(io.shown.contains("Who: lane 'overmind'"), "{}", io.shown);
        assert!(io.shown.contains("for 1h 00m 00s"), "{}", io.shown);
    }

    #[test]
    fn presets_durations_and_dates_choose_what_they_say() {
        let now = at(9, 0);
        let u = utc(now);
        let cases: [(&str, Grant); 7] = [
            ("5m", Grant::Until(u + Span::minutes(5))),
            ("1h", Grant::Until(u + Span::hours(1))),
            ("90m", Grant::Until(u + Span::minutes(90))),
            ("3d", Grant::Until(u + Span::days(3))),
            ("today", Grant::Until(next_local_midnight(now))),
            // a bare date means its 00:00 (short, never long)
            (
                "2026-10-20",
                Grant::Until(Utc.with_ymd_and_hms(2026, 10, 20, 7, 0, 0).unwrap()),
            ),
            (
                "2026-10-20 17:30",
                Grant::Until(Utc.with_ymd_and_hms(2026, 10, 21, 0, 30, 0).unwrap()),
            ),
        ];
        for (typed, want) in cases {
            let mut io = script(&[typed]);
            assert_eq!(
                run(&mut io, &req(KindId::Secret), &hour(), || now),
                Choice::Chosen(want),
                "{typed}"
            );
        }
    }

    #[test]
    fn exactly_thirty_days_need_not_be_typed_but_one_second_more_must() {
        let now = utc(at(9, 0));
        let days = Span::days(TYPED_BEYOND_DAYS);
        assert!(!must_be_typed(&Grant::for_duration(days), now));
        assert!(must_be_typed(
            &Grant::for_duration(days + Span::seconds(1)),
            now
        ));
        assert!(!must_be_typed(&Grant::Until(now + days), now));
        assert!(must_be_typed(
            &Grant::Until(now + days + Span::seconds(1)),
            now
        ));
        assert!(must_be_typed(&Grant::Forever, now));
        assert!(!must_be_typed(&hour(), now));
    }

    /// Review 2: a length is measured from the moment the person CHOSE it,
    /// and kept as that absolute end, so Hello cannot stretch it and the
    /// date typed back is the end granted.
    #[test]
    fn a_length_ends_where_it_was_chosen_not_where_the_window_opened() {
        use std::cell::Cell;
        let reads = Cell::new(0);
        // the window opens at 23:50; the person answers at 00:05 the next day
        let clock = || {
            reads.set(reads.get() + 1);
            if reads.get() == 1 {
                at(23, 50)
            } else {
                at(23, 50) + Span::minutes(15)
            }
        };
        let chosen = utc(at(23, 50)) + Span::minutes(15);
        let mut io = script(&["45d", "2026-11-22"]);
        assert_eq!(
            run(&mut io, &req(KindId::Secret), &hour(), clock),
            Choice::Chosen(Grant::Until(chosen + Span::days(45)))
        );
    }

    /// PM condition (b): Enter never accepts FOREVER.
    #[test]
    fn a_forever_proposal_is_not_accepted_by_enter() {
        let mut io = script(&["", "yes", "n"]);
        assert_eq!(
            run(
                &mut io,
                &req(KindId::LaneDialogBypass),
                &Grant::Forever,
                || at(9, 0)
            ),
            Choice::Denied
        );
        assert!(io.shown.contains("type FOREVER"), "{}", io.shown);
    }

    #[test]
    fn a_forever_proposal_is_accepted_once_the_word_is_typed() {
        let mut io = script(&["", "FOREVER"]);
        assert_eq!(
            run(
                &mut io,
                &req(KindId::LaneDialogBypass),
                &Grant::Forever,
                || at(9, 0)
            ),
            Choice::Chosen(Grant::Forever)
        );
    }

    #[test]
    fn typing_forever_as_the_choice_is_typing_it() {
        let mut io = script(&["FOREVER"]);
        assert_eq!(
            run(&mut io, &req(KindId::LaneDialogBypass), &hour(), || at(
                9, 0
            )),
            Choice::Chosen(Grant::Forever)
        );
        assert_eq!(io.asked, 1);
    }

    /// The word, exactly: a habit-typed lower-case answer is not it.
    #[test]
    fn forever_must_be_typed_in_capitals() {
        let mut io = script(&["forever", "", "forever", "n"]);
        assert_eq!(
            run(
                &mut io,
                &req(KindId::LaneDialogBypass),
                &Grant::Forever,
                || at(9, 0)
            ),
            Choice::Denied
        );
    }

    #[test]
    fn a_duration_over_thirty_days_needs_its_end_date_typed() {
        let now = at(9, 0);
        let want = Grant::Until(utc(now) + Span::days(45));
        // 2026-10-07 09:00 -07 + 45d = 2026-11-21
        let mut io = script(&["45d", "2026-11-22", "45d", "2026-11-21"]);
        assert_eq!(
            run(&mut io, &req(KindId::Secret), &hour(), || now),
            Choice::Chosen(want)
        );
        assert!(io.shown.contains("2026-11-21"), "{}", io.shown);
    }

    #[test]
    fn a_long_proposal_is_not_accepted_by_enter() {
        let long = Grant::for_duration(Span::days(400));
        let mut io = script(&["", "y", "n"]);
        assert_eq!(
            run(&mut io, &req(KindId::Secret), &long, || at(9, 0)),
            Choice::Denied
        );
    }

    #[test]
    fn a_typed_date_over_thirty_days_is_typed_already() {
        let mut io = script(&["2027-01-15"]);
        assert_eq!(
            run(&mut io, &req(KindId::Secret), &hour(), || at(9, 0)),
            Choice::Chosen(Grant::Until(
                Utc.with_ymd_and_hms(2027, 1, 15, 7, 0, 0).unwrap()
            ))
        );
        assert_eq!(io.asked, 1);
    }

    /// PM condition (2): over the maximum is refused, never clamped.
    #[test]
    fn a_choice_over_the_maximum_is_refused_not_clamped() {
        let mut io = script(&["FOREVER", "FOREVER", "n"]);
        assert_eq!(
            run(&mut io, &req(KindId::Secret), &hour(), || at(9, 0)),
            Choice::Denied
        );
        assert!(io.shown.contains("over the maximum"), "{}", io.shown);
    }

    /// FOREVER is offered only where the kind allows it.
    #[test]
    fn forever_is_offered_only_for_kinds_that_allow_it() {
        let offered = |kind| {
            let mut io = script(&["n"]);
            run(&mut io, &req(kind), &hour(), || at(9, 0));
            io.shown.contains("  FOREVER ")
        };
        assert!(!offered(KindId::Secret));
        assert!(offered(KindId::LaneDialogBypass));
    }

    /// New York 2026-11-01 01:30 happens twice (EDT, then EST): it means the
    /// earlier one (short, never long).
    #[test]
    fn a_local_time_that_repeats_means_the_earlier_one() {
        use chrono_tz::America::New_York;
        let now = New_York.with_ymd_and_hms(2026, 10, 31, 12, 0, 0).unwrap();
        let mut io = script(&["2026-11-01 01:30"]);
        assert_eq!(
            run(&mut io, &req(KindId::Secret), &hour(), || now),
            Choice::Chosen(Grant::Until(
                Utc.with_ymd_and_hms(2026, 11, 1, 5, 30, 0).unwrap()
            ))
        );
    }

    #[test]
    fn a_proposal_over_the_maximum_is_refused_without_asking() {
        let mut io = script(&[""]);
        let got = run(&mut io, &req(KindId::Secret), &Grant::Forever, || at(9, 0));
        assert!(matches!(got, Choice::Refused(_)), "{got:?}");
        assert_eq!(io.asked, 0);
    }

    #[test]
    fn past_zero_and_impossible_choices_are_refused() {
        for typed in ["0m", "2026-10-01", "99999999999999d", "-5m"] {
            let mut io = script(&[typed, "n"]);
            assert_eq!(
                run(&mut io, &req(KindId::Secret), &hour(), || at(9, 0)),
                Choice::Denied,
                "{typed}"
            );
        }
    }

    #[test]
    fn three_bad_entries_deny() {
        let mut io = script(&["what", "2026-13-01", "1y", ""]);
        assert_eq!(
            run(&mut io, &req(KindId::Secret), &hour(), || at(9, 0)),
            Choice::Denied
        );
        assert_eq!(io.asked, MAX_TRIES);
    }

    #[test]
    fn a_closed_window_denies() {
        let mut io = script(&[]);
        assert_eq!(
            run(&mut io, &req(KindId::Secret), &hour(), || at(9, 0)),
            Choice::Denied
        );
    }

    #[test]
    fn refusing_is_n() {
        for typed in ["n", "no", "N"] {
            let mut io = script(&[typed]);
            assert_eq!(
                run(&mut io, &req(KindId::Secret), &hour(), || at(9, 0)),
                Choice::Denied,
                "{typed}"
            );
        }
    }

    /// Santiago 2026-09-06 00:30 does not exist (00:00 -> 01:00).
    #[test]
    fn a_local_time_that_does_not_exist_is_refused() {
        use chrono_tz::America::Santiago;
        let now = Santiago.with_ymd_and_hms(2026, 9, 5, 18, 0, 0).unwrap();
        let mut io = script(&["2026-09-06 00:30", "n"]);
        assert_eq!(
            run(&mut io, &req(KindId::Secret), &hour(), || now),
            Choice::Denied
        );
        assert!(io.shown.contains("does not exist"), "{}", io.shown);
    }

    struct Fixed(Choice);
    impl Chooser for Fixed {
        fn choose(&self, _: &Request, _: &Grant, _: Duration) -> Choice {
            self.0.clone()
        }
    }

    struct Hello {
        answer: fn() -> ConsentOutcome,
        asked: RefCell<Vec<String>>,
    }
    impl Consent for &Hello {
        fn ask(&self, prompt: &str, _: Duration) -> ConsentOutcome {
            self.asked.borrow_mut().push(prompt.to_string());
            (self.answer)()
        }
    }

    fn hello(answer: fn() -> ConsentOutcome) -> Hello {
        Hello {
            answer,
            asked: RefCell::new(Vec::new()),
        }
    }

    const WAIT: Duration = Duration::from_secs(60);

    /// PM condition (3): Hello approves text naming the CHOSEN grant.
    #[test]
    fn hello_is_asked_about_the_chosen_grant_not_the_proposal() {
        let h = hello(|| ConsentOutcome::Approved);
        let five = Grant::for_duration(Span::minutes(5));
        let ch = ChooserChannel {
            chooser: Fixed(Choice::Chosen(five)),
            hello: HelloChannel::new(&h),
        };
        let before = Utc::now();
        let got = ch.present(&req(KindId::Secret), &hour(), WAIT);
        let Outcome::Approved(ap) = got else {
            panic!("{got:?}")
        };
        let end = ap.expires_at.unwrap();
        assert!(end <= Utc::now() + Span::minutes(5) && end >= before + Span::minutes(5));
        let asked = h.asked.borrow();
        assert_eq!(asked.len(), 1);
        assert!(asked[0].contains("for 0h 05m 00s"), "{}", asked[0]);
    }

    #[test]
    fn hello_is_not_asked_unless_the_person_chose() {
        for (c, want) in [
            (Choice::Denied, Outcome::Denied),
            (Choice::TimedOut, Outcome::TimedOut),
            (
                Choice::Unavailable("x".into()),
                Outcome::Unavailable("x".into()),
            ),
            (Choice::Refused("y".into()), Outcome::Refused("y".into())),
        ] {
            let h = hello(|| ConsentOutcome::Approved);
            let ch = ChooserChannel {
                chooser: Fixed(c),
                hello: HelloChannel::new(&h),
            };
            assert_eq!(ch.present(&req(KindId::Secret), &hour(), WAIT), want);
            assert!(h.asked.borrow().is_empty());
        }
    }

    /// Defence in depth: a chooser that returns an over-maximum grant (a
    /// forged child answer) is still refused by Hello's own check.
    #[test]
    fn an_over_maximum_choice_is_refused_before_hello() {
        let h = hello(|| ConsentOutcome::Approved);
        let ch = ChooserChannel {
            chooser: Fixed(Choice::Chosen(Grant::Forever)),
            hello: HelloChannel::new(&h),
        };
        let got = ch.present(&req(KindId::Secret), &hour(), WAIT);
        assert!(matches!(got, Outcome::Refused(_)), "{got:?}");
        assert!(h.asked.borrow().is_empty());
    }

    /// A chooser that ate the whole wait leaves Hello none.
    #[test]
    fn a_chooser_that_used_the_wait_times_out() {
        struct Slow;
        impl Chooser for Slow {
            fn choose(&self, _: &Request, g: &Grant, _: Duration) -> Choice {
                std::thread::sleep(Duration::from_millis(50));
                Choice::Chosen(g.clone())
            }
        }
        let h = hello(|| ConsentOutcome::Approved);
        let ch = ChooserChannel {
            chooser: Slow,
            hello: HelloChannel::new(&h),
        };
        assert_eq!(
            ch.present(&req(KindId::Secret), &hour(), Duration::from_millis(10)),
            Outcome::TimedOut
        );
        assert!(h.asked.borrow().is_empty());
    }
}
