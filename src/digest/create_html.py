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
from markupsafe import Markup

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict
from src.personalized.holdings_state import _clean_float
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

# recommendation -> (label, badge text colour, badge colour, bar colour, sort order: most urgent first)
SIGNALS = {
    "urgent_sell": ("Urgent sell", "#7F1D1D", "#FECACA", "#DC2626", 0),
    "risk_alert": ("Risk alert", "#9A3412", "#FFEDD5", "#F97316", 1),
    "trim": ("Trim", "#92400E", "#FEF3C7", "#F59E0B", 2),
    "hold": ("Hold", "#075985", "#E0F2FE", "#38BDF8", 3),
}
NOT_EVALUATED = ("Not evaluated", "#475569", "#F1F5F9", "#CBD5E1", 4)
SENTIMENTS = {"Bullish": ("#166534", "#DCFCE7"), "Bearish": ("#991B1B", "#FEE2E2"), "Neutral": ("#475569", "#F1F5F9")}
PRIORITY_DOTS = {"HIGH": "#DC2626", "MEDIUM": "#F59E0B", "LOW": "#94A3B8"}
CURRENCY_SYMBOLS = {"USD": "$", "CAD": "C$", "AUD": "A$", "EUR": "€", "GBP": "£"}
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
LONG_TERM_SCORES = (  # (sub-score, label, maximum), as in LLM_rec_instr.py
    ("valuation_score", "Valuation", 25), ("financial_health_score", "Financial health", 25),
    ("growth_momentum_score", "Growth momentum", 25), ("competitive_moat_score", "Competitive moat", 25),
)
SWING_SCORES = (
    ("technical_setup_score", "Technical setup", 30), ("risk_reward_ratio_score", "Risk / reward", 30),
    ("momentum_volume_score", "Momentum & volume", 20), ("catalyst_sentiment_score", "Catalysts & sentiment", 20),
)
SWING_EARNINGS_WARNING_DAYS = 14  # A swing pick reporting earnings this soon gets a visible warning


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


def _signed_money(value: Any, decimals: int = 2) -> str:
    number = _num(value)
    if number is None:
        return DASH
    return f"{'+' if number >= 0 else '-'}${abs(number):,.{decimals}f}"


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
        "alt": f"{symbol} daily candlestick chart",
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


def _issues(payload: dict) -> list[dict]:
    """Why parts of the digest are empty or incomplete (a key, a brokerage connection, a data source)."""
    issues = []
    for notice in payload.get("api_key_issues") or []:
        issues.append({"title": f"{notice.get('provider', 'API')} key", "message": str(notice.get("message", ""))})
    for notice in (payload.get("data_source_issues") or []) + (payload.get("universal_issues") or []):
        issues.append({"title": str(notice.get("source", "Data source")), "message": str(notice.get("message", ""))})
    return issues


def _session_date(holdings: list[dict]) -> Optional[str]:
    """'Oct 15': the trading session the daily P/L covers (before the open, the previous trading day)."""
    sessions = Counter(h.get("daily_pnl_session") for h in holdings if h.get("daily_pnl_session"))
    return _short_date(sessions.most_common(1)[0][0]) if sessions else None


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


