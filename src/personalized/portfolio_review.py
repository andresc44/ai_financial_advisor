#File to ask the holdings LLM (Claude by default) for a holistic review of the user's whole portfolio for the daily digest.
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
- 2026-10-17: each note also gets a short title, shown as its collapsed dropdown row in the digest email.
- 2026-10-23: written by the holdings model (params_dict["LLM"]["HOLDINGS"], Claude Haiku 5.5 at low effort) with the
  user's own Anthropic key, instead of Gemini, at Andres's request: the review was ~$0.017 a digest on Gemini, mostly
  thinking, and every user already has an Anthropic key for the holdings. It answers through a strict tool schema,
  like the holdings evaluation (src/personalized/recommendation.py); a DeepSeek holdings model works the same way.
  DIGEST.PORTFOLIO_REVIEW_MAX_TOKENS caps the answer, thinking included.
"""
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from anthropic import Anthropic, APIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.ops.run_logging import debug
from src.llm import llm_models
from src.llm.deepseek_client import DeepSeekClient, DeepSeekError, build_request, inline_refs, validated_answer
from src.market.fx_rates import CURRENCY_SYMBOLS
from src.config.parameters import params_dict
from src.personalized.api_errors import ApiKeyUnusable, anthropic_key_error, deepseek_key_error
from src.personalized.holdings_state import _clean_float
from src.tracking.usage_tracking import record_anthropic_response, record_deepseek_response

DIGEST_PARAMS = params_dict["DIGEST"]
CATEGORIES = ("WATCH", "NOTE")
REVIEW_TOOL_NAME = "submit_portfolio_review"
logger = logging.getLogger(__name__)

# 1. Define Pydantic Schema (2026-10-23: strict, as a tool's input; extra="forbid" emits additionalProperties: false)
class ReviewNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["WATCH", "NOTE"] = Field(
        ..., description="WATCH: something to keep an eye on or consider acting on. NOTE: a neutral observation or strength.")
    # Claude-authored (2026-10-17): the note's collapsed dropdown row in the digest email
    title: str = Field(..., description="A 3-8 word headline naming the point, e.g. 'Leveraged ETFs drive a 2.2 beta'.")
    text: str = Field(..., description="1-2 concise sentences citing the figures behind the note.")
    symbols: list[str] = Field(
        ..., description="Held tickers the note is about, exactly as given; an empty list for portfolio-wide notes.")

class PortfolioReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: list[ReviewNote] = Field(..., description="The notes, most important first.")

REVIEW_TOOL_SPEC = {
    "name": REVIEW_TOOL_NAME,
    "description": "Submit the portfolio review: its notes, most important first.",
    "strict": True,
    "input_schema": inline_refs(PortfolioReview.model_json_schema()),
}

class ReviewFailed(Exception):
    """An answer the review can't use (declined, cut off, or without the tool call)."""

# 2. System Prompt
SYSTEM_PROMPT = """
You are a senior portfolio manager writing a short review of a client's whole portfolio for their daily digest.
You receive a JSON summary with portfolio totals and one entry per stock: weight, gains, technicals, the buy_more/hold/risky/profit_sell/urgent_sell recommendation and sentiment from a separate per-stock analysis, upcoming earnings, and recent news.

REVIEW APPROACH:
- Look at the portfolio as a whole rather than repeating each stock's analysis: concentration in single stocks, sectors, or currencies; holdings likely to move together; overall market sensitivity (beta) and volatility; earnings dates clustered close together; how much of the portfolio sits in positions flagged 'risky', 'profit_sell', or 'urgent_sell', and which are 'buy_more' opportunities; large unrealized gains or losses; and recent news that matters at the portfolio level.
- Put things worth watching first, then neutral observations or strengths.

GROUNDING RULES:
- Use only the data provided, and cite the specific figures (with values) behind each note. Never invent events, prices, or statistics, and never assume news that is not in the data. You may use general knowledge of what each company does, e.g. to recognise holdings that tend to move together.
- This is a review, not a hunt for problems. If the portfolio is balanced or nothing stands out, say so plainly in a 'NOTE' instead of stretching for issues.
- Return at most the requested number of notes. Fewer, stronger notes are better than padding with weak or repetitive points.
- null means the value was unavailable: never treat it as zero or as a problem in itself.
- All money amounts and totals are in `money_currency`, the client's home currency; holdings listed in other currencies (see `currencies`) were converted at today's exchange rate. Write every money amount in `money_currency`, formatted like `money_format`, never in another currency.
- `daily_pnl_pct` is the move over the trading session dated `daily_pnl_session` on that stock's own exchange. When `daily_pnl_live` is true that session is still trading, so it is the move so far today; otherwise it is a completed session (before a market opens, its previous trading day), so describe it by that date, not as "today".

FORMATTING REQUIREMENTS:
Keep all fields extremely succinct and direct:
- category: Must be "WATCH" (something to keep an eye on or consider acting on) or "NOTE" (a neutral observation or strength).
- title: A 3-8 word headline naming the point, readable on its own (e.g. "Leveraged ETFs drive a 2.2 beta"). The reader sees only the title until they open the note.
- text: 1-2 concise sentences citing the supporting figures.
- symbols: The tickers the note is about, exactly as given in the data; an empty list for portfolio-wide notes.

Submit the review by calling the submit_portfolio_review tool once.
"""

