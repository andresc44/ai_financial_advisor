"""Technical indicators computed from Yahoo prices: they must match Twelve Data's, and replace its API calls."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import src.universal.recommendations.filter_tickers as filter_tickers
from src.universal.recommendations import local_indicators as li
from src.universal.recommendations.data_source_guard import DataSourceIssues

FIXTURE = Path(__file__).parent / "fixtures" / "aapl_indicators_2026-10-16.json"


def fixture_bars():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    bars = pd.DataFrame({c: data["bars"][c] for c in ("Open", "High", "Low", "Close")},
                        index=pd.to_datetime(data["bars"]["index"]))
    return bars, data["twelvedata"]


@pytest.mark.parametrize("key", ["rsi_14", "ema_9", "ema_21", "sma_50", "bbands_20", "sar", "adx_14", "atr_14"])
def test_matches_twelve_data_on_real_prices(key):
    """Saved 2026-10-16: unadjusted Yahoo daily bars for AAPL, and Twelve Data's values for the same 20 days."""
    bars, expected = fixture_bars()
    name = key.split("_")[0]
    mine = {row["datetime"]: row for row in li.compute(name, "1day", {**expected[key]["params"], "outputsize": 20}, bars)}
    for row in expected[key]["values"]:
        for field, value in row.items():
            if field != "datetime":
                assert mine[row["datetime"]][field] == pytest.approx(float(value), rel=1e-5), (key, row["datetime"], field)


def test_output_is_newest_first_and_limited_to_outputsize():
    bars, _ = fixture_bars()
    values = li.compute("rsi", "1day", {"time_period": 14, "outputsize": 5}, bars)
    assert len(values) == 5 and values[0]["datetime"] == bars.index[-1].strftime("%Y-%m-%d")
    assert [v["datetime"] for v in values] == sorted((v["datetime"] for v in values), reverse=True)
    assert li.compute("rsi", "1day", {"time_period": 14}, bars.iloc[:10]) == []  # Not enough history


def synthetic_bars(returns, start="2025-01-06"):
    close = 100 * np.cumprod(1 + np.asarray(returns))
    index = pd.bdate_range(start, periods=len(close))
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 1e6}, index=index)


def test_weekly_beta_against_the_market():
    rng = np.random.default_rng(0)
    market_returns = rng.normal(0, 0.01, 600)
    market = synthetic_bars(market_returns)
    stock = synthetic_bars(2 * market_returns)  # Moves exactly twice as much as the market
    values = li.compute("beta", "1week", {"time_period": 52, "outputsize": 3}, stock, market)
    assert len(values) == 3 and all(v["beta"] == pytest.approx(2.0, rel=0.02) for v in values)
    assert all(pd.Timestamp(v["datetime"]).dayofweek == 0 for v in values)  # Weeks are dated by their Monday
    assert li.compute("beta", "1week", {"time_period": 52}, stock, None) == []  # No market prices, no beta


@pytest.mark.parametrize("indicator, interval, params, expected", [
    ("rsi", "1day", {"time_period": 14, "outputsize": 20}, True),
    ("beta", "1week", {"time_period": 52}, True),
    ("bbands", "1day", {"time_period": 20, "ma_type": "EMA"}, False),  # Option not implemented
    ("ema", "1h", {"time_period": 9}, False),                          # Intraday
    ("macd", "1day", {}, False),                                       # Indicator not implemented
])
def test_what_is_computed_locally(indicator, interval, params, expected):
    assert li.supports(indicator, interval, params) is expected


# --------------------------------------------------------------------------------------------- in the data fetcher
@pytest.fixture
def fetcher(monkeypatch):
    rng = np.random.default_rng(1)
    downloads, api_calls = [], []

    def download(symbols):
        downloads.append(sorted(symbols))
        return {s: synthetic_bars(rng.normal(0.0005, 0.01, 400)) for s in symbols if s != "NOPE"}

    def get(url, params, timeout):
        api_calls.append(url.rsplit("/", 1)[1])
        values = [{"datetime": "2026-10-15", "macd": "1.5", "macd_signal": "1.2", "macd_hist": "0.3"}]
        return SimpleNamespace(json=lambda: {s: {"values": values} for s in params["symbol"].split(",")})

    monkeypatch.setattr(li, "download_daily_bars", download)
    fetcher = object.__new__(filter_tickers.DataFetcher)
    fetcher.data_source_issues = DataSourceIssues()
    fetcher._twelvedata_state = {}
    fetcher.twelvedata_api_key = "td-key"
    fetcher.session = SimpleNamespace(get=get)
    return SimpleNamespace(fetcher=fetcher, downloads=downloads, api_calls=api_calls)


def test_supported_indicators_skip_twelve_data(fetcher):
    indicators = [("rsi", {"time_period": 14}), ("ema", {"time_period": 9}), ("macd", {})]
    result = fetcher.fetcher.fetch_twelvedata_batch_data(["AAA", "BBB", "NOPE"], indicators)
    later = fetcher.fetcher.fetch_twelvedata_batch_data(["AAA"], [("sar", {})])  # Bars already downloaded
    assert fetcher.downloads == [["AAA", "BBB", "NOPE", "SPY"]]  # One Yahoo download for the whole run
    assert fetcher.api_calls == ["macd"]  # Only the indicator not implemented locally
    assert set(result["AAA"]) == {"rsi_14", "ema_9", "macd"} and result["AAA"]["rsi_14"]["values"]
    assert later["AAA"]["sar"]["values"][0]["sar"] > 0
    assert set(result["NOPE"]) == {"macd"}  # No Yahoo prices: no local indicator, rather than a wrong one


def test_twelve_data_source_keeps_the_old_behaviour(fetcher, monkeypatch):
    monkeypatch.setitem(filter_tickers.params_dict, "TECHNICAL_INDICATOR_SOURCE", "twelvedata")
    fetcher.fetcher.fetch_twelvedata_batch_data(["AAA"], [("rsi", {"time_period": 14})])
    assert fetcher.api_calls == ["rsi"] and fetcher.downloads == []


def test_failed_yahoo_download_is_an_issue(fetcher, monkeypatch):
    monkeypatch.setattr(li, "download_daily_bars", lambda symbols: (_ for _ in ()).throw(RuntimeError("429")))
    assert fetcher.fetcher.fetch_twelvedata_batch_data(["AAA"], [("rsi", {"time_period": 14})]) == {}
    assert [(i["source"], i["reason"]) for i in fetcher.fetcher.data_source_issues.items] == [("Yahoo prices", "failed")]
