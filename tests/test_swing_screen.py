"""The swing screen (2026-10-20): fundamentals, the two setups, the fixed trade plan, and the candidates it sends on."""
import numpy as np
import pandas as pd
import pytest

from src.universal.recommendations import swing_screen as sw


def frame(closes, volume=2_000_000, spread=1.0):
    """Daily bars, oldest first, with each day spanning `spread` either side of its close."""
    closes = np.array(closes, float)
    index = pd.bdate_range("2025-06-02", periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": closes + spread, "Low": closes - spread, "Close": closes,
                         "Volume": volume}, index=index)


# A year-long climb from 100 to 150, then an orderly dip with a few up days: a pullback in an intact trend
PULLBACK = list(np.linspace(100, 150, 250)) + [150 - x for x in np.cumsum([2, 1.5, -1, 2, -0.8, 1.5, -1, 1, -0.5, 0.6])]
# A choppy decline, then the first days up: the 9-day average crosses above the 21-day today
TURN = list(150 + np.cumsum([(-1.2 if i % 2 == 0 else 0.9) for i in range(250)]))
TURN += list(TURN[-1] + np.cumsum([1.2, -0.4, 1.0, 0.8, -0.2]))
# A steady climb that then jumps 25 in a month: already far along its run
RUNNER = list(np.linspace(100, 110, 240)) + list(np.linspace(110, 135, 20))


def test_the_facts_describe_the_setup_in_normal_days_ranges():
    facts = sw.setup_facts(frame(PULLBACK))
    assert facts["price"] == pytest.approx(144.7) and facts["atr"] == pytest.approx(2.2143, abs=1e-3)
    assert facts["high_close_20d"] == 150 and facts["pullback_atr"] == pytest.approx(5.3 / 2.2143, abs=0.01)
    assert facts["pct_vs_sma_200"] > 0 and facts["sma_50_above_sma_200"] and facts["run_from_low_atr"] < 1
    assert facts["dollar_volume_20d_m"] == pytest.approx(295, abs=2)  # ~147.5 average close x 2M shares
    assert sw.setup_facts(frame(PULLBACK[-150:])) is None  # Under ~1 year of bars: no 200-day average


def test_each_setup_is_recognised_and_a_stretched_stock_is_not():
    assert sw.setup_kind(sw.setup_facts(frame(PULLBACK))) == ("pullback", "")
    turn = sw.setup_facts(frame(TURN))
    assert turn["ema_9_crossed_above_ema_21_days_ago"] == 0 and turn["pct_vs_sma_200"] < -3
    assert sw.setup_kind(turn) == ("new_uptrend", "")  # Below its 200-day average, but a fresh turn up
    kind, why = sw.setup_kind(sw.setup_facts(frame(RUNNER)))
    assert kind is None and why.startswith("RSI")
    kind, why = sw.setup_kind(sw.setup_facts(frame(PULLBACK, volume=50_000)))  # $7M a day
    assert kind is None and why.startswith("trades $")
    kind, why = sw.setup_kind(sw.setup_facts(frame([c / 20 for c in PULLBACK], spread=0.05)))  # Under $10
    assert kind is None and why.startswith("price")


def test_a_setup_can_be_switched_off(monkeypatch):
    from src.config.parameters import params_dict

    screen = {**params_dict["SWING_SCREEN"]}
    screen["SETUP"] = {**screen["SETUP"], "NEW_UPTREND": {"ENABLED": False}}
    monkeypatch.setitem(params_dict, "SWING_SCREEN", screen)
    assert sw.setup_kind(sw.setup_facts(frame(TURN)))[0] is None
    assert sw.setup_kind(sw.setup_facts(frame(PULLBACK)))[0] == "pullback"


FACTS = {"price": 100.0, "atr": 2.0, "ema_21": 98.0, "sma_50": 95.0, "sma_200": 90.0, "bollinger_lower": 96.0,
         "bollinger_upper": 108.0, "low_10d": 97.0, "high_close_20d": 110.0, "high_close_60d": 120.0}


def test_the_plan_uses_the_nearest_support_and_the_nearest_level_overhead():
    plan = sw.trade_plan(FACTS)
    # Stop: half an ATR under the EMA 21 at 98 is 97, which is also the 1.5 ATR minimum; target: the upper band at 108,
    # the nearest level at least 1.5 ATRs up
    assert (plan["entry_price"], plan["stop_loss"], plan["price_target"], plan["risk_reward"]) == (100, 97, 108, 2.67)
    assert plan["stop_basis"] == "0.5 ATR below the EMA 21" and plan["target_basis"] == "upper Bollinger Band"
    # A support right under the price still leaves the stop 1.5 ATRs away, so ordinary noise doesn't hit it
    assert sw.trade_plan({**FACTS, "ema_21": 99.8})["stop_loss"] == 97
    # A level within 1.5 ATRs doesn't count as a target; the next one does
    assert sw.trade_plan({**FACTS, "bollinger_upper": 102.0})["target_basis"] == "highest close of the last 20 days"
    # A target further than 8 ATRs is capped there; with nothing overhead there is no plan
    far = {**FACTS, "bollinger_upper": 150.0, "high_close_20d": 150.0, "high_close_60d": 150.0}
    assert sw.trade_plan(far)["price_target"] == 116
    assert sw.trade_plan({**FACTS, "bollinger_upper": 101.0, "high_close_20d": 101.0, "high_close_60d": 99.0}) is None


