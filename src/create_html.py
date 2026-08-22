"""
render_digest.py
=================
Typed data contract + rendering pipeline for the Daily Financial Digest email
(email_template.html). Built on stdlib `dataclasses`/`typing` rather than
pydantic, so it runs with zero third-party dependencies beyond `jinja2`
(the template engine the brief explicitly calls for). Swap the dataclasses
for `pydantic.BaseModel` 1:1 if you want runtime validation / JSON-schema
export — the field names and nesting are already pydantic-shaped.

Usage:
    python3 render_digest.py
    -> writes rendered_digest_preview.html next to this script

Dependencies:
    pip install jinja2
"""

from __future__ import annotations
from connect_questrade import QuestradePortfolioFetcher
import datetime as _dt
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Literal, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# ---------------------------------------------------------------------------
# 1. TYPED DATA SCHEMA (one dataclass per zone / nested entity)
# ---------------------------------------------------------------------------

MarketStatus = Literal["Risk-On", "Risk-Off", "Consolidating"]
DecisionFlag = Literal["HOLD (IN RANGE)", "TRIM / TAKE PROFIT", "RISK ALERT"]
Sentiment = Literal["Bullish", "Bearish", "Neutral"]
CatalystType = Literal["Earnings", "Ex-Dividend", "Product Event"]


@dataclass
class MacroMetric:
    """One tile in the Zone 1 industrial macro grid."""
    label: str                                  # e.g. "ISM Mfg PMI"
    value: str                                   # pre-formatted display value, e.g. "48.7"
    change_percent: Optional[float] = None       # drives green/red badge; None = no badge


@dataclass
class Catalyst:
    """One upcoming event inside a holding's 14-day catalyst window."""
    event_type: CatalystType
    label: str                                   # e.g. "Q3 FY26 Earnings"
    event_date: _dt.date
    days_until: int


@dataclass
class HoldingLinks:
    yahoo: str
    finviz: str
    tradingview: str
    sec_filings: str


@dataclass
class Holding:
    """One Zone 3 stock card."""
    ticker: str
    company_name: str
    position_value: float
    shares_held: float
    daily_pl_dollar: float
    daily_pl_percent: float
    decision_flag: DecisionFlag
    rsi: float
    ma_status: str                               # e.g. "Above 20 EMA"
    chart_image_url: str                         # static candlestick PNG
    chart_link_url: str                          # click-through to live chart
    news_bullets: List[str]
    sentiment: Sentiment
    catalysts: List[Catalyst] = field(default_factory=list)
    links: Optional[HoldingLinks] = None


@dataclass
class SwingCandidate:
    """Zone 4 — medium-term technical setup."""
    ticker: str
    setup: str                                   # e.g. "Bull flag breakout above $142"
    entry_zone: str
    target_price: str
    stop_loss: str


@dataclass
class ValueCandidate:
    """Zone 4 — long-term fundamental candidate."""
    ticker: str
    moat: str
    valuation_metric: str                        # e.g. "P/E 18.4x (5yr avg 22.1x)"
    buy_target: str


@dataclass
class DigestData:
    """Top-level payload. Field names match the template's top-level
    Jinja variables 1:1 — this is exactly what gets passed to
    Template.render(**asdict(digest))."""

    # Zone 1 — Global Macro Pulse
    report_date: str
    market_status: MarketStatus
    macro_metrics: List[MacroMetric]
    macro_stories: List[str]
    sector_heatmap_url: str

    # Zone 2 — Executive Account Dashboard
    portfolio_value: float
    cash_balance: float
    daily_pl_dollar: float
    daily_pl_percent: float
    all_time_pl_dollar: float
    all_time_pl_percent: float
    portfolio_beta: float
    performance_chart_url: str

    # Zone 3 — Active Holdings
    holdings: List[Holding]

    # Zone 4 — Candidate Watchlist
    swing_candidates: List[SwingCandidate]
    value_candidates: List[ValueCandidate]

    # Zone 5 — Risk Guardrails & Allocation
    allocation_chart_url: str
    risk_warnings: List[str]


