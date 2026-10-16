#File to connect to Gemini API and write a holistic review of the user's whole portfolio for the daily digest.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
- 2026-10-14: created, modelled on symbol_specific_news.py (Pydantic schema, chat session, tenacity retries on
  APIError, the user's own Gemini key). Instead of hunting for "risk warnings", Gemini writes a review: at most
  params_dict["DIGEST"]["PORTFOLIO_REVIEW_BULLETS"] notes on what to watch and what stands out. Why no Google
  Search here: the review must judge only the digest's own data (holdings, Claude's recommendations, and the
  news already fetched per stock), so it cannot pull in unrelated stories or invent problems. Figures are
  precomputed in build_portfolio_summary so Gemini never does its own arithmetic.
- 2026-10-15: retries now use api_errors.GEMINI_RETRY, and a key failure (invalid, expired, no credits,
  exhausted quota) is returned to the caller instead of only being printed.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types
from pydantic import BaseModel
from tenacity import retry

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src.personalized.api_errors import GEMINI_RETRY, ApiKeyUnusable, gemini_key_error
from src.personalized.holdings_state import _clean_float
from src.usage_tracking import record_gemini_response

DIGEST_PARAMS = params_dict["DIGEST"]
GEMINI_MODEL = params_dict["GEMINI_MODELS"]["PORTFOLIO_REVIEW"]  # Claude-authored (2026-10-16): set in parameters.py
CATEGORIES = ("WATCH", "NOTE")

# 1. Define Pydantic Schema
class ReviewNote(BaseModel):
    category: str        # Expects "WATCH" (something to keep an eye on) or "NOTE" (neutral observation or strength)
    text: str            # 1-2 concise sentences citing the figures behind the note
    symbols: list[str]   # Held tickers the note is about; empty for portfolio-wide notes

class PortfolioReview(BaseModel):
    notes: list[ReviewNote]

# 2. System Prompt
SYSTEM_PROMPT = """
You are a senior portfolio manager writing a short review of a client's whole portfolio for their daily digest.
You receive a JSON summary with portfolio totals and one entry per stock: weight, gains, technicals, the hold/trim/risk_alert/urgent_sell recommendation and sentiment from a separate per-stock analysis, upcoming earnings, and recent news.

REVIEW APPROACH:
- Look at the portfolio as a whole rather than repeating each stock's analysis: concentration in single stocks, sectors, or currencies; holdings likely to move together; overall market sensitivity (beta) and volatility; earnings dates clustered close together; how much of the portfolio sits in positions flagged 'trim', 'risk_alert', or 'urgent_sell'; large unrealized gains or losses; and recent news that matters at the portfolio level.
- Put things worth watching first, then neutral observations or strengths.

GROUNDING RULES:
- Use only the data provided, and cite the specific figures (with values) behind each note. Never invent events, prices, or statistics, and never assume news that is not in the data. You may use general knowledge of what each company does, e.g. to recognise holdings that tend to move together.
- This is a review, not a hunt for problems. If the portfolio is balanced or nothing stands out, say so plainly in a 'NOTE' instead of stretching for issues.
- Return at most the requested number of notes. Fewer, stronger notes are better than padding with weak or repetitive points.
- null means the value was unavailable: never treat it as zero or as a problem in itself.
- All money amounts and totals are in USD; holdings listed in other currencies (see `currencies`) were converted at today's exchange rate.
- `daily_pnl_pct` is the move over the trading session dated `daily_pnl_session`. Before the market opens that is the previous trading day, so describe it by that date, not as "today".

FORMATTING REQUIREMENTS:
Keep all fields extremely succinct and direct:
- category: Must be "WATCH" (something to keep an eye on or consider acting on) or "NOTE" (a neutral observation or strength).
- text: 1-2 concise sentences citing the supporting figures.
- symbols: The tickers the note is about, exactly as given in the data; an empty list for portfolio-wide notes.
"""

# 3. Portfolio summary (figures precomputed so Gemini never does its own arithmetic)
def _round(value: Any) -> Optional[float]:
    number = _clean_float(value)
    return round(number, 2) if number is not None else None

def _text(value: Any) -> Optional[str]:
    """Strings pass through; pandas' NaN placeholders for missing text (e.g. no sector, failed evaluation) become None."""
    return value if isinstance(value, str) else None

def _total(rows: list[dict], key: str) -> float:
    return sum(_clean_float(row.get(key)) or 0.0 for row in rows)

def _unrealized_pnl_pct(rows: list[dict]) -> Optional[float]:
    """Unrealized gain % over the lots with a known cost basis (a 0.0 cost basis means it was not reported)."""
    known = [row for row in rows if (_clean_float(row.get("total_cost_basis")) or 0.0) > 0]
    cost = _total(known, "total_cost_basis")
    return _round((_total(known, "market_value") - cost) / cost * 100) if cost > 0 else None