class Fetcher:
    def __init__(self, infos, bars):
        self.infos, self.bars, self.asked = infos, bars, []

    def fetch_yahoo_data(self, symbol, fetch_news=False):
        return self.infos.get(symbol, {"error": "missing"})

    def _daily_bars(self, symbols):
        self.asked.append(sorted(symbols))
        return self.bars


GOOD = {"averageVolume": 2_000_000, "numberOfAnalystOpinions": 12, "recommendationMean": 2.0, "forwardPE": 20,
        "profitMargins": 0.15, "revenueGrowth": 0.08, "longName": "Good Co"}


def test_the_screen_counts_each_stage_and_sends_the_best_plans(monkeypatch):
    from src.config.parameters import params_dict

    infos = {"PULL": GOOD, "TURN": {**GOOD, "longName": "Turn Co"}, "RUN": {**GOOD, "longName": "Run Co"},
             "LOSS": {**GOOD, "profitMargins": -0.02, "longName": "Loss Co"},     # Not profitable: fails fundamentals
             "PULL2": {**GOOD, "averageVolume": 1_500_000}}                       # Another listing of Good Co
    bars = {"PULL": frame(PULLBACK), "TURN": frame(TURN), "RUN": frame(RUNNER), "PULL2": frame(PULLBACK)}
    fetcher = Fetcher(infos, bars)
    monkeypatch.setitem(params_dict, "SWING_SCREEN", {**params_dict["SWING_SCREEN"], "MIN_PLAN_RR": 1.5})
    chosen, details, counts = sw.screen(fetcher, ["PULL", "TURN", "RUN", "LOSS", "PULL2", "NODATA"])
    assert counts == {"fundamentals": 3, "setup": 2, "plan": 2, "candidates": 2}
    assert fetcher.asked == [["PULL", "RUN", "TURN"]]  # Daily bars only for the fundamentals survivors
    assert set(chosen) == {"PULL", "TURN"} and details["PULL"]["setup"] == "pullback"
    assert chosen == sorted(chosen, key=lambda t: -sw.screen_points(details[t]))  # 2026-10-22: most of the rubric first
    assert set(details["PULL"]) == {"setup", "facts", "catalyst_facts", "plan", "checks"}
    # A higher reward:risk bar, and a cap on how many go on
    monkeypatch.setitem(params_dict, "SWING_SCREEN", {**params_dict["SWING_SCREEN"], "MIN_PLAN_RR": 1.85})
    assert sw.screen(Fetcher(infos, bars), ["PULL", "TURN"])[2]["plan"] == 0
    monkeypatch.setitem(params_dict, "SWING_SCREEN", {**params_dict["SWING_SCREEN"], "MIN_PLAN_RR": 1.5, "MAX_CANDIDATES": 1})
    assert len(sw.screen(Fetcher(infos, bars), ["PULL", "TURN"])[0]) == 1
    monkeypatch.setitem(params_dict, "SWING_SCREEN", {**params_dict["SWING_SCREEN"], "ENABLED": False})
    assert sw.screen(Fetcher(infos, bars), ["PULL"]) == ([], {}, {})


# --------------------------------------------------------------------------------------------- 2026-10-22: the rubric's checks
SIGNS = {"rsi": 45.0, "rsi_5_days_ago": 40.0, "up_vs_down_volume_10d": 1.3, "days_since_low_10d": 3,
         "run_from_low_atr": 0.8, "price": 100.0, "ema_9": 99.0, "sar_below_price": False}


def test_the_four_signs_of_a_turn_are_checked_against_the_facts():
    assert sw.turn_signs(SIGNS) == dict.fromkeys(["rsi_rising", "volume_on_up_days", "higher_low", "above_ema_9_or_sar"], True)
    # RSI past 55, no down days to compare with, a new low yesterday, and under EMA 9 with the SAR above
    none = {**SIGNS, "rsi": 56.0, "up_vs_down_volume_10d": None, "days_since_low_10d": 1, "ema_9": 101.0}
    assert not any(sw.turn_signs(none).values())
    assert sw.turn_signs({**none, "sar_below_price": True})["above_ema_9_or_sar"]
    assert not sw.turn_signs({**SIGNS, "run_from_low_atr": 0.4})["higher_low"]  # Not yet half an ATR off its low


