#File to run hourly to check for alerts and send Telegram messages to users based on their profiles.
"""
Holdings watchdog: re-evaluates each user's holdings on their own schedule and sends Telegram alerts.

Authorship:
- Original version by Andres: hourly cron design, Telegram eligibility rules, alert-log pruning
  (load_and_prune_alert_logs) and cooldown check (is_alert_suppressed) - both kept unchanged, and moved to
  src/telegram_alert/notices.py on 2026-10-15 so the digest and failure alerts can share them.
- Per-user flow rewritten by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-13, at Andres's request, to add:
  * per-user check intervals and market sessions ("regular" / "extended" / "24_7") from one hourly cron job,
  * "only when needed" gates so Finnhub / Twelve Data / Claude run only for stocks that changed,
  * an hourly price tripwire between scheduled checks, and a per-user daily cap on watchdog Claude calls,
  * fixes: alerts are now sent only AFTER the cooldown check (previously sent before it), duplicates are keyed
    on (user, symbol, recommendation) instead of whole rows whose prices change every hour, and one user's
    failure no longer stops the loop or prevents the alert log from being saved.
- 2026-10-15 (Claude): API key failures. The user's Anthropic key is checked (free) before any Finnhub /
  Twelve Data / Claude spend; a check stopped by a key problem is neither counted against the daily Claude cap
  nor marked done, and the user gets one Telegram notice per cooldown explaining what to fix.
- 2026-10-15 (Claude): users are checked in parallel threads (WATCHDOG.MAX_PARALLEL_USERS), so alerts for later
  users aren't delayed by earlier users' Claude evaluations as the number of users grows.
- 2026-10-15 (Claude): a holdings source problem (SnapTrade connection disabled or unreachable, stale sync,
  unreadable Google Sheet) is sent to the user as a Telegram notice instead of looking like an empty portfolio.
See docs/watchdog.md for the full flow, settings, and crontab lines.

Usage (from the project root):
  venv/bin/python src/telegram_alert/hourly_watchdog.py              # normal run (cron, hourly at :45)
  venv/bin/python src/telegram_alert/hourly_watchdog.py --dry-run    # show decisions only
  venv/bin/python src/telegram_alert/hourly_watchdog.py --dry-run --now 2026-10-14T13:45:00-04:00
A dry run still reads holdings and free price/news data, but makes no Claude calls, sends nothing, and writes nothing.
"""
import argparse
import html
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.market_session import block_id, is_in_session, market_date, market_phase
from src.parameters import params_dict
from src.personalized.add_metrics import HoldingsDataFetcher
from src.personalized.api_errors import ApiKeyUnusable
from src.personalized.holdings_state import (
    add_claude_calls,
    claude_calls_today,
    get_user_state,
    load_state,
    update_user_state,
)
from src.personalized.pull_holdings_data import PortfolioFetcher
from src.personalized.recommendation import resolve_llm_key
from src.personalized.user_profiles import USER_PROFILES
from src.personalized.user_settings import get_active_users, get_watchdog_settings
from src.run_logging import name_thread, start_run_log, write_run_status
from src.shared_locks import MARKET_DATA_LOCK
from src.telegram_alert import notices
from src.telegram_alert.notices import (  # Shared alert log; names kept importable from this module
    COOLDOWN_HOURS,
    RETENTION_DAYS,
    alert_key,
    holdings_issue_body,
    holdings_issue_subject,
    is_alert_suppressed,
    load_and_prune_alert_logs,
    alert_admins,
    merge_and_save_alert_logs,
    send_user_notice,
)
from src.telegram_alert.send_telegram import send_telegram_message

WATCHDOG_PARAMS = params_dict["WATCHDOG"]

# Claude-authored: when the daily Claude cap is hit, stocks whose last recommendation was most severe go first.
# Never-evaluated stocks (None) rank just below risk_alert because their risk is unknown.
SEVERITY_RANK = {"urgent_sell": 4, "risk_alert": 3, None: 2, "trim": 1, "hold": 0}


