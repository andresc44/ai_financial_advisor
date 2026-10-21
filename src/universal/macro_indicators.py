# File to fetch and consolidate various macroeconomic indicators from multiple sources (Yahoo Finance, FRED, CNN Fear & Greed Index, S&P 500 Breadth) into a single structured dictionary for analysis and reporting.
"""
Claude-authored changes (2026-10-16, Claude Opus 5.5 via Claude Code, at Andres's request), when the dashboard was
enabled in the digest:
- get_market_dashboard() wraps the fetch so it never raises: a failure gives an empty dashboard plus an issue,
  instead of stopping the universal content (and so every user's digest).
- S&P 500 breadth downloaded 60 calendar days (~41 trading days), too few for a 50-day average, so it was always 0%.
- Every request has a timeout, so one slow site can't hang the digest.
- A failed FRED call no longer puts its error text in the payload: that text includes the URL with the API key.

2026-10-21, for Andres's "Global Macro Pulse" table (the email's signals are in create_html._signals):
- A year of Yahoo history instead of two months, for the S&P 500's 50- and 200-day averages ("50D MA", "200D MA").
- The 10Y yield's moves in basis points ("1D bps", "5D bps", "1M bps"), the 10Y-2Y spread's 5-day move,
  Fear & Greed's previous close, and breadth's 1-day, 5-day, and 1-month changes in points.
- Cboe's total put/call ratio for the latest published session and its 5-session average (put_call_ratio).
"""
import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.market_session import _get_calendar, market_date

# Load environment variables from .env file
load_dotenv()

# Claude-authored (2026-10-21)
SP500, TREASURY_10Y = "S&P 500 (SPY)", "10Y Treasury Yield ($TNX)"
CBOE_DAILY_URL = "https://cdn.cboe.com/data/us/options/market_statistics/daily/{day}_daily_options"
PUT_CALL_SESSIONS = 5  # The average's length; Cboe posts each session's file after the close
CURVE_RECENT_DAYS = 182  # How far back an inverted 10Y-2Y spread counts as recent


def _change_at(series: pd.Series, sessions_back: int) -> Optional[float]:
    """Claude-authored (2026-10-21): latest minus the value `sessions_back` sessions earlier, or None."""
    return float(series.iloc[-1] - series.iloc[-1 - sessions_back]) if len(series) > sessions_back else None


def fetch_put_call_ratio(now: Optional[datetime] = None, headers: Optional[dict] = None) -> dict:
    """
    Claude-authored (2026-10-21): Cboe's total put/call ratio (all options: puts traded / calls traded) for the
    latest NYSE session it has published, with the session's date and the average of the last PUT_CALL_SESSIONS
    sessions. Cboe posts one JSON file per session after the close (a missing one is a 403), so this walks back
    over recent sessions. {"Error": ...} when none can be read.
    """
    calendar = _get_calendar("XNYS")
    today = pd.Timestamp(market_date(now or datetime.now(timezone.utc)))
    sessions = calendar.sessions_in_range(today - pd.Timedelta(days=14), today)[::-1]
    ratios: list[tuple[str, float]] = []
    for session in sessions[:PUT_CALL_SESSIONS + 2]:
        day = session.date().isoformat()
        try:
            response = requests.get(CBOE_DAILY_URL.format(day=day), headers=headers, timeout=10)
            if not response.ok:
                continue
            value = next((row.get("value") for row in response.json().get("ratios", [])
                          if row.get("name") == "TOTAL PUT/CALL RATIO"), None)
            if value is not None:
                ratios.append((day, float(value)))
        except (requests.RequestException, ValueError, AttributeError):
            continue
        if len(ratios) == PUT_CALL_SESSIONS:
            break
    if not ratios:
        return {"Error": "No Cboe put/call ratio for the last sessions"}
    result = {"Ratio": ratios[0][1], "Session": ratios[0][0]}
    if len(ratios) == PUT_CALL_SESSIONS:
        result["5D Avg"] = round(sum(value for _, value in ratios) / PUT_CALL_SESSIONS, 3)
    return result


