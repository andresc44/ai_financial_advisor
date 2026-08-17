import json
import os
from datetime import datetime, timedelta
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import finnhub
from dotenv import load_dotenv
import pandas as pd
import requests
import yfinance as yf

COMPANY_NEWS_LOOKBACK_DAYS = 60
EARNINGS_LOOKBACK_DAYS = 60
EARNINGS_FUTURE_LOOKAHEAD_DAYS = 30

def get_latest_ticker_file(folder_path: Path) -> Path | None:
    """Finds the ticker CSV file with the most recent DD-MM-YY date prefix in folder_path."""
    pattern = "*_ticker_data.csv"
    matching_files = list(folder_path.glob(pattern))

    if not matching_files:
        return None

    dated_files = []
    for file in matching_files:
        # Expecting file format: DD-MM-YY_ticker_data.csv or DD-MM-YY_..._ticker_data.csv
        date_part = file.name.split("_")[0]
        try:
            file_date = datetime.strptime(date_part, "%d-%m-%y")
            dated_files.append((file_date, file))
        except ValueError:
            continue

    if not dated_files:
        return None

    # Sort by datetime (newest date last) and return the path of the latest file
    dated_files.sort(key=lambda x: x[0])
    return dated_files[-1][1]


def load_ticker_df() -> pd.DataFrame:
    """Loads the most recent ticker CSV for the configured TICKER_SET into raw_ticker_df."""
    # Handle path resolution safely across .py files and interactive REPLs
    try:
        script_dir = Path(__file__).resolve().parent
    except NameError:
        script_dir = Path.cwd()

    project_root = script_dir.parent if script_dir.name == "src" else script_dir
    ticker_data_dir = project_root / "data" / "ticker_data"

    if not ticker_data_dir.exists():
        raise FileNotFoundError(
            f"Data directory '{ticker_data_dir}' does not exist."
        )

    latest_file_path = get_latest_ticker_file(ticker_data_dir)

    if latest_file_path is None:
        raise FileNotFoundError(
            f"No matching files found in {ticker_data_dir}"
        )

    print(f"Loading most recent ticker file: {latest_file_path.name}")
    imported_ticker_df = pd.read_csv(latest_file_path)
    return imported_ticker_df

def get_finnhub_api_key(env_path: Optional[Path] = None) -> str:
    """Loads the .env file and retrieves the Finnhub API key."""
    if env_path is None:
        try:
            script_dir = Path(__file__).resolve().parent
        except NameError:
            script_dir = Path.cwd()

        project_root = script_dir.parent if script_dir.name == "src" else script_dir
        env_path = project_root / ".env"

    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("FINNHUB_API_KEY")

    if not api_key:
        raise ValueError(
            f"FINNHUB_API_KEY not found in environment or file at '{env_path}'."
        )

    return api_key




def fetch_ticker_yahoo_data(symbol: str) -> Dict[str, Any]:
    """Fetches all key metrics, historical price data, financial statements, ETF fund data,
    options chains, analyst recommendations, and corporate actions from Yahoo Finance."""
    yahoo_data: Dict[str, Any] = {}
    ticker_obj = yf.Ticker(symbol)

    def safe_get(key: str, attribute_name: str, is_callable: bool = False, *args, **kwargs):
        """Helper to execute yfinance property/method calls safely and convert results to dicts."""
        try:
            attr = getattr(ticker_obj, attribute_name)
            res = attr(*args, **kwargs) if is_callable else attr

            # 1. Convert DataFrames and Series to dictionaries
            if isinstance(res, (pd.DataFrame, pd.Series)):
                return res.to_dict()

            # 2. Extract nested objects (like FundsData or FastInfo)
            if hasattr(res, "__dict__") and not isinstance(res, (dict, list, tuple, str, int, float, bool)):
                out = {}
                for prop in dir(res):
                    if not prop.startswith("_"):
                        val = getattr(res, prop)
                        if callable(val):
                            continue
                        if isinstance(val, (pd.DataFrame, pd.Series)):
                            out[prop] = val.to_dict()
                        else:
                            out[prop] = val
                return out

            # 3. Cast custom dict-like structures
            if hasattr(res, "keys") and not isinstance(res, dict):
                try:
                    return dict(res)
                except Exception:
                    pass

            return res
        except Exception as e:
            return {"error": f"Failed to fetch {key}: {str(e)}"}

    # 1. Info, Fast Info, Identifiers & News
    info_res = safe_get("info", "info")

    if isinstance(info_res, dict):
        info_clean = info_res.copy()
        info_clean.pop("companyOfficers", None)  # Removes the key safely if present
        yahoo_data["info"] = info_clean
    else:
        yahoo_data["info"] = info_res
    yahoo_data["news"] = safe_get("news", "news")
    return yahoo_data