# ==============================================================================================
# Claude-authored (2026-10-13) from here down: per-user scheduling, change detection, and alerting.
# ==============================================================================================

def is_user_eligible(user_name: str) -> bool:
    """A user is watched when they have a SNAPTRADE/SHEETS holdings source and a Telegram chat ID in .env."""
    profile = USER_PROFILES[user_name]
    source = os.environ.get(profile.get("holdings_source") or "")
    chat_id_key = profile.get("telegram_chat_id")
    chat_id = os.environ.get(chat_id_key) if isinstance(chat_id_key, str) else None
    return source in ["SNAPTRADE", "SHEETS"] and chat_id not in [None, ""]


def _row_price(row: pd.Series) -> Optional[float]:
    """Session price when the watchdog supplied one, otherwise current price."""
    for col in ("session_price", "current_price"):
        value = row.get(col)
        if value is not None and not pd.isna(value) and float(value) > 0:
            return float(value)
    return None


def _natr_move(price: Optional[float], previous_price: Optional[float], natr: Any) -> Optional[float]:
    """Absolute % move since `previous_price`, in multiples of NATR (the stock's typical daily % range)."""
    if price is None or not previous_price or natr is None or pd.isna(natr) or float(natr) <= 0:
        return None
    return abs(price / previous_price - 1) * 100 / float(natr)


def select_symbols_to_evaluate(
    snapshot_df: pd.DataFrame,
    symbol_states: Dict[str, dict],
    is_due: bool,
    tripwire: bool,
    now: datetime,
) -> Dict[str, str]:
    """
    Returns {symbol: reason} for stocks that need a fresh Claude evaluation.

    On a scheduled (due) check: never-evaluated stocks, evaluations older than MAX_EVALUATION_AGE_HOURS,
    moves >= REEVAL_NATR_MULTIPLE x NATR since the last evaluation, or earnings within EARNINGS_WINDOW_DAYS.
    Tripwire (any run, if enabled): moves >= TRIPWIRE_NATR_MULTIPLE x NATR, or price below the last SAR stop.
    Everything else keeps its last recommendation.
    """
    reasons: Dict[str, str] = {}
    today = market_date(now)

    for _, row in snapshot_df.drop_duplicates("symbol").iterrows():
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue

        last = symbol_states.get(symbol)
        price = _row_price(row)
        move = _natr_move(price, (last or {}).get("price"), row.get("natr"))

        if is_due:
            if last is None:
                reasons[symbol] = "never evaluated"
                continue
            age_hours = (now - datetime.fromisoformat(last["evaluated_at"])).total_seconds() / 3600
            earnings = last.get("next_earnings_date")
            days_to_earnings = (date.fromisoformat(earnings) - today).days if earnings else None

            if age_hours >= WATCHDOG_PARAMS["MAX_EVALUATION_AGE_HOURS"]:
                reasons[symbol] = f"last evaluated {age_hours:.0f}h ago"
            elif move is not None and move >= WATCHDOG_PARAMS["REEVAL_NATR_MULTIPLE"]:
                reasons[symbol] = f"moved {move:.1f}x NATR since last evaluation"
            elif days_to_earnings is not None and 0 <= days_to_earnings <= WATCHDOG_PARAMS["EARNINGS_WINDOW_DAYS"]:
                reasons[symbol] = f"earnings in {days_to_earnings} day(s)"
            if symbol in reasons:
                continue

        if tripwire and last is not None:
            sar_stop = last.get("sar_stop")
            if move is not None and move >= WATCHDOG_PARAMS["TRIPWIRE_NATR_MULTIPLE"]:
                reasons[symbol] = f"tripwire: moved {move:.1f}x NATR since last evaluation"
            elif sar_stop is not None and price is not None and price < sar_stop:
                reasons[symbol] = f"tripwire: price {price:.2f} below SAR stop {sar_stop:.2f}"

    return reasons


