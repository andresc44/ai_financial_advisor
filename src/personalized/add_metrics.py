# File to enrich holdings with the same Yahoo, Finnhub, news, and Twelve Data context used for new-stock recommendations.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
- 2026-10-13: replaced a broken placeholder that referenced non-existent columns/methods. Holdings now get
  the same sanitized market context (Yahoo, Finnhub, news, Twelve Data) that new-stock recommendations use,
  so the hold/sell LLM sees the same evidence as the buy-side LLM.
- 2026-10-13: added a shared daily cache (data/market_context_cache.json). Why: the watchdog can now run
  many times a day per user; without caching, every check would spend ~9 Finnhub calls and several Twelve
  Data credits per stock. Slow-changing sections are fetched once per trading day (refreshed again after
  the close); the quote and news are refreshed when older than FRESH_CONTEXT_MAX_AGE_MINUTES.
- 2026-10-15: cache decisions, fetching, and saving happen under shared_locks.MARKET_DATA_LOCK, because the
  digest and watchdog now run users in parallel threads that share the Finnhub / Twelve Data keys.
See docs/watchdog.md.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.market_session import last_refresh_boundary
from src.parameters import params_dict
from src.shared_locks import MARKET_DATA_LOCK
from src.universal.recommendations.filter_tickers import DataFetcher
from src.universal.recommendations.sanitizer import sanitize_ticker_payload

CONTEXT_CACHE_PATH = project_root / "data" / "market_context_cache.json"
WATCHDOG_PARAMS = params_dict["WATCHDOG"]


class HoldingsDataFetcher(DataFetcher):
    """DataFetcher scoped to a fixed list of held tickers.

    The base class loads its ticker universe from the latest NASDAQ screener CSV, which is
    unrelated to a user's holdings and may not exist when the hourly watchdog runs.
    """

    def __init__(self, tickers: list[str]) -> None:
        self._holdings_tickers = tickers  # Must be set first: the base __init__ calls the loader below
        super().__init__()

    def _DataFetcher__load_ticker_df(self) -> pd.DataFrame:
        """Overrides DataFetcher's private (name-mangled) screener CSV loader with the held tickers."""
        return pd.DataFrame({"ticker": self._holdings_tickers})