# 3. Portfolio summary (figures precomputed so the model never does its own arithmetic)
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

def build_portfolio_summary(holdings: list[dict], portfolio_beta: Optional[float],
                            home: tuple[str, float] = ("USD", 1.0)) -> dict:
    """
    Condenses the digest's holdings rows (one per lot/account) into portfolio totals and one entry per stock,
    largest position first. Reads only the columns present in the payload; missing ones become null.
    2026-10-21: money amounts are in the user's home currency (`home`: its code and USD per unit), which the review
    then writes them in; the holdings' own amounts are in USD.
    """
    code, usd_per_unit = home
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
            "market_value": _round(value / usd_per_unit),
            "unrealized_pnl_pct": _unrealized_pnl_pct(lots),
            "daily_pnl_pct": _round(first.get("daily_pnl_pct")),
            "daily_pnl_session": _text(first.get("daily_pnl_session")),
            "daily_pnl_live": str(first.get("daily_pnl_live")) == "True",  # 2026-10-21: that session still trading
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
            "total_market_value": _round(total_value / usd_per_unit),
            # 2026-10-21: the currency of every money amount here, and how the review writes one
            "money_currency": code,
            "money_format": f"{CURRENCY_SYMBOLS.get(code, code + ' ')}1,234",
            "currencies": sorted({h["currency"] for h in holdings if isinstance(h.get("currency"), str)}),
            "unrealized_pnl_pct": _unrealized_pnl_pct(holdings),
            "daily_pnl_pct": _round(daily_pnl / prior_value * 100) if prior_value > 0 else None,
            "beta": _round(portfolio_beta),
        },
        "holdings": stocks,
    }

# 4. Ask the holdings model (Claude-authored, 2026-10-23: Claude or DeepSeek instead of Gemini)
def review_settings() -> dict[str, Any]:
    """{"model", "effort", "provider", "max_tokens"}: params_dict["LLM"]["HOLDINGS"] and DIGEST.PORTFOLIO_REVIEW_MAX_TOKENS."""
    settings = dict(llm_models.job_settings("HOLDINGS"))
    if settings.get("effort") not in llm_models.allowed_models().get(settings["model"], [settings.get("effort")]):
        settings["effort"] = llm_models.default_effort(settings["model"])
    settings["provider"] = llm_models.provider_of(settings["model"])
    settings["max_tokens"] = int(DIGEST_PARAMS.get("PORTFOLIO_REVIEW_MAX_TOKENS") or 8000)
    return settings

