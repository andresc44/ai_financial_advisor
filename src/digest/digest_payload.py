#main file for creating the daily digest payload and sending it to users. This file is called by a cron job to run daily.
# Claude-authored (2026-10-15): cron now runs this every hour at :10; src/digest/digest_schedule.py decides whether a
# Pre-Market or Post-Market digest is due (see docs/watchdog.md, "Running it").
import argparse
import json
import math
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
from src.usage_tracking import shared_usage  # Claude-authored (2026-10-22): the admin's costs
from src.personalized.personalized_consolidated import get_ideas_only_content, get_personal_content
from src.digest.pre_vs_post import determine_recipients, get_digest_period
from src.digest.digest_schedule import due_digest, ended_digests, record_sent, users_already_sent
from src.market_session import market_date
from src.run_logging import name_thread, start_run_log, write_run_status
from src.telegram_alert.earnings_reminder import send_earnings_reminders
from src.idea_positions import send_idea_scorecards
from src.telegram_alert.holiday_notice import send_holiday_notice
from src.telegram_alert.notices import alert_admins
from src.import_signup import sync_signups
import copy

# Claude-authored (2026-10-16): where --payload-only writes each user's payload (test_output/ is gitignored, since the
# files hold holdings and email addresses)
PAYLOAD_DIR = project_root / "test_output" / "digest_payloads"
PERIODS = ("Pre-Market", "Post-Market")


def _jsonable(value):
    """Payload values as plain JSON: NaN/inf become null, and numpy, pandas, and date values become numbers/strings."""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):  # numpy scalars
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if hasattr(value, "model_dump"):  # Pydantic models
        return _jsonable(value.model_dump())
    if hasattr(value, "isoformat"):  # date, datetime, pandas Timestamp
        return None if str(value) == "NaT" else value.isoformat()
    return str(value)