def _load_context_cache() -> dict[str, dict[str, Any]]:
    """Loads the shared market-context cache, returning an empty cache if missing or unreadable."""
    if not CONTEXT_CACHE_PATH.exists():
        return {}
    try:
        with open(CONTEXT_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[WARNING] Could not read market context cache: {e}. Rebuilding.")
        return {}


def cached_company_names(symbols: list[str]) -> dict[str, str]:
    """
    Claude-authored (2026-10-17): {symbol: Yahoo's company name} from the market-context cache, for symbols seen in
    an earlier run. The brokerage's own description can be wrong (SnapTrade has called Agilent's "A" "AgileThought,
    Inc."), and the holding news starts before this run's market context is fetched.
    """
    cache = _load_context_cache()
    names = {}
    for symbol in symbols:
        entry = cache.get(symbol) or {}
        name = ((entry.get("context") or {}).get("profile") or {}).get("name")
        if isinstance(name, str) and name.strip():
            names[symbol] = name.strip()
    return names


def _save_context_cache(cache: dict[str, dict[str, Any]]) -> None:
    """Atomically writes the cache (temp file + os.replace) so a crash never leaves a half-written file."""
    CONTEXT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=CONTEXT_CACHE_PATH.parent, prefix=".context_cache_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, default=str)
        os.replace(tmp_path, CONTEXT_CACHE_PATH)
    except OSError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _parse_time(value: Any) -> datetime:
    """Parses a cached ISO timestamp, treating missing/invalid values as infinitely old."""
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _refresh_fresh_sections(fetcher: DataFetcher, symbol: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Re-fetches only the fast-moving parts of a cached context: price action (Yahoo quote) and headlines
    (Yahoo + Finnhub news). Failed fetches keep the cached values instead of blanking them.
    """
    yahoo = fetcher.fetch_yahoo_data(symbol)
    news_df = fetcher.fetch_all_news([symbol]).get(symbol, pd.DataFrame())
    partial = sanitize_ticker_payload(
        symbol,
        {"yahoo": yahoo if isinstance(yahoo, dict) else {}, "news": news_df.to_dict(orient="records")},
    )

    refreshed = dict(context)
    if isinstance(yahoo, dict) and "error" not in yahoo:
        refreshed["price_action"] = partial["price_action"]
    if partial["recent_headlines_and_catalysts"]:
        refreshed["recent_headlines_and_catalysts"] = partial["recent_headlines_and_catalysts"]
    return refreshed


def add_metrics_to_holdings(self, holdings_df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds a 'market_context' column holding each ticker's sanitized Yahoo, Finnhub, news, and
    Twelve Data payload, matching the context the LLM receives for new-stock recommendations.
    Context is served from the shared daily cache when still valid (see module docstring).
    """
    if holdings_df.empty:
        print("Holdings DataFrame is empty. No metrics to add.")
        return holdings_df

    df = holdings_df.copy()

    # Same symbol formatting pull_holdings_data uses for yfinance lookups
    sym_map = {
        s: s.replace(":", "-")
        for s in df["symbol"].dropna().unique()
        if isinstance(s, str) and s
    }
    formatted_symbols = sorted(set(sym_map.values()))  # Dedupe symbols held across multiple accounts

    if not formatted_symbols:
        df["market_context"] = None
        return df

    # Claude-authored (2026-10-13): decide per symbol between a full fetch, a quote+news refresh, or a cache hit.
    now = datetime.now(timezone.utc)
    refresh_boundary = last_refresh_boundary(now).to_pydatetime()
    fresh_cutoff = now - timedelta(minutes=WATCHDOG_PARAMS["FRESH_CONTEXT_MAX_AGE_MINUTES"])
    # Claude-authored (2026-10-15): users can run in parallel threads. The lock keeps the shared Finnhub /
    # Twelve Data keys at single-user request rates, makes this cache read-modify-write safe, and lets a user who
    # waited find symbols another user just fetched (see src/shared_locks.py).
    with MARKET_DATA_LOCK:
        cache = _load_context_cache()

        needs_full = [
            s for s in formatted_symbols
            if s not in cache or _parse_time(cache[s].get("slow_fetched_at")) < refresh_boundary
        ]
        needs_fresh = [
            s for s in formatted_symbols
            if s not in needs_full and _parse_time(cache[s].get("fresh_fetched_at")) < fresh_cutoff
        ]
        print(
            f"Market context for {len(formatted_symbols)} held symbols: {len(needs_full)} full fetch, "
            f"{len(needs_fresh)} quote/news refresh, "
            f"{len(formatted_symbols) - len(needs_full) - len(needs_fresh)} from cache"
        )

        if needs_full or needs_fresh:
            try:
                fetcher = HoldingsDataFetcher(tickers=formatted_symbols)
            except ValueError as e:
                print(f"[ERROR] Could not initialize DataFetcher for holdings: {e}")
                fetcher = None

            if fetcher is not None:
                now_iso = now.isoformat()
                if needs_full:
                    llm_packets = fetcher.congregate_LLM_input_data(needs_full, as_dict_records=True)
                    for symbol, raw_data in llm_packets.items():
                        cache[symbol] = {
                            "context": sanitize_ticker_payload(symbol, raw_data),
                            "slow_fetched_at": now_iso,
                            "fresh_fetched_at": now_iso,
                        }
                for symbol in needs_fresh:
                    cache[symbol]["context"] = _refresh_fresh_sections(fetcher, symbol, cache[symbol]["context"])
                    cache[symbol]["fresh_fetched_at"] = now_iso

                retention_cutoff = now - timedelta(days=WATCHDOG_PARAMS["CONTEXT_CACHE_RETENTION_DAYS"])
                cache = {
                    s: entry for s, entry in cache.items()
                    if _parse_time(entry.get("fresh_fetched_at")) >= retention_cutoff
                }
                _save_context_cache(cache)

    sanitized_context: dict[str, dict[str, Any]] = {
        s: cache[s]["context"] for s in formatted_symbols if s in cache
    }
    print(f"✓ Market context ready for {len(sanitized_context)} held symbols")

    df["market_context"] = df["symbol"].map(
        lambda s: sanitized_context.get(sym_map.get(s))
    )
    return df
