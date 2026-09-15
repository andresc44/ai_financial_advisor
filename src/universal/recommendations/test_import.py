#File to test the import of the Tickers and perform filtering
import csv
from datetime import datetime, timezone
import json
import random
import pandas as pd
from ticker_fetcher import Tickers

import os
import requests
import time

import sys
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path, override=True)

from src.parameters import params_dict
from filter_tickers import DataFetcher

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
    mktcap_min=params_dict["MKTCAP_MIN"],        # Minimum market cap in Millions USD
    mktcap_max=params_dict["MKTCAP_MAX"],        # Maximum market cap in Millions USD
    volume_min=params_dict["VOLUME_MIN"],        # Minimum trading volume in Millions
    volume_max=params_dict["VOLUME_MAX"],        # Maximum trading volume in Millions
    lastsale_min=params_dict["LASTSALE_MIN"],    # Minimum share price USD
    lastsale_max=params_dict["LASTSALE_MAX"],    # Maximum share price USD
    region=params_dict["REGION"],                # Takes priority over 'country'
    country=params_dict["COUNTRY"],              # Ignored when region is defined
    sector=params_dict["SECTOR"],                # Takes priority over 'industry'
    industry=params_dict["INDUSTRY"],            # Ignored when sector is defined
    clear_existing_data=params_dict["CLEAR_EXISTING_DATA"],  # Deletes previous CSV exports in output dir
)

print(f"Filtered file generated: {file_path}")

def main():
    print("Initializing DataFetcher instance...")
    try:
        fetcher = DataFetcher()
    except ValueError as e:
        print(f"Initialization Failed: {e}")
        print("Verify that your .env file exists and contains FINNHUB_API_KEY.")
        sys.exit(1)

    
    #Fetching all tickers and testing pipeline
    print(f"Testing filter_all() for {fetcher.all_tickers} ---")
    filtered_tickers = fetcher.filter_all()
    llm_packet = fetcher.congregate_LLM_input_data(filtered_tickers,as_dict_records=True)
    current_dir = Path(__file__).resolve().parent
    output_path = current_dir / "LLM_input.json"

    # 3. Save to JSON
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(llm_packet, f, indent=4, default=str)

    print(f"✓ LLM input payload successfully saved to: {output_path}")
    
    # news_dict = fetcher.fetch_all_news(filtered_tickers)
    # random_signal = random.choice(list(news_dict.keys()))  # e.g., 'b'
    # pd.DataFrame(news_dict[random_signal]).to_csv(f"{random_signal}_news.csv", index=True)
    # print(f"Successfully saved to {random_signal}_news.csv")
    
    
    # data_dict = fetcher.fetch_all()
    # print(f"✓ Successfully fetched batch data for {list(data_dict.keys())}")

    

    # print("\n🎉 All tests passed successfully!")

if __name__ == "__main__":
    main()