def _holding_card(symbol: str, lots: list[dict]) -> dict:
    first = lots[0]  # Price, signal, news, and indicators are the same for every lot of a stock
    label, fg, bg, bar, order = SIGNALS.get(first.get("recommendation"), NOT_EVALUATED)
    value = sum(_num(lot.get("market_value")) or 0.0 for lot in lots)
    shares = sum(_num(lot.get("shares")) or 0.0 for lot in lots)
    gain, gain_pct = _unrealized(lots)
    accounts = sorted({lot["account_name"] for lot in lots if _text(lot.get("account_name"))})
    currency = _text(first.get("currency")) or "USD"
    daily_pct = _num(first.get("daily_pnl_pct"))
    weight = _num(first.get("portfolio_weight_pct"))
    rsi = _num(first.get("rsi"))

    stats = [
        _stat("Price", _money(first.get("current_price"), symbol=CURRENCY_SYMBOLS.get(currency, f"{currency} ")),
              sub=_signed_pct(daily_pct, 2) if daily_pct is not None else "", sub_tone=_tone(daily_pct)),
        _stat("Unrealized", _signed_money(gain), _tone(gain), _signed_pct(gain_pct), _tone(gain)),
        _stat("RSI 14", _fixed(rsi, 0),
              sub="Overbought" if rsi is not None and rsi >= 70 else "Oversold" if rsi is not None and rsi <= 30
              else "Neutral" if rsi is not None else "",
              sub_tone="warn" if rsi is not None and (rsi >= 70 or rsi <= 30) else "muted"),
    ]
    # digest_fields._ma_status: "Above 21 EMA (+1.2%)", or the 50-day average when there was no EMA, or "N/A"
    trend = re.match(r"^(Above|Below) (.+?) \(([+-]?[\d.]+)%\)$", str(first.get("ma_status") or ""))
    if trend:
        stats.append(_stat(f"vs {trend.group(2)}", f"{float(trend.group(3)):+.1f}%", _tone(float(trend.group(3))),
                           trend.group(1)))
    else:
        stats.append(_stat("Trend", DASH))

    volatility = []
    if _num(first.get("annualized_vol")) is not None:
        volatility.append(f"Volatility {_num(first.get('annualized_vol')):.0f}% a year")
    if _num(first.get("natr")) is not None:
        volatility.append(f"average daily range {_num(first.get('natr')):.1f}% of price (ATR)")
    volatility_line = "; ".join(volatility)
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
        "value": _money(value),
        "weight": f"{weight:.1f}% of portfolio" if weight is not None else "",
        # Claude-authored (2026-10-17): the collapsed row's right-hand line, e.g. "33.6% · +0.82%"
        "weight_short": f"{weight:.1f}%" if weight is not None else "",
        "day": {"text": _signed_pct(daily_pct, 2) if daily_pct is not None else "", "tone": _tone(daily_pct)},
        "weight_pct": weight or 0.0,
        "meta": " · ".join([f"{_shares(shares)} share{'' if shares == 1 else 's'}"] + accounts),
        "signal": _badge(label, fg, bg),
        "signal_key": first.get("recommendation") if first.get("recommendation") in SIGNALS else None,
        "signal_bar": bar,
        "order": order,
        "sentiment": _sentiment_badge(first.get("sentiment")),
        "stats": stats,
        "catalysts": catalysts,
        "rationale": _text(first.get("recommendation_rationale")) or "",
        "volatility": volatility_line,
        "news": news,
        "links": _quote_links(symbol),
        "currency": currency,
        "chart": None,  # Set by build_view
    }


def _holding_cards(holdings: list[dict]) -> list[dict]:
    """Most urgent signal first (urgent sell, risk alert, trim, hold), then largest position."""
    cards = [_holding_card(symbol, lots) for symbol, lots in _lots_by_symbol(holdings).items()]
    return sorted(cards, key=lambda card: (card["order"], -card["weight_pct"]))


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _holdings_note(cards: list[dict], holdings: list[dict]) -> str:
    """The line atop the open holdings section, which also dates the price changes on the cards."""
    note = "Most urgent signal first"
    session = _session_date(holdings)
    return f"{note} · price changes are for the {session} session" if session else note


def _signal_counts(cards: list[dict]) -> str:
    """The holdings section's summary, e.g. '6 risk alerts · 4 holds', most urgent first."""
    counts = Counter(card["signal"]["text"].lower() for card in cards)
    return " · ".join(_plural(count, label) if label != "not evaluated" else f"{count} not evaluated"
                      for label, count in counts.items())