def symbols_with_news_since(symbol_states: Dict[str, dict], symbols: Iterable[str]) -> Dict[str, str]:
    """
    Returns {symbol: reason} for stocks with Finnhub headlines newer than their last evaluation.
    Used on scheduled checks while the market is overnight/closed (24_7 sessions), when prices barely move
    and news is the only meaningful change signal. One Finnhub call per stock.
    """
    candidates = [s for s in symbols if s in symbol_states]
    if not candidates:
        return {}
    try:
        fetcher = HoldingsDataFetcher(tickers=[s.replace(":", "-") for s in candidates])
    except ValueError as e:
        print(f"[WARNING] Skipping overnight news check: {e}")
        return {}

    reasons: Dict[str, str] = {}
    with MARKET_DATA_LOCK:  # Claude-authored (2026-10-15): shared Finnhub key; users run in parallel threads
        for symbol in candidates:
            since = datetime.fromisoformat(symbol_states[symbol]["evaluated_at"]).timestamp()
            articles = fetcher.fetch_finnhub_data(symbol.replace(":", "-"), fetch_news=True)
            newest = max((a.get("datetime") or 0 for a in articles if isinstance(a, dict)), default=0)
            if newest > since:
                reasons[symbol] = "new headlines since last evaluation"
    return reasons


def apply_daily_claude_limit(
    reasons: Dict[str, str], symbol_states: Dict[str, dict], calls_used: int
) -> tuple[Dict[str, str], List[str]]:
    """Trims `reasons` to the user's remaining daily Claude calls, keeping the most severe stocks first."""
    remaining = max(0, WATCHDOG_PARAMS["MAX_CLAUDE_CALLS_PER_USER_DAY"] - calls_used)
    if len(reasons) <= remaining:
        return reasons, []

    ordered = sorted(
        reasons,
        key=lambda s: SEVERITY_RANK.get((symbol_states.get(s) or {}).get("recommendation"), 2),
        reverse=True,
    )
    return {s: reasons[s] for s in ordered[:remaining]}, ordered[remaining:]


def send_new_alerts(
    user_name: str,
    symbol_states: Dict[str, dict],
    held_symbols: set,
    alert_logs: pd.DataFrame,
    now: datetime,
    cooldown_hours: int = COOLDOWN_HOURS,
) -> pd.DataFrame:
    """
    Sends one Telegram message covering every held stock whose latest recommendation is an alert level
    (ALERT_RECOMMENDATIONS), evaluated within MAX_EVALUATION_AGE_HOURS, and not already sent within the
    cooldown. Logs only after a successful send, so a failed send is retried on the next run.

    Alerts come from the latest recorded recommendation (not only this run's evaluations), so an urgent_sell
    from the twice-daily digest also alerts, and a failed Telegram send is not lost.
    """
    alert_levels = set(WATCHDOG_PARAMS["ALERT_RECOMMENDATIONS"])
    max_age = timedelta(hours=WATCHDOG_PARAMS["MAX_EVALUATION_AGE_HOURS"])

    pending = []
    for symbol in sorted(held_symbols):
        record = symbol_states.get(symbol)
        if not record or record.get("recommendation") not in alert_levels:
            continue
        if now - datetime.fromisoformat(record["evaluated_at"]) > max_age:
            continue
        key = alert_key(symbol, record["recommendation"])
        if is_alert_suppressed(alert_logs, user_name, key, cooldown_hours=cooldown_hours):
            print(f"[SUPPRESSED] {record['recommendation']} for {symbol} already sent to {user_name} within {cooldown_hours}h.")
            continue
        pending.append((symbol, record, key))

    if not pending:
        return alert_logs

    # Telegram uses parse_mode=HTML, so model-written rationale text must be escaped
    lines = [
        f"<b>{html.escape(symbol)}</b> - {html.escape(record['recommendation'].replace('_', ' ').upper())}: "
        f"{html.escape(record.get('rationale') or '')}"
        for symbol, record, _ in pending
    ]
    body = f"🚨🚨 ACTION REQUIRED: Finnbot urgent alerts for {html.escape(user_name)}:\n\n" + "\n\n".join(lines)
    send_telegram_message(user_name, body)  # Raises on failure: nothing is logged, so it is retried next run
    print(f"[SENT] {len(pending)} alert(s) sent to {user_name}.")

    sent_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_rows = pd.DataFrame(
        [{"user": user_name, "alerts": key, "timestamp": sent_at} for _, _, key in pending]
    )
    return pd.concat([alert_logs, new_rows], ignore_index=True)


