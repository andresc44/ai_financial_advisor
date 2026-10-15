#File to consolidate universal content for the daily digest, including market data, news, and recommendations, into a single structured dictionary for easy access and display.
from pathlib import Path

import sys
import json

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
    
from src.parameters import params_dict
from src.universal.recommendations.ticker_fetcher import Tickers
from src.universal.recommendations.sanitizer import sanitize_ticker_payload
from src.universal.recommendations.scorer import select_top_candidates
from src.universal.recommendations.enriched_rec import enrich_recommendations
from src.universal.recommendations.filter_tickers import DataFetcher
from src.universal.market_news import fetch_live_macro_stories
from src.universal.macro_indicators import get_market_dashboard_data
from src.shared_locks import MARKET_DATA_LOCK

def get_universal_content():
    """
    Returns a dictionary containing universal content for the daily digest.
    This content is not user-specific and can be shared across all users.
    """
    payload = {}
    ticker_client = Tickers()
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
    # Claude-authored (2026-10-15): the digest now builds this universal content while users run in parallel.
    # Finnhub / Twelve Data use one shared key, so this fetch takes the same lock as the holdings fetch
    # (src/shared_locks.py); Claude scoring below runs outside it.
    with MARKET_DATA_LOCK:
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
        llm_packets = fetcher.congregate_LLM_input_data(filtered_tickers,as_dict_records=True)
        sanitized_LLM_inputs = {}
    
        for ticker in llm_packets.keys():
            raw_data = llm_packets[ticker]
            sanitized_payload = sanitize_ticker_payload(ticker, raw_data)
            sanitized_LLM_inputs[ticker] = sanitized_payload
            # output_filename = "LLM_input_sanitized.json"

            # with open(output_filename, "w", encoding="utf-8") as f:
            #     json.dump(sanitized_payload, f, indent=4, default=str)

            # print(f"✓ Saved output to {output_filename}")        
        print(f"✓ Sanitized payload for {len(llm_packets)} tickers")
    
    generated_recommendations = select_top_candidates(
        sanitized_inputs=sanitized_LLM_inputs,
        long_n=params_dict["LONG_TERM_N_AMOUNT"],
        swing_n=params_dict["SWING_TERM_N_AMOUNT"]
    )
    print(f"Generated_recommendations:\n{generated_recommendations}")
    recs_w_data = enrich_recommendations(generated_recommendations, sanitized_LLM_inputs)
    
    # IMPORTANT but not required for recommendations development
    # headline_news = fetch_live_macro_stories()
    headline_news = {}
    # market_data = get_market_dashboard_data()
    market_data = {}
    
    return {"recommendations": recs_w_data, "market_data": market_data, "headline_news": headline_news}

if __name__ == "__main__":
    universal_content = get_universal_content()
    print("Universal content fetched successfully.")