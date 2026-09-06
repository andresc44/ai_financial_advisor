# File to fetch and consolidate various macroeconomic indicators from multiple sources (Yahoo Finance, FRED, CNN Fear & Greed Index, S&P 500 Breadth) into a single structured dictionary for analysis and reporting.
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
    df_yf = yf.download(
        tickers_list, period="2mo", interval="1d", progress=False
    )["Close"]

    yf_metrics = {}
    for name, ticker in yf_tickers.items():
        if ticker in df_yf.columns:
            series = df_yf[ticker].dropna()
            if len(series) > 1:
                latest = series.iloc[-1]
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
        url_fred = f"https://api.stlouisfed.org/fred/series/observations?series_id=T10Y2Y&api_key={fred_api_key}&file_type=json&sort_order=desc&limit=10"
        try:
            res_fred = requests.get(url_fred).json()
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
        except Exception as e:
            fred_metrics = {"Error": str(e)}

    # 3. Fetch Fear & Greed Index
    fg_url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        res_fg = requests.get(fg_url, headers=headers).json()
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
        )
        soup = BeautifulSoup(resp_wiki.text, "html.parser")
        table = soup.find("table", {"id": "constituents"})
        df_sp500 = pd.read_html(io.StringIO(str(table)))[0]

        sp500_tickers = (
            df_sp500["Symbol"].str.replace(".", "-", regex=False).tolist()
        )

        data_breadth = yf.download(
            sp500_tickers, period="60d", interval="1d", progress=False
        )["Close"]
        ma50 = data_breadth.rolling(window=50).mean()

        latest_prices = data_breadth.iloc[-1]
        latest_ma50 = ma50.iloc[-1]

        above_50ma = (latest_prices > latest_ma50).sum()
        total_valid = latest_prices.dropna().count()

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


if __name__ == "__main__":
    import pprint

    market_data = get_market_dashboard_data()
    pprint.pprint(market_data)