def send_key_notice(
    user_name: str,
    key_error: ApiKeyUnusable,
    alert_logs: pd.DataFrame,
    cooldown_hours: int = COOLDOWN_HOURS,
) -> pd.DataFrame:
    """
    Claude-authored (2026-10-15): tells the user, at most once per cooldown for each provider and reason, that
    their API key stopped holdings evaluations, so a missing, expired, or out-of-credit key never fails silently.
    A failed send is only a warning (and is retried next run), so it can't undo the alerts already sent.
    2026-10-17: worded for the user (text_for): the fix for an admin, "Contact Andres ..." for anyone else.
    """
    body = (
        f"⚠️ Finnbot could not evaluate your holdings. {html.escape(key_error.text_for(user_name))}\n\n"
        "Holdings are not re-evaluated until this is fixed. Recommendations from the last "
        f"{WATCHDOG_PARAMS['MAX_EVALUATION_AGE_HOURS']}h can still trigger alerts."
    )
    return send_user_notice(
        user_name, f"{key_error.provider} API key", key_error.reason, body, alert_logs, cooldown_hours
    )


def send_holdings_notices(
    user_name: str, issues: list[dict], alert_logs: pd.DataFrame, cooldown_hours: int = COOLDOWN_HOURS
) -> pd.DataFrame:
    """Claude-authored (2026-10-15): one notice per (source, reason) per cooldown, shared with the digest's notices."""
    for issue in issues:
        alert_logs = send_user_notice(
            user_name, holdings_issue_subject(issue), issue["reason"], holdings_issue_body(issue), alert_logs,
            cooldown_hours,
        )
    return alert_logs


