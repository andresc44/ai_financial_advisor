# File to keep the shared Telegram alert log and send notices at most once per cooldown (watchdog, digest, and failure alerts).
"""
Authorship:
- load_and_prune_alert_logs and is_alert_suppressed: original version by Andres, moved here unchanged from
  hourly_watchdog.py on 2026-10-15 so the digest and failure alerts can use them too.
- Everything else: Claude (Claude Opus 5.5 via Claude Code), 2026-10-13 / 2026-10-15, at Andres's request.

data/alerts_sent/telegram_alerts_timestamped.csv records every alert and notice sent, so the same one is not
repeated within its cooldown. The watchdog keeps the log in memory during a run (send_user_notice) and saves it at
the end; the digest and failure alerts save immediately (send_notice_now). merge_and_save_alert_logs combines the
two, so neither overwrites rows the other wrote during the same run.
"""
import html
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config.parameters import params_dict
from src.telegram_alert.send_telegram import send_telegram_message

WATCHDOG_PARAMS = params_dict["WATCHDOG"]

CSV_DIR = project_root / "data" / "alerts_sent"
CSV_PATH = CSV_DIR / "telegram_alerts_timestamped.csv"
RETENTION_DAYS = WATCHDOG_PARAMS["ALERT_LOG_RETENTION_DAYS"]  # Number of days to keep alert logs
COOLDOWN_HOURS = WATCHDOG_PARAMS["ALERT_COOLDOWN_HOURS"]  # Number of hours to suppress repeated alerts
LOG_COLUMNS = ["user", "alerts", "timestamp"]
_LOG_LOCK = threading.Lock()  # Users run in parallel threads; one read-modify-write of the log file at a time


def load_and_prune_alert_logs(
    csv_path: Path, retention_days: int = RETENTION_DAYS
) -> pd.DataFrame:
    """Reads the CSV log, prunes entries older than retention_days, and returns the cleaned DataFrame."""
    cols = ["user", "alerts", "timestamp"]
    if not csv_path.exists():
        return pd.DataFrame(columns=cols)

    try:
        df = pd.read_csv(csv_path)
        if df.empty or not set(cols).issubset(df.columns):
            return pd.DataFrame(columns=cols)

        df["timestamp_dt"] = pd.to_datetime(
            df["timestamp"], utc=True, errors="coerce"
        )
        df = df.dropna(subset=["timestamp_dt"])

        # Preserve only rows within the retention window
        cutoff_date = datetime.now(timezone.utc) - timedelta(
            days=retention_days
        )
        pruned_df = df[df["timestamp_dt"] >= cutoff_date].copy()

        pruned_df["timestamp"] = pruned_df["timestamp_dt"].dt.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return pruned_df[cols]
    except Exception as e:
        print(f"[WARNING] Failed to parse existing alert CSV: {e}")
        return pd.DataFrame(columns=cols)


def is_alert_suppressed(
    df_logs: pd.DataFrame,
    user: str,
    serialized_alerts: str,
    cooldown_hours: int = COOLDOWN_HOURS,
) -> bool:
    """Returns True if an identical alert set was sent to the user within cooldown_hours."""
    if df_logs.empty:
        return False

    matches = df_logs[
        (df_logs["user"] == user) & (df_logs["alerts"] == serialized_alerts)
    ]
    if matches.empty:
        return False

    timestamps = pd.to_datetime(
        matches["timestamp"], utc=True, errors="coerce"
    )
    last_sent = timestamps.max()

    if pd.isna(last_sent):
        return False

    return (datetime.now(timezone.utc) - last_sent) < timedelta(
        hours=cooldown_hours
    )


def alert_key(symbol: str, recommendation: str) -> str:
    """Stable identity of one alert, stored in the log's `alerts` column and passed to is_alert_suppressed.

    Why: the old key was the whole holding row as JSON, which includes prices that change every hour,
    so the cooldown never matched. A new key per (symbol, recommendation) also lets an escalation
    (e.g. a new alert level) through immediately. Notices use (subject, reason) the same way.
    """
    return json.dumps({"symbol": symbol, "recommendation": recommendation}, sort_keys=True)


def holdings_issue_subject(issue: dict) -> str:
    """Cooldown subject for a holdings source problem; the watchdog and the digest share it, so it's sent once."""
    return f"{issue['source']} holdings"


