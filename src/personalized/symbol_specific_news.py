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
- 2026-10-16: gemini-3.6-flash returns no grounding metadata with a response schema, so no link survived the check.
  Grounding redirect links the model writes are now verified by following them (see _verify_source_urls).
- 2026-10-20: at most 2 stories per stock (SYMBOL_NEWS_BULLETS), and only those of SYMBOL_NEWS_MIN_PRIORITY
  (MEDIUM) or higher, at Andres's request: LOW stories (routine commentary, recaps, listicles) were filler and the
  news bullets were the largest part of the email. The request asks for them, and anything lower is dropped.
- 2026-10-23: cheaper, at Andres's request (Gemini was ~85% of a user's bill, mostly output tokens spent thinking):
  - one request covers several stocks (DIGEST.SYMBOL_NEWS_BATCH_SIZE) instead of one per stock. A stock the answer
    leaves out, or every stock of an answer that is cut off or isn't the expected JSON, is asked for again on its own;
  - every request uses GEMINI_CONFIG's effort (thinking level) and output cap (src/llm/gemini_settings.py);
  - fetch_symbol_news only returns the stocks it fetched, so a failed stock can be told apart from one without news
    (src/personalized/news_reuse.py keeps fetched news for the evening digest);
  - at low effort Gemini sometimes wrote the article's own address instead of the search result's link, which can't be
    verified, so the first run with it kept 0 of 10 links. The prompt now asks for the grounding redirect link by
    name: 33 of 33 links in a test of 6 requests (5 stocks and 1 stock, 3 each).
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
from pydantic import BaseModel, ValidationError
from tenacity import retry

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.ops.run_logging import debug
from src.llm.gemini_settings import gemini_model, generation_config, max_output_tokens, was_cut_off
from src.config.parameters import params_dict
from src.personalized.api_errors import GEMINI_RETRY, ApiKeyUnusable, gemini_key_error
from src.tracking.usage_tracking import record_gemini_response

DIGEST_PARAMS = params_dict["DIGEST"]
PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def kept_priorities() -> list[str]:
    """
    Claude-authored (2026-10-20): the priorities kept, e.g. ["HIGH", "MEDIUM"]. 2026-10-23: read when used, so a
    change from the settings sheet applies to the run that read it.
    """
    lowest = PRIORITY_ORDER.get(str(DIGEST_PARAMS.get("SYMBOL_NEWS_MIN_PRIORITY", "MEDIUM")).upper(), 1)
    return [priority for priority, rank in PRIORITY_ORDER.items() if rank <= lowest]


GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"  # Gemini API grounding links redirect through here
REDIRECT_TIMEOUT_SECONDS = 5

# 1. Define Pydantic Schema
# Claude-authored (2026-10-19): one sentence per story (what happened and its direct impact) at Andres's request,
# instead of an event sentence plus a 1-3 week outlook sentence; the news bullets were a fifth of the email's size.
class SymbolNewsItem(BaseModel):
    priority: str               # Expects "HIGH", "MEDIUM", or "LOW"
    summary: str                # 1 succinct sentence: what happened and its direct impact on the stock
    source_url: Optional[str]   # Link to the source; replaced by the verified URL, or None if unverifiable

# Claude-authored (2026-10-23): one request covers several stocks, each answered under its own ticker
class StockNews(BaseModel):
    ticker: str                       # The ticker exactly as given in the request
    news_items: list[SymbolNewsItem]

class NewsBatchPayload(BaseModel):
    stocks: list[StockNews]

class UnusableAnswer(Exception):
    """An answer that can't be read (cut off at the output cap, or not the expected JSON); not retried as it is."""

