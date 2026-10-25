
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sys
import os
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.ops.run_logging import debug
from src.market.fx_rates import usd_rates
from src.personalized.user_profiles import USER_PROFILES
from src.personalized.add_metrics import cached_company_names
from src.personalized.pull_holdings_data import PortfolioFetcher
from src.personalized.api_errors import ApiKeyUnusable
from src.personalized.news_reuse import as_items, news_to_refresh, remember_news, same_day_news
from src.personalized.portfolio_review import review_portfolio, review_settings
from src.tracking.idea_positions import attach_idea_origins
from src.personalized.symbol_specific_news import SymbolNewsItem, fetch_symbol_news
from src.personalized.user_settings import get_gemini_api_key, get_home_currency, get_llm_api_key, wants_holding_news
from src.telegram_alert.notices import holdings_issue_body, holdings_issue_subject, send_notice_now
from src.tracking.usage_tracking import usage_summary

def _holding_companies(full_data: pd.DataFrame) -> dict[str, Optional[str]]:
    """
    Claude-authored (2026-10-15): {symbol: company name} for each held stock, once even when held in several
    accounts. Built from the raw holdings so the news can start before market data and Claude run.
    description can be None/NaN from SnapTrade; the news then searches by ticker.
    """
    if full_data is None or full_data.empty or "symbol" not in full_data.columns:
        return {}
    descriptions = full_data["description"] if "description" in full_data.columns else [None] * len(full_data)
    companies: dict[str, Optional[str]] = {}
    for symbol, description in zip(full_data["symbol"], descriptions):
        if isinstance(symbol, str) and symbol and symbol not in companies:
            companies[symbol] = description if isinstance(description, str) and description.strip() else None
    # Claude-authored (2026-10-17): Yahoo's name from earlier runs wins over the brokerage's, which can be wrong
    companies.update(cached_company_names(list(companies)))
    return companies

def fetch_holdings_news(
    user_name: str, companies: dict[str, Optional[str]]
) -> tuple[dict[str, list[SymbolNewsItem]], Optional[ApiKeyUnusable]]:
    """
    Claude-authored (2026-10-14, split out 2026-10-15): up to DIGEST.SYMBOL_NEWS_BULLETS news items per stock from
    Gemini with the user's own key, plus the key error (missing, invalid, expired, no credits, exhausted quota)
    that stopped them, or None. Only stocks whose news was fetched are in the result.
    """
    if not companies:
        return {}, None
    try:
        return fetch_symbol_news(companies, api_key=get_gemini_api_key(user_name), user_name=user_name)
    except ApiKeyUnusable as e:  # Key not set
        print(f"[ERROR] {e} Holding news skipped.")
        return {}, e

def settle_holdings_news(
    user_name: str,
    companies: dict[str, Optional[str]],
    reused: dict[str, dict],
    fresh: dict[str, list[SymbolNewsItem]],
    key_error: Optional[ApiKeyUnusable],
    holdings: Optional[list[dict]],
) -> tuple[dict[str, list[SymbolNewsItem]], Optional[ApiKeyUnusable]]:
    """
    Claude-authored (2026-10-23): every holding's news for the digest, once the holdings are evaluated. `reused` is
    the news fetched earlier today that wasn't asked for again (news_reuse.same_day_news) and `fresh` what was. A
    reused stock that moved more than DIGEST.NEWS_REUSE.MAX_MOVE_NATR, changed signal, or reported earnings is fetched
    again now (if that fails, the earlier news is shown). Freshly fetched news is saved for later digests that day.
    Returns (news, the first key error).
    """
    refresh = news_to_refresh(reused, holdings)
    if refresh:  # 2026-10-24: counts only, so the log doesn't list what the user holds
        debug(f"[news] Fetching news again for {len(refresh)} holding(s).")
    if refresh and not (key_error is not None and key_error.blocks_run):
        more, more_error = fetch_holdings_news(user_name, {symbol: companies.get(symbol) for symbol in refresh})
        fresh = {**fresh, **more}
        key_error = key_error or more_error
    remember_news(user_name, fresh, holdings)
    kept = {symbol: as_items(entry) for symbol, entry in reused.items() if symbol not in fresh}
    if kept:
        debug(f"[news] Showing news fetched earlier today for {len(kept)} holding(s).")
    return {**kept, **fresh}, key_error

