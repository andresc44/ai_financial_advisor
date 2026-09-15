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
    # Extract ALL CAPS parameter values directly from imported params_dict
    FINANCIALS_MAX_DAYS = (
        params_dict.get("FINANCIALS_REPORTED_MAX_DAYS")
        or params_dict.get("financials_reported_max_days", 365)
    )
    SERIES_MAX_DAYS = (
        params_dict.get("SERIES_MAX_DAYS")
        or params_dict.get("series_max_days", 30)
    )

    # Unwrap nested payload if keyed by ticker symbol
    if symbol in raw_data and isinstance(raw_data[symbol], dict):
        raw_data = raw_data[symbol]

    yahoo = raw_data.get("yahoo") or {}
    finnhub = raw_data.get("finnhub") or {}
    twelvedata = raw_data.get("twelvedata") or {}
    series_data = raw_data.get("series") or {}
    news = raw_data.get("news") or []

    fh_profile = finnhub.get("profile") or {}
    fh_quote = finnhub.get("quote") or {}
    fh_financials = finnhub.get("basic_financials") or {}
    fh_reported = finnhub.get("financials_reported") or {}

    now = datetime.now()

    # Price action with Yahoo fallbacks
    curr_price = fh_quote.get("c") or yahoo.get("regularMarketPrice") or yahoo.get("currentPrice") or "N/A"
    day_high = fh_quote.get("h") or yahoo.get("regularMarketDayHigh") or yahoo.get("dayHigh") or "N/A"
    day_low = fh_quote.get("l") or yahoo.get("regularMarketDayLow") or yahoo.get("dayLow") or "N/A"

    # Filter Financials Reported (Finnhub)
    reported_items = []
    if isinstance(fh_reported, dict) and isinstance(fh_reported.get("data"), list):
        for rep in fh_reported["data"]:
            filed_str = rep.get("filedDate") or rep.get("acceptedDate") or ""
            filed_dt = parse_date(filed_str)

            if filed_dt and (now - filed_dt).days <= FINANCIALS_MAX_DAYS:
                form = rep.get("form", "N/A")
                year = rep.get("year", "N/A")
                quarter = rep.get("quarter", 0)
                q_label = f"Q{quarter}" if quarter else "FY"
                reported_items.append(f"- [{filed_str[:10]}] Form {form} ({year} {q_label})")

    financials_formatted = (
        "\n".join(reported_items[:5])
        if reported_items
        else f"No financial filings within the last {FINANCIALS_MAX_DAYS} days."
    )

    # Filter Series & Technical Data
    tech_indicators = []

    if isinstance(twelvedata, dict):
        for metric, val in twelvedata.items():
            if isinstance(val, dict) and "values" in val:
                filtered_vals = []
                for entry in val.get("values", []):
                    entry_dt = parse_date(entry.get("datetime") or entry.get("date"))
                    if entry_dt and (now - entry_dt).days <= SERIES_MAX_DAYS:
                        filtered_vals.append(entry)

                latest_val = filtered_vals[0] if filtered_vals else "Out of date range"
                tech_indicators.append(f"- **{metric}**: {latest_val}")
            else:
                tech_indicators.append(f"- **{metric}**: {val}")

    if isinstance(series_data, list):
        filtered_series = [
            s
            for s in series_data
            if (dt := parse_date(s.get("datetime") or s.get("date")))
            and (now - dt).days <= SERIES_MAX_DAYS
        ]
        if filtered_series:
            tech_indicators.append(
                f"- **Series Data**: {len(filtered_series)} entries within {SERIES_MAX_DAYS} days."
            )

    tech_formatted = (
        "\n".join(tech_indicators)
        if tech_indicators
        else "No recent technical metrics available."
    )

    # Process News Headlines
    news_items = []
    if isinstance(news, list):
        for article in news[:4]:
            date_str = article.get("date_str", "Unknown Date")
            summary = article.get("summary", "").strip()
            if summary:
                news_items.append(f"- [{date_str}] {summary[:250]}...")

    news_formatted = "\n".join(news_items) if news_items else "No recent news available."

    return f"""
### CANDIDATE EVALUATION DATA: {symbol}

**Company Profile & Overview:**
- Name: {fh_profile.get('name', yahoo.get('longName', symbol))} | Industry: {fh_profile.get('finnhubIndustry', yahoo.get('industry', 'N/A'))}
- Market Cap: ${fh_profile.get('marketCapitalization', yahoo.get('marketCap', 'N/A'))}M

**Price Action & Volatility:**
- Current Price: ${curr_price} | Day High: ${day_high} | Day Low: ${day_low}
- 52-Week High/Low: ${fh_financials.get('52WeekHigh', yahoo.get('fiftyTwoWeekHigh', 'N/A'))} / ${fh_financials.get('52WeekLow', yahoo.get('fiftyTwoWeekLow', 'N/A'))}
- Beta: {fh_financials.get('beta', yahoo.get('beta', 'N/A'))}

**Key Fundamentals:**
- P/E (TTM): {fh_financials.get('peTTM', yahoo.get('trailingPE', 'N/A'))} | EPS (TTM): {fh_financials.get('epsTTM', yahoo.get('trailingEps', 'N/A'))}
- ROE: {fh_financials.get('roeTTM', yahoo.get('returnOnEquity', 'N/A'))}% | Profit Margin: {fh_financials.get('netProfitMarginTTM', yahoo.get('profitMargins', 'N/A'))}%

**Recent Financial Filings (<= {FINANCIALS_MAX_DAYS} days):**
{financials_formatted}

**Technical & Series Metrics (<= {SERIES_MAX_DAYS} days):**
{tech_formatted}

**Recent Headlines & Catalysts:**
{news_formatted}
""".strip()