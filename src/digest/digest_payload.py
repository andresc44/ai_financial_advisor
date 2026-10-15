#main file for creating the daily digest payload and sending it to users. This file is called by a cron job to run daily.
# Claude-authored (2026-10-15): cron now runs this every hour at :10; src/digest/digest_schedule.py decides whether a
# Pre-Market or Post-Market digest is due (see docs/watchdog.md, "Running it").
import argparse
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import sys
from pathlib import Path
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src.personalized.user_profiles import USER_PROFILES
from src.personalized.user_settings import get_active_users
import os
from src.digest.create_html import html_main
from src.digest.send_gmail import send_html_email
from src.universal.universal_consolidated import get_universal_content
from src.personalized.personalized_consolidated import get_personal_content
from src.digest.pre_vs_post import determine_recipients, get_digest_period
from src.digest.digest_schedule import due_digest, record_sent, users_already_sent
from src.run_logging import name_thread, start_run_log
from src.telegram_alert.notices import alert_admins
import copy

class DigestPayload:
    def __init__(self, period: Optional[str] = None, day: Optional[date] = None):
        self.src_dir = Path(__file__).resolve().parent
        # Claude-authored (2026-10-13): one Eastern-time reading drives both the date and the
        # Pre-Market / Post-Market label, so the subject reads e.g. "Finnbot Pre-Market Digest 2026-10-14".
        # 2026-10-15: the schedule passes the period and market date it found due; the clock is only a fallback.
        now_et = datetime.now(ZoneInfo("America/New_York"))
        curr_date = day or now_et.date()
        digest_period = period or get_digest_period(now_et)
        self.payload = {
            "date": curr_date,
            "subject": f"Finnbot {digest_period} Digest {curr_date}",
            "title": "Daily Finnbot Digest",
        }
    def universal_content(self):
        self.payload.update(get_universal_content())
        return True

    def personal_content(self, user_name: str) -> dict:
        """
        Claude-authored (2026-10-15): the user's personal content, or {} when their holdings source is not
        SNAPTRADE or SHEETS. It neither reads nor changes self.payload, so several users can be built at once,
        alongside the universal content.
        """
        name_thread(user_name)  # Claude-authored (2026-10-15): log lines from this thread show the user
        user_profile = USER_PROFILES.get(user_name)
        if user_profile is None:
            raise ValueError(f"User '{user_name}' not found in USER_PROFILES.")

        holdings_source_value = user_profile.get("holdings_source")
        if holdings_source_value is None:
            raise ValueError(
                f"Holdings source value for user '{user_name}' is not configured"
                " in USER_PROFILES."
            )
        holdings_source = os.environ.get(holdings_source_value)
        if holdings_source not in ["SNAPTRADE", "SHEETS"]:
            return {}  # No personal content if holdings_source is not valid
        return get_personal_content(user_name)

    def personalized_content(self, user_name: str = "Andres", personal: Optional[dict] = None):
        # copy the current class and add personalized content based on user_name
        # Claude-authored (2026-10-15): `personal` is content already built by personal_content (e.g. in parallel)
        user_payload = copy.deepcopy(self.payload)
        user_payload["user_name"] = user_name
        user_payload.update(self.personal_content(user_name) if personal is None else personal)
        return user_payload

    def send_daily_digest_to(self, user_name: str = "Andres", personal: Optional[dict] = None):
        user_payload = self.personalized_content(user_name, personal)
        html_main(user_payload, self.src_dir)
        # populate_and_send_email(user_payload)  # Placeholder for actual email sending logic
        send_html_email(user_name, user_payload["subject"],
                        user_payload["date"],
                        self.src_dir,
                        )
        print(f"Sending daily digest to {user_name} with personalized payload")
        return True


