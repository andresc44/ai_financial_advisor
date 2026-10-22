# File to pick the swing-trade candidates: decent businesses that are cheap right now and set up to go back up soon.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-20, at Andres's request.

Why: swing ideas used to come from the long-term screen, which looks for great businesses at fair prices, so on most
days only a handful of its ~95 stocks were in a swing setup (5 of 97 on 2026-10-16) and none scored 80. This screen
runs beside it on the same stage-1 list (the NASDAQ screener: large, liquid, no penny stocks) and looks for the
setup itself, with lighter fundamentals. Everything it uses is already downloaded: the Yahoo info the long-term
screen fetched for every stock, and one batch of Yahoo daily bars. All thresholds are params_dict["SWING_SCREEN"].

The stages, each logged and counted for the digest:
1. fundamentals: SWING_SCREEN["YAHOO_FILTERS"], looser than the long-term YAHOO_FILTERS: profitable, not shrinking,
   analysts not negative, liquid.
2. setup (from the daily bars, setup_facts): a stock above MIN_PRICE that trades MIN_DOLLAR_VOLUME_M a day, whose RSI
   is between MIN_RSI and MAX_RSI, that is still early in its move (params_dict["SWING_EARLY_ENTRY"], the same check
   the picks get), and that is either
   - a PULLBACK: its longer trend is intact (near or above its 200-day average, or the 50-day still above the
     200-day) and it has pulled back at least MIN_PULLBACK_ATR normal days' ranges from its recent high, or
   - a NEW_UPTREND: its 9-day average crossed above its 21-day average within the last CROSS_WITHIN_DAYS sessions.
3. plan: trade_plan sets the entry, stop, and target from the chart's own levels (SWING_SCREEN["LEVELS"]), the same
   way for every stock, so the risk/reward score no longer depends on which levels the model happens to choose (it
   was the noisiest part of the swing score: up to 9 of its 30 points between identical runs). A plan below
   MIN_PLAN_RR reward-to-risk is dropped: it can't reach a good score.
The MAX_CANDIDATES best plans go to the LLM, which scores the rest of the rubric (setup quality, signs of a turn,
catalysts) from these facts and the usual data. Best means the most risk/reward points, then the earliest in its
move (the smallest run from its 10-day low).

