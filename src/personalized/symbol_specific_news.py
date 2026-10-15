#File to connect to Gemini API and fetch the most important recent news for each held stock, with Google Search grounding enabled.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
- 2026-10-14: created, modelled on src/universal/market_news.py (Pydantic schema, priority hierarchy,
  Google Search grounding through a chat session, tenacity retries on APIError), but run once per held
  stock with the user's own Gemini key (user_settings.get_gemini_api_key) instead of the shared key.
  The number of items per stock is params_dict["DIGEST"]["SYMBOL_NEWS_BULLETS"].
- 2026-10-14: source links are verified. Gemini writes source_url itself, so it can be wrong or invented.
  Each link is kept only if it matches a web page that Google Search grounding actually returned for that
  response (grounding redirect links are followed to the real article URL); otherwise it becomes None.
- 2026-10-15: retries now use api_errors.GEMINI_RETRY (only timeouts, per-minute 429s, and 5xx), and the loop
  stops at the first key failure (invalid, expired, no credits, daily quota, persistent rate limit) instead of
  repeating it for every stock. News already fetched is kept.
- 2026-10-15: stocks are fetched in parallel (DIGEST.SYMBOL_NEWS_CONCURRENCY at a time, sharing one client),
  and the digest starts this while Claude evaluates the holdings.
"""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from google import genai
from google.genai import types
from pydantic import BaseModel
from tenacity import retry

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src.personalized.api_errors import GEMINI_RETRY, ApiKeyUnusable, gemini_key_error
from src.usage_tracking import record_gemini_response

DIGEST_PARAMS = params_dict["DIGEST"]
GEMINI_MODEL = "gemini-3.6-flash"  # Same model as market_news.py
PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"  # Gemini API grounding links redirect through here
REDIRECT_TIMEOUT_SECONDS = 5

# 1. Define Pydantic Schema
class SymbolNewsItem(BaseModel):
    priority: str               # Expects "HIGH", "MEDIUM", or "LOW"
    event_summary: str          # 1 concise sentence on what occurred
    stock_impact: str           # 1 concise sentence on the likely effect on the stock
    source_url: Optional[str]   # Link to the source; replaced by the verified URL, or None if unverifiable

class SymbolNewsPayload(BaseModel):
    news_items: list[SymbolNewsItem]

# 2. System Prompt
SYSTEM_PROMPT = """
You are a senior equity analyst writing the news section of a daily digest for an investor who already holds the stock.
Search the web for recent news about the given company and extract the stories that matter most to a shareholder.

PRIORITY HIERARCHY:
1. HIGH: Thesis-changing company events: earnings results or guidance changes, mergers and acquisitions, major legal or regulatory actions, executive departures, large contract wins or losses, product or trial failures, or a sharp (+/- 5%) move in the stock.
2. MEDIUM: Analyst rating or price-target changes, notable product launches, partnerships, significant insider transactions, or industry news that directly affects the company.
3. LOW: Routine commentary, recaps of older news, passing mentions in market wraps, or listicles.

SELECTION RULE:
Select at most the requested number of stories, highest priority first. Do NOT balance across tiers. Only fall back to lower tiers if higher-priority stories are absent.
Report each story once, even if several outlets covered it. Return fewer stories, or none, rather than padding with stale or unrelated news. Only include news about this specific company.