def fetch_ticker_finnhub_data(
    symbol: str, client: finnhub.Client
) -> Dict[str, Any]:
    """Fetches free-tier market metrics, profile, financials, news, insider sentiment, and earnings calendar from Finnhub for a single ticker.

    Each API call is wrapped in an isolated try/except block to gracefully
    handle tier restrictions or missing data.
    """
    finnhub_data: Dict[str, Any] = {}
    now = datetime.now()

    # Dynamic date calculations based on top-level constants
    news_from_date = (now - timedelta(days=COMPANY_NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    today_date = now.strftime("%Y-%m-%d")

    # Helper function to execute API calls safely
    def safe_api_call(call_name: str, func, *args, **kwargs):
        try:
            res = func(*args, **kwargs)
            time.sleep(0.05)  # Small delay to respect rate limits
            return res
        except Exception as e:
            # Captures access denied, tier limits, or missing data without halting
            return {"error": f"Failed to fetch {call_name}: {str(e)}"}

    # 1. Company Profile
    finnhub_data["profile"] = safe_api_call(
        "company_profile2", client.company_profile2, symbol=symbol
    )

    # 2. Real-time Quote
    finnhub_data["quote"] = safe_api_call("quote", client.quote, symbol)

    # 3. Basic Financials & Ratios
    full_financials = safe_api_call(
        "company_basic_financials",
        client.company_basic_financials,
        symbol=symbol,
        metric="all",
    )
    finnhub_data["basic_financials"] = (
    full_financials.get("metric") if isinstance(full_financials, dict) and "metric" in full_financials else full_financials
)

    # 4. Recommendation Trends
    # finnhub_data["recommendation_trends"] = safe_api_call(
    #     "recommendation_trends", client.recommendation_trends, symbol
    # )

    # # 5. Insider Sentiment
    # finnhub_data["insider_sentiment"] = safe_api_call(
    #     "stock_insider_sentiment",
    #     client.stock_insider_sentiment,
    #     symbol=symbol,
    #     _from=news_from_date,
    #     to=today_date,
    # )

    # # 6. Company Earnings (Quarterly EPS surprises)
    # finnhub_data["company_earnings"] = safe_api_call(
    #     "company_earnings", client.company_earnings, symbol
    # )

    # # 7. Company News
    finnhub_data["company_news"] = safe_api_call(
        "company_news",
        client.company_news,
        symbol=symbol,
        _from=news_from_date,
        to=today_date,
    )

    # # 8. Earnings Calendar (Past events + upcoming lookahead)
    # finnhub_data["earnings_calendar"] = safe_api_call(
    #     "earnings_calendar",
    #     client.earnings_calendar,
    #     _from=earnings_from_date,
    #     to=earnings_to_date,
    #     symbol=symbol,
    # )

    return finnhub_data


# ==========================================
# 3. MASTER RETRIEVAL FUNCTION
# ==========================================
def fetch_all_market_data(
    tickers: List[str], env_path: Optional[Path] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """Master function returning a dictionary where each key is a ticker,

    and the value is a list: [yahoo_data, finnhub_data].
    """
    api_key = get_finnhub_api_key(env_path=env_path)
    finnhub_client = finnhub.Client(api_key=api_key)

    market_data: Dict[str, List[Dict[str, Any]]] = {}

    for symbol in tickers:
        print(f"Fetching Yahoo data for {symbol}...")
        yahoo_data = fetch_ticker_yahoo_data(symbol)
        print(f"Fetching Finnhub data for {symbol}...")
        finnhub_data = fetch_ticker_finnhub_data(symbol, finnhub_client)

        # Output format: { "TICKER": [yahoo_data, finnhub_data] }
        market_data[symbol] = [yahoo_data, finnhub_data]

    return market_data

def main():
    try:
        ticker_df = load_ticker_df()
        print("\n--- ticker_df Loaded Successfully ---")
        print(ticker_df.head())
        tickers = ticker_df['ticker'].dropna().tolist()
        print(f"Retrieving Yahoo and Finnhub data for: {tickers}")
        financial_data = fetch_all_market_data(tickers)
        return financial_data
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return

result = main()



def sanitize_for_json(obj: Any) -> Any:
    """Recursively converts DataFrames, Series, Timestamps, FastInfo, custom objects, and non-string dict keys into JSON-serializable types."""
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return sanitize_for_json(obj.to_dict())

    if hasattr(obj, "keys") and not isinstance(obj, dict):
        try:
            return sanitize_for_json({k: obj[k] for k in obj.keys()})
        except Exception:
            return str(obj)

    if isinstance(obj, dict):
        sanitized_dict = {}
        for key, value in obj.items():
            if isinstance(key, (pd.Timestamp, pd.Timedelta, datetime)):
                str_key = key.isoformat()
            elif not isinstance(key, (str, int, float, bool, type(None))):
                str_key = str(key)
            else:
                str_key = key

            sanitized_dict[str_key] = sanitize_for_json(value)
        return sanitized_dict

    if isinstance(obj, (list, tuple, set)):
        return [sanitize_for_json(item) for item in obj]

    if isinstance(obj, (pd.Timestamp, pd.Timedelta, datetime)):
        return obj.isoformat()

    if pd.isna(obj):
        return None

    if not isinstance(obj, (str, int, float, bool, type(None))):
        if hasattr(obj, "__dict__"):
            return sanitize_for_json(obj.__dict__)
        return str(obj)

    return obj


def export_ticker_files(result: Dict[str, List[Dict[str, Any]]], symbol: str) -> None:
    """Exports Yahoo and Finnhub data for a given ticker into separate formatted JSON files."""
    ticker_data = result.get(symbol)

    if not ticker_data or len(ticker_data) < 2:
        print(f"Data for '{symbol}' not found or improperly formatted in result dictionary.")
        return

    yahoo_clean = sanitize_for_json(ticker_data[0])
    finnhub_clean = sanitize_for_json(ticker_data[1])

    yahoo_filename = f"{symbol.lower()}_yahoo_data.json"
    finnhub_filename = f"{symbol.lower()}_finnhub_data.json"

    with open(yahoo_filename, "w", encoding="utf-8") as f:
        json.dump(yahoo_clean, f, indent=4, ensure_ascii=False)

    with open(finnhub_filename, "w", encoding="utf-8") as f:
        json.dump(finnhub_clean, f, indent=4, ensure_ascii=False)

    print(f"Successfully exported '{yahoo_filename}' and '{finnhub_filename}'!")
    
if result:
    export_ticker_files(result, "GOOGM")