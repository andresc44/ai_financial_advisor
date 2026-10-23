"""
Market session and scheduling helpers for the holdings watchdog.

Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-13, at Andres's request,
as part of the per-user watchdog scheduling plan (see docs/watchdog.md).

Why this module exists:
- The watchdog runs from ONE hourly cron job, but each user picks their own check interval and market
  session ("regular", "extended", or "24_7" in params_dict["WATCHDOG"]). These helpers answer, for any
  moment in time: which market phase is it, is a given session active, and which N-hour "block" are we in.
- Every function takes `now` explicitly (no hidden clock reads) so the schedule can be tested at fixed
  times - DST changes, half-days, holidays, weekends - without waiting for real time to pass.
- Exchange hours and holidays come from the `exchange_calendars` package so early closes and NYSE/TSX
  holiday differences are handled without a hand-maintained holiday list.
"""
import math
import sys
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config.parameters import params_dict

WATCHDOG_PARAMS = params_dict["WATCHDOG"]
MARKET_TZ = ZoneInfo(WATCHDOG_PARAMS["TIMEZONE"])

# Fixed anchor for 24_7 blocks. Counted in UTC so daylight-saving changes never shift block boundaries.
EPOCH_24_7 = datetime(2024, 1, 1, tzinfo=timezone.utc)

# Phases reported to Claude and used for choosing a price source:
#   pre_market / regular / after_hours - on a trading day, relative to that day's open and close
#   overnight - a trading day, but outside the extended-hours window
#   closed    - weekend or holiday on every configured exchange
MARKET_PHASES = ("pre_market", "regular", "after_hours", "overnight", "closed")


@lru_cache(maxsize=None)
def _get_calendar(code: str) -> xcals.ExchangeCalendar:
    """Builds each exchange calendar once per process (construction takes ~1s)."""
    return xcals.get_calendar(code)


def _to_utc(now: datetime) -> pd.Timestamp:
    """Normalizes a timezone-aware datetime to a UTC pandas Timestamp."""
    if now.tzinfo is None:
        raise ValueError("`now` must be timezone-aware (e.g. datetime.now(timezone.utc)).")
    return pd.Timestamp(now).tz_convert("UTC")


def market_date(now: datetime) -> date:
    """Returns the market-local calendar date for `now` (e.g. US Eastern date)."""
    return _to_utc(now).tz_convert(MARKET_TZ).date()


def get_session_config(session_name: str) -> dict:
    """Returns the session definition from params_dict, falling back to 'regular' for unknown names."""
    sessions = WATCHDOG_PARAMS["SESSIONS"]
    if session_name not in sessions:
        print(f"[WARNING] Unknown watchdog session '{session_name}'. Using 'regular'.")
        session_name = "regular"
    return sessions[session_name]


def trading_day_bounds(
    now: datetime, calendars: Optional[Iterable[str]] = None
) -> Optional[tuple[pd.Timestamp, pd.Timestamp]]:
    """
    Returns (earliest open, latest close) in UTC across the configured exchanges for the market-local
    date of `now`, or None when no configured exchange trades that day.
    """
    codes = list(calendars) if calendars is not None else WATCHDOG_PARAMS["EXCHANGE_CALENDARS"]
    session_label = pd.Timestamp(market_date(now))

    opens, closes = [], []
    for code in codes:
        cal = _get_calendar(code)
        if cal.is_session(session_label):
            opens.append(cal.session_open(session_label))
            closes.append(cal.session_close(session_label))

    if not opens:
        return None
    return min(opens), max(closes)


def market_phase(now: datetime, calendars: Optional[Iterable[str]] = None) -> str:
    """Returns the current market phase (one of MARKET_PHASES), independent of any user's session setting."""
    ts = _to_utc(now)
    bounds = trading_day_bounds(now, calendars)
    if bounds is None:
        return "closed"

    open_ts, close_ts = bounds
    extended = WATCHDOG_PARAMS["SESSIONS"]["extended"]
    pre_open = pd.Timedelta(minutes=extended.get("pre_open_minutes", 0))
    post_close = pd.Timedelta(minutes=extended.get("post_close_minutes", 0))

    if open_ts <= ts < close_ts:
        return "regular"
    if open_ts - pre_open <= ts < open_ts:
        return "pre_market"
    if close_ts <= ts < close_ts + post_close:
        return "after_hours"
    return "overnight"


def session_window(
    now: datetime, session_name: str, calendars: Optional[Iterable[str]] = None
) -> Optional[tuple[pd.Timestamp, pd.Timestamp]]:
    """
    Returns the (start, end) UTC window of `session_name` on the market-local date of `now`, or None if
    the session has no window that day (non-trading day, or a 24_7 session, which has no window).
    Windows must stay within one market-local day (true for the default offsets).
    """
    cfg = get_session_config(session_name)
    if not cfg.get("trading_days_only", True):
        return None

    bounds = trading_day_bounds(now, calendars)
    if bounds is None:
        return None

    open_ts, close_ts = bounds
    start = open_ts - pd.Timedelta(minutes=cfg.get("pre_open_minutes", 0))
    end = close_ts + pd.Timedelta(minutes=cfg.get("post_close_minutes", 0))
    return start, end


