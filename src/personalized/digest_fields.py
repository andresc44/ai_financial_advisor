# File to derive the email digest's display fields (moving-average status, sentiment, catalysts, portfolio beta)
# from the market context that add_metrics attaches to each holding.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
- 2026-10-14: created. Fills the digest template's ma_status, sentiment, and catalysts (per holding card)
  and portfolio_beta (account dashboard) from the cached market_context instead of placeholder values.
  No extra API calls. Runs after add_recommendations so these columns are never sent to the hold/sell LLM,
  which already sees the underlying indicators in market_context.
- 2026-10-14: sentiment now comes from Claude (add_recommendations); the analyst consensus is only a fallback
  for holdings whose evaluation failed. portfolio_beta returns 0.0 instead of None when no holding has a beta,
  so the template's "%.2f" format never receives None. Added a 'sector' column for the portfolio review
  (portfolio_review.py), which needs it to judge sector concentration.
- 2026-10-17: 'description' takes Yahoo's company name where the market context has one, since the brokerage's
  can be wrong.
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.market_session import market_date
from src.parameters import params_dict
from src.personalized.holdings_state import _clean_float

DIGEST_PARAMS = params_dict["DIGEST"]

# Fallback sentiment when Claude's evaluation failed: Yahoo's analyst consensus key -> the template's badge.
# Missing or "none" reads as Neutral.
ANALYST_SENTIMENT = {
    "strong_buy": "Bullish",
    "buy": "Bullish",
    "hold": "Neutral",
    "underperform": "Bearish",
    "sell": "Bearish",
    "strong_sell": "Bearish",
}

# Finnhub earnings-calendar "hour" codes
EARNINGS_TIMING = {
    "bmo": "before open",
    "amc": "after close",
    "dmh": "during market hours",
}


def _section(context: Any, name: str) -> dict:
    """Returns one section of a market context, or {} when the context or section is missing."""
    if not isinstance(context, dict):
        return {}
    return context.get(name) or {}


def _latest_indicator(context: Any, indicator: str, field: str) -> Optional[float]:
    """Latest value of a Twelve Data indicator series in the market context (series are newest-first)."""
    for point in _section(context, "technical_indicators").get(indicator) or []:
        value = _clean_float(point.get(field)) if isinstance(point, dict) else None
        if value is not None:
            return value
    return None


def _ma_status(context: Any, current_price: Any) -> str:
    """
    'Above 21 EMA (+3.2%)'-style label: price against the latest daily EMA 21 (the trend line the hold/sell
    rubric uses), falling back to Yahoo's 50-day average when Twelve Data had no EMA.
    """
    price = _clean_float(current_price)
    if not price or price <= 0:
        price = _clean_float(_section(context, "price_action").get("current_price"))
    if not price or price <= 0:
        return "N/A"

    reference = _latest_indicator(context, "ema_21_1day", "ema")
    label = "21 EMA"
    if not reference:
        reference = _clean_float(_section(context, "price_action").get("50_day_average"))
        label = "50D MA"
    if not reference:
        return "N/A"

    gap_pct = (price / reference - 1) * 100
    return f"{'Above' if gap_pct >= 0 else 'Below'} {label} ({gap_pct:+.1f}%)"


def _analyst_sentiment(context: Any) -> str:
    """Analyst consensus as Bullish / Neutral / Bearish (fallback for Claude's sentiment)."""
    key = _section(context, "sentiment_and_analysts").get("recommendation_key")
    return ANALYST_SENTIMENT.get(str(key).lower(), "Neutral")


def _earnings_label(event: dict) -> str:
    """e.g. 'Q3 FY26 Earnings (after close)' from a Finnhub earnings-calendar entry."""
    quarter, year = event.get("quarter"), event.get("year")
    label = f"Q{quarter} FY{int(year) % 100:02d} Earnings" if quarter and year else "Earnings"
    timing = EARNINGS_TIMING.get(event.get("hour"))
    return f"{label} ({timing})" if timing else label


