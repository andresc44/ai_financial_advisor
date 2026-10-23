from datetime import datetime, timedelta, date
import json
import sys

from src.config.parameters import params_dict


def process_news(news):
    """
    Processes news items, deduplicates entries based on normalized summary text, and formats
    valid items into '- [{date_str}] {summary}' strings. Returns up to MAX_NEWS_ARTICLES
    most recent items (default 10 if not specified in params_dict).
    """
    news_items = []
    seen_summaries = set()
    max_articles = params_dict.get("MAX_NEWS_ARTICLES", 10)  # Default to 10 if not specified

    if isinstance(news, list):
        # Sort news by timestamp (newest first) if available
        try:
            sorted_news = sorted(
                (n for n in news if isinstance(n, dict)),
                key=lambda x: (
                    x.get("timestamp") or 
                    x.get("providerPublishTime") or 
                    x.get("datetime") or
                    0
                ),
                reverse=True
            )
        except Exception:
            sorted_news = [n for n in news if isinstance(n, dict)]

        for article in sorted_news:
            if len(news_items) >= max_articles:
                print(f"Reduced news summaries from {len(sorted_news)} to {max_articles}")
                break

            date_str = (
                article.get("date_str")
                or article.get("datetime")
                or article.get("providerPublishTime")
                or "Unknown Date"
            )
            summary = (
                article.get("summary")
                or article.get("headline")
                or article.get("title")
                or ""
            ).strip()

            if summary:
                # Normalize spaces and casing to accurately detect duplicate text
                normalized_key = " ".join(summary.lower().split())
                if normalized_key not in seen_summaries:
                    seen_summaries.add(normalized_key)
                    news_items.append(f"- [{date_str}] {summary}")

    return news_items


def extract_series(series_list, key="v"):
    """Transforms quarterly series maintaining full historical duration."""
    if not series_list or not isinstance(series_list, list):
        return []
    return [
        {"period": item.get("period"), "value": item.get(key)}
        for item in series_list
        if isinstance(item, dict)
    ]


def extract_indicator_series(indicator_data, value_keys):
    """
    Transforms technical indicator data without slicing, maintaining full history
    and rounding floating point values.
    """
    if not indicator_data or "values" not in indicator_data:
        return None

    values = indicator_data.get("values", [])
    result = []
    for val in values:
        if not isinstance(val, dict):
            continue
        item = {"date": val.get("datetime")}
        for k in value_keys:
            if k in val:
                try:
                    item[k] = round(float(val[k]), 4)
                except (ValueError, TypeError):
                    item[k] = val[k]
        result.append(item)
    return result


def extract_insider_sentiment(fh_insider, days_back):
    """
    Filters insider sentiment entries to only include records within
    the specified number of days back from today.
    """
    if not isinstance(fh_insider, list):
        return []

    cutoff_date = date.today() - timedelta(days=days_back)
    filtered_series = []

    for item in fh_insider:
        if not isinstance(item, dict):
            continue

        year = item.get("year")
        month = item.get("month")
        period = item.get("period")

        entry_date = None
        period_str = period

        # Determine the entry date from year/month or period string
        if year is not None and month is not None:
            try:
                entry_date = date(int(year), int(month), 1)
                period_str = f"{int(year)}-{int(month):02d}"
            except (ValueError, TypeError):
                pass
        elif period:
            try:
                parts = [int(p) for p in str(period).split("-")]
                if len(parts) >= 2:
                    entry_date = date(parts[0], parts[1], 1)
            except (ValueError, TypeError):
                pass

        # Check if entry is within the cutoff window
        if entry_date:
            # Set cutoff comparison to the 1st of cutoff month to preserve partial-month windows
            if entry_date >= cutoff_date.replace(day=1):
                filtered_series.append({
                    "period": period_str,
                    "mspr": item.get("mspr"),
                    "change": item.get("change")
                })
        else:
            # Fallback if no date could be parsed
            filtered_series.append({
                "period": period_str,
                "mspr": item.get("mspr"),
                "change": item.get("change")
            })

    return filtered_series


