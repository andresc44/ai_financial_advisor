# File to decide when the pre- and post-market digests are due, from the exchange calendar instead of cron clock times.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-15, at Andres's request.

Why this module exists:
- Cron used to start the digest at fixed clock times (`0 7,16 * * 1-5`). The VM's clock is UTC, so those times
  were hours off, and US daylight saving would shift them twice a year. The digest also ran on market holidays,
  and 16:00 was too early for closing data (cached daily data only counts as stale from 16:01 ET).
- Now cron starts src/digest/digest_payload.py every hour at :10, whatever the VM's timezone, and this module
  decides whether a digest is due:
    * Pre-Market:  from DIGEST.PRE_MARKET_TIME_ET (07:00 US Eastern) until the market opens.
    * Post-Market: from DIGEST.POST_MARKET_DELAY_MINUTES (10) after the close (16:10 ET; earlier on half-days
      when every configured exchange closes early) for DIGEST.POST_MARKET_SEND_WINDOW_MINUTES (240).
    * Only on days when an exchange in WATCHDOG.EXCHANGE_CALENDARS trades (the same rule as the watchdog).
- data/digest_state.json remembers which users received each (date, period) digest. A missed hour (VM reboot, the
  watchdog holding the cron lock) is retried within the window, and nobody receives the same digest twice.
"""
import json
import os
import sys
import tempfile
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.market.market_session import MARKET_TZ, market_date, trading_day_bounds
from src.config.parameters import params_dict

DIGEST_PARAMS = params_dict["DIGEST"]
STATE_PATH = project_root / "data" / "digest_state.json"
STATE_RETENTION_DAYS = 14  # Older (date, period) entries are dropped
_STATE_LOCK = threading.Lock()  # Users finish in parallel threads and record themselves as sent


def _send_windows(now: datetime) -> Optional[tuple[date, dict[str, tuple[pd.Timestamp, pd.Timestamp]]]]:
    """(market date, {period: (window start, window end) in UTC}) for `now`'s day, or None when no exchange trades."""
    bounds = trading_day_bounds(now)
    if bounds is None:
        return None  # Weekend or holiday on every configured exchange
    open_ts, close_ts = bounds
    day = market_date(now)

    hour, minute = (int(part) for part in DIGEST_PARAMS["PRE_MARKET_TIME_ET"].split(":"))
    pre_start = pd.Timestamp(datetime.combine(day, time(hour, minute), tzinfo=MARKET_TZ)).tz_convert("UTC")
    post_start = close_ts + pd.Timedelta(minutes=DIGEST_PARAMS["POST_MARKET_DELAY_MINUTES"])
    post_end = post_start + pd.Timedelta(minutes=DIGEST_PARAMS["POST_MARKET_SEND_WINDOW_MINUTES"])
    return day, {"Pre-Market": (pre_start, open_ts), "Post-Market": (post_start, post_end)}


def due_digest(now: datetime) -> Optional[tuple[date, str]]:
    """
    (market date, "Pre-Market" | "Post-Market") when a digest is due at `now` (timezone-aware), otherwise None.
    Outside both windows, and on days no configured exchange trades, nothing is due.
    """
    windows = _send_windows(now)
    if windows is None:
        return None
    day, periods = windows
    ts = pd.Timestamp(now).tz_convert("UTC")
    for period, (start, end) in periods.items():
        if start <= ts < end:
            return day, period
    return None


def ended_digests(now: datetime) -> list[tuple[date, str]]:
    """
    Claude-authored (2026-10-16): today's (market date, period) digests whose send window has closed by `now`. The
    hourly run compares them with data/digest_state.json to report a digest that never went out (e.g. the VM was
    down for the whole window), which no single failed run would show.
    """
    windows = _send_windows(now)
    if windows is None:
        return []
    day, periods = windows
    ts = pd.Timestamp(now).tz_convert("UTC")
    return [(day, period) for period, (_, end) in periods.items() if ts >= end]


def _state_key(day: date, period: str) -> str:
    return f"{day.isoformat()} {period}"


def _load_state(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"[WARNING] Could not read digest state at {path}: {e}. Starting fresh.")
        return {}


def _save_state(state: dict[str, list[str]], path: Path) -> None:
    """Atomic write (temp file + os.replace), like the watchdog state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".digest_state_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, path)
    except OSError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def users_already_sent(day: date, period: str, path: Path = STATE_PATH) -> set[str]:
    """Users who already received the `period` digest for `day`."""
    return set(_load_state(path).get(_state_key(day, period), []))


def record_sent(day: date, period: str, user_name: str, path: Path = STATE_PATH) -> None:
    """Remembers that `user_name` received the `period` digest for `day`, and drops entries older than retention."""
    cutoff = day - timedelta(days=STATE_RETENTION_DAYS)
    with _STATE_LOCK:
        state = _load_state(path)
        sent = state.setdefault(_state_key(day, period), [])
        if user_name not in sent:
            sent.append(user_name)
        state = {key: users for key, users in state.items() if date.fromisoformat(key.split(" ")[0]) >= cutoff}
        _save_state(state, path)