def build_portfolio_summary(holdings: list[dict], portfolio_beta: Optional[float]) -> dict:
    """
    Condenses the digest's holdings rows (one per lot/account) into portfolio totals and one entry per stock,
    largest position first. Reads only the columns present in the payload; missing ones become null.
    """
    lots_by_symbol: dict[str, list[dict]] = {}
    for holding in holdings:
        if holding.get("symbol"):
            lots_by_symbol.setdefault(holding["symbol"], []).append(holding)

    total_value = _total(holdings, "market_value")
    daily_pnl = _total(holdings, "daily_pnl")
    # Claude-authored (2026-10-15): value at the previous close. Daily P/L stops at the session close while
    # market_value uses the latest broker price, so market_value - daily_pnl is only exact in regular hours.
    # 2026-10-15: last_close_usd, because market_value and daily_pnl are in USD (last_close is in the listing currency)
    prior_value = sum(
        (_clean_float(h.get("last_close_usd")) or _clean_float(h.get("last_close")) or 0.0)
        * (_clean_float(h.get("shares")) or 0.0)
        for h in holdings
    )
    if prior_value <= 0:  # last_close / shares not in the payload
        prior_value = total_value - daily_pnl

    stocks = []
    for symbol, lots in lots_by_symbol.items():
        first = lots[0]  # Price-based fields are identical across a stock's lots
        value = _total(lots, "market_value")
        stocks.append({
            "symbol": symbol,
            "name": _text(first.get("description")),
            "sector": _text(first.get("sector")),
            "currency": _text(first.get("currency")),
            "accounts": sorted({lot["account_name"] for lot in lots if isinstance(lot.get("account_name"), str)}),
            "weight_pct": _round(value / total_value * 100) if total_value > 0 else None,
            "market_value": _round(value),
            "unrealized_pnl_pct": _unrealized_pnl_pct(lots),
            "daily_pnl_pct": _round(first.get("daily_pnl_pct")),
            "daily_pnl_session": _text(first.get("daily_pnl_session")),
            "rsi": _round(first.get("rsi")),
            "natr_pct": _round(first.get("natr")),
            "annualized_vol_pct": _round(first.get("annualized_vol")),
            "ma_status": _text(first.get("ma_status")),
            "recommendation": _text(first.get("recommendation")),
            "sentiment": _text(first.get("sentiment")),
            "recommendation_rationale": _text(first.get("recommendation_rationale")),
            "upcoming_catalysts": [
                f"{c.get('label')} on {c.get('event_date')} (in {c.get('days_until')} days)"
                for c in first.get("catalysts") or []
            ],
            "recent_news": [f"[{n.get('priority')}] {n.get('text')}" for n in first.get("news_bullets") or []],
        })
    stocks.sort(key=lambda stock: stock["market_value"] or 0.0, reverse=True)

    return {
        "portfolio": {
            "number_of_stocks": len(stocks),
            "total_market_value": _round(total_value),
            "currencies": sorted({h["currency"] for h in holdings if isinstance(h.get("currency"), str)}),
            "unrealized_pnl_pct": _unrealized_pnl_pct(holdings),
            "daily_pnl_pct": _round(daily_pnl / prior_value * 100) if prior_value > 0 else None,
            "beta": _round(portfolio_beta),
        },
        "holdings": stocks,
    }

# 4. Fetch Function using Chat interface, matching symbol_specific_news.py
@retry(**GEMINI_RETRY)  # Retries only what can recover; key, billing, and quota errors fail immediately
def fetch_portfolio_review(
    client: genai.Client, summary: dict, max_notes: int, user_name: Optional[str] = None
) -> PortfolioReview:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    prompt_text = (
        f"Today is {today:%Y-%m-%d}. Review this portfolio and give at most {max_notes} notes, "
        f"most important first:\n\n{json.dumps(summary, indent=2, default=str)}"
    )

    chat = client.chats.create(
        model=GEMINI_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=PortfolioReview,
        )
    )

    start_time = time.time()
    response = chat.send_message(prompt_text)
    elapsed = round(time.time() - start_time, 2)
    record_gemini_response(user_name, "digest", GEMINI_MODEL, response)  # Claude-authored (2026-10-16): user's cost
    print(f"[DIAGNOSTIC] Portfolio review received in {elapsed}s.")

    return PortfolioReview.model_validate_json(response.text)

# 5. Review the whole portfolio
def review_portfolio(
    holdings: list[dict],
    portfolio_beta: Optional[float],
    api_key: str,
    user_name: Optional[str] = None,
    max_notes: Optional[int] = None,
) -> tuple[list[ReviewNote], Optional[ApiKeyUnusable]]:
    """
    Returns (notes, key_error): at most `max_notes` notes reviewing the whole portfolio, or [] when there are no
    holdings or the request fails. key_error is set when the failure was the key (invalid, expired, no credits,
    exhausted quota). Categories are normalized to WATCH / NOTE, and symbols are limited to tickers actually held.
    """
    max_notes = max_notes or DIGEST_PARAMS["PORTFOLIO_REVIEW_BULLETS"]
    summary = build_portfolio_summary(holdings or [], portfolio_beta)
    if not summary["holdings"]:
        return [], None

    print(f"\n[DIAGNOSTIC] Reviewing portfolio of {len(summary['holdings'])} stocks (max {max_notes} notes)...")
    client = genai.Client(api_key=api_key)  # The user's own key, never the shared GEMINI_API_KEY
    try:
        review = fetch_portfolio_review(client, summary, max_notes, user_name)
    except Exception as e:  # The review is optional: an API, network, or parsing failure must not stop the digest
        key_error = gemini_key_error(e, user_name)
        print(f"[ERROR] Could not fetch portfolio review: {key_error or e}")
        return [], key_error

    held = {stock["symbol"].upper(): stock["symbol"] for stock in summary["holdings"]}
    for note in review.notes:
        note.category = note.category.strip().upper()
        if note.category not in CATEGORIES:
            note.category = "NOTE"
        note.symbols = list(dict.fromkeys(
            held[symbol.strip().upper()] for symbol in note.symbols if symbol.strip().upper() in held
        ))
    return review.notes[:max_notes], None