def _portfolio(holdings: list[dict], beta: Any, idea_counts: tuple[int, int]) -> Optional[dict]:
    if not holdings:
        return None
    totals = build_portfolio_summary(holdings, _num(beta))["portfolio"]
    daily_pnl = sum(_num(h.get("daily_pnl")) or 0.0 for h in holdings)
    gain, gain_pct = _unrealized(holdings)
    accounts = {h.get("account_name") for h in holdings if _text(h.get("account_name"))}
    long_n, swing_n = idea_counts
    session = _session_date(holdings)
    return {
        "label": "Your portfolio" + (" · in USD" if len(totals["currencies"]) > 1 else ""),
        "value": _money(totals["total_market_value"]),
        "change": f"{_signed_money(daily_pnl)} ({_signed_pct(totals['daily_pnl_pct'], 2)})",
        "change_pct": _signed_pct(totals["daily_pnl_pct"], 2),
        "change_tone": _tone(daily_pnl),
        "session": f"{session} session" if session else "Last session",
        "stats": [
            _stat("Positions", str(totals["number_of_stocks"]),
                  sub=f"{len(accounts)} account{'s' if len(accounts) != 1 else ''}" if accounts else ""),
            _stat("Unrealized", _signed_money(gain, 0), _tone(gain), _signed_pct(gain_pct), _tone(gain)),
            _stat("New ideas", str(long_n + swing_n), sub=f"{long_n} long · {swing_n} swing"),
        ],
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
    summary = [f"Beta {beta_view['value']} · {beta_view['label'].lower()}"] if beta_view else []
    if watch:
        summary.append(f"{len(watch)} to watch")
    return {
        "beta": beta_view,
        "mix": mix,
        "largest": f"{largest['symbol']}, {largest['weight_pct']:.1f}% of the portfolio" if largest else "",
        "watch": watch,
        "notes": [note for note in notes if note["category"] != "WATCH"],
        "summary": " · ".join(summary),  # Claude-authored (2026-10-17): shown while the section is collapsed
    }


def _account(company: Any, holdings: list[dict]) -> Optional[dict]:
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
         "value": _money(entry["value"])}
        for name, entry in sorted(accounts.items(), key=lambda item: -item[1]["value"])
    ]
    company = _text(company) or "Brokerage"
    summary = f"{company} · {_plural(len(rows), 'account')}" if rows else company
    return {"company": company, "accounts": rows, "summary": summary}


def _change(text: Any) -> dict:
    """A dashboard change string such as '+0.28%' -> {'text', 'tone'}."""
    raw = str(text or "").strip()
    try:
        number = float(raw.rstrip("%"))
    except ValueError:
        return {"text": DASH, "tone": "muted"}
    return {"text": f"{number:+.1f}%", "tone": _tone(number)}


def _market_name(name: str) -> tuple[str, str]:
    """'S&P 500 (SPY)' -> ('S&P 500', 'SPY'), with short names for the long ones."""
    if name in MARKET_NAMES:
        return MARKET_NAMES[name]
    match = re.match(r"^(.*?)\s*\((.+)\)$", name)
    return (match.group(1), match.group(2).lstrip("$")) if match else (name, "")


def _market(market_data: Any) -> Optional[dict]:
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
        rows.append({"name": short, "ticker": ticker, "last": last,
                     "d1": _change(metric.get("1D %")), "d5": _change(metric.get("5D %")),
                     "m1": _change(metric.get("1M %"))})

    chips = []
    fear_greed = market_data.get("fear_and_greed") or {}
    if _num(fear_greed.get("Score")) is not None:
        rating = str(fear_greed.get("Rating") or "")
        tone = "neg" if "fear" in rating.lower() else "pos" if "greed" in rating.lower() else "ink"
        chips.append(_stat("Fear & Greed", f"{_num(fear_greed['Score']):.0f}", tone, rating, tone))
    curve = market_data.get("yield_curve_10y_2y") or {}
    if curve.get("Spread") and not curve.get("Error"):
        spread = _num(str(curve["Spread"]).rstrip("%"))
        chips.append(_stat("10Y–2Y spread", str(curve["Spread"]), "neg" if spread is not None and spread < 0 else "ink",
                           f"{curve.get('1D Change', '')} today".strip() if curve.get("1D Change") else ""))
    breadth = (market_data.get("sp500_breadth") or {}).get("% > 50MA")
    if breadth:
        share = _num(str(breadth).rstrip("%"))
        tone = "ink" if share is None else "neg" if share < 40 else "pos" if share > 60 else "ink"
        chips.append(_stat("S&P breadth", f"{share:.0f}%" if share is not None else str(breadth), tone,
                           "above 50-day avg"))
    if not rows and not chips:
        return None
    # Claude-authored (2026-10-17): the collapsed section's summary, e.g. "S&P 500 +0.3% · Nasdaq-100 -0.1% · Fear 32"
    summary = [f"{row['name']} {row['d1']['text']}" for row in rows[:2]]
    if chips and chips[0]["label"] == "Fear & Greed":
        summary.append(f"{chips[0]['sub'] or 'Fear & Greed'} {chips[0]['value']}")
    return {"rows": rows, "chips": chips, "summary": " · ".join(summary)}


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
        "sentiment": _sentiment_badge(pick.get("sentiment")),
        "sector": sector,
        "thesis": _text(pick.get("thesis")) or "",
        "analyst": _analyst_line(pick),
        "links": _quote_links(ticker),
        "chart": None,  # Set by build_view
    }


