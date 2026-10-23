"""Daily P/L at every time a run can happen, yfinance column shapes, and USD conversion."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import src.market.fx_rates as fx_rates
import src.personalized.pull_holdings_data as phd
from src.market.market_session import exchange_calendar_code, latest_regular_session

ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize("when, symbol, session, in_progress", [
    ("2026-10-15T07:30", "AAPL", "2026-10-14", False),     # Pre-market: yesterday's session
    ("2026-10-15T09:30", "AAPL", "2026-10-15", True),      # From the open
    ("2026-10-15T16:00", "AAPL", "2026-10-15", False),     # Closed
    ("2026-10-16T00:30", "AAPL", "2026-10-15", False),     # After midnight
    ("2026-10-17T12:00", "AAPL", "2026-10-16", False),     # Weekend
    ("2026-10-12T10:00", "SHOP.TO", "2026-10-09", False),  # Canadian Thanksgiving: TSX closed ...
    ("2026-10-12T10:00", "AAPL", "2026-10-12", True),      # ... NYSE open
    ("2026-11-26T10:00", "AAPL", "2026-11-25", False),     # US Thanksgiving
    ("2026-11-27T13:30", "AAPL", "2026-11-27", False),     # US half day, closed at 13:00
    # 2026-10-21: each listing's own exchange. A 07:30 ET pre-market digest is mid-session in Europe, after the
    # close in Sydney, and before the open in Bogota
    ("2026-10-15T07:30", "SAP.DE", "2026-10-15", True),
    ("2026-10-15T07:30", "VOD.L", "2026-10-15", True),
    ("2026-10-15T07:30", "BHP.AX", "2026-10-15", False),
    ("2026-10-15T07:30", "ECOPETROL.CL", "2026-10-14", False),
    ("2026-12-28T10:00", "VOD.L", "2026-12-24", False),    # London's Boxing Day (observed); the NYSE trades
])
def test_latest_regular_session(when, symbol, session, in_progress):
    now = datetime.fromisoformat(when).replace(tzinfo=ET)
    assert latest_regular_session(now, exchange_calendar_code(symbol)) == (date.fromisoformat(session), in_progress)


def closes(*pairs):
    return pd.Series([c for _, c in pairs], index=[date.fromisoformat(d) for d, _ in pairs])


TUE, WED, THU = ("2026-10-13", 100.0), ("2026-10-14", 102.0), ("2026-10-15", 105.0)


# 2026-10-21: `live`, whether the session is still trading (daily_pnl_live), for the email's labels
@pytest.mark.parametrize("when, yahoo, broker_price, expected_pnl, expected_session, live", [
    ("2026-10-15T09:31", [TUE, WED], 104.0, 20.0, "2026-10-15", True),                       # No bar for today yet
    ("2026-10-15T12:00", [TUE, WED, ("2026-10-15", 104.0)], 104.0, 20.0, "2026-10-15", True),
    ("2026-10-15T16:10", [TUE, WED, THU], 107.5, 30.0, "2026-10-15", False),                 # After-hours price ignored
    ("2026-10-15T07:30", [TUE, WED], 104.5, 20.0, "2026-10-14", False),                      # Pre-market price ignored
    ("2026-10-15T07:30", [TUE, WED, ("2026-10-15", 104.5)], 104.5, 20.0, "2026-10-14", False),  # Early bar for today
    ("2026-10-15T12:00", [], 104.0, 0.0, "2026-10-15", True),                                # No Yahoo data
])
def test_daily_pnl(monkeypatch, when, yahoo, broker_price, expected_pnl, expected_session, live):
    fixed = datetime.fromisoformat(when).replace(tzinfo=ET)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed

    monkeypatch.setattr(phd, "datetime", FakeDateTime)
    fetcher = phd.PortfolioFetcher.__new__(phd.PortfolioFetcher)
    series = closes(*yahoo)
    monkeypatch.setattr(fetcher, "_fetch_yfinance_daily_closes", lambda symbols: {"AAPL": series} if len(series) else {})
    out = fetcher.calculate_daily_pnl(pd.DataFrame({"symbol": ["AAPL"], "shares": [10.0], "current_price": [broker_price]}))
    assert out["daily_pnl"].iloc[0] == pytest.approx(expected_pnl)
    assert out["daily_pnl_session"].iloc[0] == expected_session
    assert bool(out["daily_pnl_live"].iloc[0]) is live



@pytest.mark.parametrize("when, broker_price, valued_at", [
    ("2026-10-19T07:20", 103.39, 93.67),  # Pre-market Monday: Friday's close, not the pre-market quote
    ("2026-10-16T19:00", 94.31, 93.67),   # After hours: the session's close
    ("2026-10-19T11:00", 101.00, 101.00),  # During the session: the live price
])
def test_holdings_are_valued_at_the_price_the_daily_pnl_uses(monkeypatch, when, broker_price, valued_at):
    # 2026-10-20: a 7:20 ET Monday run valued SOXL at its pre-market quote beside Friday's P/L, so the portfolio's
    # value minus the day's change wasn't the previous close's value
    fixed = datetime.fromisoformat(when).replace(tzinfo=ET)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed

    monkeypatch.setattr(phd, "datetime", FakeDateTime)
    fetcher = phd.PortfolioFetcher.__new__(phd.PortfolioFetcher)
    series = closes(("2026-10-15", 107.35), ("2026-10-16", 93.67))
    monkeypatch.setattr(fetcher, "_fetch_yfinance_daily_closes", lambda symbols: {"SOXL": series})
    out = fetcher.calculate_daily_pnl(pd.DataFrame({"symbol": ["SOXL"], "shares": [8.0], "current_price": [broker_price],
                                                    "total_cost_basis": [860.24]})).iloc[0]
    assert out["valuation_price"] == pytest.approx(valued_at)
    assert out["current_price"] == broker_price  # The broker's quote is kept for the watchdog
    assert out["market_value"] == pytest.approx(8 * valued_at)
    assert out["market_value"] - out["daily_pnl"] == pytest.approx(8 * out["last_close"])  # The figures add up
    assert out["unrealized_pnl"] == pytest.approx(8 * valued_at - 860.24)
    assert out["unrealized_pnl_pct"] == pytest.approx((8 * valued_at - 860.24) / 860.24 * 100)


def test_single_ticker_download_shape_works(monkeypatch):
    """yfinance 1.x returns per-ticker columns even for one ticker; that used to give $0 P/L and NaN indicators."""
    index = pd.DatetimeIndex(pd.bdate_range("2026-06-01", periods=60))
    frame = pd.DataFrame({(field, "AAPL"): pd.Series(range(100, 160), index=index, dtype=float) + offset
                          for field, offset in (("Close", 0.0), ("High", 1.0), ("Low", -1.0))})
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    monkeypatch.setattr(phd.yf, "download", lambda **kwargs: frame)
    fetcher = phd.PortfolioFetcher.__new__(phd.PortfolioFetcher)
    assert len(fetcher._fetch_yfinance_daily_closes(["AAPL"])["AAPL"]) == 60
    indicators = fetcher.add_technical_indicators(pd.DataFrame({"symbol": ["AAPL"]}))
    assert indicators[["rsi", "natr", "annualized_vol"]].notna().all(axis=None)


# --------------------------------------------------------------------------------------------- FX
def test_normalize_currency():
    assert fx_rates.normalize_currency({"code": "cad", "name": "Canadian Dollar"}) == "CAD"
    assert fx_rates.normalize_currency(None, "SHOP.TO") == "CAD"
    # 2026-10-21: without a currency, the listing's suffix decides
    assert [fx_rates.normalize_currency(None, symbol) for symbol in ("SAP.DE", "MC.PA", "VOD.L", "BHP.AX", "ECOPETROL.CL",
                                                                     "BRK.B")] == ["EUR", "EUR", "GBX", "AUD", "COP", "USD"]
    assert fx_rates.normalize_currency("COP", "EC") == "COP"  # What the broker or sheet says wins
    assert fx_rates.normalize_currency("", "AAPL") == "USD"
    assert fx_rates.normalize_currency("GBp") == "GBX"


def test_rates_fall_back_to_the_last_good_rate(monkeypatch):
    monkeypatch.setattr(fx_rates, "_fetch_live", lambda codes: {"CAD": 0.72})
    assert fx_rates.usd_rates(["USD", "CAD"]) == {"USD": 1.0, "CAD": 0.72}
    monkeypatch.setattr(fx_rates, "_fetch_live", lambda codes: {})  # Yahoo down
    assert fx_rates.usd_rates(["CAD"])["CAD"] == 0.72
    assert fx_rates.usd_rates(["EUR"])["EUR"] is None  # Never seen: left unconverted, not treated as USD


def test_convert_to_usd(monkeypatch):
    monkeypatch.setattr(phd, "usd_rates", lambda codes: {"USD": 1.0, "CAD": 0.7})
    fetcher = phd.PortfolioFetcher.__new__(phd.PortfolioFetcher)
    df = pd.DataFrame({"symbol": ["AAPL", "SHOP.TO"], "currency": [{"code": "USD"}, None], "shares": [10.0, 20.0],
                       "current_price": [300.0, 100.0], "avg_cost_basis": [250.0, 80.0], "last_close": [295.0, 101.0],
                       "market_value": [3000.0, 2000.0], "total_cost_basis": [2500.0, 1600.0],
                       "unrealized_pnl": [500.0, 400.0], "daily_pnl": [50.0, -20.0]})
    out = fetcher.convert_to_usd(df).set_index("symbol")
    assert out.loc["SHOP.TO", ["market_value", "total_cost_basis", "unrealized_pnl", "daily_pnl"]].tolist() == pytest.approx([1400.0, 1120.0, 280.0, -14.0])
    assert out.loc["SHOP.TO", "current_price"] == 100.0  # Per-share price stays in CAD ...
    assert out.loc["SHOP.TO", "current_price_usd"] == pytest.approx(70.0)  # ... with a USD copy
    assert out.loc["AAPL", "market_value"] == 3000.0 and out.loc["AAPL", "currency"] == "USD"
