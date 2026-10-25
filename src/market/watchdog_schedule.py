# File to decide when a user's next scheduled watchdog check is due, counting from their last check or digest.
"""
Authorship: Written by Claude (Claude Sonnet 5.5 via Claude Code) on 2026-10-25, at Andres's request.

Why: the watchdog's checks used to sit on fixed N-hour blocks counted from the session start (market_session.block_id),
so a digest at 7:10 and a watchdog at 8:45 looked at the same holdings twice, and a 15:45 check ran just before the
16:10 digest. Now the digest resets the clock, and a check is due N hours after the last check or digest.

The rules (the watchdog runs at :45 every hour, the digest at :10; N = the user's interval in hours):
1. A digest counts as a check at the next :45 after it (a digest at 7:10 counts as 7:45). The clock for the next check
   runs from the later of that and the last watchdog check: due N hours on, at the first run on or after it.
2. The first check of each market day is due at the first run in the session, whatever the day before did.
   A digest before the session opens (7:10, for a regular session from 9:30) resets the clock like any other.
3. A check that is due is skipped when a digest is about to run: one due to start within 1.5 hours of it. Not for an
   interval of 1 hour (which runs every hour). So a regular session at 8 hours or more gets no watchdog check: the
   clock from the 7:10 digest runs out at 15:45, right before the 16:10 digest.
4. When the next check would fall after the session closes, one runs at the last :45 before the close instead, unless
   a check or digest already ran within the 1.5 hours before it (an extended session: 19:45, not if 18:15 or later).
5. A 24_7 user has no session, so no first check of the day or last one before a close; the clock just rolls on,
   reset by each digest, and the skip before a digest still applies. Weekends: no digest, so no reset.
6. Changing the interval or session makes the user due at once (the last check records both); a check that failed
   (key problem) isn't recorded, so it stays due and is retried.
"""
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.market.market_session import get_session_config, market_date, session_window

SLOT_MINUTE = 45               # The watchdog's cron minute
DIGEST_MINUTE = 10             # The digest's cron minute
TOLERANCE = pd.Timedelta(minutes=15)   # A run a little off its slot (a late cron, a slow job) still counts as on time
NEAR_DIGEST = pd.Timedelta(minutes=90)  # A digest this soon after a check makes it skippable
MIN_GAP = pd.Timedelta(minutes=90)      # The last check of a session is skipped if one ran this recently


@dataclass(frozen=True)
class Due:
    due: bool
    reason: str


def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def slot_at_or_after(moment) -> pd.Timestamp:
    """The first watchdog slot (:45) at or after `moment`: a digest at 7:10 counts as the check at 7:45."""
    ts = _utc(moment)
    slot = ts.floor("h") + pd.Timedelta(minutes=SLOT_MINUTE)
    return slot if slot >= ts else slot + pd.Timedelta(hours=1)


def digest_just_ran(now, last_digest_at: Optional[str], minutes: int = 60) -> bool:
    """
    True when the user's last digest went out less than `minutes` before `now`. The digest has just evaluated every
    holding, so the tripwire (a price move since the last evaluation) has nothing to compare against that hour.
    """
    return bool(last_digest_at) and timedelta(0) <= _utc(now) - _utc(last_digest_at) < timedelta(minutes=minutes)


def digest_about_to_run(now, user_name: str, due_digest: Optional[Callable] = None,
                        already_sent: Optional[Callable] = None) -> bool:
    """True when a digest for `user_name` is due to run within NEAR_DIGEST after `now` (it runs at :10 each hour)."""
    if due_digest is None or already_sent is None:
        from src.digest.digest_schedule import due_digest as due_digest_, users_already_sent
        due_digest, already_sent = due_digest or due_digest_, already_sent or users_already_sent
    ts = _utc(now)
    run = ts.floor("h") + pd.Timedelta(minutes=DIGEST_MINUTE)
    if run <= ts:
        run += pd.Timedelta(hours=1)
    while run - ts <= NEAR_DIGEST:
        due = due_digest(run.to_pydatetime())
        if due is not None and user_name not in already_sent(*due):
            return True
        run += pd.Timedelta(hours=1)
    return False


def check_due(
    now: datetime, interval_hours: int, session: str, last_check: Optional[dict], last_digest_at: Optional[str],
    user_name: str = "", digest_soon: Optional[Callable[[datetime, str], bool]] = None,
) -> Due:
    """
    Whether a scheduled check is due at `now` (see the module docstring). `last_check` is {"at", "interval",
    "session"} of the last successful check, `last_digest_at` when the user's last digest went out (both ISO text).
    """
    if interval_hours <= 0:
        return Due(False, "watchdog off")
    ts = _utc(now)
    rolling = not get_session_config(session).get("trading_days_only", True)  # 24_7
    window = None if rolling else session_window(now, session)

    anchor = None
    if last_check and last_check.get("at"):
        anchor = _utc(last_check["at"])
    if last_digest_at:
        digest_slot = slot_at_or_after(last_digest_at)
        anchor = digest_slot if anchor is None else max(anchor, digest_slot)

    changed = bool(last_check) and (last_check.get("interval") != interval_hours or last_check.get("session") != session)
    if changed:
        due, reason = True, "interval or session changed"
    elif anchor is None:
        due, reason = True, "no earlier check"
    elif not rolling and market_date(anchor.to_pydatetime()) != market_date(now):
        due, reason = True, "first check of the day"
    else:
        due, reason = _after_anchor(ts, anchor, interval_hours, window)

    if due and interval_hours > 1 and not changed:
        soon = digest_soon or (lambda moment, name: digest_about_to_run(moment, name))
        if soon(now, user_name):
            return Due(False, "a digest is about to run")
    return Due(due, reason)


def _after_anchor(ts: pd.Timestamp, anchor: pd.Timestamp, interval_hours: int, window) -> tuple[bool, str]:
    """Due N hours after `anchor`; or, when that falls past the close, at the session's last slot (rule 4)."""
    next_due = anchor + pd.Timedelta(hours=interval_hours)
    if ts >= next_due - TOLERANCE:
        return True, f"{interval_hours} hours since the last check or digest"
    if window is not None and next_due > window[1] and ts + pd.Timedelta(hours=1) >= window[1] \
            and ts - anchor >= MIN_GAP:
        return True, "last check before the close"
    return False, f"next check from {next_due.strftime('%H:%M')} UTC"