# 2. System Prompt
SYSTEM_PROMPT = """
You are a senior equity analyst writing the news section of a daily digest for an investor who already holds these stocks.
Search the web for recent news about each company you are given and extract the stories that matter most to a shareholder.

PRIORITY HIERARCHY:
1. HIGH: Thesis-changing company events: earnings results or guidance changes, mergers and acquisitions, major legal or regulatory actions, executive departures, large contract wins or losses, product or trial failures, or a sharp (+/- 5%) move in the stock.
2. MEDIUM: Analyst rating or price-target changes, notable product launches, partnerships, significant insider transactions, or industry news that directly affects the company.
3. LOW: Routine commentary, recaps of older news, passing mentions in market wraps, or listicles.

SELECTION RULE:
For each company, select at most the requested number of stories, highest priority first. Do NOT balance across tiers. Only fall back to lower tiers if higher-priority stories are absent.
Report each story once, even if several outlets covered it. Return fewer stories, or none, rather than padding with stale or unrelated news.
Only include news about that specific company in its entry: never put one company's story under another company's ticker.
Return one entry per company, with its ticker exactly as given, even when it has no stories (an empty news_items list).

FORMATTING REQUIREMENTS:
Keep all fields extremely succinct and direct:
- priority: Must be "HIGH", "MEDIUM", or "LOW".
- summary: ONE succinct sentence of at most 25 words covering what happened, when (e.g. "Oct 15"), and its direct impact on the stock, e.g. "Beat Q3 estimates on Oct 15 and raised full-year guidance; shares rose 6%." or "Agreed on Oct 5 to buy RXO for $5.8B, adding debt and pausing buybacks." Never add a second sentence or a generic outlook.
- source_url: The search result's link exactly as your Google Search tool returned it (it starts with https://vertexaisearch.cloud.google.com/grounding-api-redirect/), copied character for character. Never replace it with the article's own address, and never construct or guess a URL; use null if you have no such link for the story.
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

def _is_grounding_redirect(url: Optional[str]) -> bool:
    if not url:
        return False
    parts = urlsplit(url.strip())
    return parts.hostname == GROUNDING_REDIRECT_HOST and parts.path.startswith("/grounding-api-redirect/")

def _verify_source_urls(items: list[SymbolNewsItem], grounded_urls: list[str]) -> None:
    """
    Replaces each item's source_url with a verified article URL, or None when it can't be verified.

    - A URL matching a page Google Search grounding returned (grounded_urls, from the grounding metadata) is kept
      as that page's URL.
    - Claude-authored (2026-10-16): with a response schema, gemini-3.6-flash returns no grounding metadata, so that
      check kept no link at all (0 of 30 holding-news links and 0 of 5 macro links in the first full run). The model
      does write the grounding redirect links its search returned (vertexaisearch.cloud.google.com/grounding-api-
      redirect/...). Only a real search produces one that redirects, so each is followed one hop (without
      downloading the article) and replaced by the article's URL; one that doesn't redirect is dropped.
    Used by any item with a source_url (the macro news too).
    """
    verified = {}
    for url in grounded_urls:
        key = _url_key(url)
        if key:
            verified.setdefault(key, url)
    redirects = sorted({item.source_url.strip() for item in items if _is_grounding_redirect(item.source_url)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        resolved = dict(zip(redirects, pool.map(_resolve_grounding_uri, redirects)))
    for item in items:
        if _is_grounding_redirect(item.source_url):
            article = resolved.get(item.source_url.strip())
            item.source_url = article if article and not _is_grounding_redirect(article) else None
        else:
            item.source_url = verified.get(_url_key(item.source_url))

# 3. Fetch Function (several stocks in one request) using Chat interface to avoid the AFC warning
def _news_prompt(stocks: list[tuple[str, str]], max_items: int, lookback_days: int) -> str:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    listed = "\n".join(f"- {company} (ticker: {symbol})" for symbol, company in stocks)
    return (
        f"Today is {today:%Y-%m-%d}. Perform a web search for news from the past {lookback_days} days about each of "
        f"these companies:\n{listed}\n\nFor each company, select the top {max_items} items based on priority. "
        f"Include only {' or '.join(kept_priorities())} priority stories; return fewer, or none, rather than a "
        f"lower-priority one."
    )

@retry(**GEMINI_RETRY)  # Retries only what can recover; key, billing, and quota errors fail immediately
def fetch_news_batch(
    client: genai.Client,
    stocks: list[tuple[str, str]],
    max_items: int,
    lookback_days: int,
    user_name: Optional[str] = None,
) -> dict[str, list[SymbolNewsItem]]:
    """
    Claude-authored (2026-10-23): {symbol: news items} for the (symbol, company) pairs in `stocks`, from one request.
    A stock the answer left out is missing from the result. Raises UnusableAnswer when the answer was cut off at
    GEMINI_CONFIG's output cap or isn't the expected JSON.
    """
    model = gemini_model("HOLDING_NEWS")
    chat = client.chats.create(
        model=model,
        config=generation_config(
            system_instruction=SYSTEM_PROMPT,
            tools=[{"google_search": {}}],  # Enables Search Grounding
            response_mime_type="application/json",
            response_schema=NewsBatchPayload,
        ),
    )

    start_time = time.time()
    response = chat.send_message(_news_prompt(stocks, max_items, lookback_days))
    elapsed = round(time.time() - start_time, 2)
    record_gemini_response(user_name, "digest", model, response)  # Claude-authored (2026-10-16): user's cost

    if was_cut_off(response):
        raise UnusableAnswer(f"cut off at {max_output_tokens()} output tokens")
    try:
        payload = NewsBatchPayload.model_validate_json(response.text or "")
    except ValidationError as e:
        raise UnusableAnswer(f"not the expected JSON ({str(e).splitlines()[0]})") from e

    wanted = {symbol.upper(): symbol for symbol, _ in stocks}
    news: dict[str, list[SymbolNewsItem]] = {}
    for stock in payload.stocks:
        symbol = wanted.get(stock.ticker.strip().upper())
        if symbol is not None and symbol not in news:
            news[symbol] = stock.news_items
    items = [item for found in news.values() for item in found]
    _verify_source_urls(items, _grounded_urls(response))
    debug(
        f"[DIAGNOSTIC] News for {len(stocks)} stock(s) received in {elapsed}s, "
        f"{sum(1 for item in items if item.source_url)}/{len(items)} source links verified."
    )
    return news

def _kept(items: list[SymbolNewsItem], max_items: int) -> list[SymbolNewsItem]:
    """The top `max_items` items of a kept priority, highest priority first."""
    for item in items:
        item.priority = item.priority.strip().upper()
    kept = [item for item in items if item.priority in kept_priorities()]
    kept.sort(key=lambda item: PRIORITY_ORDER.get(item.priority, len(PRIORITY_ORDER)))
    return kept[:max_items]

def _batches(stocks: list, size: int) -> list[list]:
    """`stocks` in order, in as few batches of at most `size` as possible, as even as possible (7 by 5: 4 + 3)."""
    count = -(-len(stocks) // max(1, size))
    base, extra = divmod(len(stocks), count)
    batches, start = [], 0
    for index in range(count):
        end = start + base + (1 if index < extra else 0)
        batches.append(stocks[start:end])
        start = end
    return batches

# 4. Fetch news for every held stock, several stocks per request and several requests at a time
def fetch_symbol_news(
    companies: dict[str, Optional[str]],
    api_key: str,
    user_name: Optional[str] = None,
    max_items: Optional[int] = None,
    lookback_days: Optional[int] = None,
    max_workers: Optional[int] = None,
    batch_size: Optional[int] = None,
) -> tuple[dict[str, list[SymbolNewsItem]], Optional[ApiKeyUnusable]]:
    """
    Returns ({symbol: [SymbolNewsItem, ...]}, key_error): at most `max_items` items per stock, highest priority
    first. `companies` maps each symbol to its company name (used to disambiguate short tickers).
    2026-10-23: `batch_size` stocks (DIGEST.SYMBOL_NEWS_BATCH_SIZE) share one request, and up to `max_workers`
    requests (DIGEST.SYMBOL_NEWS_CONCURRENCY) run at once. A stock an answer leaves out, or every stock of an answer
    that can't be read, is asked for again on its own. Only stocks whose news was fetched are in the result (with []
    when there was none), so one failure never blocks the rest of the digest. A key failure stops the run: requests
    not started yet are skipped (every request would fail the same way) and it is returned as key_error; news
    fetched before it is kept.
    """
    if not companies:
        return {}, None
    max_items = max_items or DIGEST_PARAMS["SYMBOL_NEWS_BULLETS"]
    lookback_days = lookback_days or DIGEST_PARAMS["SYMBOL_NEWS_LOOKBACK_DAYS"]
    batches = _batches([(symbol, company or symbol) for symbol, company in companies.items()],
                       int(batch_size or DIGEST_PARAMS.get("SYMBOL_NEWS_BATCH_SIZE") or 1))
    max_workers = max(1, min(max_workers or DIGEST_PARAMS["SYMBOL_NEWS_CONCURRENCY"], len(batches)))

    debug(
        f"[DIAGNOSTIC] Fetching up to {max_items} news items for {len(companies)} held stocks in "
        f"{len(batches)} request(s) ({max_workers} at a time)..."
    )
    news: dict[str, list[SymbolNewsItem]] = {}
    key_errors: list[ApiKeyUnusable] = []  # First key failure stops the requests that haven't started
    stop = threading.Event()
    skipped = []
    # The user's own key, never the shared GEMINI_API_KEY. One client shared by the worker threads: creating a
    # client costs ~0.3-0.9 s of CPU, and its httpx connection pool is safe to use from several threads.
    client = genai.Client(api_key=api_key)

    def fetch(batch: list[tuple[str, str]]) -> None:
        symbols = ", ".join(symbol for symbol, _ in batch)
        if stop.is_set():
            skipped.extend(symbol for symbol, _ in batch)
            return
        problem = "left out of the answer"
        try:
            found = fetch_news_batch(client, batch, max_items, lookback_days, user_name)
        except UnusableAnswer as e:
            found, problem = {}, str(e)
        except Exception as e:  # News is optional: an API, network, or parsing failure must not stop the digest
            key_error = gemini_key_error(e, user_name)
            if key_error is None:
                print(f"[ERROR] {symbols}: could not fetch news: {e}")
            elif not stop.is_set():
                key_errors.append(key_error)
                stop.set()
            return

        for symbol, items in found.items():
            news[symbol] = _kept(items, max_items)
        missing = [stock for stock in batch if stock[0] not in found]
        if missing and len(batch) > 1:
            print(f"[WARNING] News for {', '.join(symbol for symbol, _ in missing)}: {problem}; asking for each "
                  f"on its own.")
            for stock in missing:
                fetch([stock])
        elif missing:
            print(f"[ERROR] {symbols}: no news ({problem}).")

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="news") as pool:
        list(pool.map(fetch, batches))

    key_error = key_errors[0] if key_errors else None
    if key_error is not None:
        print(f"[ERROR] {key_error} News skipped for {len(skipped)} stock(s) not yet started.")
    return news, key_error