def sanitize_ticker_payload(ticker_symbol, raw_data):
    yahoo = raw_data.get("yahoo", {})
    finnhub = raw_data.get("finnhub", {})
    twelvedata = raw_data.get("twelvedata", {})
    raw_news = raw_data.get("news") or yahoo.get("news") or finnhub.get("news") or []

    fh_profile = finnhub.get("profile", {})
    fh_basic = finnhub.get("basic_financials", {})
    fh_earnings = finnhub.get("earnings_calendar", {}).get("earningsCalendar", [])
    # Claude-authored (2026-10-16): soonest first, so "catalysts_and_earnings.upcoming_earnings.0" is the next report
    # (RECOMMENDATION_FIELDS in parameters.py)
    fh_earnings = sorted(
        (e for e in fh_earnings if isinstance(e, dict)) if isinstance(fh_earnings, list) else [],
        key=lambda e: str(e.get("date") or "9999-99-99"),
    )
    fh_surprises = finnhub.get("earnings_surprise", [])
    fh_insider = finnhub.get("insider_sentiment", {}).get("data", [])
    fh_series = finnhub.get("series_financials", {}).get("quarterly", {})

    insider_days_back = (
        params_dict.get("INSIDER_SENTIMENT", {}).get("DAYS_BACK", 365)
    )

    sanitized = {
        "symbol": ticker_symbol,
        "profile": {
            "name": yahoo.get("longName") or fh_profile.get("name"),
            "sector": yahoo.get("sector"),
            "industry": yahoo.get("industry"),
            "business_summary": yahoo.get("longBusinessSummary"),
            "full_time_employees": yahoo.get("fullTimeEmployees"),
            "ipo_date": fh_profile.get("ipo")
        },
        "price_action": {
            "current_price": yahoo.get("currentPrice") or yahoo.get("regularMarketPrice"),
            "previous_close": yahoo.get("previousClose"),
            "open": yahoo.get("open"),
            "day_low": yahoo.get("dayLow"),
            "day_high": yahoo.get("dayHigh"),
            "52_week_low": yahoo.get("fiftyTwoWeekLow"),
            "52_week_high": yahoo.get("fiftyTwoWeekHigh"),
            # Claude-authored (2026-10-23): a fraction (0.25 = 25%), like Yahoo's; Finnhub's fallback is in percent
            "52_week_change_percent": yahoo.get("52WeekChange") if yahoo.get("52WeekChange") is not None else (
                fh_basic["52WeekPriceReturnDaily"] / 100
                if isinstance(fh_basic.get("52WeekPriceReturnDaily"), (int, float)) else None),
            "50_day_average": yahoo.get("fiftyDayAverage"),
            "200_day_average": yahoo.get("twoHundredDayAverage"),
            "volume": yahoo.get("volume"),
            "average_volume_10d": yahoo.get("averageVolume10days"),
            "average_volume_3m": yahoo.get("averageVolume"),
            "beta": yahoo.get("beta") or fh_basic.get("beta")
        },
        "valuation_and_fundamentals": {
            "market_cap": yahoo.get("marketCap"),
            "enterprise_value": yahoo.get("enterpriseValue"),
            "pe_trailing": yahoo.get("trailingPE"),
            "pe_forward": yahoo.get("forwardPE"),
            "peg_ratio": yahoo.get("pegRatio"),
            "price_to_book": yahoo.get("priceToBook"),
            "price_to_sales": yahoo.get("priceToSalesTrailing12Months"),
            "enterprise_to_ebitda": yahoo.get("enterpriseToEbitda"),
            "enterprise_to_revenue": yahoo.get("enterpriseToRevenue"),
            "revenue_total": yahoo.get("totalRevenue"),
            "revenue_growth_yoy": yahoo.get("revenueGrowth"),
            "revenue_growth_3y_cagr": fh_basic.get("revenueGrowth3Y"),
            "revenue_growth_5y_cagr": fh_basic.get("revenueGrowth5Y"),
            "gross_margin": yahoo.get("grossMargins"),
            "ebitda_margin": yahoo.get("ebitdaMargins"),
            "operating_margin": yahoo.get("operatingMargins"),
            "profit_margin": yahoo.get("profitMargins"),
            "earnings_growth_yoy": yahoo.get("earningsGrowth"),
            "eps_trailing": yahoo.get("trailingEps"),
            "eps_forward": yahoo.get("forwardEps"),
            "free_cash_flow": yahoo.get("freeCashflow"),
            "operating_cash_flow": yahoo.get("operatingCashflow"),
            "total_cash": yahoo.get("totalCash"),
            "total_debt": yahoo.get("totalDebt"),
            "debt_to_equity": yahoo.get("debtToEquity"),
            "current_ratio": yahoo.get("currentRatio"),
            "quick_ratio": yahoo.get("quickRatio"),
            "return_on_assets": yahoo.get("returnOnAssets") or fh_basic.get("roaTTM"),
            "return_on_equity": yahoo.get("returnOnEquity") or fh_basic.get("roeTTM"),
            "return_on_invested_capital": fh_basic.get("roiTTM")
        },
        "sentiment_and_analysts": {
            "recommendation_key": yahoo.get("recommendationKey"),
            "recommendation_mean": yahoo.get("recommendationMean"),
            "target_mean_price": yahoo.get("targetMeanPrice"),
            "target_high_price": yahoo.get("targetHighPrice"),
            "target_low_price": yahoo.get("targetLowPrice"),
            "analyst_opinions_count": yahoo.get("numberOfAnalystOpinions"),
            "short_percent_of_float": yahoo.get("shortPercentOfFloat"),
            "short_ratio": yahoo.get("shortRatio"),
            "held_percent_insiders": yahoo.get("heldPercentInsiders"),
            "held_percent_institutions": yahoo.get("heldPercentInstitutions"),
            "insider_sentiment_series": extract_insider_sentiment(
                fh_insider, insider_days_back
            )
        },
        "catalysts_and_earnings": {
            "upcoming_earnings": fh_earnings,
            "past_earnings_surprises": [
                {
                    "quarter": f"{s.get('year')} Q{s.get('quarter')}",
                    "estimate": s.get("estimate"),
                    "actual": s.get("actual"),
                    "surprise_percent": s.get("surprisePercent")
                }
                for s in fh_surprises if isinstance(s, dict)
            ]
        },
        "quarterly_financial_trends": {
            "eps": extract_series(fh_series.get("eps", []), "v"),
            "revenue_per_share": extract_series(fh_series.get("salesPerShare", []), "v"),
            "operating_margin": extract_series(fh_series.get("operatingMargin", []), "v"),
            "net_margin": extract_series(fh_series.get("netMargin", []), "v"),
            "free_cash_flow_margin": extract_series(fh_series.get("fcfMargin", []), "v")
        },
        "technical_indicators": {
            "rsi_14_1day": extract_indicator_series(twelvedata.get("rsi_14_1day"), ["rsi"]),
            "ema_9_1day": extract_indicator_series(twelvedata.get("ema_9_1day"), ["ema"]),
            "ema_21_1day": extract_indicator_series(twelvedata.get("ema_21_1day"), ["ema"]),
            "bbands_20_1day": extract_indicator_series(
                twelvedata.get("bbands_20_1day"), ["upper_band", "middle_band", "lower_band"]
            ),
            "sar_1day": extract_indicator_series(twelvedata.get("sar_1day"), ["sar"]),
            "beta_52_1week": extract_indicator_series(twelvedata.get("beta_52_1week"), ["beta"])
        },
        # Claude-authored (2026-10-18): the last DAILY_BARS_DAYS daily bars, newest first (DataFetcher.recent_daily_bars)
        "daily_bars": raw_data.get("daily_bars") or [],
        "recent_headlines_and_catalysts": process_news(raw_news)
    }
    return sanitized



