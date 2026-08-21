import csv
from datetime import datetime, timezone
import random
import pandas as pd
from ticker_fetcher import Tickers


import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()

ticker_client = Tickers()

# --- Step 1: Query available parameters ---
# sectors = ticker_client.get_available_sectors()
# regions_with_countries = ticker_client.get_available_regions(include_countries=True)
# industries = ticker_client.get_available_industries()
# print("Available Sectors:", sectors)
# print("Available Regions:", list(regions_with_countries.keys()))
# print("Available industries:", industries)

# --- Step 2: Pass all filter arguments explicitly ---

file_path = ticker_client.fetch_ticker_data(
    exchanges={"NASDAQ": True, "NYSE": True, "AMEX": True},
    mktcap_min=50000.0,          # Minimum market cap in Millions USD
    mktcap_max=None,        # Maximum market cap in Millions USD
    volume_min=0.1,        # Minimum trading volume in Millions
    volume_max=None,     # Maximum trading volume in Millions
    lastsale_min=5.0,         # Minimum share price USD
    lastsale_max=500,        # Maximum share price USD
    region=None,    # Takes priority over 'country'
    country=None,   # Ignored when region is defined
    sector=None,       # Takes priority over 'industry'
    industry="Software",       # Ignored when sector is defined
    clear_existing_data=True,  # Deletes previous CSV exports in output dir
)

print(f"Filtered file generated: {file_path}")

import sys
from filter_tickers import DataFetcher


def test_single_symbol(fetcher: DataFetcher, symbol: str = "AAPL") -> dict:
    """Tests fetching Yahoo and Finnhub data for a single symbol."""
    print(f"\n--- 1. Testing Single Symbol Fetch ({symbol}) ---")

    print(f"Fetching Yahoo data for {symbol}...")
    yahoo_data = fetcher.fetch_yahoo_data(symbol)
    assert isinstance(yahoo_data, dict), "Yahoo data must return a dictionary"
    print(f"✓ Yahoo keys fetched: {list(yahoo_data.keys())}")

    print(f"Fetching Finnhub data for {symbol}...")
    finnhub_data = fetcher.fetch_finnhub_data(symbol)
    assert isinstance(finnhub_data, dict), "Finnhub data must return a dictionary"
    print(f"✓ Finnhub keys fetched: {list(finnhub_data.keys())}")

    return {symbol: {"yahoo": yahoo_data, "finnhub": finnhub_data}}



def test_export(fetcher: DataFetcher, market_data: dict, symbol: str) -> None:
    """Tests JSON sanitization and file export functionality."""
    print(f"\n--- 3. Testing Export Functionality for {symbol} ---")
    output_dir = fetcher.project_root / "test_output"

    fetcher.export_ticker_files(market_data, symbol=symbol, output_dir=output_dir)

    yahoo_file = output_dir / f"{symbol.lower()}_yahoo_data.json"
    finnhub_file = output_dir / f"{symbol.lower()}_finnhub_data.json"

    assert yahoo_file.exists(), f"Expected output file '{yahoo_file}' does not exist."
    assert finnhub_file.exists(), f"Expected output file '{finnhub_file}' does not exist."

    print(f"✓ Exported Yahoo JSON: {yahoo_file.name} ({yahoo_file.stat().st_size} bytes)")
    print(f"✓ Exported Finnhub JSON: {finnhub_file.name} ({finnhub_file.stat().st_size} bytes)")

#https://github.com/twelvedata/twelvedata-python
def fetch_advanced_momentum(
    symbol: str = "HPE", interval: str = "1day"
) -> pd.DataFrame:
    api_key = os.getenv("TWELVEDATA_API_KEY")
    if not api_key:
        raise ValueError("TWELVEDATA_API_KEY not found in .env file.")

    # Endpoint names corrected according to Twelve Data API docs
    indicators = {
        "CMO": ("cmo", {"time_period": 14}),
        "Ultimate Oscillator": (
            "ultosc",
            {"time_period1": 7, "time_period2": 14, "time_period3": 28},
        ),
        "MFI": ("mfi", {"time_period": 14}),
        "PPO": ("ppo", {"fast_period": 12, "slow_period": 26}),
        "StochRSI": ("stochrsi", {"time_period": 14}),
        "KST": ("kst", {}),
    }

    parsed_rows = []

    for label, (endpoint, params) in indicators.items():
        url = f"https://api.twelvedata.com/{endpoint}"
        query_params = {
            "symbol": symbol,
            "interval": interval,
            "apikey": api_key,
            "outputsize": 1,
            **params,
        }

        try:
            response = requests.get(url, params=query_params, timeout=10)

            # Check if response is valid JSON
            if response.status_code == 200:
                res = response.json()
                if "values" in res and len(res["values"]) > 0:
                    latest = res["values"][0]
                    latest.pop("datetime", None)

                    # Unpack multiple returned metrics (e.g., StochRSI -> %K, %D)
                    for metric_name, val in latest.items():
                        parsed_rows.append({
                            "Indicator": label,
                            "Metric": metric_name,
                            "Value": (
                                round(float(val), 4)
                                if val is not None
                                else "N/A"
                            ),
                        })
                else:
                    err_msg = res.get("message", "No data returned")
                    parsed_rows.append({
                        "Indicator": label,
                        "Metric": "Error",
                        "Value": err_msg,
                    })
            else:
                parsed_rows.append({
                    "Indicator": label,
                    "Metric": "Error",
                    "Value": f"HTTP {response.status_code}",
                })

        except Exception as e:
            parsed_rows.append(
                {"Indicator": label, "Metric": "Error", "Value": str(e)}
            )

        # Pause 1 sec between calls to respect 8 reqs/min free rate limit
        time.sleep(1)

    return pd.DataFrame(parsed_rows)


def main():
    print("Initializing DataFetcher instance...")
    try:
        fetcher = DataFetcher()
    except ValueError as e:
        print(f"Initialization Failed: {e}")
        print("Verify that your .env file exists and contains FINNHUB_API_KEY.")
        sys.exit(1)

    # Step 1: Single symbol test
    test_symbol = "GOOGM"
    # single_data = test_single_symbol(fetcher, symbol=test_symbol)

    # print(f"\n--- 4. Testing filter_all() for {fetcher.all_tickers} ---")
    # filtered_tickers = fetcher.filter_all()
    # news_dict = fetcher.fetch_all_news(filtered_tickers)
    # random_signal = random.choice(list(news_dict.keys()))  # e.g., 'b'
    # pd.DataFrame(news_dict[random_signal]).to_csv(f"{random_signal}_news.csv", index=True)
    # print(f"Successfully saved to {random_signal}_news.csv")
    
    ticker = "HPE"
    print(f"Fetching advanced momentum indicators for {ticker}...\n")
    df_momentum = fetch_advanced_momentum(ticker)
    print(df_momentum.to_string(index=False))
    # data_dict = fetcher.fetch_all()
    # print(f"✓ Successfully fetched batch data for {list(data_dict.keys())}")

    # # Step 4: Test sanitization and JSON export
    # test_export(fetcher, single_data, symbol=test_symbol)

    # print("\n🎉 All tests passed successfully!")


if __name__ == "__main__":
    main()