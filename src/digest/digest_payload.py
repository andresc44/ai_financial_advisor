#main file for creating the daily digest payload and sending it to users. This file is called by a cron job to run daily.
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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
import copy

class DigestPayload:
    def __init__(self):
        self.src_dir = Path(__file__).resolve().parent
        # Claude-authored (2026-10-13): one Eastern-time reading drives both the date and the
        # Pre-Market / Post-Market label, so the subject reads e.g. "Finnbot Pre-Market Digest 2026-10-14".
        now_et = datetime.now(ZoneInfo("America/New_York"))
        curr_date = now_et.date()
        digest_period = get_digest_period(now_et)
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


def run_daily_digests(recipients: list[str]) -> None:
    """Builds and sends the digest for every recipient scheduled for this period (called by cron via __main__)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] Starting digest check...")

    digest_recipients = determine_recipients(recipients)

    if not digest_recipients:
        print(f"[{now}] No recipients scheduled for this period. Exiting.")
        return

    # Claude-authored (2026-10-15): the universal content and up to DIGEST.MAX_PARALLEL_USERS users are built at the
    # same time. Each user's Claude and Gemini calls use their own keys; fetching from the shared Finnhub / Twelve Data
    # keys is serialized by src/shared_locks.MARKET_DATA_LOCK. Emails go out as each user finishes, once the universal
    # content is ready.
    max_parallel_users = max(1, params_dict["DIGEST"]["MAX_PARALLEL_USERS"])
    print(
        f"[{now}] Active recipients found: {digest_recipients}. Building universal content and up to "
        f"{max_parallel_users} users' content in parallel..."
    )
    digest = DigestPayload()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="universal") as universal_pool, \
            ThreadPoolExecutor(max_workers=max_parallel_users, thread_name_prefix="user") as user_pool:
        universal_future = universal_pool.submit(digest.universal_content)
        personal_futures = {user_pool.submit(digest.personal_content, user): user for user in digest_recipients}

        try:
            universal_ok = universal_future.result()
        except (Exception, SystemExit) as e:  # get_universal_content calls sys.exit(1) if its data fetcher can't start
            print(f"[{now}] Universal content failed: {e!r}")
            universal_ok = False

        if not universal_ok:
            for future in personal_futures:
                future.cancel()  # Users not started yet are skipped; running ones finish but nothing is sent
            print(f"[{now}] Failed to generate universal content. Digest aborted.")
        else:
            print(f"[{now}] Universal content loaded. Sending digests...")
            for future in as_completed(personal_futures):
                user = personal_futures[future]
                try:
                    digest.send_daily_digest_to(user, personal=future.result())
                    print(f"[{now}] Successfully sent digest to {user}.")
                except Exception as e:
                    print(f"[{now}] ERROR sending digest to {user}: {e}")


if __name__ == "__main__":
    run_daily_digests(get_active_users())  # Claude-authored (2026-10-15): params_dict["ACTIVE_USERS"]