# Claude-authored (2026-10-20): what each horizon's LLM call is sent, by params_dict["LLM_PAYLOAD"]
DEFAULT_LLM_PAYLOAD = {
    "LONG_TERM": ["profile", "price_action", "valuation_and_fundamentals", "sentiment_and_analysts",
                  "catalysts_and_earnings", "quarterly_financial_trends", "recent_headlines_and_catalysts"],
    "SWING": ["profile", "price_action", "sentiment_and_analysts", "catalysts_and_earnings", "technical_indicators",
              "daily_bars", "recent_headlines_and_catalysts"],
    "SWING_FUNDAMENTALS": ["market_cap", "pe_forward", "revenue_growth_yoy", "earnings_growth_yoy", "profit_margin",
                           "debt_to_equity"],
}


def horizon_payload(sanitized, horizon, swing_setup=None):
    """
    Claude-authored (2026-10-20): the part of a sanitized payload one horizon's LLM call uses, by
    params_dict["LLM_PAYLOAD"]: long-term calls skip the technicals and daily bars, swing calls the full fundamentals
    and quarterly trends (keeping the SWING_FUNDAMENTALS fields as "key_fundamentals"), and get the swing screen's
    setup, facts, and plan as "swing_setup". The full sanitized payload is still what the picks are built from.
    """
    config = {**DEFAULT_LLM_PAYLOAD, **(params_dict.get("LLM_PAYLOAD") or {})}
    payload = {"symbol": sanitized.get("symbol")}
    for section in config["SWING" if horizon == "swing" else "LONG_TERM"]:
        if section in sanitized:
            payload[section] = sanitized[section]
    if horizon == "swing":
        fundamentals = sanitized.get("valuation_and_fundamentals") or {}
        payload["key_fundamentals"] = {field: fundamentals.get(field) for field in config["SWING_FUNDAMENTALS"]}
        payload["swing_setup"] = swing_setup or {}
    return payload


def process_file(input_filename, output_filename):
    with open(input_filename, 'r') as f:
        data = json.load(f)

    sanitized_output = {}
    for ticker, raw_ticker_data in data.items():
        sanitized_output[ticker] = sanitize_ticker_payload(ticker, raw_ticker_data)

    with open(output_filename, 'w') as f:
        json.dump(sanitized_output, f, indent=2)

    print(f"Successfully processed {input_filename} -> {output_filename}")


if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "LLM_input_new.json"
    output_file = sys.argv[2] if len(sys.argv) > 2 else "LLM_input_sanitized.json"
    process_file(input_file, output_file)