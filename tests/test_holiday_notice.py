"""
Tests for the US market holiday notice (src/telegram_alert/holiday_notice.py). Written by Claude (Claude Opus 5.5 via
Claude Code) on 2026-10-21, at Andres's request.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import src.telegram_alert.holiday_notice as holiday

ET = ZoneInfo("America/New_York")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=ET)


@pytest.mark.parametrize("when, expected", [
    ("2026-11-26T07:10", "Thanksgiving"),
    ("2026-07-03T07:10", "Independence Day"),       # July 4th falls on a Saturday, observed Friday
    ("2026-02-16T07:10", "Presidents' Day"),
    ("2026-01-19T07:10", "Martin Luther King Jr. Day"),
    ("2026-04-03T07:10", "Good Friday"),
    ("2025-01-09T07:10", "a special closure"),       # The national day of mourning for President Carter
    ("2026-11-28T07:10", None),                      # A Saturday
    ("2026-10-21T07:10", None),                      # A trading day
    ("2026-10-12T07:10", None),                      # Canadian Thanksgiving: the NYSE trades
    ("2026-11-27T07:10", None),                      # A half day still trades
])
def test_us_market_holiday(when, expected):
    assert holiday.us_market_holiday(at(when)) == expected


def test_message_says_when_trading_resumes_and_what_finnbot_does():
    thanksgiving = holiday.holiday_message(at("2026-11-26T07:10"), "Thanksgiving")
    assert "US stock markets are closed today, Thursday, November 26, for Thanksgiving." in thanksgiving
    assert "Trading resumes Friday, November 27, closing early at 1:00 PM ET." in thanksgiving
    assert "Finnbot's digests still go out today, since the TSX is open." in thanksgiving  # Not a Canadian holiday
    christmas = holiday.holiday_message(at("2026-12-25T07:10"), "Christmas")
    assert "Trading resumes Monday, December 28. No Finnbot digests today." in christmas  # The TSX is closed too


def test_sent_to_the_admins_once_from_the_pre_market_time(telegram):
    assert not holiday.send_holiday_notice(at("2026-11-26T06:10"))  # Before DIGEST.PRE_MARKET_TIME_ET
    assert holiday.send_holiday_notice(at("2026-11-26T07:10"))
    assert not holiday.send_holiday_notice(at("2026-11-26T08:10"))  # Already sent today
    assert [user for user, _ in telegram.messages] == holiday.admins() == ["Andres"]
    assert "Thanksgiving" in telegram.messages[0][1]
    assert not holiday.send_holiday_notice(at("2026-11-27T07:10"))  # The next day trades
    assert holiday.send_holiday_notice(at("2026-12-25T09:10"))  # The next holiday is a new notice
    assert len(telegram.messages) == 2


def test_can_be_switched_off_and_never_raises(telegram, monkeypatch):
    monkeypatch.setitem(holiday.params_dict["OPS"], "HOLIDAY_NOTICE", False)
    assert not holiday.send_holiday_notice(at("2026-11-26T07:10"))
    monkeypatch.setitem(holiday.params_dict["OPS"], "HOLIDAY_NOTICE", True)
    telegram.fail = True  # Telegram down: logged, not raised, and tried again next run
    assert not holiday.send_holiday_notice(at("2026-11-26T07:10"))
    monkeypatch.setattr(holiday, "us_market_holiday", lambda now: 1 / 0)
    assert not holiday.send_holiday_notice(at("2026-11-26T07:10"))
    assert telegram.messages == []
