params_dict = {
    # Lookback and Lookahead Windows (Days)
    "COMPANY_NEWS_LOOKBACK_DAYS": 60,
    "EARNINGS_LOOKBACK_DAYS": 60,
    "EARNINGS_FUTURE_LOOKAHEAD_DAYS": 30,
    # API Settings
    "FINNHUB_RATE_LIMIT_SLEEP": 0.05,
    # Ticker Filter Parameters
    "MKTCAP_MIN": 50000.0,  # Minimum market cap in Millions USD
    "MKTCAP_MAX": None,  # Maximum market cap in Millions USD
    "VOLUME_MIN": 0.1,  # Minimum trading volume in Millions
    "VOLUME_MAX": None,  # Maximum trading volume in Millions
    "LASTSALE_MIN": 5.0,  # Minimum share price USD
    "LASTSALE_MAX": 500,  # Maximum share price USD
    "REGION": None,  # Takes priority over 'country'
    "COUNTRY": None,  # Ignored when region is defined
    "SECTOR": None,  # Takes priority over 'industry'
    "INDUSTRY": "Software",  # Ignored when sector is defined
    "CLEAR_EXISTING_DATA": True,  # Deletes previous CSV exports in output dir
}