def attach_news_bullets(holdings: list[dict], news: dict[str, list[SymbolNewsItem]]) -> list[dict]:
    """
    Claude-authored (2026-10-14): adds 'news_bullets' to each holding:
    {"priority": "HIGH" | "MEDIUM" | "LOW", "text": "What happened and its direct impact.", "source_url": "..." | None}
    dicts, highest priority first. source_url is only set when it matches a page Google Search grounding returned
    (see symbol_specific_news._verify_source_urls). Stocks without news get an empty list.
    """
    for holding in holdings or []:
        holding["news_bullets"] = [
            {
                "priority": item.priority,
                "text": item.summary,
                "source_url": item.source_url,
            }
            for item in news.get(holding.get("symbol"), [])
        ]
    return holdings or []

def _home(user_name: str) -> tuple[str, float]:
    """
    Claude-authored (2026-10-21): (home currency, USD per unit) for the portfolio review, which writes its amounts
    in the currency the digest's overview uses; USD when that currency has no rate (as the overview does).
    """
    code = get_home_currency(user_name)
    rate = usd_rates([code]).get(code) if code != "USD" else 1.0
    return (code, rate) if rate else ("USD", 1.0)

def get_portfolio_review(
    user_name: str,
    holdings: list[dict],
    portfolio_beta: float,
    llm_key_issues: Optional[list[dict]] = None,
) -> tuple[list[dict], Optional[ApiKeyUnusable]]:
    """
    Claude-authored (2026-10-14): up to DIGEST.PORTFOLIO_REVIEW_BULLETS notes reviewing the whole portfolio, as
    {"category": "WATCH" | "NOTE", "title": "...", "text": "...", "symbols": [held tickers]}. Run after the news is
    attached so the review can weigh each stock's news. Empty when the user has no holdings or key, or the request
    fails.

    2026-10-23: written by the holdings model (params_dict["LLM"]["HOLDINGS"], Claude Haiku 5.5 by default) with the
    user's own key for its provider, instead of Gemini. `llm_key_issues` are the key problems the holdings
    evaluation already hit (the payload's api_key_issues): unless it was a passing rate limit, a key known not to
    work is skipped without a request. Returns (notes, new key error or None).
    """
    if not holdings:
        return [], None
    provider = review_settings()["provider"]
    failed = next((issue for issue in llm_key_issues or []
                   if issue.get("provider") == provider and issue.get("reason") != "rate_limited"), None)
    if failed is not None:
        print(f"[ERROR] Portfolio review skipped: the {provider} key failed earlier ({failed['reason']}).")
        return [], None
    try:
        api_key = get_llm_api_key(user_name, provider)
    except ApiKeyUnusable as e:  # Key not set (already reported by the holdings evaluation)
        return [], e
    notes, key_error = review_portfolio(holdings, portfolio_beta, api_key, user_name=user_name,
                                        home=_home(user_name))
    return [note.model_dump() for note in notes], key_error

US_EASTERN = ZoneInfo("America/New_York")

def _display_time(moment: datetime) -> str:
    """e.g. "Friday, October 16, 2026, 7:02 AM EDT" (built by hand: %-I isn't available on Windows)."""
    return f"{moment:%A, %B} {moment.day}, {moment.year}, {moment.hour % 12 or 12}:{moment:%M %p} {moment.tzname()}"