def test_the_trend_is_one_fact():
    assert sw.trend_state({"pct_vs_sma_200": 3.0, "sma_50_above_sma_200": True}) == "intact"
    assert sw.trend_state({"pct_vs_sma_200": -2.0, "sma_50_above_sma_200": True}) == "mixed"  # Below both averages
    assert sw.trend_state({"pct_vs_sma_200": 3.0, "sma_50_above_sma_200": False}) == "mixed"
    assert sw.trend_state({"pct_vs_sma_200": -2.0, "sma_50_above_sma_200": False}) == "weak"


TECHNICAL = {"trend": "intact", "pct_vs_sma_200": 5.0, "pullback_atr": 3.0, "ema_9_crossed_above_ema_21_days_ago": 2,
             "price": 100.0, "ema_21": 98.0, "run_from_low_atr": 1.0}


def test_the_technical_start_loses_6_points_per_weakness_in_the_facts():
    assert sw.technical_checks("pullback", TECHNICAL, {"above_support_atr": 0.5}) == (30.0, [])
    start, why = sw.technical_checks("pullback", {**TECHNICAL, "trend": "mixed", "pct_vs_sma_200": -2.0, "pullback_atr": 7.0},
                                     {"above_support_atr": 1.5})
    assert start == 12.0 and len(why) == 3
    assert why[0] == "mixed trend: below its 200-day average, though the 50-day is above it (-6)"
    assert why[1] == "1.5 ATRs above the support its stop sits under (-6)"
    # A new uptrend: a weak trend and far from support count twice; an old cross, under EMA 21, and a long run once
    late = {**TECHNICAL, "trend": "weak", "pct_vs_sma_200": -4.0, "ema_9_crossed_above_ema_21_days_ago": 4,
            "price": 97.0, "run_from_low_atr": 2.5}
    start, why = sw.technical_checks("new_uptrend", late, {"above_support_atr": 2.5})
    assert start == 0.0 and len(why) == 5 and why[0].endswith("(-12)") and why[1].endswith("(-12)")
    assert sw.technical_checks("new_uptrend", TECHNICAL, None) == (30.0, [])  # The pullback depth isn't checked


def test_the_catalyst_facts_come_from_yahoo_then_finnhub():
    from datetime import date, datetime

    today = date(2026, 10, 22)  # A Thursday

    def stamp(day):
        return int(datetime(day.year, day.month, day.day, 12, tzinfo=sw.MARKET_TZ).timestamp())

    # Yahoo's first date still ahead, in trading days: Oct 22, 23, 26-30 before Monday Nov 2
    info = {"earningsTimestampStart": stamp(date(2026, 10, 19)), "earningsTimestamp": stamp(date(2026, 11, 2)),
            "targetMeanPrice": 115.0}
    catalysts = sw.info_catalysts(info, 100.0, today)
    assert catalysts == {"next_earnings_date": "2026-11-02", "sessions_to_earnings": 7, "earnings_beats_last_4": None,
                         "analyst_target_upside_pct": 15.0, "insider_mspr_3m": None}
    assert sw.lift_checks(catalysts) == {"earnings_after_beats": False, "analyst_upside": True, "insider_buying": False}
    facts = sw.setup_facts(frame(PULLBACK))
    setups = {symbol: {"setup": "pullback", "facts": facts, "plan": sw.trade_plan(facts),
                       "catalyst_facts": sw.info_catalysts(info if symbol == "AAA" else {}, 100.0, today)}
              for symbol in ("AAA", "BBB")}
    surprises = [{"quarter": q, "actual": a, "estimate": 1.0} for q, a in
                 (("2025 Q2", 0.5), ("2026 Q2", 1.1), ("2026 Q1", 0.9), ("2025 Q4", 1.2), ("2025 Q3", 1.3))]
    sanitized = {
        "AAA": {"catalysts_and_earnings": {"past_earnings_surprises": surprises},
                "sentiment_and_analysts": {"insider_sentiment_series": [
                    {"period": "2026-10", "mspr": 20}, {"period": "2026-08", "mspr": -10}, {"period": "2026-07", "mspr": -100}]}},
        "BBB": {"catalysts_and_earnings": {"upcoming_earnings": [{"date": "2026-10-01"}, {"date": "2026-10-27"}]}},
    }
    sw.add_report_facts(setups, sanitized, today)
    # The newest 4 reports (3 beats), and August-October's insiders
    assert setups["AAA"]["catalyst_facts"]["earnings_beats_last_4"] == 3
    assert setups["AAA"]["catalyst_facts"]["insider_mspr_3m"] == 5.0
    assert setups["AAA"]["checks"]["lifts"] == {"earnings_after_beats": True, "analyst_upside": True, "insider_buying": True}
    # Finnhub's next date when Yahoo has none; nothing known stays None
    assert setups["BBB"]["catalyst_facts"] == {"next_earnings_date": "2026-10-27", "sessions_to_earnings": 3,
                                               "earnings_beats_last_4": None, "analyst_target_upside_pct": None,
                                               "insider_mspr_3m": None}