def get_market_dashboard_data() -> dict:
    """Fetch all market dashboard indicators and return them in a single structured dictionary."""
    fred_api_key = os.getenv("FRED_API_KEY")

    # 1. Fetch Yahoo Finance Metrics
    yf_tickers = {
        "S&P 500 (SPY)": "SPY",
        "Nasdaq-100 (QQQ)": "QQQ",
        "Dow Jones (DIA)": "DIA",
        "NYSE Composite ($NYA)": "^NYA",
        "Russell 2000 (IWM)": "IWM",
        "VIX Index": "^VIX",
        "US Dollar Index (DXY)": "DX-Y.NYB",
        "10Y Treasury Yield ($TNX)": "^TNX",
        "Crude Oil (WTI)": "CL=F",
    }

    tickers_list = list(yf_tickers.values())
    try:
        df_yf = yf.download(  # Claude-authored (2026-10-21): a year, for the 200-day average (was 2 months)
            tickers_list, period="1y", interval="1d", progress=False
        )["Close"]
    except Exception as e:  # Claude-authored (2026-10-16): the other sections can still be shown
        print(f"[WARNING] Market dashboard: Yahoo download failed: {e}")
        df_yf = pd.DataFrame()

    yf_metrics = {}
    for name, ticker in yf_tickers.items():
        if ticker in df_yf.columns:
            series = df_yf[ticker].dropna()
            if len(series) > 1:
                latest = float(series.iloc[-1])
                d1_change = ((latest - series.iloc[-2]) / series.iloc[-2]) * 100
                d5_change = (
                    ((latest - series.iloc[-6]) / series.iloc[-6]) * 100
                    if len(series) > 5
                    else 0
                )
                m1_change = (
                    ((latest - series.iloc[-22]) / series.iloc[-22]) * 100
                    if len(series) > 21
                    else 0
                )

                yf_metrics[name] = {
                    "Latest": round(latest, 2),
                    "1D %": f"{d1_change:+.2f}%",
                    "5D %": f"{d5_change:+.2f}%",
                    "1M %": f"{m1_change:+.2f}%",
                }
                # Claude-authored (2026-10-21): a yield moves in basis points (Yahoo quotes ^TNX in percent), and
                # the S&P 500 is read against its 50- and 200-day averages
                if name == TREASURY_10Y:
                    for label, back in (("1D bps", 1), ("5D bps", 5), ("1M bps", 21)):
                        move = _change_at(series, back)
                        if move is not None:
                            yf_metrics[name][label] = round(move * 100, 1)
                if name == SP500:
                    for label, days in (("50D MA", 50), ("200D MA", 200)):
                        if len(series) >= days:
                            yf_metrics[name][label] = round(float(series.tail(days).mean()), 2)

    # 2. Fetch FRED Yield Curve
    if not fred_api_key:
        fred_metrics = {"Error": "FRED_API_KEY missing from .env"}
    else:
        url_fred = "https://api.stlouisfed.org/fred/series/observations"
        # Claude-authored (2026-10-21): about 6 months of history (was 10 days), to see a recent un-inversion
        fred_params = {"series_id": "T10Y2Y", "api_key": fred_api_key, "file_type": "json", "sort_order": "desc", "limit": 140}
        try:
            res_fred = requests.get(url_fred, params=fred_params, timeout=15).json()
            observations = [
                obs
                for obs in res_fred.get("observations", [])
                if obs["value"] != "."
            ]

            if len(observations) >= 2:
                latest_f = float(observations[0]["value"])
                prev_f = float(observations[1]["value"])
                bps_change = round((latest_f - prev_f) * 100, 2)
                fred_metrics = {
                    "Spread": f"{latest_f}%",
                    "1D Change": f"{bps_change:+.1f} bps",
                }
                if len(observations) > 5:  # Claude-authored (2026-10-21): the 5-day move
                    fred_metrics["5D Change"] = f"{(latest_f - float(observations[5]['value'])) * 100:+.1f} bps"
                # Claude-authored (2026-10-21): the last day the curve was inverted within CURVE_RECENT_DAYS, if any.
                # A curve that turns positive after inverting has historically come closer to a recession than the
                # inversion itself.
                cutoff = (pd.Timestamp(observations[0]["date"])
                          - pd.Timedelta(days=CURVE_RECENT_DAYS)).date().isoformat()
                fred_metrics["Last inverted"] = next((obs["date"] for obs in observations
                                                      if obs["date"] >= cutoff and float(obs["value"]) < 0), None)
            else:
                fred_metrics = {"Error": "Insufficient Data"}
        except Exception as e:  # Claude-authored (2026-10-16): the type only; the message would include the key
            fred_metrics = {"Error": f"FRED request failed ({type(e).__name__})"}

    # 3. Fetch Fear & Greed Index
    fg_url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        res_fg = requests.get(fg_url, headers=headers, timeout=15).json()
        fg_metrics = {
            "Score": round(res_fg["fear_and_greed"]["score"], 1),
            "Rating": res_fg["fear_and_greed"]["rating"].capitalize(),
        }
        if res_fg["fear_and_greed"].get("previous_close") is not None:  # Claude-authored (2026-10-21)
            fg_metrics["Previous close"] = round(res_fg["fear_and_greed"]["previous_close"], 1)
    except Exception as e:
        fg_metrics = {"Error": f"Scraping Failed ({e})"}

    # 4. Fetch S&P 500 Breadth
    try:
        resp_wiki = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers=headers,
            timeout=15,
        )
        soup = BeautifulSoup(resp_wiki.text, "html.parser")
        table = soup.find("table", {"id": "constituents"})
        df_sp500 = pd.read_html(io.StringIO(str(table)))[0]

        sp500_tickers = (
            df_sp500["Symbol"].str.replace(".", "-", regex=False).tolist()
        )

        # Claude-authored (2026-10-16): 6 months, not 60 days: 60 calendar days is ~41 sessions, too few for a
        # 50-day average, so every stock compared against NaN and breadth was always 0%
        data_breadth = yf.download(
            sp500_tickers, period="6mo", interval="1d", progress=False
        )["Close"]
        ma50 = data_breadth.rolling(window=50).mean()

        # Claude-authored (2026-10-21): the share on every session, for its 1-day, 5-day, and 1-month changes. Each
        # day counts only the stocks with a price and a 50-day average that day (2026-10-16: e.g. recent listings).
        comparable = data_breadth.notna() & ma50.notna()
        counted = comparable.sum(axis=1)
        shares = ((data_breadth > ma50) & comparable).sum(axis=1)[counted > 0] / counted[counted > 0] * 100

        breadth_pct = float(shares.iloc[-1])
        breadth_metrics = {"% > 50MA": f"{breadth_pct:.2f}%"}
        for label, back in (("1D Change", 1), ("5D Change", 5), ("1M Change", 21)):
            move = _change_at(shares, back)
            if move is not None:
                breadth_metrics[label] = round(move, 1)  # Percentage points
    except Exception as e:
        breadth_metrics = {"Error": str(e)}

    # Combine all results into one master dictionary
    return {
        "yahoo_finance": yf_metrics,
        "yield_curve_10y_2y": fred_metrics,
        "fear_and_greed": fg_metrics,
        "sp500_breadth": breadth_metrics,
        "put_call_ratio": fetch_put_call_ratio(headers=headers),  # Claude-authored (2026-10-21)
    }


def get_market_dashboard() -> tuple[dict, list[dict]]:
    """
    Claude-authored (2026-10-16): (dashboard, issues) for the universal content. Never raises. Each section that
    couldn't be fetched (it holds {"Error": ...}, or Yahoo returned nothing) becomes an issue
    ({"source", "reason", "message"}), so the digest can say why a figure is missing.
    """
    try:
        dashboard = get_market_dashboard_data()
    except Exception as e:
        return {}, [{"source": "Market dashboard", "reason": "failed",
                     "message": f"Market indicators couldn't be fetched ({type(e).__name__})."}]
    issues = []
    if not dashboard.get("yahoo_finance"):
        issues.append({"source": "Market dashboard: yahoo_finance", "reason": "failed",
                       "message": "No index, VIX, dollar, yield, or oil prices came back from Yahoo."})
    for section in ("yield_curve_10y_2y", "fear_and_greed", "sp500_breadth", "put_call_ratio"):
        error = (dashboard.get(section) or {}).get("Error")
        if error:
            issues.append({"source": f"Market dashboard: {section}", "reason": "failed", "message": str(error)[:200]})
    return dashboard, issues


if __name__ == "__main__":
    import pprint

    market_data = get_market_dashboard_data()
    pprint.pprint(market_data)