def _catalysts(context: Any, today: date, window_days: int) -> list[dict]:
    """
    Upcoming earnings within `window_days` of `today`, soonest first, shaped like create_html.Catalyst
    (event_type, label, event_date, days_until). Ex-dividend dates are not in the market context yet.
    """
    catalysts = []
    for event in _section(context, "catalysts_and_earnings").get("upcoming_earnings") or []:
        if not isinstance(event, dict) or not event.get("date"):
            continue
        try:
            event_date = date.fromisoformat(str(event["date"])[:10])
        except ValueError:
            continue
        days_until = (event_date - today).days
        if 0 <= days_until <= window_days:
            catalysts.append({
                "event_type": "Earnings",
                "label": _earnings_label(event),
                "event_date": event_date,
                "days_until": days_until,
            })
    return sorted(catalysts, key=lambda c: c["event_date"])


def add_digest_fields(holdings_df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds 'ma_status', 'catalysts', 'next_earnings_date', and 'sector' columns derived from each holding's
    'market_context', and fills
    'sentiment' (set by Claude in add_recommendations) from the analyst consensus where it is missing.
    """
    if holdings_df.empty:
        return holdings_df

    df = holdings_df.copy()
    contexts = df["market_context"] if "market_context" in df.columns else pd.Series(None, index=df.index)
    today = market_date(datetime.now(timezone.utc))
    window_days = DIGEST_PARAMS["CATALYST_WINDOW_DAYS"]

    # 2026-10-20: the price the card shows (valuation_price), not an extended-hours quote
    prices = df["valuation_price"] if "valuation_price" in df.columns else df["current_price"]
    df["ma_status"] = [_ma_status(ctx, price) for ctx, price in zip(contexts, prices)]
    claude_sentiment = df["sentiment"] if "sentiment" in df.columns else pd.Series(None, index=df.index)
    df["sentiment"] = [
        sentiment if isinstance(sentiment, str) else _analyst_sentiment(ctx)
        for sentiment, ctx in zip(claude_sentiment, contexts)
    ]
    # Explicit object Series: a plain list of (possibly empty) lists can be read as a 2-D array
    df["catalysts"] = pd.Series(
        [_catalysts(ctx, today, window_days) for ctx in contexts], index=df.index, dtype=object
    )
    # Claude-authored (2026-10-21): the next earnings date at any distance, so the card can say when none is known
    # (often for listings outside the US) rather than leave the reader to wonder; ISO date, or None
    df["next_earnings_date"] = [
        next((c["event_date"].isoformat() for c in _catalysts(ctx, today, 10_000)), None) for ctx in contexts
    ]
    df["sector"] = [_section(ctx, "profile").get("sector") for ctx in contexts]
    # Claude-authored (2026-10-17): Yahoo's company name over the brokerage's description, which can be wrong
    # (SnapTrade has called Agilent's "A" "AgileThought, Inc."); used by the email and the portfolio review
    yahoo_names = [_section(ctx, "profile").get("name") for ctx in contexts]
    current = df["description"] if "description" in df.columns else pd.Series(None, index=df.index)
    df["description"] = [
        name.strip() if isinstance(name, str) and name.strip() else description
        for name, description in zip(yahoo_names, current)
    ]
    return df


def portfolio_beta(holdings_df: pd.DataFrame) -> float:
    """
    Market-value-weighted average beta over the holdings that have one (Yahoo/Finnhub beta, falling back to
    Twelve Data's 52-week beta). 0.0 when no holding has a beta. Approximate: ignores currency differences.
    """
    if holdings_df.empty or "market_context" not in holdings_df.columns:
        return 0.0

    weighted_sum, covered_value = 0.0, 0.0
    for context, market_value in zip(holdings_df["market_context"], holdings_df["market_value"]):
        value = _clean_float(market_value)
        beta = _clean_float(_section(context, "price_action").get("beta"))
        if beta is None:
            beta = _latest_indicator(context, "beta_52_1week", "beta")
        if beta is None or not value or value <= 0:
            continue
        weighted_sum += beta * value
        covered_value += value

    if covered_value == 0:
        print("[WARNING] No holding has a beta. Reporting portfolio beta as 0.0.")
        return 0.0

    total_value = holdings_df["market_value"].clip(lower=0).sum()
    print(f"Portfolio beta covers {covered_value / total_value:.0%} of market value")
    return round(weighted_sum / covered_value, 2)
