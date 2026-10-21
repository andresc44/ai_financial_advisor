# File to tell the admins on Telegram when the US stock market is closed on a weekday (a market holiday).
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-21, at Andres's request.

Why: on a US market holiday Finnbot goes quiet (no Pre-Market or Post-Market digest when no configured exchange
trades, and no watchdog checks in a regular session), which looks the same as Finnbot being down. A short notice says
why, and when trading resumes.

The digest's hourly cron run (src/digest/digest_payload.py) calls send_holiday_notice. On a weekday the NYSE (XNYS)
doesn't trade, the first run at or after DIGEST.PRE_MARKET_TIME_ET (07:00 ET) sends it to OPS.ALERT_USERS, once per
holiday: the alert log's key includes the date, so later runs that day skip it. OPS.HOLIDAY_NOTICE turns it off.
Holidays come from the exchange calendar (exchange_calendars), so there is no list to maintain.
"""
import html
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.admins import admins
from src.market_session import MARKET_TZ, _get_calendar, market_date
from src.parameters import params_dict
from src.telegram_alert.notices import send_notice_now

US_CALENDAR = "XNYS"
EXCHANGE_NAMES = {"XTSE": "TSX"}  # How the notice names the other exchanges in WATCHDOG.EXCHANGE_CALENDARS
SUBJECT = "US market holiday"
# exchange_calendars' names for some holidays -> how people say them
HOLIDAY_NAMES = {
    "President's Day": "Presidents' Day",
    "July 4th": "Independence Day",
    "Juneteenth National Independence Day": "Juneteenth",
    "Dr. Martin Luther King Jr. Day": "Martin Luther King Jr. Day",
}


def _long_date(day: date) -> str:
    return f"{day:%A, %B} {day.day}"


def _clock(moment: pd.Timestamp) -> str:
    local = moment.tz_convert(MARKET_TZ)
    return f"{local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'} ET"


def us_market_holiday(now: datetime) -> Optional[str]:
    """
    The holiday's name when `now` falls on a weekday (US Eastern date) the NYSE doesn't trade, e.g. "Thanksgiving",
    or "a special closure" for one-off closures (e.g. a national day of mourning). None on trading days and weekends.
    """
    day = market_date(now)
    calendar = _get_calendar(US_CALENDAR)
    if day.weekday() >= 5 or calendar.is_session(pd.Timestamp(day)):
        return None
    named = calendar.regular_holidays.holidays(pd.Timestamp(day), pd.Timestamp(day), return_name=True)
    if len(named):
        return HOLIDAY_NAMES.get(str(named.iloc[0]), str(named.iloc[0]))
    return "a special closure"


def holiday_message(now: datetime, holiday: str) -> str:
    """The notice's Telegram text (HTML): the holiday, when trading resumes, and what Finnbot does today."""
    day = market_date(now)
    calendar = _get_calendar(US_CALENDAR)
    resumes = calendar.date_to_session(pd.Timestamp(day), direction="next")
    close = calendar.session_close(resumes)
    regular_close = pd.Timestamp(datetime.combine(resumes.date(), time(16, 0), tzinfo=MARKET_TZ))
    early = f", closing early at {_clock(close)}" if close < regular_close else ""
    # The digests follow WATCHDOG.EXCHANGE_CALENDARS, so they still go out when another exchange there trades
    trading = [EXCHANGE_NAMES.get(code, code) for code in params_dict["WATCHDOG"]["EXCHANGE_CALENDARS"]
               if code != US_CALENDAR and _get_calendar(code).is_session(pd.Timestamp(day))]
    digests = (f"Finnbot's digests still go out today, since the {' and '.join(trading)} "
               f"{'is' if len(trading) == 1 else 'are'} open." if trading else "No Finnbot digests today.")
    return (
        f"\U0001F3D6️ US stock markets are closed today, {_long_date(day)}, for {html.escape(holiday, quote=False)}."
        f"\n\nTrading resumes {_long_date(resumes.date())}{early}. {digests}"
    )


def send_holiday_notice(now: datetime) -> bool:
    """
    Sends the holiday notice to each admin when it's due at `now` (see the module docstring). Returns True when it
    was sent to anyone. Never raises: a failed notice must not stop the digest run.
    """
    if not params_dict["OPS"].get("HOLIDAY_NOTICE", True):
        return False
    try:
        holiday = us_market_holiday(now)
        hour, minute = (int(part) for part in params_dict["DIGEST"]["PRE_MARKET_TIME_ET"].split(":"))
        if holiday is None or now.astimezone(MARKET_TZ).time() < time(hour, minute):
            return False
        body = holiday_message(now, holiday)
        reason = market_date(now).isoformat()  # Once per holiday: later runs that day find it in the alert log
        sent = [admin for admin in admins() if send_notice_now(admin, SUBJECT, reason, body, cooldown_hours=24)]
    except Exception as e:  # e.g. an unreadable alert log or calendar; the digest run goes on
        print(f"[WARNING] Could not send the US market holiday notice: {e}")
        return False
    if sent:
        print(f"Sent the US market holiday notice ({holiday}) to {', '.join(sent)}.")
    return bool(sent)
