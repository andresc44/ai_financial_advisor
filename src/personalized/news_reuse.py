# File to keep each user's holding news from earlier in the day, so the evening digest can show it again without asking Gemini.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-23, at Andres's request.

Why: the Post-Market digest asked Gemini for every holding's news again about 9 hours after the Pre-Market one, and
it was mostly the same stories. Gemini was ~85% of a user's bill. Now a holding's news fetched earlier the same US
Eastern day is shown again, unless one of these is true (they are checked after the evaluation, which is when the
price, the signal, and the earnings date are known, and those holdings are fetched again):
- its price moved more than DIGEST.NEWS_REUSE.MAX_MOVE_NATR normal days' ranges (its NATR) since then;
- its recommendation (the signal) changed;
- its earnings date was that day, or has changed since (it reported);
- the move can't be measured (no price or NATR).
DIGEST.NEWS_REUSE.ENABLED False always fetches.

Kept per user in data/holding_news/<user>.json as {symbol: {"fetched_at", "market_date", "price", "natr",
"recommendation", "next_earnings_date", "items": [{"priority", "summary", "source_url"}]}}. Only freshly fetched news
is written. News shown again keeps its first entry, so each later digest that day compares against when it was
fetched. Entries from earlier days are dropped when the file is written.
"""
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config.parameters import params_dict
from src.personalized.holdings_state import _clean_float
from src.personalized.symbol_specific_news import SymbolNewsItem

CACHE_DIR = project_root / "data" / "holding_news"
MARKET_TZ = ZoneInfo("America/New_York")


def _settings() -> dict:
    return params_dict["DIGEST"].get("NEWS_REUSE") or {}


def _path(user_name: str) -> Path:
    return CACHE_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', user_name)}.json"


def _market_date(moment: datetime) -> str:
    return moment.astimezone(MARKET_TZ).date().isoformat()


def load_news(user_name: str) -> dict:
    """The user's saved news, {} when there is none or the file can't be read."""
    try:
        cache = json.loads(_path(user_name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cache if isinstance(cache, dict) else {}


def same_day_news(user_name: str, symbols: Iterable[str], now: Optional[datetime] = None) -> dict[str, dict]:
    """{symbol: saved entry} for the held `symbols` whose news was fetched earlier today (US Eastern); {} when off."""
    if not _settings().get("ENABLED", False):
        return {}
    today = _market_date(now or datetime.now(timezone.utc))
    held = set(symbols)
    return {symbol: entry for symbol, entry in load_news(user_name).items()
            if symbol in held and isinstance(entry, dict) and entry.get("market_date") == today}


def as_items(entry: dict) -> list[SymbolNewsItem]:
    return [SymbolNewsItem(**item) for item in entry.get("items") or []]


def _first_rows(holdings: Optional[list[dict]]) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for row in holdings or []:
        if row.get("symbol"):
            rows.setdefault(row["symbol"], row)  # Price, signal, and dates are the same across a stock's lots
    return rows


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _date_text(value: Any) -> Optional[str]:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return _text(value)[:10] if _text(value) else None


def refresh_reason(entry: dict, row: Optional[dict], today: str) -> Optional[str]:
    """Why news saved earlier today must be fetched again for this holding (`row`, from this run), or None."""
    if row is None:
        return None  # Not evaluated this run: nothing to compare, so the earlier news stands
    price_then, price_now = _clean_float(entry.get("price")), _clean_float(row.get("current_price"))
    natr = _clean_float(entry.get("natr")) or _clean_float(row.get("natr"))
    if not (price_then and price_now and natr):
        return "can't measure its move"
    move_pct = abs(price_now / price_then - 1) * 100
    limit_pct = float(_settings().get("MAX_MOVE_NATR", 1.0)) * natr
    if move_pct > limit_pct:
        return f"moved {move_pct:.1f}%, more than {limit_pct:.1f}%"
    signal_then, signal_now = _text(entry.get("recommendation")), _text(row.get("recommendation"))
    if signal_then and signal_now and signal_then != signal_now:
        return f"signal changed from {signal_then} to {signal_now}"
    earnings_then, earnings_now = _date_text(entry.get("next_earnings_date")), _date_text(row.get("next_earnings_date"))
    if earnings_then and (earnings_then <= today or earnings_now != earnings_then):
        return "it has reported earnings"
    return None


def news_to_refresh(reused: dict[str, dict], holdings: Optional[list[dict]],
                    now: Optional[datetime] = None) -> dict[str, str]:
    """{symbol: reason} for the saved entries in `reused` that must be fetched again (refresh_reason)."""
    today = _market_date(now or datetime.now(timezone.utc))
    rows = _first_rows(holdings)
    reasons = {symbol: refresh_reason(entry, rows.get(symbol), today) for symbol, entry in reused.items()}
    return {symbol: reason for symbol, reason in reasons.items() if reason}


def remember_news(user_name: str, fresh: dict[str, list[SymbolNewsItem]], holdings: Optional[list[dict]],
                  now: Optional[datetime] = None) -> None:
    """
    Saves freshly fetched news with the price, NATR, signal, and earnings date of this run. Never raises: a news
    file that can't be written only means the next digest fetches again.
    """
    if not fresh:
        return
    now = now or datetime.now(timezone.utc)
    today = _market_date(now)
    cache = {symbol: entry for symbol, entry in load_news(user_name).items()
             if isinstance(entry, dict) and entry.get("market_date") == today}
    rows = _first_rows(holdings)
    for symbol, items in fresh.items():
        row = rows.get(symbol) or {}
        cache[symbol] = {
            "fetched_at": now.isoformat(timespec="seconds"),
            "market_date": today,
            "price": _clean_float(row.get("current_price")),
            "natr": _clean_float(row.get("natr")),
            "recommendation": _text(row.get("recommendation")),
            "next_earnings_date": _date_text(row.get("next_earnings_date")),
            "items": [item.model_dump() for item in items],
        }
    path = _path(user_name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        print(f"[WARNING] Could not save {user_name}'s holding news for reuse: {e}")
