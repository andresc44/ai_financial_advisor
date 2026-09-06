
from pathlib import Path
from filter_tickers import DataFetcher
from market_news import fetch_live_macro_stories
from macro_indicators import get_market_dashboard_data
import sys


def get_universal_content():
    """
    Returns a dictionary containing universal content for the daily digest.
    This content is not user-specific and can be shared across all users.
    """
    payload = {}
    print("Initializing DataFetcher instance...")
    try:
        fetcher = DataFetcher()
    except ValueError as e:
        print(f"Initialization Failed: {e}")
        print("Verify that your .env file exists and contains FINNHUB_API_KEY.")
        sys.exit(1)
    filtered_tickers = fetcher.filter_all()
    # news_dict = fetcher.fetch_all_news(filtered_tickers)
    headline_news = fetch_live_macro_stories()
    market_data = get_market_dashboard_data()
    
    
    
    
    
    
    return {
        "summary": "Market summary and key highlights.",
        "market_data": {
            "S&P 500": {"value": 4500, "change": "+1.2%"},
            "NASDAQ": {"value": 15000, "change": "-0.5%"},
            "Dow Jones": {"value": 35000, "change": "+0.8%"},
        },
        "news": [
            {"title": "Tech stocks rally", "link": "https://example.com/tech-rally"},
            {"title": "Economic outlook improves", "link": "https://example.com/economic-outlook"},
        ],
        "recommendations": [
            {"symbol": "AAPL", "action": "Buy", "reason": "Strong earnings report."},
            {"symbol": "TSLA", "action": "Hold", "reason": "Volatile market conditions."},
        ],
    }