class DigestPayload:
    def __init__(self, period: Optional[str] = None, day: Optional[date] = None):
        self.src_dir = Path(__file__).resolve().parent
        # Claude-authored (2026-10-13): one Eastern-time reading drives both the date and the
        # Pre-Market / Post-Market label, so the subject reads e.g. "Finnbot Pre-Market Digest 2026-10-14".
        # 2026-10-15: the schedule passes the period and market date it found due; the clock is only a fallback.
        now_et = datetime.now(ZoneInfo("America/New_York"))
        curr_date = day or now_et.date()
        digest_period = period or get_digest_period(now_et)
        self.period = digest_period
        self.run_started_at = datetime.now(timezone.utc)  # Claude-authored (2026-10-22): the admin's "this run" costs
        self.payload = {
            "date": curr_date,
            "period": digest_period,  # Claude-authored (2026-10-17): for create_html's header and file name
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
        2026-10-21: NO_HOLDINGS users get an ideas-only digest (personalized_consolidated.get_ideas_only_content).
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
        if holdings_source == "NO_HOLDINGS":
            return get_ideas_only_content(user_name)
        if holdings_source not in ["SNAPTRADE", "SHEETS"]:
            return {}  # No personal content if holdings_source is not valid
        return get_personal_content(user_name)

    def personalized_content(self, user_name: str = "Andres", personal: Optional[dict] = None):
        # copy the current class and add personalized content based on user_name
        # Claude-authored (2026-10-15): `personal` is content already built by personal_content (e.g. in parallel)
        user_payload = copy.deepcopy(self.payload)
        user_payload["user_name"] = user_name
        user_payload.update(self.personal_content(user_name) if personal is None else personal)
        # Claude-authored (2026-10-22, Andres): the admin's costs (the shared keys: ideas and market news) beside the
        # user's own. Added here, after the shared content is done: the user's content can finish before it
        usage = user_payload.get("api_usage")
        user_payload["api_usage"] = {**(usage if isinstance(usage, dict) else {"currency": "USD"}),
                                     "admin": shared_usage(self.run_started_at)}
        return user_payload

    def send_daily_digest_to(self, user_name: str = "Andres", personal: Optional[dict] = None):
        user_payload = self.personalized_content(user_name, personal)
        # Claude-authored (2026-10-17): the rendered HTML, plain text, and AMP version go straight to the email
        rendered = html_main(user_payload, self.src_dir)
        send_html_email(user_name, user_payload["subject"], rendered.html, rendered.text, amp=rendered.amp)
        return True

    def save_payload(self, user_name: str, personal: Optional[dict] = None, out_dir: Optional[Path] = None) -> Path:
        """
        Claude-authored (2026-10-16): --payload-only. Writes the user's complete digest payload (universal + personal,
        exactly what send_daily_digest_to would hand to create_html) to out_dir as JSON, instead of rendering and
        emailing it. Named <user>_<date>_<period>_<HHMM ET>.json, so earlier runs aren't overwritten.
        """
        user_payload = self.personalized_content(user_name, personal)
        out_dir = out_dir or PAYLOAD_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        saved_at = datetime.now(ZoneInfo("America/New_York")).strftime("%H%M")
        path = out_dir / f"{user_name}_{self.payload['date']}_{self.period}_{saved_at}.json"
        path.write_text(json.dumps(_jsonable(user_payload), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


def send_saved_payload(path: Path, email: bool = True) -> str:
    """
    Claude-authored (2026-10-17): --from-payload. Renders a payload saved by --payload-only and emails it to that
    user's address in .env: the last two steps of a real run (create_html, then send_gmail), for testing the email
    without rebuilding the data. The subject starts with "[Test]", and nothing is recorded as sent. `email=False`
    (--no-email) only saves the HTML under src/digest/html_reports/. Returns a one-line summary.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    user_name = payload.get("user_name")
    if not user_name:
        raise ValueError(f"{path} has no user_name; pass a file saved by --payload-only.")
    rendered = html_main(payload, Path(__file__).resolve().parent)
    if not email:
        return f"Rendered {user_name}'s saved digest to {rendered.path} (not emailed)."
    send_html_email(user_name, f"[Test] {payload.get('subject', 'Finnbot Digest')}", rendered.html, rendered.text,
                    amp=rendered.amp)
    return f"Emailed {user_name}'s saved digest ({Path(path).name})."


def missed_digests(recipients: list[str], now: datetime, alert: bool = True) -> list[str]:
    """
    Claude-authored (2026-10-16): one line per digest from today whose send window has closed without reaching every
    recipient, e.g. "Pre-Market 2026-10-16 (1 user)". Catches what no single run reports as failed: the VM or cron
    being down for the whole window, or a user whose digest failed on every retry. Names are printed to the log
    only, because the returned lines go to healthchecks.io. `alert=False` (--check) skips the admin alert.
    """
    missed = []
    for day, period in ended_digests(now):
        missing = sorted(set(determine_recipients(recipients, period)) - users_already_sent(day, period))
        if missing:
            print(f"[ERROR] The {period} digest for {day} never went out to: {missing}.")
            if alert:
                alert_admins(f"{period} digest for {day} never went out", f"Not sent to: {', '.join(missing)}")
            missed.append(f"{period} {day} ({len(missing)} user{'s' if len(missing) > 1 else ''})")
    return missed


def run_daily_digests(
    recipients: list[str],
    now: Optional[datetime] = None,
    check_only: bool = False,
    payload_only: bool = False,
    period: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Builds and sends the digest for every recipient scheduled for this period (called by cron via __main__).

    Claude-authored (2026-10-15): cron runs this every hour. Nothing happens unless digest_schedule.due_digest says a
    Pre-Market or Post-Market digest is due right now; users who already received it are skipped, and each user is
    recorded as sent as soon as their email goes out. `check_only` prints who would receive it, without sending.

    2026-10-16: returns (ok, status) for the heartbeat (scripts/cron_job.sh). ok is False when the universal content
    or any user's digest failed, or an earlier digest today never went out (missed_digests). status is one line
    with counts only.

    2026-10-16, `payload_only` (--payload-only): builds everything the same way (universal content and users in
    parallel, recent watchdog evaluations reused), but saves each user's payload as JSON (DigestPayload.save_payload)
    instead of running create_html and emailing it. Nothing is recorded as sent, users who already received the
    digest are built anyway, and missed digests aren't checked, so a test run never changes what cron sends.
    `period` ("Pre-Market" / "Post-Market", payload-only) builds that digest now, even outside its send window.

    2026-10-21: on a weekday the NYSE is closed, a cron run from DIGEST.PRE_MARKET_TIME_ET on tells the admins on
    Telegram, once (src/telegram_alert/holiday_notice.py). Every cron run also reminds users of held stocks
    reporting earnings within 24 hours, once per report (src/telegram_alert/earnings_reminder.py).
    """
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    print(f"[{stamp}] Starting digest {'payload build' if payload_only else 'check'}...")
    if not (check_only or payload_only):
        send_holiday_notice(now)
        send_earnings_reminders(now)
        send_idea_scorecards(now)  # Claude-authored (2026-10-22): once a month, on Telegram (src/idea_positions.py)
    missed = [] if payload_only else missed_digests(recipients, now, alert=not check_only)
    missed_note = f" Missed today: {'; '.join(missed)}." if missed else ""

    due = (market_date(now), period) if period else due_digest(now)
    if due is None:
        hint = " Pass --period Pre-Market or --period Post-Market to build one anyway." if payload_only else ""
        print(f"[{stamp}] No digest is due now (outside the Pre-/Post-Market windows, or no exchange trades today).{hint}")
        return not missed, f"No digest due.{missed_note}"
    day, period = due
    already_sent = set() if payload_only else users_already_sent(day, period)
    digest_recipients = [
        user for user in determine_recipients(recipients, period) if user not in already_sent
    ]

    if not digest_recipients:
        print(f"[{stamp}] {period} digest for {day}: no recipients left to send to (a user only receives it when "
              f"<NAME>_RECEIVE_{'PRE' if period == 'Pre-Market' else 'POST'}MARKET_DIGEST=True). Exiting.")
        return not missed, f"{period} {day}: already sent to every recipient.{missed_note}"
    if check_only:
        print(f"[{stamp}] {period} digest for {day} is due for: {digest_recipients} (check only; nothing sent).")
        return not missed, f"{period} {day}: due for {len(digest_recipients)} recipient(s) (check only).{missed_note}"

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
            print(f"[{stamp}] Universal content loaded. {'Saving payloads' if payload_only else 'Sending digests'}...")
            for future in as_completed(personal_futures):
                user = personal_futures[future]
                try:
                    if payload_only:  # Claude-authored (2026-10-16): no create_html, no email, not recorded as sent
                        path = digest.save_payload(user, personal=future.result())
                        print(f"[{stamp}] Saved {user}'s payload to {path}")
                    else:
                        digest.send_daily_digest_to(user, personal=future.result())
                        record_sent(day, period, user)  # Not sent again if a later hourly run is still in the window
                        print(f"[{stamp}] Successfully sent digest to {user}.")
                    sent.append(user)
                except Exception as e:
                    failed.append(user)
                    print(f"[{stamp}] ERROR sending digest to {user}: {e}")
                    traceback.print_exc()
                    alert_admins(f"{period} digest failed for {user}", repr(e))

    # Claude-authored (2026-10-15): one summary line per run, easy to grep in logs/digest.log
    elapsed = time.monotonic() - started
    verb = "saved" if payload_only else "sent"
    print(f"[{stamp}] {period} digest run finished in {elapsed:.0f}s: {verb} {sent or 'none'}, failed {failed or 'none'}.")
    universal_note = "" if universal_ok else f" Universal content failed, so nothing was {verb}."
    status = f"{period} {day}: {verb} {len(sent)}, failed {len(failed)} in {elapsed:.0f}s.{universal_note}{missed_note}"
    return universal_ok and not failed and not missed, status


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
    # Claude-authored (2026-10-16): build real payloads without create_html or email, e.g. for a local test run
    parser.add_argument(
        "--payload-only",
        action="store_true",
        help="Build the due digest with live data and save each user's payload as JSON in "
             "test_output/digest_payloads/; no HTML, no email, nothing recorded as sent.",
    )
    parser.add_argument(
        "--period",
        choices=PERIODS,
        default=None,
        help="With --payload-only: build this digest now, even outside its send window.",
    )
    # Claude-authored (2026-10-17): test the email itself from a payload saved by --payload-only
    parser.add_argument(
        "--from-payload",
        type=Path,
        default=None,
        metavar="PATH",
        help="Render this saved payload JSON and email it to its user (subject starts with [Test]); fetches no "
             "data and records nothing as sent.",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="With --from-payload: only save the HTML under src/digest/html_reports/.",
    )
    args = parser.parse_args()
    if args.from_payload and (args.check or args.payload_only or args.period or args.now):
        parser.error("--from-payload can't be combined with --check, --now, --payload-only, or --period.")
    if args.no_email and not args.from_payload:
        parser.error("--no-email can only be used with --from-payload.")
    if args.from_payload:
        print(send_saved_payload(args.from_payload, email=not args.no_email))
        sys.exit(0)
    if args.now is not None and (args.now.tzinfo is None or not args.check):
        parser.error("--now must include a UTC offset and can only be used with --check.")
    if args.payload_only and args.check:
        parser.error("--payload-only and --check can't be combined.")
    if args.period and not args.payload_only:
        parser.error("--period can only be used with --payload-only.")
    # Claude-authored (2026-10-15): everything goes to logs/digest.log; an unexpected crash also alerts the admins
    start_run_log("digest")
    reports_status = not (args.check or args.payload_only)  # Only cron runs feed the healthchecks.io heartbeat
    # Claude-authored (2026-10-22): new or edited sign-up form answers go into .env first, and form users with
    # nothing required missing into ACTIVE_USERS, before the users are read (src/import_signup.py). Cron runs only.
    if reports_status:
        sync_signups()
    try:
        ok, status = run_daily_digests(
            get_active_users(), now=args.now, check_only=args.check, payload_only=args.payload_only, period=args.period
        )
    except Exception as e:
        traceback.print_exc()
        alert_admins("digest run crashed", repr(e))
        if reports_status:
            write_run_status("digest", f"Run crashed: {type(e).__name__} (traceback in logs/digest.log).")
        raise
    # Claude-authored (2026-10-16): the exit code and status line are what the healthchecks.io heartbeat reports
    if reports_status:
        write_run_status("digest", status)
    sys.exit(0 if ok else 1)
