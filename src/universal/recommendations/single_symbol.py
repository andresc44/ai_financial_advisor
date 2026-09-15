#File to see all yahoo and finnhub data for a specific symbol
import csv
import sys
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path, override=True)

from filter_tickers import DataFetcher

def test_single_symbol(fetcher: DataFetcher, symbol: str = "AAPL") -> dict:
    """Tests fetching Yahoo and Finnhub data for a single symbol. Returns a dict"""
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
    """Tests JSON sanitization and file export functionality. Outputs Yahoo and Finnhub data"""
    print(f"\n--- 3. Testing Export Functionality for {symbol} ---")
    output_dir = fetcher.project_root / "test_output"

    fetcher.export_ticker_files(market_data, symbol=symbol, output_dir=output_dir)

    yahoo_file = output_dir / f"{symbol.lower()}_yahoo_data.json"
    finnhub_file = output_dir / f"{symbol.lower()}_finnhub_data.json"

    assert yahoo_file.exists(), f"Expected output file '{yahoo_file}' does not exist."
    assert finnhub_file.exists(), f"Expected output file '{finnhub_file}' does not exist."

    print(f"✓ Exported Yahoo JSON: {yahoo_file.name} ({yahoo_file.stat().st_size} bytes)")
    print(f"✓ Exported Finnhub JSON: {finnhub_file.name} ({finnhub_file.stat().st_size} bytes)")

def main():
    print("Initializing DataFetcher instance...")
    try:
        fetcher = DataFetcher()
    except ValueError as e:
        print(f"Initialization Failed: {e}")
        print("Verify that your .env file exists and contains FINNHUB_API_KEY.")
        sys.exit(1)

    #Single symbol test to tune filters and verify data fetching
    test_symbol = "SNPS"
    single_data = test_single_symbol(fetcher, symbol=test_symbol)
    test_export(fetcher, single_data, symbol=test_symbol)
    

if __name__ == "__main__":
    main()
