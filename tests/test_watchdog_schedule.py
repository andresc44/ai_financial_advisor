"""Claude-authored (2026-10-25, Andres): a watchdog check is due N hours after the last check or digest."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.market.market_session import session_window
from src.market.watchdog_schedule import check_due, slot_at_or_after

ET = ZoneInfo("America/New_York")
DIGESTS = ("07:10", "16:10")  # A full trading day's pre- and post-market digests


def at(day, hhmm):
    hour, minute = (int(p) for p in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def run_day(session, interval, day=datetime(2026, 10, 21), last_check=None, last_digest=None, digests=DIGESTS):
    """The times (ET) a watchdog check runs on a trading day, with the hourly :10 digest and :45 watchdog."""
    ran = []
    planned = [at(day, d) for d in digests]
    for hour in range(24):
        for d in planned:
            if d.hour == hour:
                last_digest = d.isoformat()
        now = at(day, f"{hour:02d}:45")
        window = session_window(now, session)
        if window is None or not (window[0] <= now < window[1]):
            continue
        soon = lambda moment, name: any(timedelta(0) < d - moment <= timedelta(minutes=90) for d in planned)  # noqa: E731
        due = check_due(now, interval, session, last_check, last_digest, "Andres", digest_soon=soon)
        if due.due:
            ran.append(f"{hour:02d}:45")
            last_check = {"at": now.isoformat(), "interval": interval, "session": session}
    return ran


def test_extended_every_four_hours_follows_the_digests():
    # Andres's example: 4:45; the 7:10 digest resets the clock (next 11:45, not 8:45); 15:45 is skipped for the 16:10
    # digest; 16:45 + 4 hours is past the close, so one more at 19:45
    assert run_day("extended", 4) == ["04:45", "11:45", "19:45"]


def test_the_last_check_is_skipped_when_one_ran_within_an_hour_and_a_half():
    # The next check after a 17:10 digest (counted as 17:45) would be 21:45, past the close, so 19:45 runs
    ran = run_day("extended", 4, digests=("07:10", "17:10"))
    assert ran == ["04:45", "11:45", "19:45"]
    ran = run_day("extended", 4, digests=("07:10", "18:10"))  # Digest at 18:10 counts as 18:45, 1 hour before 19:45
    assert ran == ["04:45", "11:45", "15:45"]  # 15:45 is 2h25 before the digest, so it runs; nothing at 19:45


def test_two_hour_checks_skip_the_one_before_each_digest():
    assert run_day("extended", 2) == ["04:45", "09:45", "11:45", "13:45", "18:45"]


def test_hourly_checks_run_every_hour_except_where_a_digest_covered_it():
    ran = run_day("extended", 1)
    assert "06:45" in ran and "15:45" in ran  # The skip before a digest is not for hourly
    assert "07:45" not in ran and "16:45" not in ran  # The digest counted as that hour's check


def test_regular_session_follows_the_digest_too():
    # The 7:10 digest starts the clock, so there is no 9:45 check; 11:45 runs; 15:45 is skipped for the 16:10 digest
    assert run_day("regular", 4) == ["11:45"]


def test_the_first_check_of_a_day_ignores_yesterdays_clock():
    yesterday = {"at": at(datetime(2026, 10, 20), "19:45").isoformat(), "interval": 4, "session": "extended"}
    assert run_day("extended", 4, last_check=yesterday)[0] == "04:45"


def test_changing_the_interval_or_session_makes_a_user_due_at_once():
    now = at(datetime(2026, 10, 21), "12:45")
    recent = {"at": at(datetime(2026, 10, 21), "11:45").isoformat(), "interval": 4, "session": "extended"}
    assert not check_due(now, 4, "extended", recent, None, "Andres", digest_soon=lambda *a: False).due
    assert check_due(now, 2, "extended", recent, None, "Andres", digest_soon=lambda *a: False).reason == \
        "interval or session changed"
    assert check_due(now, 4, "regular", recent, None, "Andres", digest_soon=lambda *a: False).due


def test_off_and_a_digest_slot():
    assert not check_due(at(datetime(2026, 10, 21), "12:45"), 0, "extended", None, None).due
    assert slot_at_or_after(at(datetime(2026, 10, 21), "07:10")) == at(datetime(2026, 10, 21), "07:45").astimezone(
        slot_at_or_after(at(datetime(2026, 10, 21), "07:10")).tzinfo)
    assert slot_at_or_after(at(datetime(2026, 10, 21), "07:45")) == slot_at_or_after(at(datetime(2026, 10, 21), "07:30"))


def test_a_24_7_clock_rolls_on_from_the_last_check_or_digest():
    now = datetime(2026, 10, 24, 12, 45, tzinfo=ET)  # A Saturday: no digest
    last = {"at": datetime(2026, 10, 24, 9, 45, tzinfo=ET).isoformat(), "interval": 4, "session": "24_7"}
    no_digest = lambda *a: False  # noqa: E731
    assert not check_due(now, 4, "24_7", last, None, "Andres", digest_soon=no_digest).due  # 3 hours on
    assert check_due(now + timedelta(hours=1), 4, "24_7", last, None, "Andres", digest_soon=no_digest).due
    digest = datetime(2026, 10, 24, 11, 10, tzinfo=ET).isoformat()  # A digest at 11:10 counts as 11:45
    assert not check_due(now + timedelta(hours=1), 4, "24_7", last, digest, "Andres", digest_soon=no_digest).due
    assert check_due(datetime(2026, 10, 24, 15, 45, tzinfo=ET), 4, "24_7", last, digest, "Andres",
                     digest_soon=no_digest).due


def test_a_long_interval_on_a_regular_session_has_no_check():
    # The clock from the 7:10 digest runs out at 15:45, right before the 16:10 digest, which skips it (Andres, 2026-10-25)
    assert run_day("regular", 8) == [] and run_day("regular", 24) == []
    assert run_day("extended", 8) == ["04:45", "19:45"]


def test_no_tripwire_check_within_an_hour_of_a_digest():
    from src.market.watchdog_schedule import digest_just_ran

    now = at(datetime(2026, 10, 21), "07:45")
    assert digest_just_ran(now, at(datetime(2026, 10, 21), "07:35").isoformat())
    assert not digest_just_ran(now, at(datetime(2026, 10, 21), "06:35").isoformat())  # Exactly an hour ago: not "just"
    assert not digest_just_ran(now, None)
    assert not digest_just_ran(now, at(datetime(2026, 10, 21), "08:10").isoformat())  # A digest still to come
