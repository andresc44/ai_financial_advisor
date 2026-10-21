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
"""
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src.universal.recommendations import local_indicators
from src.universal.recommendations.yahoo_filter_helper import OPERATORS, one_listing_per_company

logger = logging.getLogger("Finnbot.SwingScreen")

ATR_DAYS = 14  # A normal day's range, as LLM_recommendation.daily_atr measures it (simple average of true ranges)
# LLM_recommendation.plan_risk_reward_score gives full marks from 4.5:1, so plans beyond it rank as equals
FULL_MARKS_RR = 4.5


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


def setup_facts(bars: Optional[pd.DataFrame]) -> Optional[Dict[str, Any]]:
    """
    What the daily bars say about a swing setup, in plain numbers, or None without ~1 year of bars. Prices are in the
    stock's currency; distances are in ATRs (a normal day's range). Also given to the LLM with the stock, so it judges
    the same figures the screen did.
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
    low_10 = float(low[-support_days:].min())
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
    return {
        "price": round(price, 2),
        "atr": round(atr, 4),
        "atr_pct": round(atr / price * 100, 2),
        "rsi": round(_last(rsi), 1) if _last(rsi) is not None else None,
        "rsi_5_days_ago": round(_last(rsi, 5), 1) if _last(rsi, 5) is not None else None,
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
        f"high_close_{target_days}d": round(float(close[-target_days:].max()), 2),
        "ema_9_crossed_above_ema_21_days_ago": crossed,
        "up_vs_down_volume_10d": round(up_volume / down_volume, 2) if down_volume > 0 else None,
        "dollar_volume_20d_m": round(float((close[-20:] * volume[-20:]).mean()) / 1e6, 1),
    }


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
    }


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
    setups: Dict[str, Dict[str, Any]] = {}
    for symbol in fundamentals:
        facts = setup_facts(bars.get(symbol))
        if facts is None:
            continue
        kind, why = setup_kind(facts)
        if kind is None:
            logger.debug(f"[{symbol}] Swing screen, setup: {why}")
            continue
        setups[symbol] = {"setup": kind, "facts": facts, "plan": trade_plan(facts)}
    min_rr = config.get("MIN_PLAN_RR", 1.5)
    planned = {symbol: s for symbol, s in setups.items() if s["plan"] and s["plan"]["risk_reward"] >= min_rr}
    # Best plan first, by what its risk/reward is worth in the score (no more past FULL_MARKS_RR); among equals the
    # earliest in its move (2026-10-20: ranking by the raw ratio favoured new uptrends, whose targets are further)
    ranked = sorted(planned, key=lambda symbol: (-min(planned[symbol]["plan"]["risk_reward"], FULL_MARKS_RR),
                                                 planned[symbol]["facts"]["run_from_low_atr"]))
    chosen = ranked[: int(config.get("MAX_CANDIDATES", 25))]
    counts = {"fundamentals": len(fundamentals), "setup": len(setups), "plan": len(planned), "candidates": len(chosen)}
    logger.info(f"Swing screen: {counts}; candidates: {chosen}")
    return chosen, {symbol: planned[symbol] for symbol in chosen}, counts
