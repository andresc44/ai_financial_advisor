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
    from src.parameters import params_dict

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
    from src.parameters import params_dict

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
    assert chosen == sorted(chosen, key=lambda t: -details[t]["plan"]["risk_reward"])  # Best plan first
    # A higher reward:risk bar, and a cap on how many go on
    monkeypatch.setitem(params_dict, "SWING_SCREEN", {**params_dict["SWING_SCREEN"], "MIN_PLAN_RR": 1.85})
    assert sw.screen(Fetcher(infos, bars), ["PULL", "TURN"])[2]["plan"] == 0
    monkeypatch.setitem(params_dict, "SWING_SCREEN", {**params_dict["SWING_SCREEN"], "MIN_PLAN_RR": 1.5, "MAX_CANDIDATES": 1})
    assert len(sw.screen(Fetcher(infos, bars), ["PULL", "TURN"])[0]) == 1
    monkeypatch.setitem(params_dict, "SWING_SCREEN", {**params_dict["SWING_SCREEN"], "ENABLED": False})
    assert sw.screen(Fetcher(infos, bars), ["PULL"]) == ([], {}, {})


def test_the_swing_settings_are_well_formed():
    from src.parameters import params_dict
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
