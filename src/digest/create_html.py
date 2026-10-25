"""
create_html.py
==============
Turns one user's digest payload (digest_payload.py: universal + personal content) into the email: a mobile-first
HTML page (email_template.html, Jinja2) and a plain-text version for mail apps that don't show HTML.

Authorship: Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
- 2026-10-17: rewritten. The old file filled the template with placeholder data (made-up macro tiles, holding
  figures, and watchlist) and fetched the holdings itself. Everything now comes from the real payload, in this
  order: greeting and overview, portfolio risk, connected account, markets and macro news, holdings, swing ideas,
  long-term ideas. build_view does all the formatting here, so the template only lays out ready-made strings.
  Long text (each holding's reasoning and news, a story's market impact, a pick's extra figures) sits in
  <details> dropdowns. Apple Mail and Samsung Email collapse them; Gmail and Outlook can't, so they show them
  expanded under their labels (Gmail turns the tags into <u>, whose underline the template's CSS removes).
- 2026-10-17: each holding and idea gets a Finviz candlestick chart (shown as soon as its row is opened), and render_amp builds an AMP for Email version from the same template, whose dropdowns work in the Gmail
  app (DIGEST.AMP_EMAIL; setup in docs/watchdog.md). The design now avoids dark backgrounds with white text, which
  the Gmail app's dark mode recolours badly.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.users.admins import contact_admin, is_admin, notice_text
from src.market.fx_rates import CURRENCY_SYMBOLS, usd_rate_change, usd_rates
from src.tracking.idea_positions import idea_label
from src.config.parameters import params_dict
from src.personalized.holdings_state import _clean_float
from src.personalized.LLM_hold_instr import current_recommendation
from src.digest.glossary import GLOSSARY, HOLDINGS_ONLY_TERMS, IDEAS_ONLY_CATEGORY_NAMES, terms_in_use, visible_text
from src.personalized.portfolio_review import build_portfolio_summary

TEMPLATE_NAME = "email_template.html"
REPORTS_DIR = Path(__file__).resolve().parent / "html_reports"  # gitignored: each report holds the user's holdings
GMAIL_CLIP_BYTES = 102_000  # Gmail hides everything past ~102 KB of HTML behind "[Message clipped]"
AMP_MAX_BYTES = 200_000  # The AMP for Email spec's limit for the whole AMP part
# Gmail doesn't document its own AMP limit (some guides say 100 KB), so the AMP version is trimmed toward this
# like the HTML one; a ~90 KB digest is known to render
AMP_TARGET_BYTES = 100_000
CHART_WIDTH, CHART_HEIGHT = 466, 219  # Finviz's large daily chart (s=l)

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK, BODY, MUTED = "#0F2A44", "#334155", "#64748B"
TONES = {"pos": "#15803D", "neg": "#B91C1C", "warn": "#B45309", "flat": BODY, "ink": INK, "muted": MUTED}
# Claude-authored (2026-10-19): each tone's class in the template's <style> block, which the template uses instead of
# repeating style="color:..." on every figure (the same colours as TONES)
TONE_CLASSES = {"pos": "pos", "neg": "neg", "warn": "wn", "flat": "fl", "ink": "ink", "muted": "mu"}

# recommendation -> (label, badge text colour, badge colour, bar colour, sort order: most urgent first)
# 2026-10-18: the labels in LLM_hold_instr.RECOMMENDATIONS; an older payload's labels are translated first
SIGNALS = {
    "urgent_sell": ("Urgent sell", "#7F1D1D", "#FECACA", "#DC2626", 0),
    "risky": ("Risky", "#9A3412", "#FFEDD5", "#F97316", 1),
    # 2026-10-24 (Andres): profit sell green (Buy more's old colours), buy more blue.
    # 2026-10-25 (Andres): Buy more and Hold swapped blues: Buy more light sky blue, Hold the deeper blue
    "profit_sell": ("Profit sell", "#166534", "#DCFCE7", "#16A34A", 2),
    "buy_more": ("Buy more", "#075985", "#E0F2FE", "#38BDF8", 3),
    "hold": ("Hold", "#1E40AF", "#DBEAFE", "#2563EB", 4),
}
NOT_EVALUATED = ("Not evaluated", "#475569", "#F1F5F9", "#CBD5E1", 5)
# How a count of each reads, e.g. "2 urgent sells", "3 risky", "1 buy more"
SIGNAL_PLURALS = {"Urgent sell": "urgent sells", "Risky": "risky", "Profit sell": "profit sells",
                  "Buy more": "buy more", "Hold": "holds", "Not evaluated": "not evaluated"}
SENTIMENTS = {"Bullish": ("#166534", "#DCFCE7"), "Bearish": ("#991B1B", "#FEE2E2"), "Neutral": ("#475569", "#F1F5F9")}
PRIORITY_DOTS = {"HIGH": "#DC2626", "MEDIUM": "#F59E0B", "LOW": "#94A3B8"}
# CURRENCY_SYMBOLS (e.g. "C$" for CAD) is in fx_rates, which the portfolio review shares (2026-10-21)
# Claude-authored (2026-10-21): shown without cents, since a share or a portfolio runs to thousands or millions of them
WHOLE_UNIT_CURRENCIES = ("COP",)
# Claude-authored (2026-10-17): the currency of the holdings' money amounts (market value, daily and unrealized
# P/L, cost): pull_holdings_data converts them to USD (USD_MONEY_COLUMNS). Per-share prices stay in each holding's
# listing currency, its `currency` field. The swing and long-term ideas come from the US screener and the market
# dashboard is US ETFs, indexes, and WTI, so both are always USD (IDEAS_CURRENCY). Holdings without a currency are
# assumed USD (DEFAULT_CURRENCY), and build_view notes them in the run log.
MONEY_CURRENCY = "USD"
DEFAULT_CURRENCY = "USD"
IDEAS_CURRENCY = "USD"
ETF_TICKERS = ("SPY", "QQQ", "DIA", "IWM")  # Market rows priced in USD (the others are index levels or rates)
ANALYST_RATINGS = {
    "strong_buy": "Strong buy", "buy": "Buy", "hold": "Hold",
    "underperform": "Underperform", "sell": "Sell", "strong_sell": "Strong sell",
}
# Market dashboard names (macro_indicators.py) -> (short name, ticker) for a phone-width table
MARKET_NAMES = {
    "VIX Index": ("VIX", ""),
    "US Dollar Index (DXY)": ("US Dollar", "DXY"),
    "10Y Treasury Yield ($TNX)": ("10Y Yield", "TNX"),
    "Crude Oil (WTI)": ("Crude Oil", "WTI"),
    "NYSE Composite ($NYA)": ("NYSE Comp.", "NYA"),
}
# Claude-authored (2026-10-21): Markets & macro is Andres's "Global Macro Pulse" table: each indicator in its group,
# with a dot by params_dict["MARKET_SIGNALS"] (_signals). These are the dashboard's names (macro_indicators.py).
SP500, NASDAQ, DOW, NYSE, RUSSELL = (
    "S&P 500 (SPY)", "Nasdaq-100 (QQQ)", "Dow Jones (DIA)", "NYSE Composite ($NYA)", "Russell 2000 (IWM)")
VIX, DOLLAR, TEN_YEAR, OIL = "VIX Index", "US Dollar Index (DXY)", "10Y Treasury Yield ($TNX)", "Crude Oil (WTI)"
SIGNAL_GROUPS = ("Benchmarks", "Sentiment", "Macro")
SIGNAL_DOT_CLASSES = {"pos": "dp", "warn": "dw", "neg": "dn", "flat": "df"}  # Green, yellow, red, grey (template CSS)
SIGNAL_EMOJI = {"pos": "🟢", "warn": "🟡", "neg": "🔴", "flat": "⚪"}  # The same dots in the plain-text version
WINDOW_WORDS = {"1D": "1 day", "5D": "5 days", "1M": "1 month"}
# Claude-authored (2026-10-21): a " · "-separated phrase this short is kept on one line (_phrases). The Gmail app
# enlarges text about 1.75x on Andres's phone, where a card's line holds about 26 characters of 12 px text
PHRASE_MAX_CHARS = 24
PART_MAX_CHARS = 18  # The same for a part of a collapsed section's summary line, which is narrower
SECTION_ICONS = {  # (emoji, tile colour) beside each section's title
    "risk": ("🛡️", "#FFEDD5"), "markets": ("🌐", "#E0F2FE"), "holdings": ("💼", "#E0E7FF"),
    "swing": ("⚡", "#FEF3C7"), "long_term": ("🌱", "#DCFCE7"), "glossary": ("📖", "#F1F5F9"),
}
LONG_TERM_SCORES = (  # (sub-score, label, maximum), as in LLM_rec_instr.py
    ("valuation_score", "Valuation", 25), ("financial_health_score", "Financial health", 25),
    ("growth_momentum_score", "Growth momentum", 25), ("competitive_moat_score", "Competitive moat", 25),
)
SWING_SCORES = (
    # 2026-10-18: labels for the swing rubric's meaning (a pullback likely to bounce); the field names are unchanged
    ("technical_setup_score", "Pullback setup", 30), ("risk_reward_ratio_score", "Risk / reward", 30),
    ("momentum_volume_score", "Rebound signals", 20), ("catalyst_sentiment_score", "Catalysts & sentiment", 20),
)
SWING_EARNINGS_WARNING_DAYS = 14  # A swing pick reporting earnings this soon gets a visible warning
# Claude-authored (2026-10-20): swing_screen.setup_kind's setups, as the swing card names them
SWING_SETUPS = {"pullback": "Dip in an uptrend", "new_uptrend": "Fresh turn up"}
# Claude-authored (2026-10-24, Andres): the company blurb on each idea's "About the company" dropdown, cut from Yahoo's
# business summary (often over 1,000 characters). 10 ideas at this length add about 4 KB toward Gmail's limit.
ABOUT_MAX_CHARS = 280
# Words that end in a period without ending the sentence, besides initials ("C.H. Robinson", "U.S.")
NOT_SENTENCE_ENDS = {"inc", "corp", "co", "cos", "ltd", "plc", "bros", "no", "st", "jr", "sr", "dr", "mr", "mrs",
                     "vs", "etc", "approx"}


# ---------------------------------------------------------------------------
# Formatting (every value the template shows is a string made here; missing values show as "—")
# ---------------------------------------------------------------------------

DASH = "—"


def _num(value: Any) -> Optional[float]:
    """A float, or None for missing / NaN / non-numeric values."""
    return _clean_float(value) if not isinstance(value, bool) else None


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _money(value: Any, decimals: int = 2, symbol: str = "$") -> str:
    number = _num(value)
    if number is None:
        return DASH
    return f"{'-' if number < 0 else ''}{symbol}{abs(number):,.{decimals}f}"


def _signed_money(value: Any, decimals: int = 2, symbol: str = "$") -> str:
    number = _num(value)
    if number is None:
        return DASH
    return f"{'+' if number >= 0 else '-'}{symbol}{abs(number):,.{decimals}f}"


def _symbol(code: str) -> str:
    """e.g. 'C$' for CAD, or 'CHF ' for a currency without a symbol here."""
    return CURRENCY_SYMBOLS.get(code, f"{code} ")


def _money_in(value: Any, code: str, decimals: int = 2, signed: bool = False) -> str:
    """Claude-authored (2026-10-21): an amount in `code`, e.g. 'C$1,234.50' or 'COP$4,120,000' (no cents for COP)."""
    decimals = 0 if code in WHOLE_UNIT_CURRENCIES else decimals
    return (_signed_money if signed else _money)(value, decimals, symbol=_symbol(code))


def _narrow_money_in(value: Any, code: str, max_chars: int = 7) -> str:
    """
    Claude-authored (2026-10-21): a signed whole amount for a narrow figure box, which fits about 7 characters at a
    280 px phone width: '-A$626', else compact ('+$12.3K', '+A$123K', '+C$1.2K'), else compact without the symbol
    ('-1.98M', for pesos), which the box's card label names.
    """
    full = _money_in(value, code, 0, signed=True)
    number = _num(value)
    if number is None or len(full) <= max_chars or abs(number) < 1000:
        return full
    size = abs(number)
    divisor, suffix = next(((d, s) for limit, d, s in ((999_500_000, 1e9, "B"), (999_500, 1e6, "M"))
                            if size >= limit), (1e3, "K"))
    scaled, sign, symbol = size / divisor, "+" if number >= 0 else "-", _symbol(code).strip()

    def digits(decimals_under_10: int) -> str:  # e.g. 1.19 / 12.3 / 123 (3 significant), or 1.2 / 12 (2)
        decimals = decimals_under_10 if scaled < 10 else max(0, decimals_under_10 - 1) if scaled < 100 else 0
        return f"{scaled:.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{scaled:.0f}"

    for text in (f"{sign}{symbol}{digits(2)}{suffix}", f"{sign}{symbol}{digits(1)}{suffix}"):
        if len(text) <= max_chars:
            return text
    return f"{sign}{digits(2)}{suffix}"


def _rate_text(code: str, usd_per_unit: float) -> str:
    """
    Claude-authored (2026-10-21): '1 CAD = 0.711 USD', or per dollar when one unit is worth under a cent
    ('1 USD = 3,167 COP'), so the rate never shows as 0.000. Three decimals, at Andres's request.
    """
    if usd_per_unit < 0.01:
        return f"1 USD = {1 / usd_per_unit:,.0f} {code}"
    return f"1 {code} = {usd_per_unit:.3f} USD"


def _phrases(text: Any, limit: int = PHRASE_MAX_CHARS) -> Markup:
    """
    Claude-authored (2026-10-21): `text` (a Jinja filter) with each " · "-separated phrase of `limit` characters or
    fewer kept on one line, so where the text wraps a whole phrase like "1 AUD = 0.702 USD" moves to the next line
    instead of being split. The dot stays at the end of the line before. Longer phrases wrap as usual, since a
    phrase longer than a phone's line would push the email wider.
    """
    parts = [Markup('<span class="nw">%s</span>') % part if len(part) <= limit else escape(part)
             for part in str(text).split(" · ")]
    return Markup("&nbsp;&middot; ").join(parts)


def _signed_pct(value: Any, decimals: int = 1) -> str:
    """A value already in percent, e.g. 0.75 -> '+0.75%' (decimals=2)."""
    number = _num(value)
    return DASH if number is None else f"{number:+.{decimals}f}%"


def _fraction_pct(value: Any, decimals: int = 1, signed: bool = False) -> str:
    """A fraction as a percent, e.g. 0.36 -> '36.0%'."""
    number = _num(value)
    if number is None:
        return DASH
    return f"{number * 100:+.{decimals}f}%" if signed else f"{number * 100:.{decimals}f}%"


def _fixed(value: Any, decimals: int = 2) -> str:
    number = _num(value)
    return DASH if number is None else f"{number:,.{decimals}f}"


def _big_money(value: Any) -> str:
    number = _num(value)
    if number is None:
        return DASH
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(number) >= size:
            return f"${number / size:,.2f}{suffix}"
    return _money(number, 0)


def _shares(value: Any) -> str:
    number = _num(value)
    return DASH if number is None else f"{number:,.4f}".rstrip("0").rstrip(".")


def _tone(value: Any) -> str:
    number = _num(value)
    if number is None or abs(number) < 1e-9:
        return "flat"
    return "pos" if number > 0 else "neg"


def _day(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _short_date(value: Any) -> str:
    day = _day(value)
    return f"{day:%b} {day.day}" if day else DASH


def _in_days(days: Optional[int]) -> str:
    if days is None:
        return ""
    return "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"


def _stat(label: str, value: str, tone: str = "ink", sub: str = "", sub_tone: str = "muted") -> dict:
    """One cell of a stats row (template macro `stats`)."""
    return {"label": label, "value": value, "tone": tone, "sub": sub, "sub_tone": sub_tone}


def _rsi_stat(rsi: Optional[float]) -> dict:
    """The RSI 14 tile, as on the holding cards: overbought at 70+, oversold at 30 or less (Claude-authored, 2026-10-23)."""
    return _stat("RSI 14", _fixed(rsi, 0),
                 sub="Overbought" if rsi is not None and rsi >= 70 else "Oversold" if rsi is not None and rsi <= 30
                 else "Neutral" if rsi is not None else "",
                 sub_tone="warn" if rsi is not None and (rsi >= 70 or rsi <= 30) else "muted")


def _badge(text: str, fg: str, bg: str) -> dict:
    return {"text": text, "fg": fg, "bg": bg}


def _sentiment_badge(value: Any) -> Optional[dict]:
    sentiment = _text(value)
    if sentiment not in SENTIMENTS:
        return None
    return _badge(sentiment, *SENTIMENTS[sentiment])


def _chart(symbol: str, version: str) -> dict:
    """
    Finviz's daily candlestick chart (moving averages, trendlines, volume), as in the first version of the email.
    Finviz covers US listings; for symbols it doesn't know, it returns a "Chart not available" image rather than an
    error. `version` changes with each digest, so Gmail's image proxy can't show an earlier digest's cached chart.
    """
    finviz_symbol = symbol.replace("/", "-").replace(".", "-")
    return {
        "src": f"https://finviz.com/chart.ashx?t={finviz_symbol}&ty=c&ta=1&p=d&s=l&v={version}",
        "link": f"https://finviz.com/quote.ashx?t={finviz_symbol}",
        "alt": f"{symbol} chart",
        "width": CHART_WIDTH,
        "height": CHART_HEIGHT,
    }


def _quote_links(symbol: str) -> dict:
    yahoo_symbol = symbol.replace("/", "-")
    return {
        "yahoo": f"https://finance.yahoo.com/quote/{yahoo_symbol}",
        "finviz": f"https://finviz.com/quote.ashx?t={yahoo_symbol}",
        "tradingview": f"https://www.tradingview.com/symbols/{yahoo_symbol}/",
    }


# ---------------------------------------------------------------------------
# Holdings in USD
# ---------------------------------------------------------------------------

# Claude-authored (2026-10-17): holdings in other currencies are shown in US dollars at the current rate. GBX is a
# London price in pence (fx_rates turns it into GBP / 100). 2026-10-21: every currency, EUR and COP included; it
# used to be only CAD, AUD, and GBP, and an EUR holding's price stayed in euros beside values in dollars.
PER_SHARE_FIELDS = ("current_price", "valuation_price", "avg_cost_basis", "last_close")
MONEY_FIELDS = ("market_value", "total_cost_basis", "unrealized_pnl", "daily_pnl")  # pull_holdings_data's USD_MONEY_COLUMNS


def holdings_in_usd(holdings: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """
    Claude-authored (2026-10-17): the holdings with every amount in another currency (CAD, EUR, GBP or pence, AUD,
    COP, ...) in US dollars at the current rate (fx_rates.usd_rates: live from Yahoo, else the last cached rate, else
    the payload's own rate).
    - Per-share prices (in the listing's currency in the payload) are converted, and the original price and rate are
      kept as listed_price, listed_currency, and fx_rate_to_usd for the card.
    - Money amounts are already in USD at the payload's rate when it had one (PortfolioFetcher.convert_to_usd), so
      they are restated at the current rate; without a payload rate they were left unconverted, so they are
      converted now. Percentages don't change.
    - Portfolio weights are recomputed from the converted values.
    Returns (holdings, log lines, symbols that couldn't be converted). Those keep their own currency, and the email
    says so.
    """
    def code_of(holding: dict) -> str:
        return (_text(holding.get("currency")) or "").upper()

    codes = sorted({code_of(h) for h in holdings if code_of(h) not in ("", MONEY_CURRENCY)})
    if not codes:
        return holdings, [], []
    current = usd_rates(codes)
    converted, unconverted, used = [], [], {}
    for holding in holdings:
        code = code_of(holding)
        if code in ("", MONEY_CURRENCY):
            converted.append(holding)
            continue
        payload_rate = _num(holding.get("fx_rate_to_usd"))
        rate = current.get(code) or payload_rate
        if not rate:
            unconverted.append(str(holding.get("symbol")))
            converted.append(holding)
            continue
        holding = dict(holding)
        holding["listed_currency"], holding["listed_price"] = code, _price(holding)
        for field in PER_SHARE_FIELDS:
            if _num(holding.get(field)) is not None:
                holding[field] = holding[f"{field}_usd"] = _num(holding[field]) * rate
        factor = rate / payload_rate if payload_rate else rate
        for field in MONEY_FIELDS:
            if _num(holding.get(field)) is not None:
                holding[field] = _num(holding[field]) * factor
        holding["currency"], holding["fx_rate_to_usd"] = "USD", rate
        used[code] = rate
        converted.append(holding)

    values: dict[str, float] = {}
    for holding in converted:
        values[holding.get("symbol")] = values.get(holding.get("symbol"), 0.0) + (_num(holding.get("market_value")) or 0.0)
    total = sum(value for value in values.values() if value > 0)
    if total > 0:
        for holding in converted:
            holding["portfolio_weight_pct"] = round(values[holding.get("symbol")] / total * 100, 2)
    notes = [f"converted {code} holdings to USD at {_rate_text(code, rate)}" for code, rate in used.items()]
    if unconverted:
        notes.append(f"no exchange rate, left in their own currency: {', '.join(unconverted)}")
    return converted, notes, unconverted


def home_currency(payload: dict) -> tuple[str, float, list[str]]:
    """
    Claude-authored (2026-10-21): (the user's home currency, USD per unit of it at the current rate, log lines).
    The overview shows the portfolio in it (<NAME>_HOME_CURRENCY), and also in USD when it isn't USD. Payloads from
    before 2026-10-21, and a currency without any rate (fx_rates: live, else the last cached rate), stay in USD.
    """
    code = (_text(payload.get("home_currency")) or MONEY_CURRENCY).upper()
    if code == MONEY_CURRENCY:
        return MONEY_CURRENCY, 1.0, []
    rate = usd_rates([code]).get(code)
    if not rate:
        return MONEY_CURRENCY, 1.0, [f"no {code} exchange rate, so the overview is in USD instead of {code}"]
    return code, rate, []


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _period(payload: dict) -> str:
    period = _text(payload.get("period"))
    if period:
        return period
    return "Post-Market" if "Post-Market" in str(payload.get("subject", "")) else "Pre-Market"


def _clock(display: Optional[str], iso: Optional[str]) -> Optional[str]:
    """'Fri 9:50 PM AEST' from section_started_at's display string and ISO timestamp."""
    if not display or not iso:
        return None
    moment = datetime.fromisoformat(iso)
    zone = display.rsplit(" ", 1)[-1]
    return f"{moment:%a} {moment.hour % 12 or 12}:{moment:%M} {'AM' if moment.hour < 12 else 'PM'} {zone}"


def _greeting(started: dict, user_name: str) -> str:
    """By the hour in the user's home timezone, when their section of the digest was built."""
    iso = started.get("home_iso") or started.get("us_eastern_iso")
    hour = datetime.fromisoformat(iso).hour if iso else None
    if hour is None or hour < 5:
        return f"Hello, {user_name}"
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    return f"Good {part}, {user_name}"


def _prepared(started: dict) -> str:
    home = _clock(started.get("home"), started.get("home_iso"))
    eastern = _clock(started.get("us_eastern"), started.get("us_eastern_iso"))
    if started.get("home_timezone") == "America/New_York" or not home:
        home = None
    clocks = [clock for clock in (home, eastern) if clock]
    return f"Prepared {' · '.join(clocks)}" if clocks else ""


def _user_text(notice: dict, reader: Optional[str]) -> str:
    """A key or holdings notice worded for its reader (user_message); older payloads only have the admin's text."""
    if notice.get("user_message"):
        return str(notice["user_message"])
    return notice_text(str(notice.get("message", "")), "", reader)


def _issues(payload: dict) -> list[dict]:
    """
    Why parts of the digest are empty or incomplete (a key, a brokerage connection, a data source).
    2026-10-17: an admin sees each problem with its fix. Anyone else sees their own key and holdings problems with
    "Contact Andres to address this issue.", and one plain line for the shared data sources, whose messages name
    keys and plans only an admin can change (the admins are alerted about those separately).
    """
    reader = _text(payload.get("user_name"))
    issues = []
    for notice in payload.get("api_key_issues") or []:
        issues.append({"title": f"{notice.get('provider', 'API')} key", "message": _user_text(notice, reader)})
    for notice in payload.get("data_source_issues") or []:
        issues.append({"title": str(notice.get("source", "Holdings")), "message": _user_text(notice, reader)})
    shared = payload.get("universal_issues") or []
    if is_admin(reader):
        for notice in shared:
            issues.append({"title": str(notice.get("source", "Data source")), "message": str(notice.get("message", ""))})
    elif shared:
        sources = list(dict.fromkeys(str(notice.get("source") or "A data source") for notice in shared))
        named = sources[0] if len(sources) == 1 else ", ".join(sources[:-1]) + f" and {sources[-1]}"
        issues.append({"title": "Market data", "message": (
            f"{named} had {'a problem' if len(shared) == 1 else 'problems'} this run, so some ideas or market data "
            f"may be missing or incomplete. {contact_admin()}")})
    return issues


def _session_date(holdings: list[dict], weekday: bool = False) -> Optional[str]:
    """
    'Oct 15', or 'Thu, Oct 15' with weekday=True (2026-10-18, for the overview): the trading session the daily
    P/L covers (before the open, the previous trading day).
    """
    sessions = Counter(h.get("daily_pnl_session") for h in holdings if h.get("daily_pnl_session"))
    if not sessions:
        return None
    session = sessions.most_common(1)[0][0]
    day = _day(session)
    return f"{day:%a}, {_short_date(session)}" if weekday and day else _short_date(session)


def _has_value(text: str) -> bool:
    """False for display strings made only of placeholders, e.g. '— / —'."""
    return bool(text.replace(DASH, "").strip(" /–:"))


def _unrealized(lots: list[dict]) -> tuple[Optional[float], Optional[float]]:
    """($, %) over the lots with a known cost basis (a 0.0 cost basis means the broker didn't report it)."""
    known = [lot for lot in lots if (_num(lot.get("total_cost_basis")) or 0.0) > 0]
    cost = sum(_num(lot.get("total_cost_basis")) or 0.0 for lot in known)
    if cost <= 0:
        return None, None
    gain = sum(_num(lot.get("market_value")) or 0.0 for lot in known) - cost
    return gain, gain / cost * 100


def _lots_by_symbol(holdings: list[dict]) -> dict[str, list[dict]]:
    """One entry per stock: a stock held in several accounts arrives as one row (lot) per account."""
    lots: dict[str, list[dict]] = {}
    for holding in holdings:
        if _text(holding.get("symbol")):
            lots.setdefault(holding["symbol"], []).append(holding)
    return lots


def _own_currency(holding: dict) -> tuple[str, float]:
    """
    Claude-authored (2026-10-21): (the currency a holding card shows its money in, USD per unit of it). At Andres's
    request each card stays in the stock's own currency, and only the overview converts everything to the home
    currency. A London listing (GBX, quoted in pence) shows its value in pounds. A US listing, or a holding
    holdings_in_usd couldn't convert (no rate; still in its own currency, with a notice), is as it is.
    """
    code, rate = _text(holding.get("listed_currency")), _num(holding.get("fx_rate_to_usd"))
    if not code or not rate:
        return _text(holding.get("currency")) or MONEY_CURRENCY, 1.0
    return ("GBP", rate * 100) if code == "GBX" else (code, rate)


# Claude-authored (2026-10-21): a card's amounts longer than this are shortened ('COP$3.58M'): in the stock's own
# currency, a pesos holding's "COP$3,579,200" pushed a 375 px phone's view 19 px past its edge at the Gmail app's
# enlarged text (1.75x); 11 characters ("C$14,196.47") fit
CARD_MONEY_CHARS = 11


def _card_money(value: Any, code: str, signed: bool = False) -> str:
    """An amount on a holding card: in full, e.g. 'C$1,234.50', or compact when that's too long ('+COP$699K')."""
    full = _money_in(value, code, signed=signed)
    if len(full) <= CARD_MONEY_CHARS or _num(value) is None:
        return full
    short = _narrow_money_in(value, code, CARD_MONEY_CHARS)
    return short if signed else short.lstrip("+")


def _own_price(holding: dict, code: str) -> str:
    """The card's price in the stock's own currency, as listed: e.g. 'C$280.17', or in pence for London ('418.5p')."""
    listed = _text(holding.get("listed_currency"))
    if not listed:
        return _card_money(_price(holding), code)
    price = _num(holding.get("listed_price"))
    if listed == "GBX":
        return DASH if price is None else f"{price:,.2f}".rstrip("0").rstrip(".") + "p"
    return _card_money(price, code)


def _avg_cost(lots: list[dict], code: str, usd_per_unit: float, pence: bool) -> str:
    """
    Claude-authored (2026-10-23, Andres): the average price paid per share, in the card's currency (pence for a London
    listing, like its price), over the lots with a known cost basis (a 0.0 one wasn't reported); "" when none is
    known. The cost basis is in USD at the same rate the card converts back at, so this is the price as paid.
    """
    known = [lot for lot in lots if (_num(lot.get("total_cost_basis")) or 0.0) > 0 and (_num(lot.get("shares")) or 0.0) > 0]
    shares = sum(_num(lot.get("shares")) for lot in known)
    if not known or shares <= 0:
        return ""
    per_share = sum(_num(lot.get("total_cost_basis")) for lot in known) / shares / usd_per_unit
    if pence:
        return f"{per_share * 100:,.2f}".rstrip("0").rstrip(".") + "p"
    return _card_money(per_share, code)


def _day_label(holding: dict, today: Optional[date], period_day: str) -> str:
    """
    Claude-authored (2026-10-21): what a holding's daily change covers on its own exchange (pull_holdings_data
    measures each stock over its exchange's latest session): "live" while that session is still trading (the move
    so far, at the latest price); otherwise its latest full session, close to close: `period_day` ("prev. day"
    before the US open, "day" after the close) for the session that label means, "today" for another market's
    session completed today (Sydney's, before the US opens), and "prev. day" for an earlier one. Before, every card
    said `period_day`, and a pre-market digest called London's move mid-session "prev. day".
    """
    if str(holding.get("daily_pnl_live")) == "True":
        return "live"
    session = _day(holding.get("daily_pnl_session"))
    if session is None or today is None:
        return period_day
    if session >= today:
        return "day" if period_day == "day" else "today"
    return "prev. day"


DAY_LABELS = ("prev. day", "today", "day", "live")  # The order the holdings note explains them in


def _price(holding: dict) -> Optional[float]:
    """
    Claude-authored (2026-10-20): the price the holding's value and daily P/L are measured at (valuation_price: the
    session's close, or the live price while it's open). Payloads before 2026-10-20 only have the broker's price.
    """
    return _num(holding.get("valuation_price")) if _num(holding.get("valuation_price")) is not None else _num(
        holding.get("current_price"))


def _holding_card(symbol: str, lots: list[dict], day: str) -> dict:
    first = lots[0]  # Price, signal, news, and indicators are the same for every lot of a stock
    recommendation = current_recommendation(first.get("recommendation"))
    label, fg, bg, bar, order = SIGNALS.get(recommendation, NOT_EVALUATED)
    value = sum(_num(lot.get("market_value")) or 0.0 for lot in lots)
    shares = sum(_num(lot.get("shares")) or 0.0 for lot in lots)
    gain, gain_pct = _unrealized(lots)
    accounts = sorted({lot["account_name"] for lot in lots if _text(lot.get("account_name"))})
    listed_currency = _text(first.get("currency"))
    # 2026-10-21: the card's money in the stock's own currency (the amounts here are USD, so back at the same rate)
    currency, usd_per_unit = _own_currency(first)
    daily_pct = _num(first.get("daily_pnl_pct"))
    weight = _num(first.get("portfolio_weight_pct"))
    rsi = _num(first.get("rsi"))
    pence = _text(first.get("listed_currency")) == "GBX"
    avg_cost = _avg_cost(lots, currency, usd_per_unit, pence)  # 2026-10-23, Andres: the average price paid

    stats = [
        _stat(f"Price · {'pence' if pence else currency}", _own_price(first, currency),
              sub=f"{_signed_pct(daily_pct, 2)} {day}" if daily_pct is not None else "", sub_tone=_tone(daily_pct)),
        _stat(f"Unrealized · {currency}", _card_money(gain / usd_per_unit if gain is not None else None, currency,
                                                      signed=True), _tone(gain), _signed_pct(gain_pct), _tone(gain)),
        _rsi_stat(rsi),
    ]
    # digest_fields._ma_status: "Above 21 EMA (+1.2%)", or the 50-day average when there was no EMA, or "N/A"
    trend = re.match(r"^(Above|Below) (.+?) \(([+-]?[\d.]+)%\)$", str(first.get("ma_status") or ""))
    if trend:
        stats.append(_stat(f"vs {trend.group(2)}", f"{float(trend.group(3)):+.1f}%", _tone(float(trend.group(3))),
                           trend.group(1)))
    else:
        stats.append(_stat("Trend", DASH))

    # 2026-10-25 (Andres): the reasoning's last sentence, worded shorter, e.g. "Volatility 23% a year, daily range 1.9% (ATR)."
    volatility = []
    if _num(first.get("annualized_vol")) is not None:
        volatility.append(f"Volatility {_num(first.get('annualized_vol')):.0f}% a year")
    if _num(first.get("natr")) is not None:
        volatility.append(f"daily range {_num(first.get('natr')):.1f}% (ATR)")
    volatility_line = ", ".join(volatility)
    volatility_line = volatility_line[:1].upper() + volatility_line[1:] + "." if volatility_line else ""

    catalysts = []
    for event in first.get("catalysts") or []:
        days = event.get("days_until")
        catalysts.append({
            "when": _in_days(days).capitalize() or _short_date(event.get("event_date")),
            "text": f"{event.get('label') or event.get('event_type') or 'Event'} · {_short_date(event.get('event_date'))}",
        })

    news = [
        {
            "text": str(item.get("text", "")),
            "priority": str(item.get("priority") or "").upper(),
            "dot": PRIORITY_DOTS.get(str(item.get("priority") or "").upper(), PRIORITY_DOTS["LOW"]),
            "url": _text(item.get("source_url")),
        }
        for item in first.get("news_bullets") or []
        if _text(item.get("text"))
    ]

    return {
        "symbol": symbol,
        "name": _text(first.get("description")) or "",
        "value": _card_money(value / usd_per_unit, currency),
        "weight": f"{weight:.1f}% of portfolio" if weight is not None else "",
        # Claude-authored (2026-10-17): the collapsed row's right-hand line, e.g. "33.6% · +0.82%"
        "weight_short": f"{weight:.1f}%" if weight is not None else "",
        # 2026-10-21: `label` says which session the move covers on the stock's own exchange (_day_label)
        "day": {"text": _signed_pct(daily_pct, 2) if daily_pct is not None else "", "tone": _tone(daily_pct),
                "label": day, "session": _text(first.get("daily_pnl_session"))},
        "weight_pct": weight or 0.0,
        "sector": _text(first.get("sector")),  # 2026-10-25: for the risk section's sector chart
        "meta": " · ".join([f"{_shares(shares)} share{'' if shares == 1 else 's'}"]
                           + ([f"avg cost {avg_cost}"] if avg_cost else []) + accounts),
        "signal": _badge(label, fg, bg),
        "signal_key": recommendation if recommendation in SIGNALS else None,
        "signal_bar": bar,
        "order": order,
        "sentiment": _sentiment_badge(first.get("sentiment")),
        "stats": stats,
        "catalysts": catalysts,
        # The soonest event, for the overview's "Earnings soon" line
        "catalyst_days": next((event.get("days_until") for event in first.get("catalysts") or []
                               if isinstance(event.get("days_until"), int)), None),
        "catalyst_date": next((_short_date(event.get("event_date")) for event in first.get("catalysts") or []
                               if isinstance(event.get("days_until"), int)), ""),
        # Claude-authored (2026-10-21): said when no earnings date is known at all (often for listings outside the
        # US); a date past the cards' CATALYST_WINDOW_DAYS isn't shown or mentioned. Payloads from before
        # next_earnings_date existed say nothing.
        # Claude-authored (2026-10-22): the Finnbot idea it was bought from, e.g. "Swing idea · Oct 21 · entry $100.00
        # · stop $95.00 · target $112.00" (src/tracking/idea_positions.py); "" for the rest
        "idea": idea_label(first.get("idea_origin")),
        "earnings_note": ("No earnings date available" if not catalysts and "next_earnings_date" in first
                          and not _text(first.get("next_earnings_date")) else ""),
        "rationale": _text(first.get("recommendation_rationale")) or "",
        "volatility": volatility_line,
        "news": news,
        "links": _quote_links(symbol),
        "currency": currency,  # 2026-10-21: the currency of the card's money (the stock's own), see _own_currency
        "currency_assumed": listed_currency is None,
        "us_listing": (first.get("listed_currency") or currency) == "USD",  # Finviz charts cover US listings
        "chart": None,  # Set by build_view
        # Claude-authored (2026-10-17): the row's id in the AMP version, so the overview's chips can open it
        "anchor": "h-" + re.sub(r"[^a-z0-9]", "-", symbol.lower()),
    }


def _scale_news(cards: list[dict]) -> None:
    """
    Claude-authored (2026-10-22, Andres's rule): with more holdings than DIGEST.NEWS_FULL_UP_TO_HOLDINGS, each card
    keeps its NEWS_LARGE_PORTFOLIO_BULLETS most important stories (they come highest priority first), except the
    stocks flagged NEWS_FULL_SIGNALS (urgent sell, risky), which keep all of theirs. A planned cut, so a big
    portfolio fits Gmail's size limit before fit_for_gmail has to drop more.
    """
    digest = params_dict["DIGEST"]
    if len(cards) <= digest.get("NEWS_FULL_UP_TO_HOLDINGS", 10):
        return
    keep, full = digest.get("NEWS_LARGE_PORTFOLIO_BULLETS", 1), set(digest.get("NEWS_FULL_SIGNALS") or [])
    for card in cards:
        if card["signal_key"] not in full:
            card["news"] = card["news"][:keep]


def _holding_cards(holdings: list[dict], day: str, today: Optional[date] = None) -> list[dict]:
    """Most urgent signal first (urgent sell, risky, profit sell, buy more, hold), then largest position."""
    cards = [_holding_card(symbol, lots, _day_label(lots[0], today, day))
             for symbol, lots in _lots_by_symbol(holdings).items()]
    return sorted(cards, key=lambda card: (card["order"], -card["weight_pct"]))


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _signal_count(count: int, label: str) -> str:
    """e.g. '1 urgent sell', '2 urgent sells', '3 risky' (SIGNAL_PLURALS)."""
    return f"{count} {label.lower() if count == 1 else SIGNAL_PLURALS.get(label, label.lower())}"


def _holdings_note(cards: list[dict], holdings: list[dict], day: str) -> str:
    """
    The line atop the open holdings section, which also dates the price changes on the cards. 2026-10-21: each
    stock's money is in its own currency, and each day label in use is explained, e.g. "Prev. day = the Oct 20
    session · Today = the Oct 21 session · Live = still trading" for a portfolio across several markets.
    """
    own = {card["currency"] for card in cards}
    notes = ["Most urgent signal first", f"Values in {own.pop()}" if len(own) == 1 else "Values in each stock's currency"]
    for label in DAY_LABELS:
        sessions = Counter(card["day"]["session"] for card in cards
                           if card["day"]["label"] == label and card["day"]["text"] and card["day"]["session"])
        if label == "live" and any(card["day"]["label"] == "live" for card in cards):
            notes.append("Live = still trading")
        elif sessions:
            notes.append(f"{label.capitalize()} = the {_short_date(sessions.most_common(1)[0][0])} session")
    if len(notes) == 2 and _session_date(holdings):  # No card has a daily change: date the period's label anyway
        notes.append(f"{day.capitalize()} = the {_session_date(holdings)} session")
    return " · ".join(notes)


def _part(text: str, tone: Optional[str] = None, dot: Optional[str] = None, prefix: str = "", suffix: str = "",
          whole: Optional[bool] = None) -> dict:
    """
    One piece of a collapsed section's summary line: `text` coloured by `tone` (between an uncoloured `prefix` and
    `suffix`), or led by a dot of colour `dot`.
    """
    # 2026-10-21: `whole` keeps the part on one line (by default, when it's PART_MAX_CHARS or shorter); False where
    # keeping it whole was measured to add a line at the Gmail app's enlarged text
    return {"text": text, "tone": tone, "dot": dot, "prefix": prefix, "suffix": suffix,
            "whole": len(prefix + text + suffix) <= PART_MAX_CHARS if whole is None else whole}


def _signal_counts(cards: list[dict]) -> list[dict]:
    """The holdings section's summary, e.g. '● 6 risky · ● 4 holds', most urgent first."""
    counts: dict[str, list] = {}
    for card in cards:
        counts.setdefault(card["signal"]["text"], [0, card["signal_bar"]])[0] += 1
    return [_part(_signal_count(count, label), dot=color) for label, (count, color) in counts.items()]


FLAGGED_SIGNALS = ("urgent_sell", "risky", "profit_sell")  # The "Flagged" figure: reasons to sell or watch
ATTENTION_SIGNALS = FLAGGED_SIGNALS + ("buy_more",)  # The "Needs attention" chips: anything to act on


def _currency_week(code: str, week: Optional[tuple[float, float]]) -> str:
    """
    Claude-authored (2026-10-21): how the home currency moved against the dollar over the past week, e.g.
    "AUD +0.6% this week" (it bought 0.6% more dollars), shown after the exchange rate. `week` is (USD per unit now,
    a week ago) from fx_rates.usd_rate_change; "" without it. 2026-10-21: condensed at Andres's request from a
    separate line that also gave the effect on the USD holdings.
    """
    if not week:
        return ""
    move = (week[0] / week[1] - 1) * 100
    return f"{code} flat this week" if abs(move) < 0.05 else f"{code} {move:+.1f}% this week"


def _portfolio(holdings: list[dict], beta: Any, cards: list[dict], idea_counts: tuple[int, int],
               day: str, home: tuple[str, float] = (MONEY_CURRENCY, 1.0),
               home_week: Optional[tuple[float, float]] = None) -> Optional[dict]:
    """
    The overview card. 2026-10-17 (UI pass): built to answer "do I need to do anything?" at a glance: the day's
    change as a pill, flagged positions and their share of the portfolio, chips for each flagged stock (most urgent
    first), and holdings reporting earnings soon.
    2026-10-21: its money (value, the day's change, unrealized P/L) is in the user's home currency (`home`: code and
    USD per unit), with the value in USD, the rate and the home currency's move against the dollar over the past
    week (`home_week`) on the line below when that isn't USD.
    """
    if not holdings:
        return None
    totals = build_portfolio_summary(holdings, _num(beta))["portfolio"]
    daily_pnl = sum(_num(h.get("daily_pnl")) or 0.0 for h in holdings)
    gain, gain_pct = _unrealized(holdings)
    long_n, swing_n = idea_counts
    session = _session_date(holdings, weekday=True)  # e.g. "Day · Fri, Oct 16"
    # 2026-10-21: the cards' day labels (_day_label); stocks on markets in different sessions (London live beside
    # New York's last close) share no single date, so the day's change is then over each market's latest session
    labels = {card["day"]["label"] for card in cards if card["day"]["text"]}
    day_label = labels.pop() if len(labels) == 1 else day if not labels else "latest sessions"
    flagged = [card for card in cards if card["signal_key"] in FLAGGED_SIGNALS]
    flagged_share = sum(card["weight_pct"] for card in flagged)
    attention = [card for card in cards if card["signal_key"] in ATTENTION_SIGNALS]  # 2026-10-18: buy more too
    flagged_tone = ("neg" if any(card["signal_key"] == "urgent_sell" for card in flagged)
                    else "warn" if flagged else "pos")
    earnings = sorted(
        ((card["catalyst_days"], card["symbol"], card["catalyst_date"], card["anchor"]) for card in cards
         if card["catalyst_days"] is not None),
    )
    daily_pct = _num(totals["daily_pnl_pct"])
    code, usd_per_unit = home
    total = _num(totals["total_market_value"])
    value = _money_in(total / usd_per_unit if total is not None else None, code)
    return {
        "label": f"Your portfolio · {code}",
        "currency": code,
        "value": value,
        # Claude-authored (2026-10-21): a long total (a million dollars, or pesos) gets a smaller font, so it fits a
        # phone. The Gmail app enlarges email text by the phone's font-size setting (Andres's about 1.5-1.75x) and
        # clips what doesn't fit: "A$12,060.73" at 34 px was cut off, so 34 px is for 9 characters at most
        "value_size": 34 if len(value) <= 9 else 26 if len(value) <= 11 else 22 if len(value) <= 13 else 18,
        # e.g. "$102,345.67 USD · 1 AUD = 0.703 USD · AUD +0.6% this week", when the home currency isn't USD
        "usd_value": (" · ".join(part for part in (f"{_money(total)} {MONEY_CURRENCY}", _rate_text(code, usd_per_unit),
                                                   _currency_week(code, home_week)) if part)
                      if code != MONEY_CURRENCY else ""),
        # e.g. "▼ $17.29 · 0.20%" in a red pill
        "change": (f"{'▲' if daily_pnl >= 0 else '▼'} {_money_in(abs(daily_pnl) / usd_per_unit, code)}"
                   + (f" · {abs(daily_pct):.2f}%" if daily_pct is not None else "")),
        "change_pct": _signed_pct(totals["daily_pnl_pct"], 2),
        "change_tone": _tone(daily_pnl),
        "session": ("Latest session per market" if day_label == "latest sessions"
                    else f"{day_label.capitalize()} · {session}" if session else day_label.capitalize()),
        "day_label": day_label,  # For the inbox preview, e.g. "(+0.42% prev. day)"
        "stats": [
            _stat("Unrealized", _narrow_money_in(gain / usd_per_unit if gain is not None else None, code),
                  _tone(gain), _signed_pct(gain_pct), _tone(gain)),
            _stat("Flagged", f"{len(flagged)} of {len(cards)}", flagged_tone,
                  f"{flagged_share:.0f}% of value" if flagged else "none flagged"),
            _stat("New ideas", str(long_n + swing_n), sub=f"{long_n} long · {swing_n} swing"),
        ],
        # Each chip and earnings entry carries its holding's anchor: tapping it in the AMP version opens that row
        "attention": [card["signal"] | {"text": card["symbol"], "anchor": card["anchor"]} for card in attention],
        "earnings": [_badge(f"{symbol} {when}", INK, "#F1F5F9") | {"anchor": anchor}
                     for _, symbol, when, anchor in earnings],
    }


def _beta_view(beta: Any) -> Optional[dict]:
    value = _num(beta)
    if value is None or value == 0:  # digest_fields.portfolio_beta returns 0.0 when no holding had a beta
        return None
    if value < 0.8:
        label, tone = "Below-market risk", "pos"
    elif value < 1.2:
        label, tone = "Moves with the market", "flat"
    elif value < 1.6:
        label, tone = "Above-market risk", "warn"
    else:
        label, tone = "High market risk", "neg"
    return {
        "value": f"{value:.2f}",
        "label": label,
        "tone": tone,
        "text": f"When the S&P 500 moves 1%, this portfolio has typically moved about {value:.1f}%.",
    }


def _note_title(text: str, limit: int = 64) -> str:
    """
    A headline for a review note that has none (payloads from before 2026-10-17): its first sentence, cut at the
    first bracket, colon, or comma past 25 characters, then at a word boundary near the limit.
    """
    first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0].rstrip(".")
    cuts = [position for mark in (" (", ": ", ", ") if (position := first.find(mark)) >= 25]
    if cuts:
        first = first[:min(cuts)]
    if len(first) > limit:
        first = first[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return first


SECTOR_CHART_ROWS = 6  # Sectors shown by name; the rest are grouped as "Other"


def _sectors(cards: list[dict]) -> list[dict]:
    """
    Claude-authored (2026-10-25, Andres): the portfolio's share in each sector, largest first, for the risk section's
    bar chart: [{"name", "pct" ("62%" of the portfolio), "width" (bar width, % of the row, 100 for the largest)}]. A holding with no sector (a fund, say)
    counts as "Other" with the small sectors past SECTOR_CHART_ROWS. Empty when no holding has a weight.
    """
    shares: dict[str, float] = {}
    for card in cards:
        name = card.get("sector") or "Other"
        shares[name] = shares.get(name, 0.0) + card["weight_pct"]
    total = sum(shares.values())
    if total <= 0:
        return []
    named = sorted(((n, v) for n, v in shares.items() if n != "Other"), key=lambda item: (-item[1], item[0]))
    other = shares.get("Other", 0.0) + sum(v for _, v in named[SECTOR_CHART_ROWS:])
    rows = named[:SECTOR_CHART_ROWS] + ([("Other", other)] if other / total >= 0.005 else [])
    biggest = max(value for _, value in rows)  # The largest sector's bar fills the row, the others scale to it
    return [{"name": name, "pct": f"{value / total * 100:.0f}%", "width": max(1, round(value / biggest * 100))}
            for name, value in rows]


def _risk(cards: list[dict], beta: Any, review: list[dict]) -> Optional[dict]:
    if not cards and not review:
        return None
    weights: dict[Optional[str], list[float]] = {}
    for card in cards:
        weights.setdefault(card["signal_key"], []).append(card["weight_pct"])
    mix = []
    for key in list(SIGNALS) + [None]:
        if key in weights:
            label, _, _, bar, _ = SIGNALS.get(key, NOT_EVALUATED)
            share = sum(weights[key])
            mix.append({"label": label, "count": len(weights[key]), "pct": f"{share:.0f}%", "width": max(1, round(share)),
                        "color": bar})
    largest = max(cards, key=lambda card: card["weight_pct"]) if cards else None
    notes = [
        {
            "category": str(note.get("category", "NOTE")).upper(),
            "title": _text(note.get("title")) or _note_title(str(note["text"])),
            "text": str(note["text"]).strip(),
        }
        for note in review
        if _text(note.get("text"))
    ]
    beta_view = _beta_view(beta)
    watch = [note for note in notes if note["category"] == "WATCH"]
    summary = ([_part(f"Beta {beta_view['value']}"), _part(beta_view["label"], beta_view["tone"], whole=False)]
               if beta_view else [])
    if watch:
        summary.append(_part(f"{len(watch)} to watch"))
    return {
        "beta": beta_view,
        "mix": mix,
        "sectors": _sectors(cards),
        "largest": f"{largest['symbol']}, {largest['weight_pct']:.1f}% of the portfolio" if largest else "",
        "watch": watch,
        "notes": [note for note in notes if note["category"] != "WATCH"],
        "summary": summary,  # Claude-authored (2026-10-17): shown while the section is collapsed
    }


def _account(company: Any, holdings: list[dict], home: tuple[str, float] = (MONEY_CURRENCY, 1.0)) -> Optional[dict]:
    """The overview's connected-account dropdown; 2026-10-21: each account's value in the home currency (`home`)."""
    if not holdings and not _text(company):
        return None
    accounts: dict[str, dict] = {}
    for holding in holdings:
        name = _text(holding.get("account_name")) or "Account"
        entry = accounts.setdefault(name, {"symbols": set(), "value": 0.0})
        entry["symbols"].add(holding.get("symbol"))
        entry["value"] += _num(holding.get("market_value")) or 0.0
    rows = [
        {"name": name, "positions": f"{len(entry['symbols'])} position{'s' if len(entry['symbols']) != 1 else ''}",
         "value": _money_in(entry["value"] / home[1], home[0])}
        for name, entry in sorted(accounts.items(), key=lambda item: -item[1]["value"])
    ]
    company = _text(company) or "Brokerage"
    positions = len({holding.get("symbol") for holding in holdings})
    summary = f"{company} · {_plural(len(rows), 'account')} · {_plural(positions, 'position')}" if rows else company
    return {"company": company, "accounts": rows, "summary": summary}


def _figure(value: Any) -> Optional[float]:
    """Claude-authored (2026-10-21): a dashboard figure stored as a number or as text: 4.5, '+0.28%', '-4.0 bps'."""
    if isinstance(value, str):
        match = re.match(r"\s*([+-]?\d+(?:\.\d+)?)", value)
        return float(match.group(1)) if match else None
    return _num(value)


def _change(text: Any, unit: str = "%") -> dict:
    """
    A dashboard change such as '+0.28%' -> {'text', 'tone'}. 2026-10-21: `unit` "bp" (yields and the spread, in
    basis points) or "pt" (breadth, in percentage points) shows whole numbers, e.g. '+15bp'.
    """
    number = _figure(text)
    if number is None:
        return {"text": DASH, "tone": "muted"}
    return {"text": f"{number:+.1f}%" if unit == "%" else f"{number:+.0f}{unit}", "tone": _tone(number)}


def _market_name(name: str) -> tuple[str, str]:
    """'S&P 500 (SPY)' -> ('S&P 500', 'SPY'), with short names for the long ones."""
    if name in MARKET_NAMES:
        return MARKET_NAMES[name]
    match = re.match(r"^(.*?)\s*\((.+)\)$", name)
    return (match.group(1), match.group(2).lstrip("$")) if match else (name, "")


def _day_move(change: Optional[float], day: str, unit: str = "%", up_is_good: bool = True) -> tuple[str, str]:
    """A signal row's sub-line and its tone, e.g. ('+0.3% day', 'pos'); a rising VIX or yield isn't good news."""
    if change is None:
        return "", "muted"
    text = f"{change:+.1f}%" if unit == "%" else f"{change:+.0f} {unit}"
    return f"{text} {day}", _tone(change if up_is_good else -change)


def _signal(group: str, name: str, tone: str, grade: str, detail: str, value: str, sub: str = "",
            sub_tone: str = "muted") -> dict:
    """
    One market indicator's row. 2026-10-21: `grade` is its short label (e.g. "Uptrend", "Calm"), coloured like its dot,
    and `detail` what it rests on. The name links to its chart (DIGEST.INDICATOR_LINKS).
    """
    link = (params_dict["DIGEST"].get("INDICATOR_LINKS") or {}).get(name, "")
    return {"group": group, "name": name, "tone": tone, "dot": SIGNAL_DOT_CLASSES[tone], "emoji": SIGNAL_EMOJI[tone],
            "grade": grade, "detail": detail, "text": f"{grade}: {detail}" if detail else grade, "value": value,
            "sub": sub, "sub_tone": sub_tone, "link": link}


def _mood(market_data: dict) -> Optional[dict]:
    """
    Claude-authored (2026-10-21): VIX and Fear & Greed read together, from MARKET_SIGNALS["MOOD_MATRIX"] (VIX state by
    Fear & Greed state), as a "Market mood" signal row: grade = the matrix's label, detail = what it means. VIX comes
    from S&P 500 option prices and jumps within hours of a shock; Fear & Greed blends seven gauges and moves over days,
    so the pair tells a fresh scare from a settled mood. None without both.
    """
    levels = params_dict.get("MARKET_SIGNALS") or {}
    vix_metric = (market_data.get("yahoo_finance") or {}).get(VIX)
    vix = _figure(vix_metric.get("Latest")) if isinstance(vix_metric, dict) else None
    score = _figure((market_data.get("fear_and_greed") or {}).get("Score"))
    matrix = levels.get("MOOD_MATRIX") or {}
    if vix is None or score is None or not matrix:
        return None
    calm, panic = levels.get("VIX_CALM_BELOW", 20), levels.get("VIX_PANIC_ABOVE", 30)
    fear, greed = levels.get("FEAR_GREED_FEAR_BELOW", 45), levels.get("FEAR_GREED_GREED_ABOVE", 55)
    volatility = "calm" if vix < calm else "panic" if vix > panic else "elevated"
    mood = "greed" if score > greed else "fear" if score < fear else "neutral"
    label, tone, meaning = matrix[volatility][mood]
    return _signal("Sentiment", "Market mood", tone, label, meaning, f"VIX {vix:.1f}", f"F&G {score:.0f}")


def _signals(market_data: dict, day: str, today: Optional[date] = None) -> list[dict]:
    """
    Claude-authored (2026-10-21): one row per market indicator, in its group (SIGNAL_GROUPS), graded green, yellow,
    or red by params_dict["MARKET_SIGNALS"] (grey when there's no clear signal), with its value and move. Started from
    Andres's "Global Macro Pulse" table; the levels are set from research and 2 years of data (see parameters.py).
    The Nasdaq-100 and the Dow share a row, since one leading the other is the signal for both. An indicator missing
    from the dashboard has no row.
    """
    levels = params_dict.get("MARKET_SIGNALS") or {}
    yahoo = market_data.get("yahoo_finance") or {}

    def get(name: str, key: str) -> Optional[float]:
        metric = yahoo.get(name)
        return _figure(metric.get(key)) if isinstance(metric, dict) else None

    signals = []
    # Benchmarks
    price, ma50, ma200 = (get(SP500, key) for key in ("Latest", "50D MA", "200D MA"))
    if price is not None:
        tone, grade, detail = {
            (True, True): ("pos", "Uptrend", "above its 50- and 200-day averages"),
            (True, False): ("warn", "Recovering", "above its 50-day average, still below its 200-day"),
            (False, True): ("warn", "Pullback", "below its 50-day average, still above its 200-day"),
            (False, False): ("neg", "Downtrend", "below its 50- and 200-day averages"),
        }[(price > ma50, price > ma200)] if ma50 and ma200 else ("flat", "No trend data", "too little history")
        signals.append(_signal("Benchmarks", "S&P 500", tone, grade, detail, _money(price),
                               *_day_move(get(SP500, "1D %"), day)))
    window = levels.get("RELATIVE_WINDOW", "5D")
    gap, span = float(levels.get("RELATIVE_GAP_PTS", 2.0)), f"over {WINDOW_WORDS.get(window, window)}"

    def lead(first: str, second: str) -> Optional[float]:  # first's move minus second's, in percentage points
        a, b = get(first, f"{window} %"), get(second, f"{window} %")
        return None if a is None or b is None else a - b

    growth, broad, small = lead(NASDAQ, DOW), lead(NYSE, SP500), lead(RUSSELL, SP500)
    if growth is not None:
        tone, grade, detail = (("pos", "Risk-on", "growth stocks beat value") if growth >= gap else
                               ("warn", "Defensive", "value stocks beat growth") if growth <= -gap else
                               ("flat", "Even", "growth and value moved together"))
        signals.append(_signal("Benchmarks", "Nasdaq-100 vs Dow", tone, grade, detail, f"{growth:+.1f} pts", span))
    if broad is not None:
        tone, grade, detail = (("warn", "Concentrated", "the S&P beat the broad market: a few giants carry it")
                               if broad <= -gap else
                               ("flat", "Giants lagging", "the broad market beat the S&P") if broad >= gap else
                               ("pos", "Broad", "in step with the S&P: wide participation"))
        signals.append(_signal("Benchmarks", "NYSE vs S&P 500", tone, grade, detail, f"{broad:+.1f} pts", span))
    if small is not None:
        tone, grade, detail = (("neg", "Credit warning", "small caps lagged the S&P") if small <= -gap else
                               ("pos", "Confident", "small caps led the S&P") if small >= gap else
                               ("flat", "In step", "small caps moved with the S&P"))
        signals.append(_signal("Benchmarks", "Russell 2000 vs S&P", tone, grade, detail, f"{small:+.1f} pts", span))
    # Sentiment
    mood = _mood(market_data)
    if mood:
        signals.append(mood)
    vix = get(VIX, "Latest")
    if vix is not None:
        calm, panic = levels.get("VIX_CALM_BELOW", 20), levels.get("VIX_PANIC_ABOVE", 30)
        tone, grade, detail = (("pos", "Calm", f"under {calm:g}, its long-run average") if vix < calm else
                               ("neg", "High fear", f"over {panic:g}: big swings expected") if vix > panic else
                               ("warn", "Elevated", f"{calm:g} to {panic:g}: choppier markets"))
        signals.append(_signal("Sentiment", "VIX", tone, grade, detail, f"{vix:.2f}",
                               *_day_move(get(VIX, "1D %"), day, up_is_good=False)))
    put_call = market_data.get("put_call_ratio") or {}
    ratio, average = _figure(put_call.get("Ratio")), _figure(put_call.get("5D Avg"))
    if ratio is not None:
        low, high = levels.get("PUT_CALL_LOW_BELOW", 0.70), levels.get("PUT_CALL_HIGH_ABOVE", 1.00)
        tone, grade, detail = (("warn", "Complacent", f"under {low:.2f}: heavy call buying, often over-exuberance")
                               if ratio < low else
                               ("neg", "Fearful", f"over {high:.2f}: heavy put buying") if ratio > high else
                               ("pos", "Balanced", "puts and calls in a normal mix"))
        session = _day(put_call.get("Session"))
        detail += f" ({_short_date(session)} session)" if session else ""
        signals.append(_signal("Sentiment", "Put/call ratio", tone, grade, detail, f"{ratio:.2f}",
                               f"avg {average:.2f}" if average is not None else ""))
    fear_greed = market_data.get("fear_and_greed") or {}
    score = _figure(fear_greed.get("Score"))
    if score is not None:
        extreme_fear, fear = levels.get("FEAR_GREED_EXTREME_FEAR_BELOW", 25), levels.get("FEAR_GREED_FEAR_BELOW", 45)
        greed, extreme_greed = levels.get("FEAR_GREED_GREED_ABOVE", 55), levels.get("FEAR_GREED_EXTREME_GREED_ABOVE", 75)
        tone, grade, detail = (
            ("neg", "Extreme fear", "often near a bottom for patient buyers") if score < extreme_fear else
            ("warn", "Fear", "investors are wary") if score < fear else
            ("warn", "Extreme greed", "froth: often near a top") if score > extreme_greed else
            ("pos", "Greed", "investors are buying") if score > greed else
            ("pos", "Neutral", "a balanced mood"))
        previous = _figure(fear_greed.get("Previous close"))
        signals.append(_signal("Sentiment", "Fear & Greed", tone, grade, detail, f"{score:.0f}",
                               f"was {previous:.0f}" if previous is not None else ""))
    breadth = market_data.get("sp500_breadth") or {}
    share = _figure(breadth.get("% > 50MA"))
    if share is not None:
        weak, strong = levels.get("BREADTH_WEAK_BELOW", 40), levels.get("BREADTH_STRONG_ABOVE", 60)
        tone, grade = (("pos", "Strong") if share > strong else ("neg", "Weak") if share < weak else ("warn", "Mixed"))
        signals.append(_signal("Sentiment", "S&P breadth", tone, grade,
                               f"{share:.0f}% of S&P 500 stocks above their 50-day average", f"{share:.0f}%",
                               *_day_move(_figure(breadth.get("1D Change")), day, unit="pts")))
    # Macro
    dollar, dollar_5d = get(DOLLAR, "Latest"), get(DOLLAR, "5D %")
    if dollar is not None and dollar_5d is not None:
        tone, grade, detail = (
            ("pos", "Falling", "a tailwind for stocks") if dollar_5d <= -levels.get("DOLLAR_FALL_PCT", 1.0) else
            ("neg", "Rising fast", "a headwind for stocks") if dollar_5d >= levels.get("DOLLAR_FAST_RISE_PCT", 1.5) else
            ("flat", "Steady", "no big move"))
        signals.append(_signal("Macro", "US Dollar (DXY)", tone, grade, f"{detail}, {dollar_5d:+.1f}% in 5 days",
                               f"{dollar:.2f}", f"{dollar_5d:+.1f}% 5D"))
    ten, month = get(TEN_YEAR, "Latest"), get(TEN_YEAR, "1M bps")
    if ten is not None:
        move = levels.get("YIELD_MOVE_BPS", 25)
        tone, grade, detail = (("flat", "Steady", "") if month is None else
                               ("neg", "Rising", "pressure on stock valuations") if month >= move else
                               ("pos", "Falling", "support for stock valuations") if month <= -move else
                               ("flat", "Steady", "no big move"))
        detail += f"{', ' if detail else ''}{month:+.0f} bps in a month" if month is not None else ""
        signals.append(_signal("Macro", "10Y yield", tone, grade, detail, f"{ten:.2f}%",
                               *_day_move(get(TEN_YEAR, "1D bps"), day, unit="bps", up_is_good=False)))
    curve = market_data.get("yield_curve_10y_2y") or {}
    spread = None if curve.get("Error") else _figure(curve.get("Spread"))
    if spread is not None:
        last_inverted = _day(curve.get("Last inverted"))
        recent = (last_inverted is not None and today is not None
                  and (today - last_inverted).days <= levels.get("CURVE_RECENT_DAYS", 182))
        tone, grade, detail = (
            ("neg", "Inverted", "a recession warning") if spread < 0 else
            ("warn", "Recently un-inverted", f"positive since {_short_date(last_inverted)}: historically close to "
                                             "recessions") if recent else
            ("pos", "Positive", "no recession signal"))
        signals.append(_signal("Macro", "10Y–2Y spread", tone, grade, detail, f"{spread:+.2f}%",
                               *_day_move(_figure(curve.get("1D Change")), day, unit="bps")))
    oil, oil_5d = get(OIL, "Latest"), get(OIL, "5D %")
    if oil is not None and oil_5d is not None:
        breakout = levels.get("OIL_BREAKOUT_PCT", 10.0)
        tone, grade, detail = (("neg", "Spiking", "an inflation risk") if oil_5d >= breakout else
                               ("warn", "Plunging", "often a sign of weaker demand") if oil_5d <= -breakout else
                               ("flat", "Calm", "no sharp weekly move"))
        signals.append(_signal("Macro", "Crude oil (WTI)", tone, grade, f"{detail}, {oil_5d:+.1f}% in 5 days",
                               _money(oil), f"{oil_5d:+.1f}% 5D"))
    return signals


def _market(market_data: Any, day: str, today: Optional[date] = None) -> Optional[dict]:
    if not isinstance(market_data, dict) or not market_data:
        return None
    rows = []
    for name, metric in (market_data.get("yahoo_finance") or {}).items():
        if not isinstance(metric, dict) or _num(metric.get("Latest")) is None:
            continue
        short, ticker = _market_name(name)
        latest = _num(metric["Latest"])
        if "Yield" in name:
            last = f"{latest:.2f}%"
        elif "Crude" in name:
            last = f"${latest:.2f}"
        else:
            last = f"{latest:,.0f}" if latest >= 10_000 else f"{latest:,.2f}"
            if ticker in ETF_TICKERS:  # A US ETF's share price
                last = f"{CURRENCY_SYMBOLS[IDEAS_CURRENCY]}{latest:,.2f}"
        rows.append({"name": short, "ticker": ticker, "last": last, "d1": _change(metric.get("1D %"))})
    signals = _signals(market_data, day, today)
    if not rows and not signals:
        return None
    # Claude-authored (2026-10-21): the key signals (MARKET_SIGNALS["KEY_SIGNALS"], in that order) show first when the
    # section opens; the rest are under "All indicators", in their groups. `rows` feeds the collapsed summary and the
    # inbox preview (the 1-day / 5-day / 1-month table they used to fill was removed the same day).
    key_names = list((params_dict.get("MARKET_SIGNALS") or {}).get("KEY_SIGNALS") or [])
    by_name = {signal["name"]: signal for signal in signals}
    key = [by_name[name] for name in key_names if name in by_name]
    rest = [signal for signal in signals if signal["name"] not in key_names]
    groups = [{"name": group, "signals": [s for s in rest if s["group"] == group]} for group in SIGNAL_GROUPS]
    return {"rows": rows, "signals": signals, "key": key, "groups": [group for group in groups if group["signals"]],
            "mood": by_name.get("Market mood")}


def _stories(headline_news: Any) -> list[dict]:
    stories = []
    for story in (headline_news.get("macro_stories") or []) if isinstance(headline_news, dict) else []:
        if not isinstance(story, dict) or not _text(story.get("event_summary")):
            continue
        priority = str(story.get("priority") or "").upper()
        stories.append({
            "priority": priority.capitalize(),
            "dot": PRIORITY_DOTS.get(priority, PRIORITY_DOTS["LOW"]),
            "summary": story["event_summary"].strip(),
            "impact": _text(story.get("market_impact")) or "",
            "url": _text(story.get("source_url")),
        })
    return stories


def _analyst_line(pick: dict) -> str:
    parts = []
    rating = ANALYST_RATINGS.get(str(pick.get("analyst_rating") or "").lower())
    count = _num(pick.get("analyst_count"))
    if rating:
        parts.append(f"{rating}" + (f" ({count:.0f} analysts)" if count else ""))
    target, price = _num(pick.get("analyst_target_price")), _num(pick.get("current_price"))
    if target:
        upside = f" ({(target / price - 1) * 100:+.0f}%)" if price else ""
        parts.append(f"target {_money(target)}{upside}")
    return "Analysts: " + " · ".join(parts) if parts else ""


def _earnings(pick: dict, today: Optional[date]) -> tuple[str, Optional[int]]:
    day = _day(pick.get("next_earnings_date"))
    if not day:
        return "", None
    days = (day - today).days if today else None
    if days is not None and days < 0:
        return "", None
    return f"{_short_date(day)}" + (f" ({_in_days(days)})" if days is not None else ""), days


def _range(pick: dict) -> Optional[dict]:
    low, high, price = (_num(pick.get(key)) for key in ("52_week_low", "52_week_high", "current_price"))
    if not (low and high and price) or high <= low:
        return None
    position = min(97, max(3, round((price - low) / (high - low) * 100)))
    below_high = (1 - price / high) * 100
    note = "at its 52-week high" if below_high < 0.5 else f"{below_high:.0f}% below its 52-week high"
    return {"low": _money(low), "high": _money(high), "left": position, "right": 100 - position, "note": note}


def _brief(text: Any, limit: int = ABOUT_MAX_CHARS) -> str:
    """
    Claude-authored (2026-10-24, Andres): the opening sentences of a business summary that fit in `limit` characters.
    A first sentence longer than that is cut at a word, with an ellipsis.
    """
    text = " ".join(text.split()) if isinstance(text, str) else ""
    if len(text) <= limit:
        return text
    words = {match.end(): text[:match.start()].rsplit(" ", 1)[-1] for match in re.finditer(r"\.(?= [A-Z\"'(])", text)}
    ends = [end for end, word in words.items()
            if len(word) > 1 and "." not in word and word.lower() not in NOT_SENTENCE_ENDS]
    fitting = [end for end in ends if end <= limit]
    if fitting:
        return text[:fitting[-1]]
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _scores(pick: dict, spec: tuple) -> list[tuple[str, str]]:
    sub_scores = pick.get("sub_scores") or {}
    return [(label, f"{_num(sub_scores.get(key)):.0f} / {top}") for key, label, top in spec
            if _num(sub_scores.get(key)) is not None]


def _pick_common(pick: dict, score_key: str) -> dict:
    ticker = str(pick.get("ticker", ""))
    sector = " · ".join(part for part in (_text(pick.get("sector")), _text(pick.get("industry"))) if part)
    score = _num(pick.get(score_key))
    return {
        "ticker": ticker,
        "name": _text(pick.get("name")) or "",
        "price": _money(pick.get("current_price")),
        "score": f"{score:.0f}" if score is not None else DASH,
        # The score pill: green for 90 and up, blue below
        "score_badge": _badge(f"Score {score:.0f}" if score is not None else "Score —",
                              *(("#166534", "#DCFCE7") if score is not None and score >= 90 else ("#075985", "#E0F2FE"))),
        "sentiment": _sentiment_badge(pick.get("sentiment")),
        "sector": sector,
        "thesis": _text(pick.get("thesis")) or "",
        "analyst": _analyst_line(pick),
        "links": _quote_links(ticker),
        "chart": None,  # Set by build_view
    }


def _swing_card(pick: dict, today: Optional[date]) -> dict:
    card = _pick_common(pick, "swingScore")
    # Claude-authored (2026-10-24): "About the company". 2026-10-25 (Andres): swing ideas only
    card["about"] = _brief(pick.get("business_summary"))
    entry, stop, target = (_num(pick.get(key)) for key in ("entry_price", "stop_loss", "price_target"))
    if entry and stop and target:  # scorer.swing_reasoning keeps levels only when 0 < stop < entry < target
        reward_risk = _num(pick.get("risk_reward"))
        card["plan"] = [
            _stat("Entry", _money(entry)),
            _stat("Stop", _money(stop), sub=_signed_pct((stop / entry - 1) * 100), sub_tone="neg"),
            _stat("Target", _money(target), sub=_signed_pct((target / entry - 1) * 100), sub_tone="pos"),
            _stat("Ratio", f"{reward_risk:.1f} : 1" if reward_risk else DASH, sub="reward : risk"),
        ]
    else:
        card["plan"] = []
    earnings, days = _earnings(pick, today)
    card["earnings_warning"] = (
        f"Earnings {earnings}: inside a typical swing-trade window."
        if days is not None and days <= SWING_EARNINGS_WARNING_DAYS else ""
    )
    volume, average = _num(pick.get("volume")), _num(pick.get("average_volume_3m"))
    rows = [
        # Claude-authored (2026-10-20): the swing screen's setup, and what the plan's stop and target sit at
        ("Setup", SWING_SETUPS.get(_text(pick.get("setup")) or "", DASH)),
        ("RSI 14", _fixed(pick.get("rsi_14"), 0)),
        ("EMA 9 / 21", f"{_fixed(pick.get('ema_9'))} / {_fixed(pick.get('ema_21'))}"),
        ("Bollinger bands (20)", f"{_fixed(pick.get('bollinger_lower'))} – {_fixed(pick.get('bollinger_upper'))}"),
        ("Parabolic SAR", _fixed(pick.get("parabolic_sar"))),
        ("50 / 200-day average", f"{_fixed(pick.get('50_day_average'))} / {_fixed(pick.get('200_day_average'))}"),
        ("Beta", _fixed(pick.get("beta"))),
        ("Volume vs 3-month average", f"{volume / average:.1f}x" if volume and average else DASH),
        ("52-week range", f"{_money(pick.get('52_week_low'))} – {_money(pick.get('52_week_high'))}"),
        ("Next earnings", earnings or DASH),
        ("Long-term score", _fixed(pick.get("longScore"), 0)),
    ]
    card["details"] = [(label, value) for label, value in rows if _has_value(value)]
    card["score_rows"] = _scores(pick, SWING_SCORES)
    return card


def _long_term_card(pick: dict, today: Optional[date]) -> dict:
    card = _pick_common(pick, "longScore")
    card["about"] = ""  # 2026-10-25 (Andres): "About the company" is on swing ideas only
    market_cap = _big_money(pick.get("market_cap"))
    card["sector"] = " · ".join(part for part in (card["sector"], market_cap if market_cap != DASH else None) if part)
    card["key_risk"] = _text(pick.get("key_risk")) or ""
    card["stats"] = [
        _stat("Fwd P/E", _fixed(pick.get("pe_forward"), 1)),
        _stat("PEG", _fixed(pick.get("peg_ratio"))),
        _stat("Revenue", _fraction_pct(pick.get("revenue_growth_yoy"), 0, signed=True), _tone(pick.get("revenue_growth_yoy")),
              "growth YoY"),
        _stat("Margin", _fraction_pct(pick.get("profit_margin"), 0), sub="net profit"),
        # Claude-authored (2026-10-23, Andres): momentum, RSI 14 and the 12-month price return (the classic long-term
        # momentum measure; Yahoo's 52-week change, a fraction)
        _rsi_stat(_num(pick.get("rsi_14"))),
        _stat("12-month", _fraction_pct(pick.get("52_week_change_percent"), 0, signed=True),
              _tone(pick.get("52_week_change_percent")), "price return"),
    ]
    card["range"] = _range(pick)
    earnings, _ = _earnings(pick, today)
    debt_to_equity = _num(pick.get("debt_to_equity"))  # Yahoo reports it in percent: 16.5 means 0.165x
    rows = [
        ("Trailing P/E", _fixed(pick.get("pe_trailing"), 1)),
        ("Earnings growth YoY", _fraction_pct(pick.get("earnings_growth_yoy"), 0, signed=True)),
        ("Return on equity", _fraction_pct(pick.get("return_on_equity"), 0)),
        ("Debt / equity", f"{debt_to_equity:.0f}%" if debt_to_equity is not None else DASH),
        ("Next earnings", earnings or DASH),
        ("Swing score", _fixed(pick.get("swingScore"), 0)),
    ]
    card["details"] = [(label, value) for label, value in rows if _has_value(value)]
    card["score_rows"] = _scores(pick, LONG_TERM_SCORES)
    return card


PROVIDER_LABELS = {"anthropic": "Anthropic", "deepseek": "DeepSeek", "gemini": "Gemini"}
# Claude-authored (2026-10-22, Andres): the providers each side's cost line always shows, $0.00 included: the user's
# own keys (holdings reviews on Claude, news and the portfolio review on Gemini) and the admin's shared ones (ideas
# on DeepSeek, market news on Gemini). Any other provider that cost something follows, so the parts add up.
USAGE_PROVIDERS = {"you": ("anthropic", "gemini"), "admin": ("deepseek", "gemini")}


def _usage_row(block: Any, label: str, code: str, always: tuple[str, ...] = ()) -> Optional[tuple[str, str]]:
    """One cost line: the total, then `always`'s providers and any other that cost anything."""
    total = _num(block.get("total_cost_usd")) if isinstance(block, dict) else None
    if total is None:
        return None

    def cost(name: str) -> float:
        return (_num(block[name].get("cost_usd")) or 0.0) if isinstance(block.get(name), dict) else 0.0

    names = list(always) + [name for name in PROVIDER_LABELS if name not in always and cost(name) > 0]
    providers = [f"{PROVIDER_LABELS[name]} {_money(cost(name))}" for name in names]
    return label, f"{_money(total)} {code}" + (f" ({' · '.join(providers)})" if providers else "")


def _usage(api_usage: Any) -> list[tuple[str, str]]:
    """
    The costs in About this digest. 2026-10-22 (Andres): this run and the last 30 days, each split into the reader's
    own keys ("you") and the admin's shared ones ("admin": new-stock ideas and market news). Payloads saved before
    then have today and the month to date instead, shown as they were.
    """
    if not isinstance(api_usage, dict):
        return []
    code = _text(api_usage.get("currency")) or DEFAULT_CURRENCY
    admin = api_usage.get("admin") if isinstance(api_usage.get("admin"), dict) else {}
    periods = (("this_digest", "This run"), ("last_30_days", "Last 30 days"))
    if "last_30_days" not in api_usage and "last_30_days" not in admin:
        periods = (("this_digest", "This run"), ("today", "Today"), ("month_to_date", "Month to date"))
    rows = []
    for key, label in periods:
        for block, who in ((api_usage.get(key), "you"), (admin.get(key), "admin")):
            row = _usage_row(block, f"{label} · {who}", code, USAGE_PROVIDERS[who])
            if row:
                rows.append(row)
    return rows


# Claude-authored (2026-10-17): what each stage of the universal content's screening checks
# (universal_consolidated.screening_counts), in plain words for the email
# 2026-10-20: the long-term and swing screens run side by side on the size, volume & price list, so their stages
# are named for their path
SCREENING_LABELS = {
    "listings": "US-listed stocks",
    "screener": "Size, volume & price",
    "yahoo": "Long-term: analysts & fundamentals",
    "finnhub": "Long-term: growth & debt",
    "technical": "Long-term: technical screen",
    "long_term_ranked": "Long-term: best by the rubric's figures",
    "scored": "Long-term: scored by AI",
    "long_term_score": "Long-term score {min_score}+",
    "swing_fundamentals": "Swing: fundamentals",
    "swing_setup": "Swing: dip or fresh turn up",
    "swing_plan": "Swing: reward : risk {min_score}+",
    "swing_scored": "Swing: best setups scored by AI",
    "swing_score": "Swing score {min_score}+",
    "swing_early": "Still early in their run",
    "picked": "Picked",
}


def _screening(screening: Any) -> list[tuple[str, str]]:
    """
    Claude-authored (2026-10-17): (stage, stocks left) rows for "About this digest", e.g. ("Scored by AI", "36 of 37")
    and ("Picked", "2 long-term · 0 swing"). Empty when the payload has no screening counts.
    """
    stages = screening.get("stages") if isinstance(screening, dict) else None
    rows = []
    for stage in stages if isinstance(stages, list) else []:
        count = _num(stage.get("count")) if isinstance(stage, dict) else None
        if count is None or stage.get("stage") not in SCREENING_LABELS:
            continue
        min_score = _num(stage.get("min_score"))
        label = SCREENING_LABELS[stage["stage"]].format(min_score=f"{min_score:g}" if min_score is not None else "")
        value = f"{count:,.0f}"
        sent = _num(stage.get("of"))
        if stage["stage"] in ("long_term_ranked", "scored", "swing_scored") and sent is not None and sent > count:
            value += f" of {sent:,.0f}"
        elif stage["stage"] == "picked":
            value = f"{_num(stage.get('long_term')) or 0:.0f} long-term · {_num(stage.get('swing')) or 0:.0f} swing"
        rows.append((label, value))
    return rows


def _glossary(with_holdings: bool) -> list[dict]:
    """
    The glossary card's categories. Claude-authored (2026-10-21): a digest without holdings (an ideas-only user)
    leaves out the terms only the holdings parts use.
    """
    if with_holdings:
        return [{"category": category, "terms": terms} for category, terms in GLOSSARY]
    kept = [(IDEAS_ONLY_CATEGORY_NAMES.get(category, category), [t for t in terms if t[0] not in HOLDINGS_ONLY_TERMS])
            for category, terms in GLOSSARY]
    return [{"category": category, "terms": terms} for category, terms in kept if terms]


def _preheader(portfolio: Optional[dict], cards: list[dict], ideas: int, market: Optional[dict], day: str) -> str:
    """The inbox preview line under the subject."""
    parts = []
    if portfolio:
        parts.append(f"Portfolio {portfolio['value']} {portfolio['currency']} ({portfolio['change_pct']} "
                     f"{portfolio.get('day_label') or day})")
    flagged = Counter(card["signal"]["text"] for card in cards if card["signal_key"] in ATTENTION_SIGNALS)
    parts += [_signal_count(count, label) for label, count in flagged.items()]
    parts.append(f"{ideas} new idea{'s' if ideas != 1 else ''}")
    if market and market["rows"]:
        first = market["rows"][0]
        parts.append(f"{first['name']} {first['d1']['text']}")
    return " · ".join(parts)


def build_view(payload: dict) -> dict:
    """Everything the template shows, formatted, from one user's digest payload."""
    holdings = [h for h in payload.get("holdings") or [] if isinstance(h, dict)]
    holdings, fx_notes, unconverted = holdings_in_usd(holdings)  # Holdings in other currencies, in USD from here on
    home_code, home_rate, home_notes = home_currency(payload) if holdings else (MONEY_CURRENCY, 1.0, [])
    home = (home_code, home_rate)
    home_week = usd_rate_change(home_code) if holdings and home_code != MONEY_CURRENCY else None
    recommendations = payload.get("recommendations") or {}
    today = _day(payload.get("date"))
    user_name = str(payload.get("user_name") or "there")
    started = payload.get("personal_started_at") or {}

    # Claude-authored (2026-10-17): every daily change covers one full session, close to close. Before the open
    # that is the previous trading day, so the pre-market digest labels it "Prev. day"; the post-market one, "Day".
    day = "prev. day" if _period(payload) == "Pre-Market" else "day"
    cards = _holding_cards(holdings, day, today)  # 2026-10-21: each card's label by its own market's session
    _scale_news(cards)  # 2026-10-22: fewer stories per stock in a big portfolio
    swing = [_swing_card(pick, today) for pick in recommendations.get("top_swing") or []]
    long_term = [_long_term_card(pick, today) for pick in recommendations.get("top_long_term") or []]
    portfolio = _portfolio(holdings, payload.get("portfolio_beta"), cards, (len(long_term), len(swing)), day, home,
                           home_week)
    market = _market(payload.get("market_data"), day, today)
    stories = _stories(payload.get("headline_news"))
    # Claude-authored (2026-10-17): the collapsed Markets & macro section, e.g. "S&P 500 +0.3% · Mood: Steady ·
    # 4 stories". 2026-10-21: the market mood (VIX and Fear & Greed together) instead of the VIX level, when known.
    mood = market["mood"] if market else None
    markets_summary = [
        _part(row["d1"]["text"], row["d1"]["tone"], prefix=f"{row['name']} ", suffix=f" {day}") if row["name"] != "VIX"
        else _part(mood["grade"], mood["tone"], prefix="Mood: ") if mood else _part(f"VIX {row['last']}")
        for row in (market["rows"] if market else []) if row["name"] in ("S&P 500", "VIX")
    ]
    if stories:
        markets_summary.append(_part(f"{len(stories)} {'story' if len(stories) == 1 else 'stories'}"))
    models = sorted({str(pick["model"]) for pick in (recommendations.get("top_swing") or [])
                     + (recommendations.get("top_long_term") or []) if pick.get("model")})
    # Claude-authored (2026-10-17): one chart per stock. Holdings in other currencies are skipped (Finviz covers US
    # listings); the ideas come from the US screener.
    version = f"{today:%Y%m%d}" if today else "0"
    version += "pre" if _period(payload) == "Pre-Market" else "post"
    for card in cards:
        card["chart"] = _chart(card["symbol"], version) if card["us_listing"] else None
    # Claude-authored (2026-10-22): an idea the user already holds says so
    held = {str(h.get("symbol")).upper() for h in holdings if h.get("symbol")}
    for card in swing + long_term:
        card["chart"] = _chart(card["ticker"], version) if card["ticker"] else None
        card["held"] = str(card["ticker"] or "").upper() in held
    # Claude-authored (2026-10-17): currencies the payload doesn't give are assumed USD, and listed for the run log.
    # The ideas and the market dashboard are always USD (see IDEAS_CURRENCY), so they aren't listed.
    currency_notes = []
    if [card["symbol"] for card in cards if card["currency_assumed"]]:
        currency_notes.append("holdings without a currency, shown as USD: "
                              + ", ".join(card["symbol"] for card in cards if card["currency_assumed"]))
    if isinstance(payload.get("api_usage"), dict) and not _text(payload["api_usage"].get("currency")):
        currency_notes.append("API usage has no currency; costs are shown as USD")

    return {
        "subject": str(payload.get("subject") or "Finnbot Digest"),
        "period": _period(payload),
        "period_icon": "☀️" if _period(payload) == "Pre-Market" else "🌙",
        "date_long": f"{today:%A, %B} {today.day}, {today.year}" if today else "",
        "date_short": f"{today:%a, %b} {today.day}" if today else "",
        "icons": SECTION_ICONS,
        "greeting": _greeting(started, user_name),
        "prepared": _prepared(started),
        # Claude-authored (2026-10-21): the button under the greeting (DIGEST.HEAT_MAP_URL)
        "heat_map": ({"url": params_dict["DIGEST"]["HEAT_MAP_URL"],
                      "label": params_dict["DIGEST"].get("HEAT_MAP_LABEL") or "S&P 500 heat map"}
                     if params_dict["DIGEST"].get("HEAT_MAP_URL") else None),
        "preheader": _preheader(portfolio, cards, len(swing) + len(long_term), market, day),
        "day_label": day.capitalize(),  # "Prev. day" or "Day"
        "day": day,
        "issues": _issues(payload) + ([{
            "title": "Exchange rate",
            "message": notice_text(f"No current rate to convert {', '.join(unconverted)} to USD, so they are shown "
                                   "in their own currency and not added to the USD totals correctly.", "",
                                   _text(payload.get("user_name"))),
        }] if unconverted else []),
        "fx_notes": fx_notes + home_notes,
        "portfolio": portfolio,
        "risk": _risk(cards, payload.get("portfolio_beta"), payload.get("portfolio_review") or []),
        "account": _account(payload.get("account_company"), holdings, home),
        "market": market,
        "stories": stories,
        "markets_summary": markets_summary,
        "holdings": cards,
        "holdings_note": _holdings_note(cards, holdings, day),
        "holdings_summary": _signal_counts(cards),
        "swing": swing,
        "long_term": long_term,
        # Claude-authored (2026-10-17): the collapsed sections' summaries, e.g. "TSM 93 · SKHY 92"
        "swing_summary": [_part(f"{card['ticker']} {card['score']}") for card in swing]
        or [_part(f"No setups scored {params_dict.get('MIN_SWING_SCORE', 60):.0f}+ today")],
        "long_summary": [_part(f"{card['ticker']} {card['score']}") for card in long_term]
        or [_part(f"No ideas scored {params_dict.get('MIN_LONG_SCORE', 60):.0f}+ today")],
        "min_swing_score": f"{params_dict.get('MIN_SWING_SCORE', 60):.0f}",
        "min_long_score": f"{params_dict.get('MIN_LONG_SCORE', 60):.0f}",
        # The footer's "About this digest" dropdown: what it cost, and which model scored the ideas
        "usage": _usage(payload.get("api_usage")) + ([("Ideas scored by", ", ".join(models))] if models else []),
        "screening": _screening(payload.get("screening")),  # Claude-authored (2026-10-17)
        "trimmed": False,  # Set by fit_for_gmail when content was left out to fit Gmail's size limit
        # Claude-authored (2026-10-17): the static glossary card at the bottom (src/digest/glossary.py); emptied by
        # fit_for_gmail, before anything else, when the digest would pass Gmail's clipping limit
        # 2026-10-25 (Andres): with DIGEST.GLOSSARY_URL set, a one-line link to the glossary's web page instead
        "glossary": [] if params_dict["DIGEST"].get("GLOSSARY_URL") else _glossary(bool(cards)),
        "glossary_url": params_dict["DIGEST"].get("GLOSSARY_URL") or "",
        "glossary_summary": [_part("Terms, abbreviations, and indicators explained")] if not params_dict["DIGEST"].get(
            "GLOSSARY_URL") else [_part("Every term explained, and how the ideas are picked")],
        "ideas_currency": IDEAS_CURRENCY,
        "currency_notes": currency_notes,
        # Main sections that start expanded: any of risk, account, markets, holdings, swing, long_term
        "open_sections": set(params_dict["DIGEST"].get("OPEN_SECTIONS") or ()),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def build_environment(template_dir: Path) -> Environment:
    """Autoescaping on: news, reasoning, and theses are LLM-written, so stray markup in them must stay text."""
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,  # fail loudly on a missing/misspelled variable
    )
    # FONT is our own constant: as Markup its quotes aren't escaped to &#39; in every cell that uses it
    env.globals.update(FONT=Markup(FONT), INK=INK, BODY=BODY, MUTED=MUTED, TONES=TONES, TC=TONE_CLASSES)
    env.filters["phrases"] = _phrases  # Claude-authored (2026-10-21)
    return env


# Claude-authored (2026-10-22): badge and dot colours the template writes itself (the view carries the rest)
TEMPLATE_BADGE_COLOURS = {("#E0F2FE", "#075985")}  # An event's "In 5 days" and "You hold this"
TEMPLATE_DOT_COLOURS = {"#F97316", "#38BDF8"}        # The risk notes' dots (notes() in the template)
# Line breaks right before a block-level tag never show on the page, so render_html drops them
_BLOCK_BREAKS = re.compile(r"\n+(?=</?(?:div|table|tr|td|th|tbody|section|details|summary|header|amp-accordion|"
                           r"style|head|meta|script|title|html|body|link)\b)")


def style_classes(view: dict) -> tuple[dict, dict]:
    """
    Claude-authored (2026-10-22): a short class for every badge colour pair ((background, text) -> "b0") and dot
    colour ("#DC2626" -> "k0") this email uses, so each badge and dot names its class instead of repeating its
    colours inline (about 45 bytes each, hundreds per email). The template writes the classes' rules into its
    <style> block; a colour without a class keeps its inline style, so nothing can lose its colour.
    """
    badges, dots = set(TEMPLATE_BADGE_COLOURS), set(TEMPLATE_DOT_COLOURS)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("bg"), str) and isinstance(value.get("fg"), str):
                badges.add((value["bg"], value["fg"]))
            for key in ("dot", "color"):
                if isinstance(value.get(key), str) and value[key].startswith("#"):
                    dots.add(value[key])
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(view)
    return ({pair: f"b{i}" for i, pair in enumerate(sorted(badges))},
            {color: f"k{i}" for i, color in enumerate(sorted(dots))})


def render_html(view: dict, template_dir: Path, amp: bool = False) -> str:
    """The email as HTML, or with amp=True as AMP for Email (the template's dropdowns become amp-accordion)."""
    badge_classes, dot_classes = style_classes(view)
    html = build_environment(template_dir).get_template(TEMPLATE_NAME).render(
        **view, amp=amp, badge_classes=badge_classes, dot_classes=dot_classes)
    html = re.sub(r"\n[ \t]+", "\n", html)  # Leading indentation only adds bytes toward Gmail's clipping limit
    return _BLOCK_BREAKS.sub("", html)  # 2026-10-22: so do line breaks before block-level tags


def render_amp(view: dict, template_dir: Path) -> Optional[str]:
    """
    Claude-authored (2026-10-17): the AMP version of the email, or None when it's over the AMP spec's 200 KB limit
    (Gmail then shows the HTML version). Gmail renders it, with working dropdowns, only for senders allowed in the
    recipient's Gmail settings (docs/watchdog.md); every other mail app ignores it and shows the HTML version.
    """
    amp_html = fit_for_gmail(view, template_dir, amp=True)
    size = len(amp_html.encode("utf-8"))
    if size > AMP_MAX_BYTES:
        print(f"[WARNING] The AMP digest is {size / 1000:.0f} KB, over AMP's 200 KB limit; sending HTML only.")
        return None
    return amp_html


def _drop_about(view: dict) -> None:
    """Claude-authored (2026-10-21): the footer's "About this digest" (costs, model, screening counts)."""
    view["usage"], view["screening"] = [], []


def _drop_sector_chart(view: dict) -> None:
    """Claude-authored (2026-10-25, Andres): the risk section's sector bar chart (~0.7 KB), just before the swing blurbs."""
    if view["risk"]:
        view["risk"]["sectors"] = []


def _drop_pick_about(view: dict) -> None:
    """Claude-authored (2026-10-24): the swing ideas' "About the company" dropdowns."""
    for card in view["swing"]:
        card["about"] = ""


def _drop_quote_links(view: dict) -> None:
    """
    Claude-authored (2026-10-24): the Yahoo / Finviz / TradingView buttons on every card (~240 bytes each). The chart
    still links to Finviz. 2026-10-25 (Andres): back on the cards, dropped right before "About this digest".
    """
    for card in view["holdings"] + view["swing"] + view["long_term"]:
        card["links"] = None


def _drop_glossary(view: dict) -> None:
    view["glossary"] = []


def _drop_low_news(view: dict) -> None:
    for card in view["holdings"]:
        card["news"] = [item for item in card["news"] if item["priority"] != "LOW"]


def _drop_pick_details(view: dict) -> None:
    for card in view["swing"] + view["long_term"]:
        card["details"], card["score_rows"] = [], []


def _drop_hold_news(view: dict) -> None:
    for card in view["holdings"]:
        if card["signal_key"] == "hold":
            card["news"] = [item for item in card["news"] if item["priority"] == "HIGH"]


def _top_news_only(view: dict) -> None:
    """2026-10-22: the stocks flagged NEWS_FULL_SIGNALS (urgent sell, risky) keep theirs until _flagged_top_news_only."""
    full = set(params_dict["DIGEST"].get("NEWS_FULL_SIGNALS") or [])
    for card in view["holdings"]:
        if card["signal_key"] not in full:
            card["news"] = card["news"][:1]


def _no_hold_news(view: dict) -> None:
    for card in view["holdings"]:
        if card["signal_key"] == "hold":
            card["news"] = []


def _flagged_top_news_only(view: dict) -> None:
    """Claude-authored (2026-10-22): the urgent-sell and risky stocks too, once every other card is cut."""
    for card in view["holdings"]:
        card["news"] = card["news"][:1]


def _no_news(view: dict) -> None:
    for card in view["holdings"]:
        card["news"] = []


def _drop_hold_charts(view: dict) -> None:
    """Claude-authored (2026-10-24): the charts of the Hold cards (~330 bytes each), the ones least likely to be opened."""
    for card in view["holdings"]:
        if card["signal_key"] == "hold":
            card["chart"] = None


# Applied in order until the HTML fits, least useful content first. Holding news arrives highest priority first.
# Each holding's signal, figures, and reasoning are never dropped.
# 2026-10-21: "About this digest" goes first, then the glossary (Andres's order)
# 2026-10-22: the urgent-sell and risky stocks keep their news longest (Andres's rule, as in _scale_news)
# 2026-10-24: the swing ideas' company blurbs go early (Andres's order; 2026-10-25: after the glossary), and before
# Gmail would clip the bottom of a big portfolio's email (the ideas), the Hold cards' charts
# 2026-10-25 (Andres's order): the Hold cards' lesser news and every Low story first, then the quote buttons, the
# footer, and the glossary
TRIM_STEPS = (_drop_hold_news, _drop_low_news, _drop_quote_links, _drop_about, _drop_glossary, _drop_sector_chart,
              _drop_pick_about,
              _top_news_only,
              _no_hold_news, _drop_pick_details, _flagged_top_news_only, _no_news, _drop_hold_charts)


def fit_for_gmail(view: dict, template_dir: Path, amp: bool = False) -> str:
    """
    The view's HTML, small enough that Gmail shows all of it. Gmail hides everything past ~102 KB behind
    "[Message clipped]", which would cut off the ideas at the bottom. If the full digest is larger (e.g. a big
    portfolio, three news items per holding), TRIM_STEPS leave out content until it fits, and the footer says so.
    A portfolio too big to fit even then (around 20 stocks or more) is sent with every step applied, and Gmail's
    "View entire message" link shows the rest. With amp=True, the same for the AMP version, toward AMP_TARGET_BYTES.
    """
    limit = AMP_TARGET_BYTES if amp else GMAIL_CLIP_BYTES
    # Claude-authored (2026-10-22, at Andres's request): the glossary keeps only the terms this digest uses
    if view.get("glossary"):
        view["glossary"] = terms_in_use(view["glossary"], visible_text(render_html({**view, "glossary": []},
                                                                                 template_dir)))
    html = render_html(view, template_dir, amp=amp)
    for step in TRIM_STEPS:
        if len(html.encode("utf-8")) <= limit:
            break
        step(view)
        view["trimmed"] = True
        html = render_html(view, template_dir, amp=amp)
    return html


def render_text(view: dict) -> str:
    """The plain-text part of the email: the same content, without the dropdowns."""
    lines = [view["subject"], view["date_long"], "", view["greeting"] + ".", view["prepared"], ""]
    if view["heat_map"]:
        lines[-1:-1] = [f"{view['heat_map']['label']}: {view['heat_map']['url']}"]
    for issue in view["issues"]:
        lines.append(f"! {issue['title']}: {issue['message']}")
    portfolio, risk, account = view["portfolio"], view["risk"], view["account"]
    if portfolio:
        lines += [f"PORTFOLIO ({portfolio['currency']}) {portfolio['value']}  {portfolio['change']} ({portfolio['session']})"]
        if portfolio["usd_value"]:
            lines.append(f"  In USD: {portfolio['usd_value']}")
        lines += [f"  {s['label']}: {s['value']} {s['sub']}".rstrip() for s in portfolio["stats"]]
    if risk:
        lines += ["", "PORTFOLIO RISK"]
        if risk["beta"]:
            lines.append(f"  Beta {risk['beta']['value']} ({risk['beta']['label']}). {risk['beta']['text']}")
        if risk["sectors"]:
            lines.append("  Sectors: " + ", ".join(f"{r['name']} {r['pct']}" for r in risk["sectors"]))
        if risk["mix"]:
            lines.append("  Signals: " + ", ".join(f"{m['label']} {m['count']} ({m['pct']})" for m in risk["mix"]))
        lines += [f"  - {note['category']}: {note['title']}. {note['text']}" for note in risk["watch"] + risk["notes"]]
    if account:
        lines += ["", f"CONNECTED ACCOUNT {account['company']}"]
        lines += [f"  {row['name']}: {row['value']} ({row['positions']})" for row in account["accounts"]]
    if view["market"]:
        # Claude-authored (2026-10-21): the key signals, then the rest by group, as in the email
        def signal_line(s: dict) -> str:
            return f"  {s['emoji']} {s['name']} {s['value']}" + (f" ({s['sub']})" if s["sub"] else "") + f": {s['text']}"

        if view["market"]["key"]:
            lines += ["", "KEY MARKET SIGNALS"] + [signal_line(s) for s in view["market"]["key"]]
        for group in view["market"]["groups"]:
            lines += ["", f"{group['name'].upper()}"] + [signal_line(s) for s in group["signals"]]
    if view["stories"]:
        lines += ["", "MACRO NEWS"]
        for story in view["stories"]:
            lines += [f"- {story['summary']}", f"  Why it matters: {story['impact']}" if story["impact"] else ""]
    if view["holdings"]:
        lines += ["", "YOUR HOLDINGS", view["holdings_note"]]
        for card in view["holdings"]:
            lines.append(f"{card['symbol']} - {card['signal']['text']} - {card['value']} ({card['weight']})")
            lines.append("  " + " | ".join(f"{s['label']} {s['value']} {s['sub']}".strip() for s in card["stats"]))
            if card["idea"]:
                lines.append(f"  From Finnbot's {card['idea']}")
            lines += [f"  {c['when']}: {c['text']}" for c in card["catalysts"]]
            if card["earnings_note"]:
                lines.append(f"  {card['earnings_note']}")
            if card["rationale"]:
                lines.append(f"  Reasoning: {card['rationale']}")
            lines += [f"  * {n['text']}" for n in card["news"]]
            lines.append("")
    lines += ["SWING IDEAS"]
    if not view["swing"]:
        lines.append(f"  No swing setups scored {view['min_swing_score']} or more today.")
    for card in view["swing"]:
        plan = " | ".join(f"{s['label']} {s['value']}" for s in card["plan"])
        lines += [f"{card['ticker']} (score {card['score']}) {card['price']}  {plan}" + ("  (you hold this)" if card.get("held") else ""), f"  {card['thesis']}"]
        if card["earnings_warning"]:
            lines.append(f"  {card['earnings_warning']}")
        if card["about"]:  # Claude-authored (2026-10-24)
            lines.append(f"  About: {card['about']}")
    lines += ["", "LONG-TERM IDEAS"]
    if not view["long_term"]:
        lines.append(f"  No long-term ideas scored {view['min_long_score']} or more today.")
    for card in view["long_term"]:
        lines += [f"{card['ticker']} (score {card['score']}) {card['price']}" + ("  (you hold this)" if card.get("held") else ""), f"  {card['thesis']}"]
        if card["key_risk"]:
            lines.append(f"  Key risk: {card['key_risk']}")
    if view["screening"]:  # Claude-authored (2026-10-17)
        lines += ["", "STOCKS LEFT AFTER EACH SCREENING STAGE"]
        lines += [f"  {label}: {value}" for label, value in view["screening"]]
    if view.get("glossary_url"):  # Claude-authored (2026-10-25)
        lines += ["", f"GLOSSARY: {view['glossary_url']}"]
    return "\n".join(line for line in lines if line is not None) + "\n"


def verify_output(html: str, view: dict) -> None:
    """Cheap smoke tests; not a substitute for looking at the email in a real mail app."""
    assert "{{" not in html and "{%" not in html, "Unrendered Jinja syntax leaked into the output."
    assert html.lstrip().startswith("<!DOCTYPE html>"), "The doctype must come first."
    for card in view["holdings"]:
        assert f">{card['symbol']}<" in html, f"Holding {card['symbol']} is missing from the output."
    size = len(html.encode("utf-8"))
    if size > GMAIL_CLIP_BYTES:
        print(f"[WARNING] The digest HTML is {size / 1000:.0f} KB; Gmail clips emails over ~102 KB.")


@dataclass
class RenderedDigest:
    html: str
    text: str
    path: Path  # Where the HTML was saved (in REPORTS_DIR)
    amp: Optional[str] = None  # The AMP version, when DIGEST.AMP_EMAIL is on and it fits


def report_path(payload: dict) -> Path:
    """e.g. html_reports/Andres_2026-10-16_Pre-Market.html, so the day's two digests don't overwrite each other."""
    day = _day(payload.get("date")) or datetime.now(ZoneInfo("America/New_York")).date()
    return REPORTS_DIR / f"{payload.get('user_name', 'user')}_{day.isoformat()}_{_period(payload)}.html"


def html_main(payload: dict, here: Path = Path(__file__).resolve().parent) -> RenderedDigest:
    """
    Renders the user's digest from the template in `here`, saves the HTML in REPORTS_DIR, and returns it with
    the plain-text version and, when DIGEST.AMP_EMAIL is on, the AMP version (saved next to it as .amp.html).
    """
    view = build_view(payload)
    text = render_text(view)  # Before fit_for_gmail, so the plain-text version keeps everything
    html = fit_for_gmail(view, template_dir=here)
    verify_output(html, view)
    for note in view["currency_notes"]:
        print(f"[NOTE] {payload.get('user_name')}'s digest: {note} (not in the payload).")
    for note in view["fx_notes"]:
        print(f"[NOTE] {payload.get('user_name')}'s digest: {note}.")
    path = report_path(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    print(f"Wrote {payload.get('user_name')}'s digest to {path} ({len(html.encode('utf-8')) / 1000:.0f} KB)")

    amp_html = None
    if params_dict["DIGEST"].get("AMP_EMAIL", False):
        # A fresh view, since fit_for_gmail trims the HTML and AMP versions separately
        amp_html = render_amp(build_view(payload), template_dir=here)
        if amp_html:
            path.with_suffix(".amp.html").write_text(amp_html, encoding="utf-8")
    return RenderedDigest(html=html, text=text, path=path, amp=amp_html)
