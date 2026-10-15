
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import os
from typing import Optional

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.personalized.user_profiles import USER_PROFILES
from src.personalized.pull_holdings_data import PortfolioFetcher
from src.personalized.api_errors import ApiKeyUnusable
from src.personalized.portfolio_review import review_portfolio
from src.personalized.symbol_specific_news import SymbolNewsItem, fetch_symbol_news
from src.personalized.user_settings import get_gemini_api_key

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
    return companies

def fetch_holdings_news(
    user_name: str, companies: dict[str, Optional[str]]
) -> tuple[dict[str, list[SymbolNewsItem]], Optional[ApiKeyUnusable]]:
    """
    Claude-authored (2026-10-14, split out 2026-10-15): up to DIGEST.SYMBOL_NEWS_BULLETS news items per stock from
    Gemini with the user's own key, plus the key error (missing, invalid, expired, no credits, exhausted quota)
    that stopped them, or None, so the portfolio review can skip a key that is known not to work.
    """
    if not companies:
        return {}, None
    try:
        return fetch_symbol_news(companies, api_key=get_gemini_api_key(user_name), user_name=user_name)
    except ApiKeyUnusable as e:  # Key not set
        print(f"[ERROR] {e} Holding news skipped.")
        return {}, e

def attach_news_bullets(holdings: list[dict], news: dict[str, list[SymbolNewsItem]]) -> list[dict]:
    """
    Claude-authored (2026-10-14): adds 'news_bullets' to each holding:
    {"priority": "HIGH" | "MEDIUM" | "LOW", "text": "What happened. Likely effect.", "source_url": "..." | None}
    dicts, highest priority first. source_url is only set when it matches a page Google Search grounding returned
    (see symbol_specific_news._verify_source_urls). Stocks without news get an empty list.
    """
    for holding in holdings or []:
        holding["news_bullets"] = [
            {
                "priority": item.priority,
                "text": f"{item.event_summary} {item.stock_impact}",
                "source_url": item.source_url,
            }
            for item in news.get(holding.get("symbol"), [])
        ]
    return holdings or []

def get_portfolio_review(
    user_name: str,
    holdings: list[dict],
    portfolio_beta: float,
    gemini_key_error: Optional[ApiKeyUnusable] = None,
) -> tuple[list[dict], Optional[ApiKeyUnusable]]:
    """
    Claude-authored (2026-10-14): up to DIGEST.PORTFOLIO_REVIEW_BULLETS notes reviewing the whole portfolio, from
    Gemini with the user's own key, as {"category": "WATCH" | "NOTE", "text": "...", "symbols": [held tickers]}.
    Run after the news is attached so the review can weigh each stock's news. Empty when the user has no holdings
    or Gemini key, or the request fails.

    2026-10-15: `gemini_key_error` is the key failure the news already hit with the same key. Unless it was a
    passing rate limit, the review is skipped without a request. Returns (notes, new key error or None).
    """
    if not holdings:
        return [], None
    if gemini_key_error is not None and gemini_key_error.blocks_run:
        print(f"[ERROR] Portfolio review skipped: the Gemini key failed earlier ({gemini_key_error.reason}).")
        return [], None
    try:
        api_key = get_gemini_api_key(user_name)
    except ApiKeyUnusable as e:  # Key not set (already reported by the news when it ran first)
        return [], e
    notes, key_error = review_portfolio(holdings, portfolio_beta, api_key, user_name=user_name)
    return [note.model_dump() for note in notes], key_error

def _key_issue_notices(holdings_dict: dict, *key_errors: Optional[ApiKeyUnusable]) -> list[dict]:
    """One notice per (provider, reason): the Anthropic issue from the holdings payload plus any Gemini issues."""
    notices = list(holdings_dict.get("api_key_issues") or [])
    notices += [error.as_notice() for error in key_errors if error is not None]
    return list({(n["provider"], n["reason"]): n for n in notices}.values())

def get_personal_content(user_name: str = "Andres"):
    """
    Returns a dictionary containing personalized content for the daily digest.
    This content is specific to the user and may include account information,
    watchlists, and other user-specific data.
    """
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
    # Claude-authored (2026-10-15): Gemini news only needs each stock's symbol and name, so it runs in a background
    # thread while market data and Claude run below. The portfolio review then waits for both. Evaluations the
    # watchdog made in the last hour are reused instead of calling Claude again.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{user_name}-news") as pool:
        news_future = pool.submit(fetch_holdings_news, user_name, _holding_companies(portfolio.full_data))
        holdings_dict = portfolio.get_holdings_digest_payload(reuse_recent_evaluations=True)
        news, news_key_error = news_future.result()
    holdings = attach_news_bullets(holdings_dict.get("holdings"), news)
    portfolio_review, review_key_error = get_portfolio_review(
        user_name, holdings, holdings_dict.get("portfolio_beta"), gemini_key_error=news_key_error
    )

    return {
        "account_company": account_company,
        "destination_email": destination_email,
        "home_timezone": home_timezone,
        "holdings": holdings,
        "portfolio_beta": holdings_dict.get("portfolio_beta"),
        "portfolio_review": portfolio_review,
        # Claude-authored (2026-10-15): why parts of the digest are empty, e.g. an expired or out-of-credit key
        "api_key_issues": _key_issue_notices(holdings_dict, news_key_error, review_key_error),
    }