def holdings_issue_body(issue: dict) -> str:
    """
    Telegram text for a holdings source problem (see PortfolioFetcher.holdings_issues). 2026-10-17: worded for the
    holdings' owner (user_message): the fix for an admin, "Contact Andres to address this issue." for anyone else.
    """
    return (
        f"\u26a0\ufe0f Finnbot couldn't read all of your holdings from {html.escape(issue['source'])}. "
        f"{html.escape(issue.get('user_message') or issue['message'])}\n\n"
        "Until this is fixed, holdings checks, alerts, and the digest's holdings section may be missing or incomplete."
    )


def _append(alert_logs: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """Concatenates log rows (skipping empty frames, which pandas warns about)."""
    frames = [frame for frame in (alert_logs, rows) if not frame.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=LOG_COLUMNS)


def _save(alert_logs: pd.DataFrame) -> None:
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = CSV_PATH.with_suffix(".tmp")
    alert_logs[LOG_COLUMNS].to_csv(tmp_path, index=False)
    os.replace(tmp_path, CSV_PATH)


def send_user_notice(
    user_name: str,
    subject: str,
    reason: str,
    body: str,
    alert_logs: pd.DataFrame,
    cooldown_hours: int = COOLDOWN_HOURS,
    urgent: bool = False,
) -> pd.DataFrame:
    """
    Sends `body` (Telegram HTML) to the user, at most once per cooldown for each (subject, reason), and returns the
    alert log with the new row. A failed send is only a warning and isn't logged, so it is retried next run.
    2026-10-23: `urgent` (only for something urgent and time-sensitive) starts it with "Urgent alert for <user>";
    any other message starts "Hi <user>," (send_telegram_message).
    """
    key = alert_key(subject, reason)
    if is_alert_suppressed(alert_logs, user_name, key, cooldown_hours=cooldown_hours):
        print(f"[SUPPRESSED] {subject} notice ({reason}) already sent to {user_name} within {cooldown_hours}h.")
        return alert_logs
    try:
        send_telegram_message(user_name, body, urgent=urgent)
    except (ValueError, requests.RequestException) as e:
        print(f"[WARNING] Could not send the {subject} notice to {user_name}: {e}")
        return alert_logs

    sent_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _append(alert_logs, pd.DataFrame([{"user": user_name, "alerts": key, "timestamp": sent_at}]))


def send_notice_now(
    user_name: str, subject: str, reason: str, body: str, cooldown_hours: int = COOLDOWN_HOURS, urgent: bool = False
) -> bool:
    """Like send_user_notice, but reads and saves the alert log file right away. Returns True if it was sent."""
    with _LOG_LOCK:
        alert_logs = load_and_prune_alert_logs(CSV_PATH)
        updated = send_user_notice(user_name, subject, reason, body, alert_logs, cooldown_hours, urgent=urgent)
        if len(updated) == len(alert_logs):
            return False
        _save(updated)
        return True


def alert_admins(event: str, detail: str) -> None:
    """
    Tells the OPS.ALERT_USERS that a run failed (`event`, e.g. "digest failed for Steven"), at most once per
    OPS.ALERT_COOLDOWN_HOURS for each event. The full traceback is in the run log; this only says where to look.
    Never raises, so reporting a failure can't cause another one.
    """
    ops = params_dict["OPS"]
    body = (
        f"\U0001f6e0\ufe0f Finnbot problem: {html.escape(event)}\n\n{html.escape(detail[:500])}\n\n"
        "Details are in the logs/ folder on the server."
    )
    for admin in ops["ALERT_USERS"]:
        try:
            send_notice_now(admin, "ops", event, body, cooldown_hours=ops["ALERT_COOLDOWN_HOURS"])
        except Exception as e:  # e.g. an unreadable alert log; the failure itself is already in the run log
            print(f"[WARNING] Could not send the failure alert to {admin}: {e}")


def merge_and_save_alert_logs(alert_logs: pd.DataFrame) -> pd.DataFrame:
    """Saves the watchdog's in-memory log, keeping any rows send_notice_now wrote to the file during the run."""
    with _LOG_LOCK:
        merged = _append(load_and_prune_alert_logs(CSV_PATH), alert_logs)
        merged = merged.drop_duplicates(subset=LOG_COLUMNS, ignore_index=True)
        _save(merged)
        return merged
