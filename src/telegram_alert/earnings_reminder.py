# File to remind each user on Telegram when a stock they hold reports earnings within the next 24 hours.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-21, at Andres's request.

How it works:
- The digest's hourly cron run (src/digest/digest_payload.py) calls send_earnings_reminders, every hour of every day,
  so a Monday-morning report is caught on Sunday.
- Each active user with holdings (SNAPTRADE or SHEETS) and a Telegram chat ID is checked. Their held stocks come from
  data/watchdog_state.json; the watchdog forgets sold positions on every check.
- Each stock's next report comes from the shared market-context cache (data/market_context_cache.json): Finnhub's
  earnings calendar, refreshed every trading day. No API calls are made here.
- A report is due when its expected time falls within WATCHDOG.EARNINGS_REMINDER_HOURS (24). Expected time (US
  Eastern): before the open 08:00, after the close 16:05, during market hours 12:00, and 08:00 when Finnhub gives
  no time, the earliest, so the reminder is never late.
- Each (stock, report date) is sent once: the alert log's key holds both. A stock held in several accounts is one
  position, so it gets one reminder.
"""
import html
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.market_session import MARKET_TZ, market_date
from src.parameters import params_dict
from src.personalized.add_metrics import _load_context_cache
from src.personalized.holdings_state import get_user_state, load_state
from src.personalized.LLM_hold_instr import current_recommendation
from src.personalized.user_profiles import USER_PROFILES
from src.personalized.user_settings import get_active_users
from src.telegram_alert.notices import send_notice_now

SUBJECT = "earnings"
# Finnhub's earnings-calendar "hour" codes -> (expected time, US Eastern; how the reminder says it)
REPORT_TIMES = {
    "bmo": (time(8, 0), "before the open"),
    "amc": (time(16, 5), "after the close"),
    "dmh": (time(12, 0), "during market hours"),
}
UNKNOWN_TIME = time(8, 0)
SIGNAL_LABELS = {"urgent_sell": "Urgent sell", "risky": "Risky", "profit_sell": "Profit sell", "buy_more": "Buy more",
                 "hold": "Hold"}


def _eligible(user_name: str) -> bool:
    """A user with holdings (SNAPTRADE or SHEETS) and a Telegram chat ID, like the watchdog's users."""
    profile = USER_PROFILES.get(user_name, {})
    source = os.environ.get(profile.get("holdings_source") or "", "").strip()
    chat_id = os.environ.get(profile.get("telegram_chat_id") or "", "").strip()
    return source in ("SNAPTRADE", "SHEETS") and bool(chat_id)


def upcoming_reports(user_name: str, now: datetime, hours: float, state: dict, cache: dict) -> list[dict]:
    """
    The user's held stocks reporting within `hours` of `now`, soonest first, one report per stock:
    {"symbol", "name", "date", "hour", "at" (expected time, US Eastern), "recommendation"}.
    """
    reports = []
    for symbol, saved in get_user_state(state, user_name)["symbols"].items():
        context = (cache.get(str(symbol).replace(":", "-")) or {}).get("context") or {}
        events = (context.get("catalysts_and_earnings") or {}).get("upcoming_earnings") or []
        candidates = [(event.get("date"), event.get("hour")) for event in events if isinstance(event, dict)]
        if not candidates and saved.get("next_earnings_date"):  # The date the last evaluation saw, without a time
            candidates = [(saved["next_earnings_date"], None)]
        for raw_date, hour in candidates:
            try:
                day = date.fromisoformat(str(raw_date)[:10])
            except ValueError:
                continue
            at = datetime.combine(day, REPORT_TIMES.get(hour, (UNKNOWN_TIME, ""))[0], tzinfo=MARKET_TZ)
            if now < at <= now + timedelta(hours=hours):
                reports.append({"symbol": symbol, "name": (context.get("profile") or {}).get("name"), "date": day,
                                "hour": hour, "at": at, "recommendation": saved.get("recommendation")})
                break
    return sorted(reports, key=lambda report: report["at"])


def reminder_text(report: dict, now: datetime) -> str:
    """The reminder's Telegram text (HTML), e.g. NVDA reporting tomorrow after the close, in about 23 hours."""
    days_away = (report["date"] - market_date(now)).days
    when = {0: "today, ", 1: "tomorrow, "}.get(days_away, "on ")
    timing = REPORT_TIMES.get(report["hour"], (None, ""))[1]
    hours_away = max(1, round((report["at"] - now).total_seconds() / 3600))
    name = f" ({html.escape(str(report['name']))})" if report.get("name") else ""
    text = (f"\U0001F4C5 Earnings soon: <b>{html.escape(str(report['symbol']))}</b>{name} reports {when}"
            f"{report['date']:%A, %B} {report['date'].day}{', ' + timing if timing else ''}"
            f" (in about {hours_away} hour{'' if hours_away == 1 else 's'}).")
    signal = SIGNAL_LABELS.get(current_recommendation(report.get("recommendation")))
    if signal:
        text += f"\n\nFinnbot's latest call on it: {signal}."
    if not timing:
        text += "\n\nThe report's time isn't known yet, so this reminder assumes the morning."
    return text


def send_earnings_reminders(now: datetime, users: Optional[list[str]] = None) -> int:
    """
    Sends every due reminder (see the module docstring) and returns how many went out. Never raises: a failed
    reminder must not stop the digest run, and an unsent one is tried again next hour.
    """
    hours = params_dict["WATCHDOG"].get("EARNINGS_REMINDER_HOURS", 24)
    if not hours:
        return 0
    sent = 0
    try:
        state, cache = load_state(), _load_context_cache()
        for user_name in users if users is not None else get_active_users():
            if not _eligible(user_name):
                continue
            for report in upcoming_reports(user_name, now, hours, state, cache):
                reason = f"{report['symbol']} {report['date'].isoformat()}"  # Once per stock and report
                if send_notice_now(user_name, SUBJECT, reason, reminder_text(report, now), cooldown_hours=24 * 7):
                    sent += 1
                    print(f"Sent {user_name} an earnings reminder for {report['symbol']} ({report['date']}).")
    except Exception as e:  # e.g. an unreadable state file or alert log; the digest run goes on
        print(f"[WARNING] Could not send earnings reminders: {e}")
    return sent