def check_user(
    user_name: str,
    now: datetime,
    alert_logs: pd.DataFrame,
    cooldown_hours: int = COOLDOWN_HOURS,
    dry_run: bool = False,
) -> pd.DataFrame:
    """Runs the gates for one user (cheapest first), evaluates only what changed, and sends any new alerts."""
    tag = f"[{user_name}]"
    settings = get_watchdog_settings(user_name)

    # Gate: watchdog switched off for this user
    if settings.interval_hours == 0:
        print(f"{tag} Watchdog off (interval 0). Skipping.")
        return alert_logs

    # Gate: outside the user's market session
    if not is_in_session(now, settings.session):
        print(f"{tag} Outside the '{settings.session}' session. Skipping.")
        return alert_logs

    # Gate: not due and no tripwire -> nothing to do this hour
    phase = market_phase(now)
    block = block_id(now, settings.interval_hours, settings.session)
    user_state = get_user_state(load_state(), user_name)
    is_due = block is not None and block != user_state["last_block"]
    print(
        f"{tag} session={settings.session} phase={phase} interval={settings.interval_hours}h "
        f"tripwire={settings.tripwire} due={is_due} block={block}"
    )
    if not is_due and not settings.tripwire:
        return alert_logs

    # Cheap snapshot: holdings, P&L, RSI/NATR (no Finnhub / Twelve Data / Claude)
    fetcher = PortfolioFetcher(user_name=user_name)
    # Claude-authored (2026-10-15): a broken connection or unreadable sheet is reported, not mistaken for no holdings
    for issue in fetcher.holdings_issues:
        print(f"{tag} [ERROR] {issue['source']} holdings problem ({issue['reason']}): {issue['message']}")
    if fetcher.holdings_issues and not dry_run:
        alert_logs = send_holdings_notices(user_name, fetcher.holdings_issues, alert_logs, cooldown_hours)
    if fetcher.full_data is None or fetcher.full_data.empty:
        # An outage looks the same as an empty portfolio, so don't mark this check as done
        print(f"{tag} [WARNING] No holdings returned (empty portfolio or data-source outage). Will retry next run.")
        return alert_logs
    fetcher.full_data = fetcher.add_session_prices(fetcher.full_data, phase)
    held_symbols = set(fetcher.full_data["symbol"].dropna())

    # Decide which stocks actually need Claude
    symbol_states = user_state["symbols"]
    reasons = select_symbols_to_evaluate(fetcher.full_data, symbol_states, is_due, settings.tripwire, now)
    if is_due and phase in ("overnight", "closed"):
        unselected = [s for s in held_symbols if s not in reasons]
        reasons.update(symbols_with_news_since(symbol_states, unselected))

    today = market_date(now)
    reasons, skipped = apply_daily_claude_limit(reasons, symbol_states, claude_calls_today(user_state, today))
    for symbol, reason in reasons.items():
        print(f"{tag}   evaluate {symbol}: {reason}")
    if skipped:
        print(f"{tag} [WARNING] Daily Claude cap reached; not evaluated this run: {skipped}")
    if not reasons:
        print(f"{tag} Nothing changed enough to re-evaluate; keeping last recommendations.")

    if dry_run:
        print(f"{tag} Dry run: no Claude calls, no Telegram, no state written.")
        return alert_logs

    # Full evaluation of the selected stocks only (market context + Claude); results are recorded in the
    # state file by add_recommendations.
    # Claude-authored (2026-10-15): the Anthropic key is checked first (free), so a missing, invalid, expired, or
    # unpermitted key costs no Finnhub / Twelve Data / Claude calls. An empty credit balance or a spend limit
    # only shows up on the first Claude request; evaluations finished before it are kept and counted.
    key_error: Optional[ApiKeyUnusable] = None
    claude_calls = 0
    if reasons:
        try:
            resolve_llm_key(user_name, fetcher.llm_settings["provider"])  # 2026-10-16: Claude or DeepSeek key
        except ApiKeyUnusable as e:
            key_error = e
        else:
            payload = fetcher.get_holdings_digest_payload(
                desired_columns=["symbol", "recommendation", "recommendation_rationale"],
                symbols=list(reasons),
            )
            key_error = fetcher.llm_key_error
            evaluated = {h["symbol"] for h in payload["holdings"] if isinstance(h.get("recommendation"), str)}
            claude_calls = len(evaluated) if key_error else len(reasons)  # Key-rejected requests aren't billed
        if key_error is not None:
            print(f"{tag} [ERROR] {key_error} The scheduled check will be retried next run.")

    def _bookkeeping(state: dict) -> None:
        if is_due and key_error is None:  # A check stopped by a key problem stays due, so it's retried next run
            state["last_block"] = block
        add_claude_calls(state, today, claude_calls)
        state["symbols"] = {s: v for s, v in state["symbols"].items() if s in held_symbols}  # forget sold positions

    update_user_state(user_name, _bookkeeping)

    latest_symbol_states = get_user_state(load_state(), user_name)["symbols"]
    alert_logs = send_new_alerts(user_name, latest_symbol_states, held_symbols, alert_logs, now, cooldown_hours)
    if key_error is not None and key_error.blocks_run:
        alert_logs = send_key_notice(user_name, key_error, alert_logs, cooldown_hours)
    return alert_logs


