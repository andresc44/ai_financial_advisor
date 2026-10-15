"""
Persistent memory of each user's holding evaluations and watchdog bookkeeping (data/watchdog_state.json).

Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-13, at Andres's request,
as part of the per-user watchdog scheduling plan (see docs/watchdog.md).

Why this module exists:
- To call Finnhub / Twelve Data / Claude "only when needed", the watchdog must remember, per user and per
  stock, when it last evaluated, at what price, what it concluded, and the SAR stop / next earnings date it
  saw at the time. It also remembers each user's last scheduled block and Claude calls used today.
- Evaluations are recorded from add_recommendations, so the twice-daily digest's full evaluation also
  counts as a recent check and the watchdog won't immediately repeat it.
- Writes are atomic (temp file + os.replace) so a crash can never leave a half-written file. Cross-process
  exclusion (watchdog vs. digest) is handled by sharing one `flock` lock in the crontab (docs/watchdog.md).
- 2026-10-15: users now run in parallel threads, so update_user_state holds an in-process lock around its
  read-modify-write (otherwise two users saving at once could drop each other's updates). Evaluations also store
  the sentiment, and reusable_evaluations lets the digest reuse a watchdog evaluation made minutes earlier.

File layout:
{
  "users": {
    "<user>": {
      "last_block": "<market_session.block_id of the last successful scheduled check>",
      "claude_calls": {"date": "YYYY-MM-DD", "count": int},
      "symbols": {
        "<symbol>": {"evaluated_at": iso, "price": float, "natr": float, "recommendation": str,
                     "sentiment": str | null, "rationale": str, "sar_stop": float | null,
                     "next_earnings_date": "YYYY-MM-DD" | null}
      }
    }
  }
}
"""
import json
import math
import os
import sys
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

STATE_PATH = project_root / "data" / "watchdog_state.json"
_STATE_LOCK = threading.Lock()  # Users run in parallel threads; see update_user_state