2026-10-22 (Claude-authored, at Andres's request): the screen matches the rubric more closely, and works out the
rubric's checks a fact can settle (rubric_checks, given to the LLM with the stock: LLM_rec_instr has the numbers).
- New rules, each from the rubric: a cross at most 4 sessions old (CROSS_WITHIN_DAYS 5, was 7), a pullback of at
  least 1.5 ATRs (was 1), a pullback at most 5% under its 200-day average even when its 50-day is above it
  (OR_SMA50_ABOVE_SMA200 off: SE, 20% under, got through), the price at most MAX_ABOVE_SUPPORT_ATR above the
  support its stop sits under, no earnings within EARNINGS_BLACKOUT_SESSIONS (Yahoo's date), and no falling knives
  (no sign of a turn while RSI is at a 10-day low).
- Best now means the most of the rubric the code can check (screen_points: the plan's risk/reward points, the
  technical start, and the momentum band's middle), so a stock in a weaker trend or with no sign of a turn yet is
  sent only when there aren't MAX_CANDIDATES better ones. Measured on 2026-10-22's data: requiring a sign of a turn
  and no pullbacks under both averages, as hard rules, would have cut the 78 plans to 20; this way 40 still go.
- add_report_facts adds the facts from each candidate's Finnhub data (earnings beats, insider buying), fetched
  after the screen.
"""
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src.universal.recommendations import local_indicators
from src.universal.recommendations import LLM_rec_instr as rubric
from src.universal.recommendations.LLM_recommendation import plan_risk_reward_score
from src.universal.recommendations.yahoo_filter_helper import OPERATORS, one_listing_per_company

logger = logging.getLogger("Finnbot.SwingScreen")

ATR_DAYS = 14  # A normal day's range, as LLM_recommendation.daily_atr measures it (simple average of true ranges)
SAR_STEP, SAR_MAX = 0.02, 0.2  # The Parabolic SAR the model is shown (TWELVEDATA_CONTEXT_INDICATORS' sar_1day)
MARKET_TZ = ZoneInfo("America/New_York")  # Earnings dates and "today" are the US market's
EARNINGS_DATE_FIELDS = ("earningsTimestampStart", "earningsTimestamp")  # Yahoo info; the first one still ahead
INSIDER_MONTHS = 3  # insider_mspr_3m: this month and the two before


def market_today() -> date:
    return datetime.now(MARKET_TZ).date()


def settings() -> Dict[str, Any]:
    return params_dict.get("SWING_SCREEN") or {}


# ------------------------------------------------------------------------------------------------- 1. fundamentals
def passes_fundamentals(info: Any, rules: List[tuple]) -> Tuple[bool, str]:
    """(passed, why not) for one stock's Yahoo info against (field, operator, threshold) rules. Missing data fails."""
    if not isinstance(info, dict) or "error" in info:
        return False, "no Yahoo data"
    for field, op, threshold in rules:
        value = info.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False, f"{field} missing"
        if not OPERATORS[op](value, threshold):
            return False, f"{field} {value:g} (needs {op} {threshold:g})"
    return True, ""


# ------------------------------------------------------------------------------------------------- 2. setup
def _last(values: np.ndarray, back: int = 0) -> Optional[float]:
    index = len(values) - 1 - back
    if index < 0 or np.isnan(values[index]):
        return None
    return float(values[index])


def market_change(market: Optional[pd.DataFrame], since: Any, until: Any) -> Optional[float]:
    """The market's (SPY's) % change from the close on `since` to the close on `until`, or None without its bars."""
    if market is None or "Close" not in market:
        return None
    closes = market["Close"].dropna()
    try:
        start, end = float(closes.asof(since)), float(closes.asof(until))
    except (TypeError, ValueError, KeyError):
        return None
    return round((end / start - 1) * 100, 1) if start > 0 and end > 0 else None  # NaN (no close yet) fails both


def trend_state(facts: Dict[str, Any]) -> str:
    """The rubric's trend: "intact" (above its 200-day average, with the 50-day above the 200-day), "mixed" (one of the
    two), or "weak" (neither)."""
    holds = int(facts["pct_vs_sma_200"] >= 0) + int(bool(facts["sma_50_above_sma_200"]))
    return ("weak", "mixed", "intact")[holds]


def setup_facts(bars: Optional[pd.DataFrame], market: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    """
    What the daily bars say about a swing setup, in plain numbers, or None without ~1 year of bars. Prices are in the
    stock's currency; distances are in ATRs (a normal day's range). Also given to the LLM with the stock, so it judges
    the same figures the screen did. 2026-10-22: also the trend, the days since the 10-day low, whether RSI is at a
    10-day low, the Parabolic SAR, and the market's (`market`, SPY's bars) move since the stock's recent high.
    """
    if bars is None or len(bars) < 210:
        return None
    high, low = bars["High"].to_numpy(float), bars["Low"].to_numpy(float)
    close, volume = bars["Close"].to_numpy(float), bars["Volume"].fillna(0).to_numpy(float)
    true_range = local_indicators._true_range(high, low, close)
    atr = float(true_range[-ATR_DAYS:].mean())
    price = float(close[-1])
    if atr <= 0 or price <= 0:
        return None
    rules = settings().get("LEVELS") or {}
    support_days = int(rules.get("SUPPORT_LOOKBACK_DAYS", 10))
    pullback_days = int(settings().get("SETUP", {}).get("PULLBACK", {}).get("LOOKBACK_DAYS", 20))
    target_days = int(rules.get("TARGET_LOOKBACK_DAYS", 60))
    ema9, ema21 = local_indicators._ema(close, 9), local_indicators._ema(close, 21)
    upper, middle, lower = local_indicators.bbands(close, 20, 2)
    rsi = local_indicators.rsi(close, 14)
    sma50 = float(close[-50:].mean())
    sma200 = float(close[-200:].mean())
    recent_high = float(close[-pullback_days:].max())
    high_day = bars.index[len(close) - pullback_days + int(np.argmax(close[-pullback_days:]))]
    low_10 = float(low[-support_days:].min())
    sar = local_indicators.sar(high, low, SAR_STEP, SAR_MAX)
    recent_rsi = rsi[-10:]
    changes = np.diff(close[-11:])
    up_volume = float(volume[-10:][changes > 0].sum())
    down_volume = float(volume[-10:][changes < 0].sum())
    crossed = None  # Sessions since the 9-day average last crossed above the 21-day one (0 = today)
    above = ema9 > ema21
    for back in range(0, 30):
        i = len(close) - 1 - back
        if i < 1 or np.isnan(ema21[i - 1]):
            break
        if above[i] and not above[i - 1]:
            crossed = back
            break
        if not above[i]:
            break
    facts = {
        "price": round(price, 2),
        "atr": round(atr, 4),
        "atr_pct": round(atr / price * 100, 2),
        "rsi": round(_last(rsi), 1) if _last(rsi) is not None else None,
        "rsi_5_days_ago": round(_last(rsi, 5), 1) if _last(rsi, 5) is not None else None,
        "rsi_at_10_day_low": bool(_last(rsi) is not None and _last(rsi) <= np.nanmin(recent_rsi)),
        "ema_9": round(_last(ema9), 2),
        "ema_21": round(_last(ema21), 2),
        "sma_50": round(sma50, 2),
        "sma_200": round(sma200, 2),
        "bollinger_lower": round(_last(lower), 2),
        "bollinger_middle": round(_last(middle), 2),
        "bollinger_upper": round(_last(upper), 2),
        "pct_vs_sma_200": round((price / sma200 - 1) * 100, 1),
        "sma_50_above_sma_200": sma50 > sma200,
        f"high_close_{pullback_days}d": round(recent_high, 2),
        "pullback_atr": round((recent_high - price) / atr, 2),
        "pullback_pct": round((1 - price / recent_high) * 100, 1),
        f"low_{support_days}d": round(low_10, 2),
        "run_from_low_atr": round((price - low_10) / atr, 2),
        f"days_since_low_{support_days}d": int(np.argmin(low[-support_days:][::-1])),  # The latest if it repeats
        f"high_close_{target_days}d": round(float(close[-target_days:].max()), 2),
        "ema_9_crossed_above_ema_21_days_ago": crossed,
        "sar_below_price": bool(not np.isnan(sar[-1]) and sar[-1] < price),
        "up_vs_down_volume_10d": round(up_volume / down_volume, 2) if down_volume > 0 else None,
        "dollar_volume_20d_m": round(float((close[-20:] * volume[-20:]).mean()) / 1e6, 1),
        "spy_change_since_high_pct": market_change(market, high_day, bars.index[-1]),
    }
    facts["trend"] = trend_state(facts)
    return facts


def turn_signs(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The rubric's four signs of a turn (momentum_volume_score), each checked against the facts."""
    rsi, before = facts["rsi"], facts["rsi_5_days_ago"]
    support_days = int((settings().get("LEVELS") or {}).get("SUPPORT_LOOKBACK_DAYS", 10))
    return {
        "rsi_rising": rsi is not None and before is not None and before < rsi < rubric.TURN_SIGN_RSI_BELOW,
        "volume_on_up_days": (facts["up_vs_down_volume_10d"] or 0) >= rubric.TURN_SIGN_UP_VOLUME,
        "higher_low": (facts[f"days_since_low_{support_days}d"] >= rubric.TURN_SIGN_LOW_HELD
                       and facts["run_from_low_atr"] >= rubric.TURN_SIGN_OFF_LOW_ATR),
        "above_ema_9_or_sar": facts["price"] > facts["ema_9"] or bool(facts["sar_below_price"]),
    }


def technical_checks(kind: str, facts: Dict[str, Any], plan: Optional[Dict[str, Any]]) -> Tuple[float, List[str]]:
    """(technical_start, weaknesses): 30 less rubric.WEAKNESS_POINTS for each weakness in the facts, by the rubric."""
    points, weaknesses = rubric.WEAKNESS_POINTS, []

    def weakness(times: int, why: str) -> None:
        weaknesses.append((times * points, f"{why} (-{times * points:g})"))

    if facts["trend"] == "weak":
        weakness(2, "weak trend: below its 200-day average, with the 50-day below the 200-day")
    elif facts["trend"] == "mixed" and facts["pct_vs_sma_200"] < 0:
        weakness(1, "mixed trend: below its 200-day average, though the 50-day is above it")
    elif facts["trend"] == "mixed":
        weakness(1, "mixed trend: above its 200-day average, but the 50-day is below it")
    above = (plan or {}).get("above_support_atr")
    if above is not None and above > rubric.SUPPORT_FAR_ATR:
        weakness(2, f"{above:g} ATRs above the support its stop sits under")
    elif above is not None and above > rubric.SUPPORT_NEAR_ATR:
        weakness(1, f"{above:g} ATRs above the support its stop sits under")
    if kind == "pullback":
        shallow, deep = rubric.ORDERLY_PULLBACK_ATR
        if not shallow <= facts["pullback_atr"] <= deep:
            weakness(1, f"pulled back {facts['pullback_atr']:g} ATRs from its recent high (orderly: {shallow:g}-{deep:g})")
    else:
        crossed = facts["ema_9_crossed_above_ema_21_days_ago"]
        if crossed is not None and crossed >= rubric.FRESH_CROSS_SESSIONS:
            weakness(1, f"its cross is {crossed} sessions old")
        if facts["price"] < facts["ema_21"]:
            weakness(1, "closing below EMA 21")
        if facts["run_from_low_atr"] > rubric.MAX_RUN_FROM_LOW_ATR:
            weakness(1, f"{facts['run_from_low_atr']:g} ATRs above its 10-day low")
    return max(0.0, 30.0 - sum(lost for lost, _ in weaknesses)), [why for _, why in weaknesses]


def lift_checks(catalysts: Dict[str, Any]) -> Dict[str, bool]:
    """The rubric's signs that something can lift the price within 2-8 weeks, from the catalyst facts."""
    sessions, beats = catalysts.get("sessions_to_earnings"), catalysts.get("earnings_beats_last_4")
    soonest, latest = rubric.EARNINGS_LIFT_SESSIONS
    return {
        "earnings_after_beats": (sessions is not None and soonest <= sessions <= latest
                                 and beats is not None and beats >= rubric.EARNINGS_LIFT_BEATS),
        "analyst_upside": (catalysts.get("analyst_target_upside_pct") or 0) >= rubric.ANALYST_LIFT_UPSIDE_PCT,
        "insider_buying": (catalysts.get("insider_mspr_3m") or 0) > 0,
    }


def rubric_checks(kind: str, facts: Dict[str, Any], plan: Optional[Dict[str, Any]],
                  catalysts: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The swing rubric's checks a fact can settle, for the LLM to apply as given (LLM_rec_instr.SWING_SYSTEM_PROMPT)."""
    start, weaknesses = technical_checks(kind, facts, plan)
    return {"technical_start": start, "weaknesses": weaknesses, "turn_signs": turn_signs(facts),
            "lifts": lift_checks(catalysts or {})}


def screen_points(setup: Dict[str, Any]) -> float:
    """
    The part of the swing score the code can check, to rank the candidates: the plan's risk/reward points, the
    technical start, and the middle of the momentum band its signs of a turn allow (0-80).
    """
    plan, checks = setup.get("plan") or {}, setup.get("checks") or {}
    low, high = rubric.TURN_SIGN_BANDS[sum((checks.get("turn_signs") or {}).values())]
    return (plan_risk_reward_score(plan.get("entry_price"), plan.get("stop_loss"), plan.get("price_target"),
                                   (setup.get("facts") or {}).get("atr"))
            + checks.get("technical_start", 0.0) + (low + high) / 2)


def setup_kind(facts: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """("pullback" | "new_uptrend" | None, why not): which setup the stock is in, by SWING_SCREEN["SETUP"]."""
    setup = settings().get("SETUP") or {}
    early = params_dict.get("SWING_EARLY_ENTRY") or {}
    if facts["price"] < setup.get("MIN_PRICE", 10):
        return None, f"price {facts['price']} under {setup.get('MIN_PRICE', 10)}"
    if facts["dollar_volume_20d_m"] < setup.get("MIN_DOLLAR_VOLUME_M", 20):
        return None, f"trades ${facts['dollar_volume_20d_m']}M a day"
    rsi = facts["rsi"]
    if rsi is None or not setup.get("MIN_RSI", 0) <= rsi <= min(setup.get("MAX_RSI", 100), early.get("MAX_RSI", 100)):
        return None, f"RSI {rsi}"
    if facts["run_from_low_atr"] > early.get("MAX_RUN_ATR", float("inf")):
        return None, f"already up {facts['run_from_low_atr']} ATRs from its {early.get('LOOKBACK_DAYS', 10)}-day low"
    if facts["price"] > facts["bollinger_upper"]:
        return None, "above its upper Bollinger Band"
    # Claude-authored (2026-10-22): a stock still falling isn't set up yet
    signs = sum(turn_signs(facts).values())
    if signs < setup.get("MIN_TURN_SIGNS", 0):
        return None, f"{signs} signs of a turn"
    if setup.get("EXCLUDE_FALLING_KNIVES", True) and signs == 0 and facts["rsi_at_10_day_low"]:
        return None, "still falling: no sign of a turn, and RSI at a 10-day low"
    pullback = setup.get("PULLBACK") or {}
    if pullback.get("ENABLED", True):
        trend_ok = facts["pct_vs_sma_200"] >= -pullback.get("MAX_BELOW_SMA200_PCT", 0) or (
            pullback.get("OR_SMA50_ABOVE_SMA200", True) and facts["sma_50_above_sma_200"])
        if (trend_ok and facts["pullback_atr"] >= pullback.get("MIN_PULLBACK_ATR", 1.5)
                and facts["pullback_pct"] <= pullback.get("MAX_PULLBACK_PCT", 100)):
            return "pullback", ""
    uptrend = setup.get("NEW_UPTREND") or {}
    crossed = facts["ema_9_crossed_above_ema_21_days_ago"]
    if uptrend.get("ENABLED", True) and crossed is not None and crossed < uptrend.get("CROSS_WITHIN_DAYS", 5):
        return "new_uptrend", ""
    return None, "no pullback in an intact trend and no fresh turn up"


# ------------------------------------------------------------------------------------------------- 3. plan
def trade_plan(facts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Entry, stop, and target from the chart's levels, the same rules for every stock (SWING_SCREEN["LEVELS"]):
    - entry: the latest close;
    - stop: STOP_BUFFER_ATR below the nearest support under the price (EMA 21, the 50- or 200-day average, the lower
      Bollinger Band, the SUPPORT_LOOKBACK_DAYS low), and at least MIN_RISK_ATR below the entry;
    - target: the nearest level at least MIN_TARGET_ATR above the entry (the recent high close, the
      TARGET_LOOKBACK_DAYS high close, the 50- or 200-day average from below, the upper Bollinger Band), at most
      MAX_TARGET_ATR above it: where the rebound should reach within 2-8 weeks.
    None when there is no such target.
    """
    rules = settings().get("LEVELS") or {}
    atr, entry = facts["atr"], facts["price"]
    support_days = int(rules.get("SUPPORT_LOOKBACK_DAYS", 10))
    supports = {
        "EMA 21": facts["ema_21"], "50-day average": facts["sma_50"], "200-day average": facts["sma_200"],
        "lower Bollinger Band": facts["bollinger_lower"], f"{support_days}-day low": facts[f"low_{support_days}d"],
    }
    below = {name: level for name, level in supports.items() if level is not None and level < entry}
    stop_basis = max(below, key=below.get) if below else None
    stop = (below[stop_basis] if stop_basis else entry) - rules.get("STOP_BUFFER_ATR", 0.5) * atr
    if entry - stop < rules.get("MIN_RISK_ATR", 1.0) * atr:
        stop = entry - rules.get("MIN_RISK_ATR", 1.0) * atr
    targets = {name: level for name, level in facts.items() if name.startswith("high_close_")}
    targets.update({"50-day average": facts["sma_50"], "200-day average": facts["sma_200"],
                    "upper Bollinger Band": facts["bollinger_upper"]})
    floor = entry + rules.get("MIN_TARGET_ATR", 1.5) * atr
    above = {name: level for name, level in targets.items() if level is not None and level >= floor}
    if not above or stop <= 0:
        return None
    target_basis = min(above, key=above.get)
    target = min(above[target_basis], entry + rules.get("MAX_TARGET_ATR", 8.0) * atr)
    names = {name: f"highest close of the last {name[len('high_close_'):-1]} days" if name.startswith("high_close_")
             else name for name in targets}
    return {
        "entry_price": round(entry, 2),
        "stop_loss": round(stop, 2),
        "price_target": round(target, 2),
        "risk_reward": round((target - entry) / (entry - stop), 2),
        "stop_basis": f"{rules.get('STOP_BUFFER_ATR', 0.5)} ATR below the {stop_basis}" if stop_basis else "1 ATR below the entry",
        "target_basis": names[target_basis],
        # Claude-authored (2026-10-22): how far the price is above the support the stop sits under (the rubric's check)
        "above_support_atr": round((entry - below[stop_basis]) / atr, 2) if stop_basis else None,
    }


def info_catalysts(info: Any, price: float, today: date) -> Dict[str, Any]:
    """
    Claude-authored (2026-10-22): the catalyst facts from a stock's Yahoo info (the next earnings, the analysts' mean
    target against the price); add_report_facts fills in the ones from Finnhub. None where Yahoo has nothing.
    """
    info = info if isinstance(info, dict) else {}
    earnings = None
    for field in EARNINGS_DATE_FIELDS:
        stamp = info.get(field)
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
            day = datetime.fromtimestamp(stamp, MARKET_TZ).date()
            if day >= today:
                earnings = day
                break
    target = info.get("targetMeanPrice")
    return {
        "next_earnings_date": earnings.isoformat() if earnings else None,
        # Weekdays from today (0: today or, on a weekend, the next session); US holidays aren't taken out
        "sessions_to_earnings": int(np.busday_count(today, earnings)) if earnings else None,
        "earnings_beats_last_4": None,
        "analyst_target_upside_pct": (round((target / price - 1) * 100, 1)
                                      if isinstance(target, (int, float)) and target > 0 and price > 0 else None),
        "insider_mspr_3m": None,
    }


def add_report_facts(setups: Dict[str, Dict[str, Any]], sanitized_inputs: Dict[str, Dict[str, Any]],
                     today: Optional[date] = None) -> None:
    """
    Claude-authored (2026-10-22): the catalyst facts from each candidate's Finnhub data, fetched after the screen
    (sanitizer.sanitize_ticker_payload): how many of the last 4 reports beat the estimate, insiders' average monthly
    purchase ratio (MSPR, -100 to 100) over this month and the two before, and the next earnings when Yahoo had no
    date. Then the rubric's checks again, with them. Changes `setups` in place.
    """
    today = today or market_today()
    year, month = today.year, today.month - (INSIDER_MONTHS - 1)
    while month < 1:
        year, month = year - 1, month + 12
    since = f"{year}-{month:02d}"
    for symbol, setup in setups.items():
        payload = sanitized_inputs.get(symbol) or {}
        reports = payload.get("catalysts_and_earnings") or {}
        surprises = sorted((s for s in reports.get("past_earnings_surprises") or [] if isinstance(s, dict)
                            and all(isinstance(s.get(k), (int, float)) for k in ("actual", "estimate"))),
                           key=lambda s: str(s.get("quarter")), reverse=True)[:4]
        mspr = [entry["mspr"] for entry in (payload.get("sentiment_and_analysts") or {}).get("insider_sentiment_series") or []
                if isinstance(entry, dict) and isinstance(entry.get("mspr"), (int, float)) and str(entry.get("period") or "") >= since]
        catalysts = setup.setdefault("catalyst_facts", {})
        catalysts["earnings_beats_last_4"] = sum(1 for s in surprises if s["actual"] > s["estimate"]) if surprises else None
        catalysts["insider_mspr_3m"] = round(sum(mspr) / len(mspr), 1) if mspr else None
        if catalysts.get("sessions_to_earnings") is None:
            upcoming = sorted(str(e.get("date")) for e in reports.get("upcoming_earnings") or []
                              if isinstance(e, dict) and str(e.get("date") or "") >= today.isoformat())
            if upcoming:
                catalysts["next_earnings_date"] = upcoming[0]
                catalysts["sessions_to_earnings"] = int(np.busday_count(today, date.fromisoformat(upcoming[0][:10])))
        setup["checks"] = rubric_checks(setup["setup"], setup["facts"], setup.get("plan"), catalysts)


# ------------------------------------------------------------------------------------------------- the screen
def screen(fetcher: Any, tickers: List[str]) -> Tuple[List[str], Dict[str, Dict[str, Any]], Dict[str, int]]:
    """
    The swing candidates among `tickers` (the stage-1 list), best plan first: (tickers, {ticker: {"setup", "facts",
    "plan"}}, {"fundamentals", "setup", "plan", "candidates"} counts). Uses the fetcher's Yahoo info (already fetched
    by the long-term screen) and its daily-bar download.
    """
    config = settings()
    if not config.get("ENABLED", True) or not tickers:
        return [], {}, {}
    rules = config.get("YAHOO_FILTERS") or []
    infos = {symbol: fetcher.fetch_yahoo_data(symbol, fetch_news=False) for symbol in tickers}
    fundamentals = []
    for symbol in tickers:
        passed, why = passes_fundamentals(infos[symbol], rules)
        if passed:
            fundamentals.append(symbol)
        else:
            logger.debug(f"[{symbol}] Swing screen, fundamentals: {why}")
    fundamentals = one_listing_per_company(fundamentals, infos)
    bars = fetcher._daily_bars(fundamentals)
    market = bars.get(local_indicators.MARKET_SYMBOL)
    blackout = (config.get("SETUP") or {}).get("EARNINGS_BLACKOUT_SESSIONS")
    today = market_today()
    setups: Dict[str, Dict[str, Any]] = {}
    for symbol in fundamentals:
        facts = setup_facts(bars.get(symbol), market)
        if facts is None:
            continue
        kind, why = setup_kind(facts)
        catalysts = info_catalysts(infos[symbol], facts["price"], today)
        sessions = catalysts["sessions_to_earnings"]
        if kind is not None and blackout is not None and sessions is not None and sessions <= blackout:
            kind, why = None, f"earnings {catalysts['next_earnings_date']}, {sessions} sessions away"  # 2026-10-22
        if kind is None:
            logger.debug(f"[{symbol}] Swing screen, setup: {why}")
            continue
        plan = trade_plan(facts)
        setups[symbol] = {"setup": kind, "facts": facts, "catalyst_facts": catalysts, "plan": plan,
                          "checks": rubric_checks(kind, facts, plan, catalysts)}
    min_rr = config.get("MIN_PLAN_RR", 1.5)
    max_above = config.get("MAX_ABOVE_SUPPORT_ATR")  # 2026-10-22
    planned = {symbol: s for symbol, s in setups.items() if s["plan"] and s["plan"]["risk_reward"] >= min_rr
               and (max_above is None or s["plan"]["above_support_atr"] is None or s["plan"]["above_support_atr"] <= max_above)}
    # 2026-10-22: best first by the part of the score the code can check (screen_points), then the earliest in its
    # move; until then by the plan's risk/reward points alone
    points = {symbol: screen_points(s) for symbol, s in planned.items()}
    ranked = sorted(planned, key=lambda symbol: (-points[symbol], planned[symbol]["facts"]["run_from_low_atr"]))
    chosen = ranked[: int(config.get("MAX_CANDIDATES", 25))]
    counts = {"fundamentals": len(fundamentals), "setup": len(setups), "plan": len(planned), "candidates": len(chosen)}
    logger.info(f"Swing screen: {counts}; candidates (points the code can check, of 80): "
                f"{', '.join(f'{symbol} {points[symbol]:.0f}' for symbol in chosen)}")
    return chosen, {symbol: planned[symbol] for symbol in chosen}, counts
