from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    # Prevents circular imports at runtime while enabling full autocomplete in VS Code/Pylance
    from filter_tickers import DataFetcher


def filter_finnhub_tickers(
    fetcher: "DataFetcher", 
    tickers: Optional[List[str]] = None
) -> List[str]:
    """Filters tickers based on Finnhub fundamental data by delegating calls back to the DataFetcher instance."""
    input_tickers = tickers if tickers is not None else fetcher.all_tickers
    filtered_finnhub_tickers = []

    # Safely retrieve private __get_metric_with_alert or single/public fallback
    get_metric = getattr(
        fetcher, 
        f"_{fetcher.__class__.__name__}__get_metric_with_alert", 
        getattr(fetcher, "_get_metric_with_alert", None)
    )

    for symbol in input_tickers:
        print(f"Fetching Finnhub data for {symbol}...")
        finnhub_data = fetcher.fetch_finnhub_data(symbol, fetch_news=False)
        if not isinstance(finnhub_data, dict):
            print(f"Alert: Finnhub data for '{symbol}' is not a dictionary. Skipping.")
            continue

        # Safely navigate nested keys matching Finnhub JSON payload
        profile = finnhub_data.get("profile", {})
        quote = finnhub_data.get("quote", {})
        basic_fin = finnhub_data.get("basic_financials", {})

        # Sample Basic Filters:
        if not (mcap_mil := get_metric(profile, "marketCapitalization", symbol)):
            continue
        if mcap_mil < 1_000:  # Market Cap in Millions USD
            continue

        if not (total_shares := get_metric(quote, "t", symbol)):
            continue
        if total_shares <= 100_000:  # Total Shares Outstanding
            continue

        if not (net_margin := get_metric(basic_fin, "netProfitMarginTTM", symbol)):
            continue
        if net_margin <= 1:
            continue

        if not (pe_ttm := get_metric(basic_fin, "peTTM", symbol, float("inf"))):
            continue
        if not (0 < pe_ttm <= 90):
            continue

        if not (rev_growth := get_metric(rev_growth_data := basic_fin, "revenueGrowthTTMYoy", symbol)):
            continue
        if rev_growth <= -30:
            continue

        filtered_finnhub_tickers.append(symbol)
        print(
            f"✓ {symbol} passed Finnhub filters: Market Cap = {mcap_mil}M, "
            f"Total Shares = {total_shares}, Net Margin = {net_margin}%, "
            f"P/E TTM = {pe_ttm}, Revenue Growth = {rev_growth}%"
        )

    print(f"Filtered final tickers: {filtered_finnhub_tickers}")
    return filtered_finnhub_tickers