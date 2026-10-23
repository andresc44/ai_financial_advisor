# File to fetch exchange rates to USD (Yahoo, with a last-known-rate cache) so holdings in CAD or other currencies are reported in USD.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-15, at Andres's request.

Why: SnapTrade and Google Sheets holdings can mix CAD (TSX listings) and USD positions. Totals, portfolio weights,
beta, and the portfolio review used to add those amounts together unconverted. PortfolioFetcher.convert_to_usd now
converts money amounts with the rates from here, so everything the reader sees is in USD.
2026-10-21: also EUR, GBP (and pence), AUD, and COP holdings, and each user's home currency, which the digest's
overview shows the portfolio in (create_html).

Rates are USD per one unit of the currency, from Yahoo's "<CODE>USD=X" daily close. If Yahoo fails, the last good
rate saved in data/fx_rates.json is used (with a warning); with neither, the rate is None and amounts in that
currency are left unconverted (with an error), never silently treated as USD.
"""
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd
import yfinance as yf

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.market.market_session import listing

CACHE_PATH = project_root / "data" / "fx_rates.json"
_LOCK = threading.Lock()  # Users' holdings load in parallel threads; one fetch and cache write at a time
PENCE_CODES = {"GBX", "GBp"}  # Yahoo quotes London listings in pence
# How the email and the portfolio review write each currency's amounts (moved here from create_html, 2026-10-21)
CURRENCY_SYMBOLS = {"USD": "$", "CAD": "C$", "AUD": "A$", "EUR": "€", "GBP": "£", "COP": "COP$"}


def normalize_currency(value: Any, symbol: Optional[str] = None) -> str:
    """
    ISO currency code from SnapTrade (a dict with "code"), Sheets / Yahoo (a string), or nothing. Missing values
    fall back to the listing's currency judged by its Yahoo suffix (2026-10-21: market_session.LISTING_SUFFIXES, e.g.
    CAD for .TO, EUR for .DE, COP for .CL), otherwise USD.
    """
    if isinstance(value, dict):
        value = value.get("code")
    if isinstance(value, str) and value.strip():
        value = value.strip()
        return "GBX" if value in PENCE_CODES else value.upper()
    return listing(symbol)[1] if symbol else "USD"


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache if isinstance(cache, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=CACHE_PATH.parent, prefix=".fx_rates_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        os.replace(tmp_path, CACHE_PATH)
    except OSError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _fetch_live(codes: list[str]) -> dict[str, float]:
    """Latest daily close of <CODE>USD=X for each code, skipping any Yahoo can't price."""
    pairs = {code: f"{'GBP' if code == 'GBX' else code}USD=X" for code in codes}
    try:
        data = yf.download(tickers=sorted(set(pairs.values())), period="5d", interval="1d", progress=False, threads=True)
        closes = data["Close"]
    except Exception as e:  # yfinance raises many kinds of errors; a failed fetch falls back to the cache
        print(f"[WARNING] Exchange-rate download failed: {e}")
        return {}

    rates = {}
    for code, pair in pairs.items():
        series = closes if isinstance(closes, pd.Series) else closes.get(pair)
        if series is None:
            continue
        series = series.dropna()
        if series.empty:
            continue
        rate = float(series.iloc[-1]) / (100 if code == "GBX" else 1)
        if 0 < rate < 1000:  # Guards against a garbage quote
            rates[code] = rate
    return rates


def usd_rate_change(code: str, sessions: int = 5) -> Optional[tuple[float, float]]:
    """
    Claude-authored (2026-10-21): (USD per unit of `code` at the latest daily close, and `sessions` closes earlier),
    from Yahoo's <CODE>USD=X, for the digest's line on how the home currency moved against the dollar over the past
    week. None for USD, or when Yahoo has no such history (the line is then left out).
    """
    if not code or code == "USD":
        return None
    try:
        data = yf.download(tickers=f"{code}USD=X", period="1mo", interval="1d", progress=False)
        closes = data["Close"]
        series = (closes if isinstance(closes, pd.Series) else closes.iloc[:, 0]).dropna()
    except Exception as e:  # yfinance raises many kinds of errors; the digest just leaves the line out
        print(f"[WARNING] {code}->USD history download failed: {e}")
        return None
    if len(series) <= sessions:
        return None
    now, then = float(series.iloc[-1]), float(series.iloc[-1 - sessions])
    return (now, then) if now > 0 and then > 0 else None


def usd_rates(currencies: Iterable[str]) -> dict[str, Optional[float]]:
    """USD per one unit of each currency code: live from Yahoo, else the last good cached rate, else None."""
    codes = sorted({code for code in currencies if code and code != "USD"})
    rates: dict[str, Optional[float]] = {"USD": 1.0}
    if not codes:
        return rates

    with _LOCK:
        cache = _load_cache()
        live = _fetch_live(codes)
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for code in codes:
            if code in live:
                rates[code] = live[code]
                cache[code] = {"rate": live[code], "as_of": now_iso}
            elif isinstance(cache.get(code), dict) and cache[code].get("rate"):
                rates[code] = float(cache[code]["rate"])
                print(f"[WARNING] Using the cached {code}->USD rate from {cache[code].get('as_of')}; Yahoo had none.")
            else:
                rates[code] = None
                print(f"[ERROR] No {code}->USD exchange rate; amounts in {code} are left unconverted.")
        if live:
            _save_cache(cache)
    return rates