def is_in_session(
    now: datetime, session_name: str, calendars: Optional[Iterable[str]] = None
) -> bool:
    """True when `now` falls inside the user's configured session (always True for 24_7)."""
    cfg = get_session_config(session_name)
    if not cfg.get("trading_days_only", True):
        return True

    window = session_window(now, session_name, calendars)
    return window is not None and window[0] <= _to_utc(now) < window[1]


def block_id(
    now: datetime,
    interval_hours: int,
    session_name: str,
    calendars: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """
    Identifies the N-hour block `now` falls in. A user is due for a scheduled check whenever this differs
    from the block of their last successful check. Returns None when the watchdog is off (interval 0) or
    `now` is outside the session.

    - regular / extended: blocks are counted from that day's session start and restart every trading day,
      so an interval at least as long as the session means "once per trading day".
    - 24_7: blocks are counted continuously in UTC from EPOCH_24_7, so intervals that don't divide 24
      (e.g. 5) or exceed a day (e.g. 48) stay evenly spaced and DST never shifts them.

    The session name and interval are part of the id, so changing either makes the user due immediately.
    """
    if interval_hours <= 0:
        return None

    ts = _to_utc(now)
    cfg = get_session_config(session_name)

    if not cfg.get("trading_days_only", True):
        hours_since_epoch = (ts - pd.Timestamp(EPOCH_24_7)).total_seconds() / 3600
        return f"24_7:{interval_hours}h:{math.floor(hours_since_epoch / interval_hours)}"

    window = session_window(now, session_name, calendars)
    if window is None or not (window[0] <= ts < window[1]):
        return None

    hours_since_start = (ts - window[0]).total_seconds() / 3600
    block = math.floor(hours_since_start / interval_hours)
    return f"{session_name}:{interval_hours}h:{market_date(now).isoformat()}:{block}"


def last_refresh_boundary(
    now: datetime, calendars: Optional[Iterable[str]] = None
) -> pd.Timestamp:
    """
    Returns the most recent market open or close (UTC) at or before `now` across the configured exchanges.
    Slow-changing data (fundamentals, quarterly series, daily indicators) fetched before this moment is
    stale - this gives "refresh at the first check of each trading day, and once more after the close".
    """
    codes = list(calendars) if calendars is not None else WATCHDOG_PARAMS["EXCHANGE_CALENDARS"]
    ts = _to_utc(now)

    candidates = []
    for code in codes:
        cal = _get_calendar(code)
        candidates.append(cal.previous_open(ts))
        candidates.append(cal.previous_close(ts))
    return max(candidates)


# Claude-authored (2026-10-15): which regular session a price belongs to, for daily P/L.
# 2026-10-21: holdings can also be listed in Europe, London, Australia, and Colombia (EUR, GBP, AUD, and COP holdings).
# Yahoo suffix -> (exchange_calendars code, the currency Yahoo quotes it in). A symbol without one of these suffixes is
# a US listing. Only holdings use this; the ideas stay US-only.
LISTING_SUFFIXES = {
    ".TO": ("XTSE", "CAD"), ".V": ("XTSE", "CAD"), ".NE": ("XTSE", "CAD"), ".CN": ("XTSE", "CAD"),  # TSX, Venture, NEO, CSE
    ".L": ("XLON", "GBX"),   # London, quoted in pence
    ".AX": ("XASX", "AUD"),  # Australia
    ".CL": ("XBOG", "COP"),  # Colombia
    # Xetra, Frankfurt, Paris, Amsterdam, Milan, Madrid, Brussels, Lisbon, Vienna, Helsinki, Dublin
    ".DE": ("XETR", "EUR"), ".F": ("XFRA", "EUR"), ".PA": ("XPAR", "EUR"), ".AS": ("XAMS", "EUR"),
    ".MI": ("XMIL", "EUR"), ".MC": ("XMAD", "EUR"), ".BR": ("XBRU", "EUR"), ".LS": ("XLIS", "EUR"),
    ".VI": ("XWBO", "EUR"), ".HE": ("XHEL", "EUR"), ".IR": ("XDUB", "EUR"),
}
US_LISTING = ("XNYS", "USD")


def listing(symbol: str) -> tuple[str, str]:
    """2026-10-21: (exchange calendar, currency) of a symbol's listing, judged by its Yahoo suffix."""
    upper = str(symbol).upper()
    return next((found for suffix, found in LISTING_SUFFIXES.items() if upper.endswith(suffix)), US_LISTING)


def exchange_calendar_code(symbol: str) -> str:
    """Calendar for a symbol's listing, judged by its Yahoo suffix (LISTING_SUFFIXES), otherwise XNYS."""
    return listing(symbol)[0]


def latest_regular_session(now: datetime, code: str) -> tuple[date, bool]:
    """
    The regular trading session a price seen at `now` belongs to on exchange `code`, and whether that session is
    still in progress. From today's open onward it is today's session (in progress until the close, early closes
    included); before the open, and on weekends and holidays, it is the most recent completed session.
    Daily P/L is measured over this session, so it never depends on whether Yahoo has created today's bar yet.
    """
    cal = _get_calendar(code)
    ts = _to_utc(now)
    local_day = pd.Timestamp(ts.tz_convert(cal.tz).date())
    if cal.is_session(local_day):
        if ts >= cal.session_open(local_day):
            return local_day.date(), ts < cal.session_close(local_day)
        return cal.previous_session(local_day).date(), False
    return cal.date_to_session(local_day, direction="previous").date(), False
