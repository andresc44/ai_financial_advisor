# Claude-authored (2026-10-22): the holdings rubric's facts, worked out in code (src/personalized/holding_checks.py)
from datetime import date, timedelta

import pandas as pd

from src.personalized import LLM_hold_instr as rubric
from src.personalized.holding_checks import holding_checks, is_fund, is_leveraged
from src.personalized.recommendation import build_holding_payloads

TODAY = date(2026, 10, 22)  # A Thursday


def context(closes, volumes=None, rsi=None, ema9=None, ema21=None, sar=None, upper=None, sma50=100.0, sma200=90.0,
            earnings=None, profile=None):
    """A market_context with daily bars for `closes` (oldest first; each day spans 1 either side of its close)."""
    n = len(closes)
    days = [TODAY - timedelta(days=n - i) for i in range(n)]
    bars = [{"date": d.isoformat(), "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": (volumes or [1e6] * n)[i]}
            for i, (d, c) in enumerate(zip(days, closes))][::-1]  # Newest first, like the real data

    def series(values, field):
        return [{"date": d.isoformat(), field: v} for d, v in zip(days[::-1], values)] if values else []

    return {
        "profile": profile or {"name": "Example Corp", "sector": "Industrials"},
        "price_action": {"50_day_average": sma50, "200_day_average": sma200},
        "technical_indicators": {"rsi_14_1day": series(rsi, "rsi"), "ema_9_1day": series(ema9, "ema"),
                                 "ema_21_1day": series(ema21, "ema"), "sar_1day": series(sar, "sar"),
                                 "bbands_20_1day": series(upper, "upper_band")},
        "daily_bars": bars,
        "catalysts_and_earnings": {"upcoming_earnings": earnings or []},
    }


# A climb to 120, a slide to 108, then two up days: a pullback where the selling may be ending
DIP = [100 + i for i in range(21)] + [118, 116, 114, 112, 110, 108, 109, 110]


def test_a_pullback_with_signs_of_a_turn():
    checks = holding_checks({"positions": [{"asset_kind": "stock"}], "market_context": context(
        DIP, rsi=[48, 45, 42, 40, 41, 44], ema9=[109.5], ema21=[112], sar=[106], upper=[125],
        earnings=[{"date": "2026-10-28"}, {"date": "2027-01-27"}])}, today=TODAY)
    assert checks["trend"] == "intact" and checks["pullback"] and not checks["recent_run"]
    assert checks["pullback_from_20d_high_atr"] >= rubric.PULLBACK_ATR
    # RSI up on 5 sessions ago (48 > 44) and under 55; no new low for 2 sessions; a close above EMA 9 / SAR below
    assert checks["turn_signs"]["rsi_rising"] and checks["turn_signs"]["higher_low"]
    assert checks["turn_signs"]["above_ema_9_or_sar"] and checks["turn_sign_count"] >= 3
    assert checks["below_ema_21"] and not checks["sar_above_price"] and not checks["stretched"]
    assert checks["next_earnings_date"] == "2026-10-28" and checks["sessions_to_earnings"] == 4  # Thu, Fri, Mon, Tue
    assert checks["fund"] is False and checks["leveraged_fund"] is False


def test_a_stretched_run_and_a_falling_stock():
    run = holding_checks({"positions": [{}], "market_context": context(
        [100 + 2 * i for i in range(21)], rsi=[68, 72, 74, 73, 70], upper=[135], sma50=120, sma200=100)}, today=TODAY)
    assert run["recent_run"] and run["stretched"]  # Up 40 from its 20-day low, closing above its upper band (135)
    assert run["rsi_falling_from_high"]  # 74 down to 68 within 5 sessions
    falling = holding_checks({"positions": [{}], "market_context": context(
        [120 - i for i in range(21)], rsi=[25, 27, 29, 31, 33, 35], ema9=[103], ema21=[108], sar=[106],
        sma50=95, sma200=110)}, today=TODAY)
    assert falling["trend"] == "weak" and falling["new_low_in_last_2_sessions"]
    assert falling["turn_sign_count"] == 0 and falling["below_ema_21"] and falling["sar_above_price"]


def test_funds_and_leveraged_etfs_are_recognised():
    soxl = {"positions": [{"asset_kind": "etf"}], "market_context": {"profile": {
        "name": "Direxion Daily Semiconductor Bull 3X Shares", "sector": None}}}
    xeqt = {"positions": [{"asset_kind": "EQUITY"}], "market_context": {"profile": {  # The sheet calls everything EQUITY
        "name": "iShares Core Equity ETF Portfolio", "sector": None}}}
    stock = {"positions": [{"asset_kind": "EQUITY"}], "market_context": {"profile": {"name": "Shell plc", "sector": "Energy"}}}
    assert is_fund(soxl) and is_leveraged(soxl) and is_fund(xeqt) and not is_leveraged(xeqt) and not is_fund(stock)
    checks = holding_checks(soxl, today=TODAY)  # No price data: the rest is unknown, not guessed
    assert checks == {"fund": True, "leveraged_fund": True, "next_earnings_date": None, "sessions_to_earnings": None}


def test_each_holding_payload_carries_its_checks():
    df = pd.DataFrame([{"symbol": "ABC", "market_value": 1000.0, "shares": 10, "market_context": context(DIP)}])
    payload = build_holding_payloads(df)["ABC"]
    assert payload["checks"]["pullback"] and payload["checks"]["trend"] == "intact"


def test_the_rubric_states_the_numbers_the_checks_use():
    prompt = rubric.SYSTEM_PROMPT
    assert "Go through the labels in this order" in prompt and "first one that applies" in prompt
    assert f"down at least {rubric.PULLBACK_ATR:g} ATRs" in prompt and f"at least {rubric.RECENT_RUN_PCT:g}%" in prompt
    assert "LEVERAGED ETFs" in prompt and "judge them on their price trend alone" in prompt
    # Andres (2026-10-22): an upcoming report is a lesser warning, so it needs another one to make a holding 'risky'
    lesser = prompt.split("   - Lesser:")[1].split("\n")[0]
    assert "an earnings report within ~5 trading days" in lesser
    assert "Not 'risky' on its own: an upcoming earnings report" in prompt
