"""
Tests for the earnings reminders (src/telegram_alert/earnings_reminder.py). Written by Claude (Claude Opus 5.5 via
Claude Code) on 2026-10-21, at Andres's request. The holdings and earnings below are made up.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import src.digest.digest_payload as dp
import src.telegram_alert.earnings_reminder as er

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 21, 17, 0, tzinfo=ET)  # Wednesday after the close


def context(name, *events):
    return {"context": {"profile": {"name": name},
                        "catalysts_and_earnings": {"upcoming_earnings": [{"date": d, "hour": h} for d, h in events]}}}


STATE = {"users": {"Tester": {"symbols": {
    "AAA": {"recommendation": "hold", "next_earnings_date": "2026-10-22"},
    "BBB": {"recommendation": "risky"},
    "CCC": {"recommendation": "risk_alert", "next_earnings_date": "2026-10-22"},  # Not in the cache: date only
    "DDD": {"recommendation": "hold"},
}}}}
CACHE = {
    "AAA": context("Alpha Corp.", ("2026-10-22", "amc")),  # Tomorrow after the close: in 23 hours
    "BBB": context("Beta Inc.", ("2026-10-23", "bmo")),    # Friday before the open: in 39 hours
    "DDD": context("Delta Co.", ("2026-10-21", "bmo")),    # This morning: past
    "EEE": context("Echo Ltd.", ("2026-10-22", "bmo")),    # Not held
}


def test_reports_due_within_24_hours():
    reports = er.upcoming_reports("Tester", NOW, 24, {"users": {"Tester": dict(STATE["users"]["Tester"])}}, CACHE)
    assert [(r["symbol"], r["at"].strftime("%a %H:%M")) for r in reports] == [("CCC", "Thu 08:00"), ("AAA", "Thu 16:05")]
    aaa = reports[1]
    text = er.reminder_text(aaa, NOW)
    assert "<b>AAA</b> (Alpha Corp.) reports tomorrow, Thursday, October 22, after the close (in about 23 hours)." in text
    assert "Finnbot's latest call on it: Hold." in text
    ccc = er.reminder_text(reports[0], NOW)
    assert "Risky" in ccc and "assumes the morning" in ccc  # An older label is read as today's; no time known


@pytest.fixture
def tester(monkeypatch):
    monkeypatch.setitem(er.USER_PROFILES, "Tester", {"holdings_source": "TESTER_SOURCE", "telegram_chat_id": "TESTER_CHAT"})
    monkeypatch.setenv("TESTER_SOURCE", "SNAPTRADE")
    monkeypatch.setenv("TESTER_CHAT", "123")
    monkeypatch.setattr(er, "load_state", lambda: {"users": {"Tester": dict(STATE["users"]["Tester"])}})
    monkeypatch.setattr(er, "_load_context_cache", lambda: CACHE)


def test_each_report_is_sent_once(tester, telegram):
    assert er.send_earnings_reminders(NOW, users=["Tester"]) == 2
    assert er.send_earnings_reminders(datetime(2026, 10, 21, 18, 0, tzinfo=ET), users=["Tester"]) == 0  # Next hour
    assert [user for user, _ in telegram.messages] == ["Tester", "Tester"]
    # Friday's report comes into the window on Thursday morning: a new reminder
    assert er.send_earnings_reminders(datetime(2026, 10, 22, 8, 10, tzinfo=ET), users=["Tester"]) == 1
    assert "<b>BBB</b>" in telegram.messages[-1][1]


def test_no_reminder_without_holdings_or_telegram_or_when_off(tester, telegram, monkeypatch):
    monkeypatch.setenv("TESTER_SOURCE", "NO_HOLDINGS")
    assert er.send_earnings_reminders(NOW, users=["Tester"]) == 0
    monkeypatch.setenv("TESTER_SOURCE", "SHEETS")
    monkeypatch.setenv("TESTER_CHAT", "")
    assert er.send_earnings_reminders(NOW, users=["Tester"]) == 0
    monkeypatch.setenv("TESTER_CHAT", "123")
    monkeypatch.setitem(er.params_dict["WATCHDOG"], "EARNINGS_REMINDER_HOURS", 0)
    assert er.send_earnings_reminders(NOW, users=["Tester"]) == 0
    monkeypatch.setitem(er.params_dict["WATCHDOG"], "EARNINGS_REMINDER_HOURS", 24)
    monkeypatch.setattr(er, "load_state", lambda: 1 / 0)  # Never raises
    assert er.send_earnings_reminders(NOW, users=["Tester"]) == 0
    assert telegram.messages == []


def test_the_hourly_digest_run_sends_them(monkeypatch):
    calls = []
    monkeypatch.setattr(dp, "send_earnings_reminders", lambda now: calls.append(now))
    monkeypatch.setattr(dp, "send_holiday_notice", lambda now: None)
    monkeypatch.setattr(dp, "missed_digests", lambda recipients, now, alert=True: [])
    sunday = datetime(2026, 10, 25, 20, 10, tzinfo=ET)  # No digest is due, but the reminders still run
    assert dp.run_daily_digests([], now=sunday) == (True, "No digest due.")
    dp.run_daily_digests([], now=sunday, check_only=True)
    assert calls == [sunday]
    assert date(2026, 10, 25).weekday() == 6