# A year-long climb, then ten straight down days: no sign of a turn yet, with RSI at its 10-day low
FALLING = list(np.linspace(100, 150, 250)) + [150 - 0.5 * day for day in range(1, 11)]


def test_the_screen_leaves_out_falling_knives_reports_due_and_stretched_plans(monkeypatch):
    from datetime import datetime

    from src.config.parameters import params_dict

    facts = sw.setup_facts(frame(FALLING))
    assert facts["rsi_at_10_day_low"] and not any(sw.turn_signs(facts).values())
    assert sw.setup_kind(facts) == (None, "still falling: no sign of a turn, and RSI at a 10-day low")
    screen = params_dict["SWING_SCREEN"]
    monkeypatch.setitem(params_dict, "SWING_SCREEN", {**screen, "SETUP": {**screen["SETUP"], "EXCLUDE_FALLING_KNIVES": False}})
    assert sw.setup_kind(facts)[0] == "pullback"
    monkeypatch.setitem(params_dict, "SWING_SCREEN", {**screen, "SETUP": {**screen["SETUP"], "MIN_TURN_SIGNS": 4}})
    assert sw.setup_kind(sw.setup_facts(frame(PULLBACK)))[1].endswith("signs of a turn")
    monkeypatch.setitem(params_dict, "SWING_SCREEN", screen)

    today = sw.market_today()
    due = int(datetime(today.year, today.month, today.day, 12, tzinfo=sw.MARKET_TZ).timestamp())
    infos = {"PULL": GOOD, "DUE": {**GOOD, "longName": "Due Co", "earningsTimestampStart": due}}
    bars = {"PULL": frame(PULLBACK), "DUE": frame(PULLBACK)}
    chosen, details, counts = sw.screen(Fetcher(infos, bars), ["PULL", "DUE"])
    assert chosen == ["PULL"] and counts["setup"] == 1  # DUE reports today
    assert details["PULL"]["plan"]["above_support_atr"] is not None
    monkeypatch.setitem(params_dict, "SWING_SCREEN", {**screen, "MAX_ABOVE_SUPPORT_ATR": 0.0})
    assert sw.screen(Fetcher(infos, bars), ["PULL"])[2]["plan"] == 0


def test_the_rubric_states_the_numbers_the_checks_use():
    from src.universal.recommendations import LLM_rec_instr as rubric

    prompt = rubric.SWING_SYSTEM_PROMPT
    assert "30, less 6 for each weakness" in prompt and "up to 5 for how the chart looks" in prompt
    assert "four 19.0 - 20.0, three 16.0 - 18.0, two 11.0 - 15.0, one 6.0 - 10.0, none 0.0 - 5.0" in prompt
    assert "earnings 6-40 trading days away" in prompt and "at least 15% above the price" in prompt
    # Every band tops out at its sub-score's maximum: 30, 20, and 8 + 8 + 4
    assert rubric.TURN_SIGN_BANDS[4][1] == 20 and sorted(rubric.TURN_SIGN_BANDS) == [0, 1, 2, 3, 4]
    assert 4 * rubric.LIFT_POINTS == 8
    assert "below both the 50- and 200-day averages" not in prompt  # The old bands' contradiction


def test_the_swing_settings_are_well_formed():
    from src.config.parameters import params_dict
    from src.universal.recommendations.yahoo_filter_helper import OPERATORS

    screen = params_dict["SWING_SCREEN"]
    for field, op, threshold in screen["YAHOO_FILTERS"]:
        assert isinstance(field, str) and op in OPERATORS and isinstance(threshold, (int, float))
    # Looser than the long-term screen, but still a profitable, covered, liquid business
    long_term = {(field, op): value for field, op, value in params_dict["YAHOO_FILTERS"]}
    swing = {(field, op): value for field, op, value in screen["YAHOO_FILTERS"]}
    assert swing[("profitMargins", ">=")] > 0 and swing[("forwardPE", ">")] == 0
    assert swing[("numberOfAnalystOpinions", ">=")] <= long_term[("numberOfAnalystOpinions", ">=")]
    assert screen["SETUP"]["MIN_PRICE"] >= 5 and screen["MIN_PLAN_RR"] >= 1.5 and screen["MAX_CANDIDATES"] > 0