def _ask_claude(api_key: str, settings: dict, prompt: str, user_name: Optional[str]) -> PortfolioReview:
    client = Anthropic(api_key=api_key)  # The SDK retries rate limits and server errors itself
    response = client.messages.create(
        model=settings["model"],
        max_tokens=settings["max_tokens"],
        output_config={"effort": settings["effort"]},
        system=SYSTEM_PROMPT,
        tools=[REVIEW_TOOL_SPEC],
        tool_choice={"type": "auto"},  # Strict schema + "auto": newer models reject a forced tool choice
        messages=[{"role": "user", "content": prompt}],
    )
    record_anthropic_response(user_name, "digest", response)  # Every answered request is billed
    if response.stop_reason in ("refusal", "max_tokens"):
        raise ReviewFailed(f"the answer stopped early ({response.stop_reason}, max_tokens {settings['max_tokens']})")
    block = next((b for b in response.content if b.type == "tool_use" and b.name == REVIEW_TOOL_NAME), None)
    if block is None:
        raise ReviewFailed(f"the model didn't call {REVIEW_TOOL_NAME}")
    return PortfolioReview(**block.input)

async def _ask_deepseek(api_key: str, settings: dict, prompt: str, user_name: Optional[str]) -> PortfolioReview:
    client = DeepSeekClient(api_key=api_key)
    try:
        request = build_request(settings["model"], settings["effort"], SYSTEM_PROMPT, prompt, REVIEW_TOOL_SPEC)
        review = await validated_answer(
            client, request, REVIEW_TOOL_SPEC, PortfolioReview, "portfolio review", logger,
            on_response=lambda response: record_deepseek_response(user_name, "digest", settings["model"], response),
        )
    finally:
        await client.aclose()
    if review is None:
        raise ReviewFailed("no valid answer")
    return review

def fetch_portfolio_review(
    api_key: str, summary: dict, max_notes: int, user_name: Optional[str] = None
) -> PortfolioReview:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    prompt_text = (
        f"Today is {today:%Y-%m-%d}. Review this portfolio and give at most {max_notes} notes, "
        f"most important first:\n\n{json.dumps(summary, indent=2, default=str)}"
    )
    settings = review_settings()
    start_time = time.time()
    if settings["provider"] == llm_models.DEEPSEEK:
        review = asyncio.run(_ask_deepseek(api_key, settings, prompt_text, user_name))
    else:
        review = _ask_claude(api_key, settings, prompt_text, user_name)
    debug(f"[DIAGNOSTIC] Portfolio review received in {round(time.time() - start_time, 2)}s "
          f"({settings['model']}, effort {settings['effort']}).")
    return review

# 5. Review the whole portfolio
def review_portfolio(
    holdings: list[dict],
    portfolio_beta: Optional[float],
    api_key: str,
    user_name: Optional[str] = None,
    max_notes: Optional[int] = None,
    home: tuple[str, float] = ("USD", 1.0),
) -> tuple[list[ReviewNote], Optional[ApiKeyUnusable]]:
    """
    Returns (notes, key_error): at most `max_notes` notes reviewing the whole portfolio, or [] when there are no
    holdings or the request fails. key_error is set when the failure was the key (invalid, expired, no credits,
    spend limit). `api_key` is the user's own key for the holdings model's provider (2026-10-23: Anthropic by
    default). Symbols are limited to tickers actually held.
    """
    max_notes = max_notes or DIGEST_PARAMS["PORTFOLIO_REVIEW_BULLETS"]
    summary = build_portfolio_summary(holdings or [], portfolio_beta, home)  # 2026-10-21: in the home currency
    if not summary["holdings"]:
        return [], None

    debug(f"[DIAGNOSTIC] Reviewing portfolio of {len(summary['holdings'])} stocks (max {max_notes} notes)...")
    try:
        review = fetch_portfolio_review(api_key, summary, max_notes, user_name)
    except (APIError, DeepSeekError) as e:
        key_error = (deepseek_key_error if isinstance(e, DeepSeekError) else anthropic_key_error)(e, user_name)
        print(f"[ERROR] Could not fetch portfolio review: {key_error or e}")
        return [], key_error
    except (ReviewFailed, ValidationError) as e:
        print(f"[ERROR] Could not use the portfolio review: {e}")
        return [], None
    except Exception as e:  # The review is optional: a network or other failure must not stop the digest
        print(f"[ERROR] Could not fetch portfolio review: {e}")
        return [], None

    held = {stock["symbol"].upper(): stock["symbol"] for stock in summary["holdings"]}
    for note in review.notes:
        note.symbols = list(dict.fromkeys(
            held[symbol.strip().upper()] for symbol in note.symbols if symbol.strip().upper() in held
        ))
    return review.notes[:max_notes], None
