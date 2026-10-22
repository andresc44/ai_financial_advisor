# File to work out, in code, the facts the holdings rubric rests on, so every model applies the same ones.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-22, at Andres's request.

Why: in the 2026-10-22 model comparison (docs/model_selection.md), every model other than Claude Opus gave Opus's label
for only about 7 holdings in 10, mostly by calling holdings 'risky' that Opus held or wanted to add to. Of those extra
'risky' labels, 64% cited the price being below its short-term averages and 56% an upcoming report: questions the
rubric left to judgment ("no sign of a floor"). The swing rubric had the same problem until code worked out its checks
(swing_screen.rubric_checks); after that, every model's momentum score agreed with Opus's to within a point.

holding_checks gives each holding the same kind of facts, from data it already carries (market_context's daily bars and
indicator series, the 50- and 200-day averages, and the next earnings date). The thresholds are LLM_hold_instr's, so the
prompt and the code use the same numbers. A fact that can't be worked out is None ("unknown" to the model).
"""
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.personalized import LLM_hold_instr as rubric

MARKET_TZ = ZoneInfo("America/New_York")
LOOKBACK = 20      # The recent high and low: the last 20 sessions' closes and lows
LOW_DAYS = 10      # The "10-day low" of the turn signs
FUND_NAME = re.compile(r"\b(ETF|ETN|Fund|Trust|Portfolio|Shares)\b", re.I)
LEVERAGED = re.compile(r"\b[23]x\b|leverag|inverse|\bultra(pro)?\b", re.I)


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _series(context: Dict[str, Any], name: str, field: str) -> List[float]:
    """An indicator series (technical_indicators[name], newest first) as numbers, stopping at the first gap."""
    values = []
    for point in (context.get("technical_indicators") or {}).get(name) or []:
        value = _number(point.get(field)) if isinstance(point, dict) else None
        if value is None:
            break
        values.append(value)
    return values


def _atr(bars: List[Dict[str, float]], days: int = 14) -> Optional[float]:
    """The average true range over the last `days` sessions (bars newest first), like LLM_recommendation.daily_atr."""
    ranges = [max(today["high"] - today["low"], abs(today["high"] - previous["close"]), abs(today["low"] - previous["close"]))
              for today, previous in zip(bars, bars[1:])][:days]
    return sum(ranges) / len(ranges) if len(ranges) >= 4 else None


def is_fund(payload: Dict[str, Any]) -> bool:
    """An ETF or other fund: the broker says so, or the stock has no sector and a fund-like name."""
    positions = payload.get("positions") or [{}]
    if str(positions[0].get("asset_kind") or "").lower() in ("etf", "fund", "mutual_fund"):
        return True
    profile = (payload.get("market_context") or {}).get("profile") or {}
    return not profile.get("sector") and bool(FUND_NAME.search(str(profile.get("name") or positions[0].get("description") or "")))


def is_leveraged(payload: Dict[str, Any]) -> bool:
    """A leveraged or inverse fund (e.g. 'Direxion Daily Semiconductor Bull 3X Shares')."""
    profile = (payload.get("market_context") or {}).get("profile") or {}
    text = " ".join(str(profile.get(k) or "") for k in ("name", "business_summary"))
    text += " " + str(((payload.get("positions") or [{}])[0]).get("description") or "")
    return bool(LEVERAGED.search(text))


def holding_checks(payload: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    """The holdings rubric's facts for one holding payload (recommendation.build_holding_payloads)."""
    context = payload.get("market_context") or {}
    today = today or datetime.now(MARKET_TZ).date()
    fund = is_fund(payload)
    checks: Dict[str, Any] = {"fund": fund, "leveraged_fund": fund and is_leveraged(payload)}
    bars = [b for b in context.get("daily_bars") or [] if isinstance(b, dict)
            and all(_number(b.get(k)) is not None for k in ("high", "low", "close"))]
    if bars:
        checks["as_of"] = bars[0].get("date")
    price = _number(bars[0]["close"]) if bars else None
    atr = _atr(bars)

    # Earnings: trading days (weekdays) to the next report still ahead
    upcoming = sorted(str(e.get("date")) for e in (context.get("catalysts_and_earnings") or {}).get("upcoming_earnings") or []
                      if isinstance(e, dict) and str(e.get("date") or "") >= today.isoformat())
    checks["next_earnings_date"] = upcoming[0][:10] if upcoming else None
    checks["sessions_to_earnings"] = int(np.busday_count(today, date.fromisoformat(upcoming[0][:10]))) if upcoming else None

    if price is None or not atr:
        return checks  # No price path: the rest is unknown
    closes = [b["close"] for b in bars[:LOOKBACK]]
    lows = [b["low"] for b in bars[:LOOKBACK]]
    high_close, low_20 = max(closes), min(lows)
    checks.update({
        "price": round(price, 4), "atr": round(atr, 4),
        "pullback_from_20d_high_pct": round((1 - price / high_close) * 100, 1),
        "pullback_from_20d_high_atr": round((high_close - price) / atr, 2),
        "run_from_20d_low_pct": round((price / low_20 - 1) * 100, 1),
        "run_from_20d_low_atr": round((price - low_20) / atr, 2),
    })
    checks["pullback"] = checks["pullback_from_20d_high_atr"] >= rubric.PULLBACK_ATR
    checks["recent_run"] = (checks["run_from_20d_low_pct"] >= rubric.RECENT_RUN_PCT
                            or checks["run_from_20d_low_atr"] >= rubric.RECENT_RUN_ATR)

    # The long-term trend, as the swing rubric defines it
    action = context.get("price_action") or {}
    sma50, sma200 = _number(action.get("50_day_average")), _number(action.get("200_day_average"))
    if sma50 and sma200:
        holds = int(price >= sma200) + int(sma50 >= sma200)
        checks["trend"] = ("weak", "mixed", "intact")[holds]
        checks["pct_vs_200_day"] = round((price / sma200 - 1) * 100, 1)
    else:
        checks["trend"] = None

    rsi = _series(context, "rsi_14_1day", "rsi")
    ema9, ema21 = _series(context, "ema_9_1day", "ema"), _series(context, "ema_21_1day", "ema")
    sar, upper = _series(context, "sar_1day", "sar"), _series(context, "bbands_20_1day", "upper_band")
    low_days = lows[:LOW_DAYS]
    days_since_low = int(np.argmin(low_days)) if low_days else None  # Newest first: the latest low if it repeats
    changes = [(b["close"] - prev["close"], _number(b.get("volume")) or 0.0) for b, prev in zip(bars[:LOW_DAYS], bars[1:LOW_DAYS + 1])]
    up_volume = sum(v for change, v in changes if change > 0)
    down_volume = sum(v for change, v in changes if change < 0)
    checks["turn_signs"] = {
        "rsi_rising": len(rsi) > 5 and rsi[5] < rsi[0] < rubric.TURN_SIGN_RSI_BELOW if rsi else None,
        "volume_on_up_days": (down_volume > 0 and up_volume / down_volume >= rubric.TURN_SIGN_UP_VOLUME) if changes else None,
        "higher_low": (days_since_low >= rubric.TURN_SIGN_LOW_HELD and price - min(low_days) >= rubric.TURN_SIGN_OFF_LOW_ATR * atr)
        if days_since_low is not None else None,
        "above_ema_9_or_sar": (price > ema9[0] or (bool(sar) and sar[0] < price)) if ema9 else None,
    }
    checks["turn_sign_count"] = sum(1 for v in checks["turn_signs"].values() if v)
    checks["below_ema_21"] = price < ema21[0] if ema21 else None
    checks["sar_above_price"] = sar[0] > price if sar else None
    checks["new_low_in_last_2_sessions"] = days_since_low is not None and days_since_low < 2
    checks["stretched"] = (bool(upper) and price > upper[0]) or (bool(rsi) and rsi[0] >= rubric.STRETCHED_RSI)
    top = max(rsi[:5]) if rsi else None
    checks["rsi_falling_from_high"] = (top is not None and top >= rubric.RSI_HIGH
                                       and rsi[0] <= top - rubric.RSI_FALL_POINTS)
    return checks