FORMATTING REQUIREMENTS:
Keep all fields extremely succinct and direct:
- priority: Must be "HIGH", "MEDIUM", or "LOW".
- event_summary: 1 concise sentence summarizing what occurred, including when.
- stock_impact: 1 concise sentence on the likely effect on the stock over the next 1-3 weeks.
- source_url: The exact URL of the search result the story came from, copied verbatim. Use null if you cannot point to one; never construct or guess a URL.
"""

# Source-link verification against the pages Google Search grounding actually returned
def _resolve_grounding_uri(uri: str) -> Optional[str]:
    """Follows a grounding redirect link one hop to the article's real URL, without downloading the article."""
    if urlsplit(uri).hostname != GROUNDING_REDIRECT_HOST:
        return uri
    try:
        response = requests.get(uri, allow_redirects=False, timeout=REDIRECT_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        print(f"[WARNING] Could not resolve grounding link: {e}")
        return None
    location = response.headers.get("Location")
    return location if response.is_redirect and location else None

def _grounded_urls(response) -> list[str]:
    """Real URLs of the web pages Google Search grounding used for this response."""
    candidates = response.candidates or []
    metadata = candidates[0].grounding_metadata if candidates else None
    chunks = (metadata.grounding_chunks if metadata else None) or []
    uris = [chunk.web.uri for chunk in chunks if chunk.web and chunk.web.uri]
    with ThreadPoolExecutor(max_workers=8) as pool:
        return [url for url in pool.map(_resolve_grounding_uri, uris) if url]

def _url_key(url: Optional[str]) -> Optional[str]:
    """Comparable form of a URL: host without 'www.' plus path without trailing slash (scheme, query, fragment ignored)."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}" if host else None

def _verify_source_urls(items: list[SymbolNewsItem], grounded_urls: list[str]) -> None:
    """Replaces each item's source_url with the matching grounded URL, or None when no grounded page matches."""
    verified = {}
    for url in grounded_urls:
        key = _url_key(url)
        if key:
            verified.setdefault(key, url)
    for item in items:
        item.source_url = verified.get(_url_key(item.source_url))

# 3. Fetch Function (one stock) using Chat interface to avoid the AFC warning
@retry(**GEMINI_RETRY)  # Retries only what can recover; key, billing, and quota errors fail immediately
def fetch_news_for_symbol(
    client: genai.Client,
    symbol: str,
    company: str,
    max_items: int,
    lookback_days: int,
    user_name: Optional[str] = None,
) -> SymbolNewsPayload:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    prompt_text = (
        f"Today is {today:%Y-%m-%d}. Perform a web search for news about {company} (ticker: {symbol}) "
        f"from the past {lookback_days} days. Select the top {max_items} items based on priority."
    )

    chat = client.chats.create(
        model=GEMINI_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[{"google_search": {}}],  # Enables Search Grounding
            response_mime_type="application/json",
            response_schema=SymbolNewsPayload,
        )
    )

    start_time = time.time()
    response = chat.send_message(prompt_text)
    elapsed = round(time.time() - start_time, 2)
    record_gemini_response(user_name, "digest", GEMINI_MODEL, response)  # Claude-authored (2026-10-16): user's cost

    payload = SymbolNewsPayload.model_validate_json(response.text)
    _verify_source_urls(payload.news_items, _grounded_urls(response))
    verified_count = sum(1 for item in payload.news_items if item.source_url)
    print(
        f"[DIAGNOSTIC] {symbol}: news received in {elapsed}s, "
        f"{verified_count}/{len(payload.news_items)} source links verified."
    )
    return payload

# 4. Fetch news for every held stock, several at a time
def fetch_symbol_news(
    companies: dict[str, Optional[str]],
    api_key: str,
    user_name: Optional[str] = None,
    max_items: Optional[int] = None,
    lookback_days: Optional[int] = None,
    max_workers: Optional[int] = None,
) -> tuple[dict[str, list[SymbolNewsItem]], Optional[ApiKeyUnusable]]:
    """
    Returns ({symbol: [SymbolNewsItem, ...]}, key_error): at most `max_items` items per stock, highest priority
    first. `companies` maps each symbol to its company name (used to disambiguate short tickers).
    Up to `max_workers` stocks (DIGEST.SYMBOL_NEWS_CONCURRENCY) are fetched at once. A stock whose request fails
    gets an empty list, so one failure never blocks the rest of the digest. A key failure stops the run: stocks
    not started yet are skipped (every request would fail the same way) and it is returned as key_error; news
    fetched before it is kept.
    """
    if not companies:
        return {}, None
    max_items = max_items or DIGEST_PARAMS["SYMBOL_NEWS_BULLETS"]
    lookback_days = lookback_days or DIGEST_PARAMS["SYMBOL_NEWS_LOOKBACK_DAYS"]
    max_workers = max(1, min(max_workers or DIGEST_PARAMS["SYMBOL_NEWS_CONCURRENCY"], len(companies)))

    print(
        f"\n[DIAGNOSTIC] Fetching up to {max_items} news items for {len(companies)} held stocks "
        f"({max_workers} at a time)..."
    )
    news: dict[str, list[SymbolNewsItem]] = {symbol: [] for symbol in companies}
    key_errors: list[ApiKeyUnusable] = []  # First key failure stops the stocks that haven't started
    stop = threading.Event()
    skipped = []
    # The user's own key, never the shared GEMINI_API_KEY. One client shared by the worker threads: creating a
    # client costs ~0.3-0.9 s of CPU, and its httpx connection pool is safe to use from several threads.
    client = genai.Client(api_key=api_key)

    def fetch_one(symbol: str, company: Optional[str]) -> None:
        if stop.is_set():
            skipped.append(symbol)
            return
        try:
            payload = fetch_news_for_symbol(client, symbol, company or symbol, max_items, lookback_days, user_name)
        except Exception as e:  # News is optional: an API, network, or parsing failure must not stop the digest
            key_error = gemini_key_error(e, user_name)
            if key_error is None:
                print(f"[ERROR] {symbol}: could not fetch news: {e}")
            elif not stop.is_set():
                key_errors.append(key_error)
                stop.set()
            return

        for item in payload.news_items:
            item.priority = item.priority.strip().upper()
        payload.news_items.sort(key=lambda item: PRIORITY_ORDER.get(item.priority, len(PRIORITY_ORDER)))
        news[symbol] = payload.news_items[:max_items]

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="news") as pool:
        list(pool.map(lambda pair: fetch_one(*pair), companies.items()))

    key_error = key_errors[0] if key_errors else None
    if key_error is not None:
        print(f"[ERROR] {key_error} News skipped for {len(skipped)} stock(s) not yet started.")
    return news, key_error