# ---------------------------------------------------------------------------
# 2. MOCK PAYLOAD
# ---------------------------------------------------------------------------
# Tickers/companies below are well-known real symbols used purely as
# realistic placeholder data for template QA — figures are fabricated, not
# live market data. Chart/image URLs point at real, public, no-auth
# endpoints (Finviz's chart.ashx) so the <img> tags resolve when previewed.
#
# PRODUCTION NOTE: don't hotlink Finviz's live endpoint directly for a real
# send — at send volume you risk their hotlink protection / rate limits,
# and a mail proxy that caches the image (Gmail's, for instance) will keep
# showing a stale chart until its cache expires regardless. Render your own
# PNG per recipient (matplotlib/plotly, or a service like QuickChart.io),
# push it to your own CDN, and append a cache-busting version/timestamp
# query param — see performance_chart_url / allocation_chart_url below.

def _finviz_chart_url(ticker: str) -> str:
    return f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l"


def _holding_links(ticker: str) -> HoldingLinks:
    return HoldingLinks(
        yahoo=f"https://finance.yahoo.com/quote/{ticker}",
        finviz=f"https://finviz.com/quote.ashx?t={ticker}",
        tradingview=f"https://www.tradingview.com/symbols/{ticker}/",
        sec_filings=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=10-K",
    )


def build_payload() -> DigestData:
    today = _dt.datetime.now(ZoneInfo("America/New_York")).date()  # for live preview, use current date
    fetcher = QuestradePortfolioFetcher()
    df_full = fetcher.get_holdings_df()

    macro_metrics = [
        MacroMetric("ISM Mfg PMI", "48.7", change_percent=-1.2),
        MacroMetric("Industrial Production", "102.4", change_percent=0.4),
        MacroMetric("US 10-Yr Treasury Yield", "4.18%", change_percent=0.9),
        MacroMetric("Cass Freight Index", "1.087", change_percent=-2.1),
        MacroMetric("CPI (YoY)", "2.9%", change_percent=-0.3),
        MacroMetric("Fed Funds Rate", "4.25–4.50%", change_percent=None),
    ]

    macro_stories = [
        "Container throughput at West Coast ports eased for a second straight "
        "week, easing near-term pressure on inbound industrial input costs.",
        "Regional Fed manufacturing surveys point to stabilizing new-order "
        "volumes, though hiring intentions among factory respondents softened.",
        "Freight carriers flagged continued rate softness as capacity outpaces "
        "demand, a headwind for logistics-linked industrial names this quarter.",
    ]

    holdings = []

    for _, row in df_full.iterrows():
        ticker = row["symbol"]
        
        holding = Holding(
            # Mapped from DataFrame
            ticker=ticker,
            company_name=row["description"],
            position_value=row["market_value"],
            shares_held=row["shares"],
            
            # Unavailable in DataFrame - Defaults from example
            daily_pl_dollar=286.40,
            daily_pl_percent=1.58,
            decision_flag="HOLD (IN RANGE)",
            rsi=58.3,
            ma_status="Above 20 EMA",
            chart_image_url=_finviz_chart_url(ticker),
            chart_link_url=f"https://www.tradingview.com/symbols/{ticker}/",
            news_bullets=[
                "Dealer statistics showed retail machine sales stabilizing "
                "in North America after three soft months.",
                "Management reaffirmed full-year margin guidance on last "
                "week's investor call, citing services-mix tailwinds.",
            ],
            sentiment="Bullish",
            catalysts=[
                Catalyst("Ex-Dividend", "Quarterly dividend", today + _dt.timedelta(days=3), 3),
                Catalyst("Earnings", "Q3 FY26 Earnings", today + _dt.timedelta(days=12), 12),
            ],
            links=_holding_links(ticker),
        )
        
        holdings.append(holding)

    swing_candidates = [
        SwingCandidate("PH", "Bull flag breakout above $148 on rising volume", "$146–$148", "$158", "$141"),
        SwingCandidate("ETN", "Ascending triangle nearing $312 resistance", "$305–$308", "$325", "$296"),
        SwingCandidate("ROK", "Reclaiming 50 EMA after basing at support", "$268–$272", "$288", "$258"),
        SwingCandidate("DOV", "Higher-low structure, MACD bullish cross", "$182–$185", "$196", "$176"),
        SwingCandidate("XYL", "Range breakout above $128 supply zone", "$126–$129", "$138", "$121"),
    ]

    value_candidates = [
        ValueCandidate("EMR", "Entrenched automation install base with high switching costs", "P/E 21.3x (5yr avg 23.6x)", "$108"),
        ValueCandidate("ITW", "Decentralized 80/20 operating model drives best-in-class margins", "P/E 22.8x (5yr avg 21.9x)", "$232"),
        ValueCandidate("PNR", "Water infrastructure exposure with recurring aftermarket revenue", "P/E 24.1x (5yr avg 26.4x)", "$74"),
    ]

    return DigestData(
        report_date = (
            f"{today:%A, %B} {today.day}, {today:%Y}"
            if hasattr(today, "strftime")
            else str(today)
            ),
        market_status="Consolidating",
        macro_metrics=macro_metrics,
        macro_stories=macro_stories,
        sector_heatmap_url="https://finviz.com/map.ashx?t=sec",
        portfolio_value=182_430.18,
        cash_balance=14_205.62,
        daily_pl_dollar=-30.50,
        daily_pl_percent=-0.02,
        all_time_pl_dollar=41_982.30,
        all_time_pl_percent=29.9,
        portfolio_beta=1.08,
        performance_chart_url=f"https://your-cdn.example.com/charts/portfolio_vs_spx_180d.png?v={today.isoformat()}",
        holdings=holdings,
        swing_candidates=swing_candidates,
        value_candidates=value_candidates,
        allocation_chart_url=f"https://your-cdn.example.com/charts/allocation_pie.png?v={today.isoformat()}",
        risk_warnings=[
            "⚠️ Industrials sector exposure at 38% — max limit 35%.",
            "⚠️ UNP position size is 5.4% of portfolio — approaching the 6% single-name cap.",
        ],
    )