def load_state(path: Path = STATE_PATH) -> dict:
    """Loads the state file, returning an empty state if it is missing or unreadable."""
    if not path.exists():
        return {"users": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[WARNING] Could not read watchdog state at {path}: {e}. Starting fresh.")
        return {"users": {}}
    state.setdefault("users", {})
    return state


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    """Atomically writes the state file (temp file in the same directory, then os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".watchdog_state_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp_path, path)
    except OSError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def get_user_state(state: dict, user_name: str) -> dict:
    """Returns (creating if needed) the mutable state dict for one user."""
    user_state = state["users"].setdefault(user_name, {})
    user_state.setdefault("last_block", None)
    user_state.setdefault("claude_calls", {"date": None, "count": 0})
    user_state.setdefault("symbols", {})
    return user_state


def update_user_state(
    user_name: str, updater: Callable[[dict], None], path: Path = STATE_PATH
) -> None:
    """
    Re-reads the file, applies `updater` to the user's state, and saves - never writes back a stale copy.
    The lock makes the read-modify-write safe when several users run in parallel threads.
    """
    with _STATE_LOCK:
        state = load_state(path)
        updater(get_user_state(state, user_name))
        save_state(state, path)


def claude_calls_today(user_state: dict, today: date) -> int:
    """Returns how many watchdog Claude calls this user has used on `today` (market-local date)."""
    calls = user_state.get("claude_calls", {})
    return calls.get("count", 0) if calls.get("date") == today.isoformat() else 0


def add_claude_calls(user_state: dict, today: date, count: int) -> None:
    """Adds `count` watchdog Claude calls to the user's tally for `today`, resetting on a new day."""
    used = claude_calls_today(user_state, today)
    user_state["claude_calls"] = {"date": today.isoformat(), "count": used + count}


def _clean_float(value: Any) -> Optional[float]:
    """Returns a JSON-safe float, or None for missing / NaN values."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _sar_stop(context: Optional[dict], price: Optional[float]) -> Optional[float]:
    """Latest daily Parabolic SAR from the market context, kept only when it sits below price (i.e. acts as a stop)."""
    if not isinstance(context, dict) or price is None:
        return None
    series = (context.get("technical_indicators") or {}).get("sar_1day") or []
    latest = series[0] if series and isinstance(series[0], dict) else {}  # Series are newest-first
    sar = _clean_float(latest.get("sar"))
    return sar if sar is not None and sar < price else None


def _next_earnings_date(context: Optional[dict], today: date) -> Optional[str]:
    """Earliest upcoming earnings date (YYYY-MM-DD) found in the market context, if any."""
    if not isinstance(context, dict):
        return None
    upcoming = (context.get("catalysts_and_earnings") or {}).get("upcoming_earnings") or []
    dates = []
    for event in upcoming:
        if not isinstance(event, dict) or not event.get("date"):
            continue
        try:
            event_date = date.fromisoformat(str(event["date"])[:10])
        except ValueError:
            continue
        if event_date >= today:
            dates.append(event_date)
    return min(dates).isoformat() if dates else None


def record_evaluations(
    user_name: str,
    evaluated_df: pd.DataFrame,
    evaluated_at: Optional[datetime] = None,
    path: Path = STATE_PATH,
) -> None:
    """
    Stores the latest recommendation per symbol for `user_name`. Rows without a recommendation (failed
    evaluations) are skipped so those symbols are retried. The price recorded is `session_price` when the
    watchdog supplied one, otherwise `current_price`.
    """
    if evaluated_df is None or evaluated_df.empty or "recommendation" not in evaluated_df.columns:
        return

    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    today = evaluated_at.date()
    rows = evaluated_df[evaluated_df["recommendation"].notna()].drop_duplicates("symbol")
    if rows.empty:
        return

    records: dict[str, dict] = {}
    for _, row in rows.iterrows():
        price = _clean_float(row.get("session_price"))
        if price is None:
            price = _clean_float(row.get("current_price"))
        context = row.get("market_context")
        sentiment = row.get("sentiment")
        records[row["symbol"]] = {
            "evaluated_at": evaluated_at.isoformat(),
            "price": price,
            "natr": _clean_float(row.get("natr")),
            "recommendation": row["recommendation"],
            "sentiment": sentiment if isinstance(sentiment, str) else None,
            "rationale": row.get("recommendation_rationale"),
            "sar_stop": _sar_stop(context, price),
            "next_earnings_date": _next_earnings_date(context, today),
        }

    update_user_state(user_name, lambda user_state: user_state["symbols"].update(records), path)


def reusable_evaluations(
    user_name: str,
    holdings_df: pd.DataFrame,
    now: datetime,
    max_age_minutes: float,
    max_natr_move: float,
    path: Path = STATE_PATH,
) -> dict[str, dict]:
    """
    Claude-authored (2026-10-15): {symbol: {"recommendation", "sentiment", "rationale"}} for recorded evaluations
    still current enough to reuse: made within `max_age_minutes`, and with the price moved less than
    `max_natr_move` x NATR since. Lets the digest skip Claude for stocks the watchdog evaluated minutes earlier.
    Stocks without a usable price, NATR, or record are left out (evaluated again), so reuse is always conservative.
    """
    if max_age_minutes <= 0 or holdings_df.empty:
        return {}
    records = get_user_state(load_state(path), user_name)["symbols"]

    reusable: dict[str, dict] = {}
    for _, row in holdings_df.drop_duplicates("symbol").iterrows():
        record = records.get(row["symbol"])
        if not record or not record.get("recommendation"):
            continue
        try:
            age_minutes = (now - datetime.fromisoformat(record["evaluated_at"])).total_seconds() / 60
        except (KeyError, TypeError, ValueError):
            continue
        price = _clean_float(row.get("session_price")) or _clean_float(row.get("current_price"))
        last_price, natr = _clean_float(record.get("price")), _clean_float(row.get("natr"))
        if not 0 <= age_minutes <= max_age_minutes or not price or not last_price or not natr or natr <= 0:
            continue
        if abs(price / last_price - 1) * 100 / natr >= max_natr_move:
            continue
        reusable[row["symbol"]] = {
            "recommendation": record["recommendation"],
            "sentiment": record.get("sentiment"),
            "rationale": record.get("rationale"),
        }
    return reusable
