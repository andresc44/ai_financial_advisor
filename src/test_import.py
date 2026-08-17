from ticker_fetcher import Tickers

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
    mktcap_min=500000.0,          # Minimum market cap in Millions USD
    mktcap_max=None,        # Maximum market cap in Millions USD
    volume_min=0.1,        # Minimum trading volume in Millions
    volume_max=None,     # Maximum trading volume in Millions
    lastsale_min=5.0,         # Minimum share price USD
    lastsale_max=300,        # Maximum share price USD
    region=None,    # Takes priority over 'country'
    country=None,   # Ignored when region is defined
    sector=None,       # Takes priority over 'industry'
    industry="Software",       # Ignored when sector is defined
    clear_existing_data=True,  # Deletes previous CSV exports in output dir
)

print(f"Filtered file generated: {file_path}")