def section_started_at(started_at: datetime, home_timezone: str, user_name: str) -> dict:
    """
    Claude-authored (2026-10-16): when this user's personal section started, in their home timezone (the IANA name in
    their <NAME>_TIMEZONE setting) and in US Eastern, the market's clock. Display strings are ready for the email;
    the ISO values are there for any other formatting. An invalid timezone leaves the home fields None (with a
    warning) instead of stopping the digest; `python -m src.config.check_config` reports it as INVALID.
    """
    eastern = started_at.astimezone(US_EASTERN)
    try:
        home = started_at.astimezone(ZoneInfo(home_timezone))
    except (ZoneInfoNotFoundError, ValueError):
        print(f"[WARNING] '{home_timezone}' is not a valid IANA timezone for {user_name}; showing US Eastern time only.")
        home = None
    return {
        "home_timezone": home_timezone if home else None,
        "home": _display_time(home) if home else None,
        "home_iso": home.isoformat(timespec="seconds") if home else None,
        "us_eastern": _display_time(eastern),
        "us_eastern_iso": eastern.isoformat(timespec="seconds"),
    }

def _key_issue_notices(holdings_dict: dict, *key_errors: Optional[ApiKeyUnusable]) -> list[dict]:
    """One notice per (provider, reason): the LLM issue from the holdings payload plus the news and review ones."""
    notices = list(holdings_dict.get("api_key_issues") or [])
    notices += [error.as_notice() for error in key_errors if error is not None]
    return list({(n["provider"], n["reason"]): n for n in notices}.values())

def _profile_value(user_name: str, profile_key: str) -> Optional[str]:
    """The stripped .env value behind USER_PROFILES[user][profile_key], or None when it isn't set."""
    env_key = USER_PROFILES.get(user_name, {}).get(profile_key)
    value = os.environ.get(env_key) if env_key else None
    return value.strip() if value and value.strip() else None

def get_ideas_only_content(user_name: str) -> dict:
    """
    Claude-authored (2026-10-21): the personal part of the digest for a user with no holdings connected
    (<NAME>_HOLDINGS_SOURCE=NO_HOLDINGS). Their digest is everything else: the markets, the macro news, and the swing
    and long-term ideas. Nothing is fetched and no API key of theirs is used; this only gives the email's header
    its greeting and time, in their <NAME>_TIMEZONE when it's set (US Eastern otherwise). The email address is
    checked first, so a missing one fails before the universal content finishes, as for a user with holdings.
    """
    started_at = datetime.now(timezone.utc)
    destination_email = _profile_value(user_name, "destination_email")
    if destination_email is None:
        raise ValueError(f"Destination email for '{user_name}' not found.")
    home_timezone = _profile_value(user_name, "home_timezone")
    return {
        "holdings_source": "NO_HOLDINGS",
        "destination_email": destination_email,
        "home_timezone": home_timezone,
        "personal_started_at": section_started_at(started_at, home_timezone or US_EASTERN.key, user_name),
        "holdings": [],
    }

