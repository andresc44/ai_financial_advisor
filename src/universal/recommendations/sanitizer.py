import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict


def parse_date(date_str: str) -> Optional[datetime]:
    """Parses standard ISO or date string formats (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        clean_str = date_str.strip().split()[0]
        return datetime.strptime(clean_str[:10], "%Y-%m-%d")
    except (ValueError, IndexError):
        return None


def sanitize_ticker_payload(symbol: str, raw_data: Dict[str, Any]) -> str:
    """Strips API noise and formats raw multi-source stock data into a compact Markdown text block."""
    FINANCIALS_MAX_DAYS = params_dict.get("FINANCIALS_REPORTED_MAX_DAYS", 365)
    SERIES_MAX_DAYS = params_dict.get("SERIES_MAX_DAYS", 30)

    # Unwrap nested payload if keyed by ticker symbol
    if symbol in raw_data and isinstance(raw_data[symbol], dict):
        raw_data = raw_data[symbol]

    yahoo = raw_data.get("yahoo") or {}
    finnhub = raw_data.get("finnhub") or {}
    twelvedata = raw_data.get("twelvedata") or {}
    news = raw_data.get("news") or []

    fh_profile = finnhub.get("profile") or {}
    fh_quote = finnhub.get("quote") or {}
    fh_financials = finnhub.get("basic_financials") or {}
    fh_earnings_calendar = finnhub.get("earnings_calendar", {}).get("earningsCalendar", [])
    fh_recommendation_trends = finnhub.get("recommendation_trends", [])
    fh_earnings_surprise = finnhub.get("earnings_surprise", [])
    fh_insider_sentiment = finnhub.get("insider_sentiment", {}).get("data", [])
    fh_financials_reported_data = finnhub.get("financials_reported", {}).get("data", [])

    now = datetime.now()

    # Price action with Yahoo fallbacks
    curr_price = fh_quote.get("c") or yahoo.get("regularMarketPrice") or yahoo.get("currentPrice") or "N/A"
    day_high = fh_quote.get("h") or yahoo.get("regularMarketDayHigh") or yahoo.get("dayHigh") or "N/A"
    day_low = fh_quote.get("l") or yahoo.get("regularMarketDayLow") or yahoo.get("dayLow") or "N/A"

    output_parts = [f"### CANDIDATE EVALUATION DATA: {symbol}"]

    # Company Profile & Overview
    output_parts.append(f"**Company Profile & Overview:**")
    output_parts.append(f"- Name: {fh_profile.get('name', yahoo.get('longName', symbol))}")
    output_parts.append(f"- Industry: {fh_profile.get('finnhubIndustry', yahoo.get('industry', 'N/A'))}")
    output_parts.append(f"- Sector: {yahoo.get('sector', 'N/A')}")
    output_parts.append(f"- Country: {fh_profile.get('country', yahoo.get('country', 'N/A'))}")
    output_parts.append(f"- Website: {fh_profile.get('weburl', yahoo.get('website', 'N/A'))}")
    output_parts.append(f"- Market Cap: ${fh_profile.get('marketCapitalization', yahoo.get('marketCap', 'N/A'))}M")
    if yahoo.get('longBusinessSummary'):
        output_parts.append(f"- Business Summary: {yahoo['longBusinessSummary'][:500]}...") # Truncate for brevity

    # Price Action & Volatility
    output_parts.append(f"\n**Price Action & Volatility:**")
    output_parts.append(f"- Current Price: ${curr_price} (Change: {fh_quote.get('dp', 'N/A')}% daily)")
    output_parts.append(f"- Day Range: ${day_low} - ${day_high}")
    output_parts.append(f"- 52-Week Range: ${fh_financials.get('52WeekLow', yahoo.get('fiftyTwoWeekLow', 'N/A'))} - ${fh_financials.get('52WeekHigh', yahoo.get('fiftyTwoWeekHigh', 'N/A'))}")
    output_parts.append(f"- Beta: {fh_financials.get('beta', yahoo.get('beta', 'N/A'))}")
    output_parts.append(f"- Avg Volume (10-day): {yahoo.get('averageVolume10days', 'N/A')}")

    # Key Fundamentals
    output_parts.append(f"\n**Key Fundamentals:**")
    output_parts.append(f"- P/E (TTM): {fh_financials.get('peTTM', yahoo.get('trailingPE', 'N/A'))} | Forward P/E: {fh_financials.get('forwardPE', yahoo.get('forwardPE', 'N/A'))}")
    output_parts.append(f"- EPS (TTM): {fh_financials.get('epsTTM', yahoo.get('trailingEps', 'N/A'))} | Forward EPS: {fh_financials.get('epsForward', yahoo.get('forwardEps', 'N/A'))}")
    output_parts.append(f"- PEG Ratio: {fh_financials.get('pegTTM', yahoo.get('pegRatio', 'N/A'))}")
    output_parts.append(f"- ROE: {fh_financials.get('roeTTM', yahoo.get('returnOnEquity', 'N/A'))}% | ROA: {fh_financials.get('roaTTM', yahoo.get('returnOnAssets', 'N/A'))}%")
    output_parts.append(f"- Profit Margin: {fh_financials.get('netProfitMarginTTM', yahoo.get('profitMargins', 'N/A'))}% | Operating Margin: {fh_financials.get('operatingMarginTTM', yahoo.get('operatingMargins', 'N/A'))}%")
    output_parts.append(f"- Gross Margin: {fh_financials.get('grossMarginTTM', yahoo.get('grossMargins', 'N/A'))}%")
    output_parts.append(f"- Revenue (TTM): ${yahoo.get('totalRevenue', 'N/A')} | Revenue Growth (YoY): {fh_financials.get('revenueGrowthTTMYoy', 'N/A')}%")
    output_parts.append(f"- Current Ratio: {fh_financials.get('currentRatioQuarterly', yahoo.get('currentRatio', 'N/A'))} | Quick Ratio: {fh_financials.get('quickRatioQuarterly', yahoo.get('quickRatio', 'N/A'))}")
    output_parts.append(f"- Debt/Equity: {fh_financials.get('totalDebt/totalEquityQuarterly', yahoo.get('debtToEquity', 'N/A'))}")
    output_parts.append(f"- Book Value Per Share: {fh_financials.get('bookValuePerShareQuarterly', yahoo.get('bookValue', 'N/A'))}")

    # Analyst Ratings
    output_parts.append(f"\n**Analyst Ratings:**")
    output_parts.append(f"- Recommendation: {yahoo.get('recommendationKey', 'N/A')} ({yahoo.get('recommendationMean', 'N/A')} average)")
    output_parts.append(f"- Target Price: ${yahoo.get('targetMeanPrice', 'N/A')} (High: ${yahoo.get('targetHighPrice', 'N/A')} | Low: ${yahoo.get('targetLowPrice', 'N/A')})")
    output_parts.append(f"- Number of Opinions: {yahoo.get('numberOfAnalystOpinions', 'N/A')}")
    
    # Recommendation Trends (Finnhub)
    if fh_recommendation_trends:
        output_parts.append(f"\n**Recent Recommendation Trends (Last 4 Periods):**")
        for trend in fh_recommendation_trends[:4]:
            output_parts.append(f"- {trend.get('period')}: Strong Buy={trend.get('strongBuy')}, Buy={trend.get('buy')}, Hold={trend.get('hold')}, Sell={trend.get('sell')}, Strong Sell={trend.get('strongSell')}")

    # Earnings Calendar
    if fh_earnings_calendar:
        output_parts.append(f"\n**Upcoming Earnings (Next 2):**")
        for entry in fh_earnings_calendar[:2]:
            output_parts.append(f"- Date: {entry.get('date')} (Q{entry.get('quarter')} {entry.get('year')}) | Est EPS: {entry.get('epsEstimate')} | Est Revenue: ${entry.get('revenueEstimate')}")

    # Earnings Surprise (Finnhub)
    if fh_earnings_surprise:
        output_parts.append(f"\n**Recent Earnings Surprises (Last 4 Quarters):**")
        for surprise in fh_earnings_surprise[:4]:
            output_parts.append(f"- {surprise.get('period')}: Actual EPS={surprise.get('actual')}, Est EPS={surprise.get('estimate')}, Surprise={surprise.get('surprise')}, Surprise%={surprise.get('surprisePercent')}%")

    # Insider Sentiment (Finnhub)
    if fh_insider_sentiment:
        output_parts.append(f"\n**Recent Insider Activity (Last 4 Months):**")
        for sentiment in fh_insider_sentiment[:4]:
            output_parts.append(f"- {sentiment.get('year')}-{sentiment.get('month')}: Change={sentiment.get('change')} shares, MSPR={sentiment.get('mspr')}")

    # Recent Financial Filings (Finnhub)
    reported_items = []
    if isinstance(fh_financials_reported_data, list):
        for rep in fh_financials_reported_data:
            filed_str = rep.get("filedDate") or rep.get("acceptedDate") or ""
            filed_dt = parse_date(filed_str)

            if filed_dt and (now - filed_dt).days <= FINANCIALS_MAX_DAYS:
                form = rep.get("form", "N/A")
                year = rep.get("year", "N/A")
                quarter = rep.get("quarter", 0)
                q_label = f"Q{quarter}" if quarter else "FY"
                reported_items.append(f"- [{filed_str[:10]}] Form {form} ({year} {q_label})")

    if reported_items:
        output_parts.append(f"\n**Recent Financial Filings (Last {FINANCIALS_MAX_DAYS} days):**")
        output_parts.extend(reported_items)
    else:
        output_parts.append(f"\nNo financial filings within the last {FINANCIALS_MAX_DAYS} days.")

    # Technical & Series Metrics (twelvedata)
    tech_indicators = []
    
    # Consolidate twelvedata's MACD and Time Series
    if twelvedata.get("macd_1day") and twelvedata["macd_1day"].get("values"):
        latest_macd = twelvedata["macd_1day"]["values"][0]
        tech_indicators.append(f"- **MACD**: MACD={latest_macd.get('macd')}, Signal={latest_macd.get('macd_signal')}, Hist={latest_macd.get('macd_hist')} (as of {latest_macd.get('datetime')})")
    
    if twelvedata.get("time_series_1day") and twelvedata["time_series_1day"].get("values"):
        latest_ts = twelvedata["time_series_1day"]["values"][0]
        tech_indicators.append(f"- **Latest Trade**: Open={latest_ts.get('open')}, High={latest_ts.get('high')}, Low={latest_ts.get('low')}, Close={latest_ts.get('close')}, Volume={latest_ts.get('volume')} (as of {latest_ts.get('datetime')})")


    if tech_indicators:
        output_parts.append(f"\n**Technical & Series Metrics (Latest Available):**")
        output_parts.extend(tech_indicators)
    else:
        output_parts.append(f"\nNo recent technical metrics available from twelvedata.")


    # Recent Headlines & Catalysts
    news_items = []
    if isinstance(news, list):
        for article in news: # Include all news for higher density
            date_str = article.get("date_str", "Unknown Date")
            summary = article.get("summary", "").strip()
            if summary:
                news_items.append(f"- [{date_str}] {summary}") # No truncation for higher density

    if news_items:
        output_parts.append(f"\n**Recent Headlines & Catalysts:**")
        output_parts.extend(news_items)
    else:
        output_parts.append(f"\nNo recent news available.")

    return "\n".join(output_parts).strip()
