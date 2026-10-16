# File to fetch and consolidate various macroeconomic indicators from multiple sources (Yahoo Finance, FRED, CNN Fear & Greed Index, S&P 500 Breadth) into a single structured dictionary for analysis and reporting.
"""
Claude-authored changes (2026-10-16, Claude Opus 5.5 via Claude Code, at Andres's request), when the dashboard was
enabled in the digest:
- get_market_dashboard() wraps the fetch so it never raises: a failure gives an empty dashboard plus an issue,
  instead of stopping the universal content (and so every user's digest).
- S&P 500 breadth downloaded 60 calendar days (~41 trading days), too few for a 50-day average, so it was always 0%.
- Every request has a timeout, so one slow site can't hang the digest.
- A failed FRED call no longer puts its error text in the payload: that text includes the URL with the API key.
"""
import io
import os
import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


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
        df_yf = yf.download(
            tickers_list, period="2mo", interval="1d", progress=False
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

    # 2. Fetch FRED Yield Curve
    if not fred_api_key:
        fred_metrics = {"Error": "FRED_API_KEY missing from .env"}
    else:
        url_fred = "https://api.stlouisfed.org/fred/series/observations"
        fred_params = {"series_id": "T10Y2Y", "api_key": fred_api_key, "file_type": "json", "sort_order": "desc", "limit": 10}
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

        latest_prices = data_breadth.iloc[-1]
        latest_ma50 = ma50.iloc[-1]

        comparable = latest_prices.notna() & latest_ma50.notna()  # Claude-authored (2026-10-16): e.g. recent listings
        above_50ma = (latest_prices[comparable] > latest_ma50[comparable]).sum()
        total_valid = int(comparable.sum())

        breadth_pct = (above_50ma / total_valid) * 100
        breadth_metrics = {"% > 50MA": f"{breadth_pct:.2f}%"}
    except Exception as e:
        breadth_metrics = {"Error": str(e)}

    # Combine all results into one master dictionary
    return {
        "yahoo_finance": yf_metrics,
        "yield_curve_10y_2y": fred_metrics,
        "fear_and_greed": fg_metrics,
        "sp500_breadth": breadth_metrics,
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
    for section in ("yield_curve_10y_2y", "fear_and_greed", "sp500_breadth"):
        error = (dashboard.get(section) or {}).get("Error")
        if error:
            issues.append({"source": f"Market dashboard: {section}", "reason": "failed", "message": str(error)[:200]})
    return dashboard, issues


if __name__ == "__main__":
    import pprint

    market_data = get_market_dashboard_data()
    pprint.pprint(market_data)