def run_daily_digests(
    recipients: list[str], now: Optional[datetime] = None, check_only: bool = False
) -> None:
    """
    Builds and sends the digest for every recipient scheduled for this period (called by cron via __main__).

    Claude-authored (2026-10-15): cron runs this every hour. Nothing happens unless digest_schedule.due_digest says a
    Pre-Market or Post-Market digest is due right now; users who already received it are skipped, and each user is
    recorded as sent as soon as their email goes out. `check_only` prints who would receive it, without sending.
    """
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    print(f"[{stamp}] Starting digest check...")

    due = due_digest(now)
    if due is None:
        print(f"[{stamp}] No digest is due now (outside the Pre-/Post-Market windows, or no exchange trades today).")
        return
    day, period = due
    already_sent = users_already_sent(day, period)
    digest_recipients = [
        user for user in determine_recipients(recipients, period) if user not in already_sent
    ]

    if not digest_recipients:
        print(f"[{stamp}] {period} digest for {day}: no recipients left to send to. Exiting.")
        return
    if check_only:
        print(f"[{stamp}] {period} digest for {day} is due for: {digest_recipients} (check only; nothing sent).")
        return

    # Claude-authored (2026-10-15): the universal content and up to DIGEST.MAX_PARALLEL_USERS users are built at the
    # same time. Each user's Claude and Gemini calls use their own keys; fetching from the shared Finnhub / Twelve Data
    # keys is serialized by src/shared_locks.MARKET_DATA_LOCK. Emails go out as each user finishes, once the universal
    # content is ready.
    max_parallel_users = max(1, params_dict["DIGEST"]["MAX_PARALLEL_USERS"])
    print(
        f"[{stamp}] Active recipients found: {digest_recipients}. Building universal content and up to "
        f"{max_parallel_users} users' content in parallel..."
    )
    digest = DigestPayload(period=period, day=day)
    started = time.monotonic()
    sent, failed = [], []

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="universal") as universal_pool, \
            ThreadPoolExecutor(max_workers=max_parallel_users, thread_name_prefix="user") as user_pool:
        universal_future = universal_pool.submit(digest.universal_content)
        personal_futures = {user_pool.submit(digest.personal_content, user): user for user in digest_recipients}

        try:
            universal_ok = universal_future.result()
        except (Exception, SystemExit) as e:  # Unexpected crashes only: missing keys and API failures now come back as
            # universal_issues with empty picks (2026-10-16), and the digest still goes out
            print(f"[{stamp}] Universal content failed: {e!r}")
            traceback.print_exc()
            alert_admins(f"{period} digest aborted: universal content failed", repr(e))
            universal_ok = False

        if not universal_ok:
            for future in personal_futures:
                future.cancel()  # Users not started yet are skipped; running ones finish but nothing is sent
            print(f"[{stamp}] Failed to generate universal content. Digest aborted.")
        else:
            print(f"[{stamp}] Universal content loaded. Sending digests...")
            for future in as_completed(personal_futures):
                user = personal_futures[future]
                try:
                    digest.send_daily_digest_to(user, personal=future.result())
                    record_sent(day, period, user)  # Not sent again if a later hourly run is still in the window
                    sent.append(user)
                    print(f"[{stamp}] Successfully sent digest to {user}.")
                except Exception as e:
                    failed.append(user)
                    print(f"[{stamp}] ERROR sending digest to {user}: {e}")
                    traceback.print_exc()
                    alert_admins(f"{period} digest failed for {user}", repr(e))

    # Claude-authored (2026-10-15): one summary line per run, easy to grep in logs/digest.log
    print(
        f"[{stamp}] {period} digest run finished in {time.monotonic() - started:.0f}s: "
        f"sent {sent or 'none'}, failed {failed or 'none'}."
    )


if __name__ == "__main__":
    # Claude-authored (2026-10-15): cron runs this hourly at :10; users come from params_dict["ACTIVE_USERS"].
    parser = argparse.ArgumentParser(description="Finnbot daily digest")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only print whether a digest is due now and for whom; build and send nothing.",
    )
    parser.add_argument(
        "--now",
        type=datetime.fromisoformat,
        default=None,
        help="Pretend it is this ISO-8601 time with UTC offset, e.g. 2026-10-15T16:10:00-04:00 (requires --check).",
    )
    args = parser.parse_args()
    if args.now is not None and (args.now.tzinfo is None or not args.check):
        parser.error("--now must include a UTC offset and can only be used with --check.")
    # Claude-authored (2026-10-15): everything goes to logs/digest.log; an unexpected crash also alerts the admins
    start_run_log("digest")
    try:
        run_daily_digests(get_active_users(), now=args.now, check_only=args.check)
    except Exception as e:
        traceback.print_exc()
        alert_admins("digest run crashed", repr(e))
        raise