def get_personal_content(user_name: str = "Andres"):
    """
    Returns a dictionary containing personalized content for the daily digest.
    This content is specific to the user and may include account information,
    watchlists, and other user-specific data.
    """
    started_at = datetime.now(timezone.utc)  # Claude-authored (2026-10-16): start of this digest's API usage
    user_profile = USER_PROFILES.get(user_name)
    if not user_profile:
        raise ValueError(f"User profile for '{user_name}' not found.")

    # Claude-authored (2026-10-15): these .env checks moved ahead of the holdings, Claude, and Gemini calls, so a
    # missing value fails before any API spend instead of after it.
    comp_key = user_profile.get("account_company")
    if comp_key is None:
        raise ValueError(f"Account company for '{user_name}' not found.")
    account_company = os.environ.get(comp_key)
    if account_company is None:
        raise ValueError(f"Account company for '{user_name}' not found.")

    destination_email_key = user_profile.get("destination_email")
    if destination_email_key is None:
        raise ValueError(f"Destination email for '{user_name}' not found.")
    destination_email = os.environ.get(destination_email_key)
    if destination_email is None:
        raise ValueError(f"Destination email for '{user_name}' not found.")

    home_timezone_key = user_profile.get("home_timezone")
    if home_timezone_key is None:
        raise ValueError(f"Home timezone for '{user_name}' not found.")
    home_timezone = os.environ.get(home_timezone_key)
    if home_timezone is None:
        raise ValueError(f"Home timezone for '{user_name}' not found.")

    portfolio = PortfolioFetcher(user_name=user_name)
    # Claude-authored (2026-10-15): tell the user when their holdings source has a problem (shares the watchdog's
    # cooldown, so the same problem isn't sent twice), and include it in the payload for the email
    for issue in portfolio.holdings_issues:
        send_notice_now(user_name, holdings_issue_subject(issue), issue["reason"], holdings_issue_body(issue))
    # Claude-authored (2026-10-22): holdings bought from Finnbot's ideas (the card's label, the plan in the AI review,
    # target / stop alerts), before anything is evaluated (src/tracking/idea_positions.py)
    portfolio.full_data = attach_idea_origins(user_name, portfolio.full_data, datetime.now(timezone.utc))
    # Claude-authored (2026-10-15): Gemini news only needs each stock's symbol and name, so it runs in a background
    # thread while market data and Claude run below. The portfolio review then waits for both. Evaluations the
    # watchdog made in the last hour are reused instead of calling Claude again.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{user_name}-news") as pool:
        # Claude-authored (2026-10-16): <NAME>_RECEIVE_HOLDING_NEWS=False skips the Gemini news (no calls, empty bullets)
        news_on = wants_holding_news(user_name)
        if not news_on:
            debug(f"Holding news is off for {user_name} (RECEIVE_HOLDING_NEWS); no Gemini news requests.")
        companies = _holding_companies(portfolio.full_data) if news_on else {}
        # Claude-authored (2026-10-23): news fetched earlier today isn't asked for again; it's checked once the
        # holdings are evaluated (settle_holdings_news)
        reused = same_day_news(user_name, companies)
        news_future = pool.submit(fetch_holdings_news, user_name,
                                  {symbol: name for symbol, name in companies.items() if symbol not in reused})
        holdings_dict = portfolio.get_holdings_digest_payload(reuse_recent_evaluations=True)
        news, news_key_error = news_future.result()
    news, news_key_error = settle_holdings_news(
        user_name, companies, reused, news, news_key_error, holdings_dict.get("holdings"))
    holdings = attach_news_bullets(holdings_dict.get("holdings"), news)
    portfolio_review, review_key_error = get_portfolio_review(
        user_name, holdings, holdings_dict.get("portfolio_beta"), llm_key_issues=holdings_dict.get("api_key_issues")
    )

    return {
        "account_company": account_company,
        "destination_email": destination_email,
        "home_timezone": home_timezone,
        "home_currency": get_home_currency(user_name),  # Claude-authored (2026-10-21): the overview's currency
        # Claude-authored (2026-10-16): when this section started, in the user's home timezone and US Eastern
        "personal_started_at": section_started_at(started_at, home_timezone, user_name),
        "holdings": holdings,
        "portfolio_beta": holdings_dict.get("portfolio_beta"),
        "portfolio_review": portfolio_review,
        # Claude-authored (2026-10-15): why parts of the digest are empty, e.g. an expired or out-of-credit key
        "api_key_issues": _key_issue_notices(holdings_dict, news_key_error, review_key_error),
        "data_source_issues": portfolio.holdings_issues,  # Claude-authored (2026-10-15): e.g. a disabled connection
        # Claude-authored (2026-10-16): estimated spend on this user's own Anthropic and Gemini keys - this digest,
        # today, and the month so far (watchdog runs included); see src/tracking/usage_tracking.py
        "api_usage": usage_summary(user_name, started_at),
    }