def _swing_card(pick: dict, today: Optional[date]) -> dict:
    card = _pick_common(pick, "swingScore")
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
    market_cap = _big_money(pick.get("market_cap"))
    card["sector"] = " · ".join(part for part in (card["sector"], market_cap if market_cap != DASH else None) if part)
    card["key_risk"] = _text(pick.get("key_risk")) or ""
    card["stats"] = [
        _stat("Fwd P/E", _fixed(pick.get("pe_forward"), 1)),
        _stat("PEG", _fixed(pick.get("peg_ratio"))),
        _stat("Revenue", _fraction_pct(pick.get("revenue_growth_yoy"), 0, signed=True), _tone(pick.get("revenue_growth_yoy")),
              "growth YoY"),
        _stat("Margin", _fraction_pct(pick.get("profit_margin"), 0), sub="net profit"),
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


def _usage(api_usage: Any) -> list[tuple[str, str]]:
    if not isinstance(api_usage, dict):
        return []
    rows = []
    for key, label in (("this_digest", "This digest"), ("today", "Today"), ("month_to_date", "Month to date")):
        block = api_usage.get(key) or {}
        total = _num(block.get("total_cost_usd"))
        if total is None:
            continue
        providers = [
            f"{label} {_money(block[name].get('cost_usd'))}"
            for name, label in (("anthropic", "Anthropic"), ("deepseek", "DeepSeek"), ("gemini", "Gemini"))
            if isinstance(block.get(name), dict) and (_num(block[name].get("cost_usd")) or 0) > 0
        ]
        rows.append((label, _money(total) + (f" ({' · '.join(providers)})" if providers else "")))
    return rows


def _preheader(portfolio: Optional[dict], cards: list[dict], ideas: int, market: Optional[dict]) -> str:
    """The inbox preview line under the subject."""
    parts = []
    if portfolio:
        parts.append(f"Portfolio {portfolio['value']} ({portfolio['change_pct']})")
    flagged = Counter(card["signal"]["text"] for card in cards if card["signal_key"] in ("urgent_sell", "risk_alert", "trim"))
    parts += [f"{count} {label.lower()}{'s' if count > 1 and label != 'Trim' else ''}" for label, count in flagged.items()]
    parts.append(f"{ideas} new idea{'s' if ideas != 1 else ''}")
    if market and market["rows"]:
        first = market["rows"][0]
        parts.append(f"{first['name']} {first['d1']['text']}")
    return " · ".join(parts)


def build_view(payload: dict) -> dict:
    """Everything the template shows, formatted, from one user's digest payload."""
    holdings = [h for h in payload.get("holdings") or [] if isinstance(h, dict)]
    recommendations = payload.get("recommendations") or {}
    today = _day(payload.get("date"))
    user_name = str(payload.get("user_name") or "there")
    started = payload.get("personal_started_at") or {}

    cards = _holding_cards(holdings)
    swing = [_swing_card(pick, today) for pick in recommendations.get("top_swing") or []]
    long_term = [_long_term_card(pick, today) for pick in recommendations.get("top_long_term") or []]
    portfolio = _portfolio(holdings, payload.get("portfolio_beta"), (len(long_term), len(swing)))
    market = _market(payload.get("market_data"))
    stories = _stories(payload.get("headline_news"))
    # Claude-authored (2026-10-17): the collapsed Markets & macro section, e.g. "S&P 500 +0.3% · 5 stories"
    markets_summary = " · ".join(
        ([f"{market['rows'][0]['name']} {market['rows'][0]['d1']['text']}"] if market and market["rows"] else [])
        + ([f"{len(stories)} {'story' if len(stories) == 1 else 'stories'}"] if stories else [])
    )
    models = sorted({str(pick["model"]) for pick in (recommendations.get("top_swing") or [])
                     + (recommendations.get("top_long_term") or []) if pick.get("model")})
    # Claude-authored (2026-10-17): one chart per stock. Holdings in other currencies are skipped (Finviz covers US
    # listings); the ideas come from the US screener.
    version = f"{today:%Y%m%d}" if today else "0"
    version += "pre" if _period(payload) == "Pre-Market" else "post"
    for card in cards:
        card["chart"] = _chart(card["symbol"], version) if card["currency"] == "USD" else None
    for card in swing + long_term:
        card["chart"] = _chart(card["ticker"], version) if card["ticker"] else None

    return {
        "subject": str(payload.get("subject") or "Finnbot Digest"),
        "period": _period(payload),
        "date_long": f"{today:%A, %B} {today.day}, {today.year}" if today else "",
        "greeting": _greeting(started, user_name),
        "prepared": _prepared(started),
        "preheader": _preheader(portfolio, cards, len(swing) + len(long_term), market),
        "issues": _issues(payload),
        "portfolio": portfolio,
        "risk": _risk(cards, payload.get("portfolio_beta"), payload.get("portfolio_review") or []),
        "account": _account(payload.get("account_company"), holdings),
        "market": market,
        "stories": stories,
        "markets_summary": markets_summary,
        "holdings": cards,
        "holdings_note": _holdings_note(cards, holdings),
        "holdings_summary": _signal_counts(cards),
        "swing": swing,
        "long_term": long_term,
        # Claude-authored (2026-10-17): the collapsed sections' summaries, e.g. "TSM 93 · SKHY 92"
        "swing_summary": " · ".join(f"{card['ticker']} {card['score']}" for card in swing) or "None today",
        "long_summary": " · ".join(f"{card['ticker']} {card['score']}" for card in long_term) or "None today",
        "min_swing_score": f"{params_dict.get('MIN_SWING_SCORE', 60):.0f}",
        "min_long_score": f"{params_dict.get('MIN_LONG_SCORE', 60):.0f}",
        "models": ", ".join(models),
        "usage": _usage(payload.get("api_usage")),
        "usage_note": str((payload.get("api_usage") or {}).get("note") or ""),
        "trimmed": False,  # Set by fit_for_gmail when content was left out to fit Gmail's size limit
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
    env.globals.update(FONT=Markup(FONT), INK=INK, BODY=BODY, MUTED=MUTED, TONES=TONES)
    return env


def render_html(view: dict, template_dir: Path, amp: bool = False) -> str:
    """The email as HTML, or with amp=True as AMP for Email (the template's dropdowns become amp-accordion)."""
    html = build_environment(template_dir).get_template(TEMPLATE_NAME).render(**view, amp=amp)
    return re.sub(r"\n[ \t]+", "\n", html)  # Leading indentation only adds bytes toward Gmail's clipping limit


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
    for card in view["holdings"]:
        card["news"] = card["news"][:1]


def _no_hold_news(view: dict) -> None:
    for card in view["holdings"]:
        if card["signal_key"] == "hold":
            card["news"] = []


def _no_news(view: dict) -> None:
    for card in view["holdings"]:
        card["news"] = []


# Applied in order until the HTML fits, least useful content first. Holding news arrives highest priority first.
# Each holding's signal, figures, and reasoning are never dropped.
TRIM_STEPS = (_drop_low_news, _drop_pick_details, _drop_hold_news, _top_news_only, _no_hold_news, _no_news)


def fit_for_gmail(view: dict, template_dir: Path, amp: bool = False) -> str:
    """
    The view's HTML, small enough that Gmail shows all of it. Gmail hides everything past ~102 KB behind
    "[Message clipped]", which would cut off the ideas at the bottom. If the full digest is larger (e.g. a big
    portfolio, three news items per holding), TRIM_STEPS leave out content until it fits, and the footer says so.
    A portfolio too big to fit even then (around 20 stocks or more) is sent with every step applied, and Gmail's
    "View entire message" link shows the rest. With amp=True, the same for the AMP version, toward AMP_TARGET_BYTES.
    """
    limit = AMP_TARGET_BYTES if amp else GMAIL_CLIP_BYTES
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
    for issue in view["issues"]:
        lines.append(f"! {issue['title']}: {issue['message']}")
    portfolio, risk, account = view["portfolio"], view["risk"], view["account"]
    if portfolio:
        lines += [f"PORTFOLIO {portfolio['value']}  {portfolio['change']} ({portfolio['session']})"]
        lines += [f"  {s['label']}: {s['value']} {s['sub']}".rstrip() for s in portfolio["stats"]]
    if risk:
        lines += ["", "PORTFOLIO RISK"]
        if risk["beta"]:
            lines.append(f"  Beta {risk['beta']['value']} ({risk['beta']['label']}). {risk['beta']['text']}")
        if risk["mix"]:
            lines.append("  Signals: " + ", ".join(f"{m['label']} {m['count']} ({m['pct']})" for m in risk["mix"]))
        lines += [f"  - {note['category']}: {note['title']}. {note['text']}" for note in risk["watch"] + risk["notes"]]
    if account:
        lines += ["", f"CONNECTED ACCOUNT {account['company']}"]
        lines += [f"  {row['name']}: {row['value']} ({row['positions']})" for row in account["accounts"]]
    if view["market"]:
        lines += ["", "MARKETS (1D / 5D / 1M)"]
        lines += [f"  {r['name']} {r['last']}  {r['d1']['text']} / {r['d5']['text']} / {r['m1']['text']}"
                  for r in view["market"]["rows"]]
        lines += [f"  {c['label']}: {c['value']} {c['sub']}".rstrip() for c in view["market"]["chips"]]
    if view["stories"]:
        lines += ["", "MACRO NEWS"]
        for story in view["stories"]:
            lines += [f"- {story['summary']}", f"  Why it matters: {story['impact']}" if story["impact"] else ""]
    if view["holdings"]:
        lines += ["", "YOUR HOLDINGS"]
        for card in view["holdings"]:
            lines.append(f"{card['symbol']} - {card['signal']['text']} - {card['value']} ({card['weight']})")
            lines.append("  " + " | ".join(f"{s['label']} {s['value']} {s['sub']}".strip() for s in card["stats"]))
            lines += [f"  {c['when']}: {c['text']}" for c in card["catalysts"]]
            if card["rationale"]:
                lines.append(f"  Reasoning: {card['rationale']}")
            lines += [f"  * {n['text']}" for n in card["news"]]
            lines.append("")
    lines += ["SWING IDEAS"]
    if not view["swing"]:
        lines.append(f"  No swing setups scored {view['min_swing_score']} or more today.")
    for card in view["swing"]:
        plan = " | ".join(f"{s['label']} {s['value']}" for s in card["plan"])
        lines += [f"{card['ticker']} (score {card['score']}) {card['price']}  {plan}", f"  {card['thesis']}"]
        if card["earnings_warning"]:
            lines.append(f"  {card['earnings_warning']}")
    lines += ["", "LONG-TERM IDEAS"]
    if not view["long_term"]:
        lines.append(f"  No long-term ideas scored {view['min_long_score']} or more today.")
    for card in view["long_term"]:
        lines += [f"{card['ticker']} (score {card['score']}) {card['price']}", f"  {card['thesis']}"]
        if card["key_risk"]:
            lines.append(f"  Key risk: {card['key_risk']}")
    lines += ["", "For information only, not investment advice. Check figures against your brokerage before acting."]
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