def process_user_alerts(
    retention_days: int = RETENTION_DAYS,
    cooldown_hours: int = COOLDOWN_HOURS,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Main execution function accepting custom retention and cooldown windows.

    Claude-authored (2026-10-13): `now` (timezone-aware) and `dry_run` were added as fallback
    parameters so the schedule can be tested without waiting for real market hours.
    2026-10-16: returns (ok, status) for the healthchecks.io heartbeat: ok is False when any user's check crashed.
    A user's own key problem (expired, out of credits) is not a failure here; they get their own notice.
    """
    now = now or datetime.now(timezone.utc)
    notices.CSV_DIR.mkdir(parents=True, exist_ok=True)

    # Load log history and prune entries older than retention_days
    alert_logs = load_and_prune_alert_logs(
        notices.CSV_PATH, retention_days=retention_days
    )

    started = time.monotonic()
    failed_users: List[str] = []

    def _check(user: str) -> pd.DataFrame:
        name_thread(user)  # Claude-authored (2026-10-15): log lines from this thread show the user
        print(f"Checking for alerts for user: {user}")
        try:
            return check_user(user, now, alert_logs, cooldown_hours=cooldown_hours, dry_run=dry_run)
        except Exception as e:
            # One user's failure (API outage, bad data) must not stop the others or lose the alert log
            print(f"[ERROR] Watchdog check failed for {user}; continuing with other users.")
            traceback.print_exc()
            failed_users.append(user)
            if not dry_run:
                alert_admins(f"watchdog check failed for {user}", repr(e))
            return alert_logs

    # Claude-authored (2026-10-15): users are checked in parallel (WATCHDOG.MAX_PARALLEL_USERS at a time), so one
    # user's Claude evaluation doesn't delay another user's alerts. Each check only appends its own rows to the
    # alert log it was given, so those rows are collected and added once at the end.
    eligible = [user for user in get_active_users() if is_user_eligible(user)]  # params_dict["ACTIVE_USERS"]
    if eligible:
        max_workers = max(1, min(WATCHDOG_PARAMS["MAX_PARALLEL_USERS"], len(eligible)))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="watchdog") as pool:
            results = list(pool.map(_check, eligible))
        new_rows = [result.iloc[len(alert_logs):] for result in results]
        new_rows = [rows for rows in new_rows if not rows.empty]
        if new_rows:
            alert_logs = pd.concat([alert_logs, *new_rows], ignore_index=True)

    if dry_run:
        print("Dry run: alert log not written.")
        return not failed_users, f"Dry run: {len(eligible)} user(s) eligible, {len(failed_users)} failed."

    # Save cleaned history back to CSV (Claude-authored 2026-10-15: merged with notices the run saved directly)
    merge_and_save_alert_logs(alert_logs)
    print(f"Alert log updated at: {notices.CSV_PATH}")
    elapsed = time.monotonic() - started
    print(f"Watchdog run finished in {elapsed:.0f}s: checked {len(eligible)} user(s), failed {failed_users or 'none'}.")
    return not failed_users, f"Checked {len(eligible)} user(s), {len(failed_users)} failed, in {elapsed:.0f}s."


if __name__ == "__main__":
    # Cron runs this hourly at the 45th minute; each user's own interval and session decide whether they are checked
    parser = argparse.ArgumentParser(description="Finnbot holdings watchdog")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which users are due and which stocks would be evaluated; no Claude, Telegram, or writes.",
    )
    parser.add_argument(
        "--now",
        type=datetime.fromisoformat,
        default=None,
        help="Pretend it is this ISO-8601 time with UTC offset, e.g. 2026-10-14T13:45:00-04:00 (requires --dry-run).",
    )
    args = parser.parse_args()
    if args.now is not None and (args.now.tzinfo is None or not args.dry_run):
        parser.error("--now must include a UTC offset and can only be used with --dry-run.")

    # Claude-authored (2026-10-15): everything goes to logs/watchdog.log; an unexpected crash also alerts the admins
    start_run_log("watchdog")
    try:
        ok, status = process_user_alerts(
            retention_days=RETENTION_DAYS, cooldown_hours=COOLDOWN_HOURS, now=args.now, dry_run=args.dry_run
        )
    except Exception as e:
        traceback.print_exc()
        if not args.dry_run:
            alert_admins("watchdog run crashed", repr(e))
            write_run_status("watchdog", f"Run crashed: {type(e).__name__} (traceback in logs/watchdog.log).")
        raise
    # Claude-authored (2026-10-16): the exit code and status line are what the healthchecks.io heartbeat reports
    if not args.dry_run:
        write_run_status("watchdog", status)
    sys.exit(0 if ok else 1)