# ---------------------------------------------------------------------------
# 3. RENDERING LOGIC
# ---------------------------------------------------------------------------

def _currency(value: float) -> str:
    return f"${value:,.2f}"


def _signed_currency(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}"


def _signed_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _pct(value: float) -> str:
    return f"{value:.1f}%"


def build_environment(template_dir: Path) -> Environment:
    """Jinja2 Environment with autoescaping on (holdings' news_bullets /
    macro_stories may ultimately be LLM-synthesized text — autoescape stops
    any stray markup in that copy from being interpreted as live HTML) and
    the custom currency/percent filters the template declares it needs."""
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,  # fail loudly on a missing/misspelled variable
    )
    # Registered as BOTH filters (`{{ value | currency }}`) and globals
    # (`{{ currency(value) }}`) — the template uses function-call syntax
    # throughout, but filter syntax works too if you prefer it.
    for name, fn in (
        ("currency", _currency),
        ("signed_currency", _signed_currency),
        ("signed_pct", _signed_pct),
        ("pct", _pct),
    ):
        env.filters[name] = fn
        env.globals[name] = fn
    return env


def render_digest(digest: DigestData, template_dir: Path, template_name: str = "email_template.html") -> str:
    env = build_environment(template_dir)
    template = env.get_template(template_name)
    return template.render(**asdict(digest))


# ---------------------------------------------------------------------------
# 4. VERIFICATION
# ---------------------------------------------------------------------------

def verify_output(html: str, digest: DigestData) -> None:
    """Cheap smoke tests — not a substitute for visual QA in an email
    client / Litmus, but enough to catch broken templating immediately."""
    assert "{{" not in html and "{%" not in html, \
        "Unrendered Jinja syntax leaked into output — check for typos in variable names."
    assert "<!DOCTYPE html>" in html, "Doctype missing from output."
    assert html.index("<!DOCTYPE html>") < html.index("<body"), "Doctype should precede <body>."
    assert digest.report_date in html, "report_date missing from rendered output."
    for h in digest.holdings:
        assert h.ticker in html, f"Holding {h.ticker} missing from rendered output."
    assert html.count("<table") >= 10, "Suspiciously few <table> elements for a table-based email layout."
    print(f"✓ verify_output passed — {len(html):,} characters, "
          f"{len(digest.holdings)} holding card(s), "
          f"{len(digest.risk_warnings)} risk warning(s).")


# ---------------------------------------------------------------------------
# 5. ENTRYPOINT
# ---------------------------------------------------------------------------

def main() -> None:
    here = Path(__file__).resolve().parent
    digest = build_payload()
    html = render_digest(digest, template_dir=here)
    verify_output(html, digest)
    today_est = datetime.now(ZoneInfo("America/New_York")).strftime("%d_%m_%y")

    out_path = here / "html_reports" / f"{today_est}_report.html"
    # out_path = here / "rendered_digest_preview.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"✓ Wrote preview to {out_path}")


if __name__ == "__main__":
    main()
