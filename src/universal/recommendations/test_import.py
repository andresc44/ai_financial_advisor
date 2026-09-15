#File to test the import of the Tickers and perform filtering
import csv
from datetime import datetime, timezone
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
    # news_dict = fetcher.fetch_all_news(filtered_tickers)
    # random_signal = random.choice(list(news_dict.keys()))  # e.g., 'b'
    # pd.DataFrame(news_dict[random_signal]).to_csv(f"{random_signal}_news.csv", index=True)
    # print(f"Successfully saved to {random_signal}_news.csv")
    
    
    # data_dict = fetcher.fetch_all()
    # print(f"✓ Successfully fetched batch data for {list(data_dict.keys())}")

    

    # print("\n🎉 All tests passed successfully!")

if __name__ == "__main__":
    main()
