#File to consolidate universal content for the daily digest, including market data, news, and recommendations, into a single structured dictionary for easy access and display.
from pathlib import Path
from recommendations.filter_tickers import DataFetcher
from market_news import fetch_live_macro_stories
from macro_indicators import get_market_dashboard_data
import sys
import json

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
    
from src.parameters import params_dict
from recommendations.ticker_fetcher import Tickers
from src.universal.recommendations.LLM_recommendation import run_llm_evaluation
from src.universal.recommendations.sanitizer import sanitize_ticker_payload

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
    # output_filename = "LLM_input.json"

    # with open(output_filename, "w", encoding="utf-8") as f:
    #     json.dump(llm_packets, f, indent=4, default=str)

    # print(f"✓ Saved output to {output_filename}")
    
    for ticker in llm_packets.keys():
        raw_data = llm_packets[ticker]
        sanitized_payload = sanitize_ticker_payload(ticker, raw_data)
        print(sanitized_payload)
        
    print(f"✓ Sanitized payload for {len(llm_packets)} tickers")
    
    # generated_recommendations = run_llm_evaluation(llm_packets) #LLM pipeline
    
    # IMPORTANT but not required for recommendations development
    # headline_news = fetch_live_macro_stories()
    # market_data = get_market_dashboard_data()
    
    
    
    
    
    
    return True

if __name__ == "__main__":
    universal_content = get_universal_content()
    print("Universal content fetched successfully.")