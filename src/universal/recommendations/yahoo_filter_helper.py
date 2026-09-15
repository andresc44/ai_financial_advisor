from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    # Avoid circular imports at runtime while keeping type hinting alive in VS Code
    from filter_tickers import DataFetcher


def filter_yahoo_tickers(
    client: "DataFetcher", 
    tickers: Optional[List[str]] = None
) -> List[str]:
    """Filters tickers based on basic Yahoo parameters by delegating calls back to the client instance."""
    input_tickers = tickers if tickers is not None else client.all_tickers
    filtered_yahoo_tickers = []

    for symbol in input_tickers:
        print(f"Fetching Yahoo data for {symbol}...")
        yahoo_metrics = client.fetch_yahoo_data(symbol=symbol, fetch_news=False)
        
        if not isinstance(yahoo_metrics, dict):
            print(f"Alert: Yahoo data for '{symbol}' is not a dictionary. Skipping.")
            continue

        # Accessing the private helper method on the class instance
        # Note: If __get_metric_with_alert is private (double underscore), Python mangles it to _ClassName__methodName
        get_metric = getattr(client, f"_{client.__class__.__name__}__get_metric_with_alert", None)
        if not get_metric:
            # Fallback if __get_metric_with_alert is made single underscore or public
            get_metric = getattr(client, "_get_metric_with_alert", None) or getattr(client, "get_metric_with_alert")

        # Sample Basic Filters TO BE REPLACED WITH PARAMETERIZED VALUES:
        if not (market_cap := get_metric(yahoo_metrics, "marketCap", symbol)):
            continue
        if market_cap < 10_000:
            continue

        if not (regularMarketPreviousClose := get_metric(yahoo_metrics, "regularMarketPreviousClose", symbol)):
            continue
        if regularMarketPreviousClose >= 100:
            continue

        filtered_yahoo_tickers.append(symbol)
        print(f"✓ {symbol} passed Yahoo filters: Market Cap = {market_cap}, Market Close = {regularMarketPreviousClose}")

    return filtered_yahoo_tickers