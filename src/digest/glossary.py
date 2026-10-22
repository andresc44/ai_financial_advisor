"""
The digest email's glossary: the terms, abbreviations, and indicators it uses, and how to read them.

Authorship: Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
- 2026-10-17: created. Static text, shown in a collapsed card at the bottom of the email (create_html.py and
  email_template.html). Keep entries to a sentence or two: fit_for_gmail drops the whole glossary first when a
  digest would pass Gmail's ~102 KB clipping limit.
- 2026-10-18: the new holdings signals (Buy more, Hold, Risky, Profit sell, Urgent sell), and swing ideas as
  pullbacks likely to bounce.
- 2026-10-20: every entry tightened to one short sentence (about a quarter fewer words), keeping each term and how
  to read it, so the glossary still fits on a day with 10 holdings and 5 swing ideas.
- 2026-10-21: a digest without holdings (an ideas-only user) leaves out HOLDINGS_ONLY_TERMS, and a swing idea is
  either setup the swing screen finds: a dip in an uptrend or a fresh turn up.
- 2026-10-21: the market indicators follow Andres's "Global Macro Pulse" table, one entry each, with the levels
  of their signal dots taken from params_dict["MARKET_SIGNALS"], so the two can't disagree.
"""
import html
import re
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict

_LEVELS = params_dict.get("MARKET_SIGNALS") or {}
_WINDOW = {"1D": "a day", "5D": "5 days", "1M": "a month"}.get(_LEVELS.get("RELATIVE_WINDOW", "5D"), "5 days")


def _level(key: str, default: float, decimals: int = -1) -> str:
    """A signal level from params_dict["MARKET_SIGNALS"] as the glossary writes it, e.g. 18, or 0.70 with decimals=2."""
    value = _LEVELS.get(key, default)
    return f"{value:.{decimals}f}" if decimals >= 0 else f"{value:g}"

# (category, [(term, explanation), ...]), in the order the email shows them
GLOSSARY = [
    ("Signals & ideas", [
        ("Buy more", "Pulled back, looks cheap, and shows signs of rebounding: a good time to add."),
        ("Hold", "Healthy; give it time to reach its next peak before selling."),
        ("Risky", "Something looks off, so watch closely; selling may be safer, though not necessarily at a good price."),
        ("Profit sell", "Up well, but the run may turn down soon: a good moment to lock in the gain."),
        ("Urgent sell", "Serious and time-sensitive: consider selling at the next chance. Also sends a phone alert."),
        ("Sentiment", "The model's overall read: Bullish (likely up), Neutral, or Bearish (likely down)."),
        ("Score", "0-100: how well a new idea fits the model's checklist; 90+ shows in green."),
        ("Swing idea", "A stock that dipped in an uptrend or just turned up, likely to rise within days to weeks, with "
                       "a planned entry, stop, and target."),
        ("Long-term idea", "A business worth owning for years, judged on valuation, financial health, growth, and "
                           "moat."),
    ]),
    ("Your portfolio", [
        ("Prev. day / Day", "One full session's change, close to close: the previous session before the open, today's "
                            "after the close."),
        ("Unrealized P/L", "Gain or loss versus what you paid on positions you still hold; realized only when you sell."),
        ("Weight", "A position's share of your portfolio's value."),
        ("Flagged", "Positions marked Risky, Profit sell, or Urgent sell."),
        ("Beta", "How much it moves with the S&P 500: 1 moves with it, 2 twice as much, below 1 calmer."),
        ("Volatility", "How much the price typically swings in a year; higher is bumpier."),
        ("ATR", "Average True Range: the typical daily price range, as a % of price; useful for setting stops."),
        ("Leveraged ETF (2x, 3x)", "Multiplies an index's daily move; gains and losses compound, and choppy markets "
                                   "erode it."),
    ]),
    ("Technical indicators", [
        ("RSI 14", "Relative Strength Index, 0-100 momentum over 14 days: above 70 overbought (may pull back), below 30 "
                   "oversold (may bounce)."),
        ("EMA 9 / 21", "9- and 21-day averages weighted to recent prices; price above the 21 suggests an uptrend, "
                       "below a downtrend."),
        ("50 / 200-day average", "Medium- and long-term trends: price above both is healthy; the 50 crossing above the "
                                 "200 is a bullish 'golden cross'."),
        ("Bollinger bands", "Two standard deviations either side of the 20-day average: near the top band the price is "
                            "stretched high, near the bottom, low."),
        ("Parabolic SAR", "A trailing stop: below the price in an uptrend, above it in a downtrend; a flip signals a "
                          "possible reversal."),
        ("Volume vs 3-month avg", "Volume versus normal; well above 1x means unusual interest, giving a move more "
                                  "weight."),
        ("52-week range", "The year's low and high, and where the price sits between them."),
        ("Entry / Stop / Target", "The suggested buy price, the price to cut the loss at, and the profit target."),
        ("Reward : risk", "Potential gain per $1 risked to the stop; 2 : 1 or better is generally good."),
    ]),
    ("Valuation & fundamentals", [
        ("Market cap", "Share price times shares outstanding: the company's total value."),
        ("P/E (trailing, forward)", "Price over the past (trailing) or next (forward) 12 months' earnings per share; "
                                    "lower can mean cheaper, given peers and growth."),
        ("PEG", "P/E divided by earnings growth: about 1 is fair, below 1 cheap for its growth."),
        ("EPS", "Earnings per share: profit divided by shares outstanding."),
        ("Revenue / earnings growth YoY", "The change versus the same period a year earlier."),
        ("Net margin", "The share of each sales dollar kept as profit."),
        ("ROE", "Return on equity: profit per dollar of shareholders' equity; above about 15% is strong."),
        ("Debt / equity", "Debt relative to shareholders' equity, in %; higher means more leverage and risk."),
        ("EBITDA", "Earnings before interest, taxes, depreciation, and amortization: rough operating profit."),
        ("Analyst rating & target", "Wall Street's consensus (Strong buy to Sell) and average 12-month target; the % "
                                    "is the upside from today's price."),
        ("Earnings (before open, after close)", "When quarterly results come out; prices often move sharply around "
                                                "them."),
    ]),
    # Claude-authored (2026-10-21): in the order of the Markets & macro section, at the levels in MARKET_SIGNALS
    ("Market indicators", [
        ("Signal grades", "Each indicator's dot and label: green is good for stocks, yellow a caution, red a warning, "
                          "grey no clear signal. The key ones show first; the rest are under All indicators."),
        ("S&P 500 (SPY)", "500 large US companies weighted by size, the market's baseline: above its 50- and 200-day "
                          "averages is an uptrend, below both a downtrend, between them a pullback or a recovery."),
        ("Nasdaq-100 (QQQ)", "100 large non-financial Nasdaq companies, tech-heavy and rate-sensitive: it leads rallies "
                             "and falls fastest, and beating the Dow means investors are taking risk (risk-on)."),
        ("Dow Jones (DIA)", "30 industrial and financial blue chips, weighted by price: they hold up better in tech "
                            "sell-offs and when rates are high, so beating the Nasdaq-100 means investors are playing "
                            "defense."),
        ("NYSE Composite", "Every NYSE stock (2,000+), not skewed by mega-caps: when the S&P 500 beats it, a few "
                           "giants are carrying the market."),
        ("Russell 2000 (IWM)", "2,000 small US companies, sensitive to borrowing costs: lagging the S&P warns of "
                               "tight credit, leading it signals confidence in the economy."),
        ("A vs B (pts)", f"A's move minus B's over {_WINDOW}, in percentage points; "
                         f"{_level('RELATIVE_GAP_PTS', 2.0)} or more counts as a lead."),
        ("Market mood", "VIX and Fear & Greed read together: VIX jumps within hours of a shock while Fear & Greed moves "
                        "over days, so a VIX spike while greed still shows is a fresh shock, and fear on both is "
                        "capitulation."),
        ("VIX", f"Expected S&P 500 volatility over 30 days, from option prices: under {_level('VIX_CALM_BELOW', 20)} "
                f"(about its long-run average) calm, {_level('VIX_CALM_BELOW', 20)}-{_level('VIX_PANIC_ABOVE', 30)} "
                f"elevated, over {_level('VIX_PANIC_ABOVE', 30)} high fear."),
        ("Put/call ratio", f"Cboe's daily put volume over call volume, beside its 5-day average: "
                           f"{_level('PUT_CALL_LOW_BELOW', 0.7, 2)}-{_level('PUT_CALL_HIGH_ABOVE', 1.0, 2)} a normal "
                           f"balance, over {_level('PUT_CALL_HIGH_ABOVE', 1.0, 2)} heavy put buying (fear), under "
                           f"{_level('PUT_CALL_LOW_BELOW', 0.7, 2)} heavy call buying (over-exuberance)."),
        ("Fear & Greed", f"CNN's 0-100 blend of 7 mood gauges, in CNN's bands: under "
                         f"{_level('FEAR_GREED_EXTREME_FEAR_BELOW', 25)} extreme fear, under "
                         f"{_level('FEAR_GREED_FEAR_BELOW', 45)} fear, up to {_level('FEAR_GREED_GREED_ABOVE', 55)} "
                         f"neutral, up to {_level('FEAR_GREED_EXTREME_GREED_ABOVE', 75)} greed, then extreme greed; "
                         f"the extremes often come near turning points."),
        ("S&P breadth", f"The share of S&P 500 stocks above their 50-day average: over "
                        f"{_level('BREADTH_STRONG_ABOVE', 60)}% a broad, strong market, under "
                        f"{_level('BREADTH_WEAK_BELOW', 40)}% a weak one carried by a few giants."),
        ("US Dollar (DXY)", f"The dollar against major currencies: down {_level('DOLLAR_FALL_PCT', 1.0)}%+ in 5 days "
                            f"is a tailwind for stocks, up {_level('DOLLAR_FAST_RISE_PCT', 1.5)}%+ a headwind for "
                            f"multinationals' earnings and risk assets."),
        ("10Y yield", f"The 10-year Treasury rate, the base cost of capital: up {_level('YIELD_MOVE_BPS', 25)}+ bps "
                      f"in a month weighs on stock valuations, especially growth stocks."),
        ("10Y-2Y spread", "The 10-year minus the 2-year Treasury yield: below zero (inverted) has often preceded "
                          "recessions, and turning positive again has historically come even closer to one."),
        ("Crude oil (WTI)", f"The US oil price, the main energy input: up {_level('OIL_BREAKOUT_PCT', 10.0)}%+ in 5 "
                            f"days feeds inflation; down as much often signals weaker demand."),
        ("5D", "Over the last 5 trading days."),
        ("bps", "Basis points: hundredths of a percent, so 100 bps = 1%."),
        ("pts", "Percentage points: the gap between two percentages, e.g. 50% to 53% is +3 pts."),
    ]),
]

# Claude-authored (2026-10-21): terms only the holdings parts use (their signals, the portfolio's figures). A digest
# without holdings leaves them out, and names what's left of "Your portfolio" IDEAS_ONLY_CATEGORY_NAMES' way.
HOLDINGS_ONLY_TERMS = {"Buy more", "Hold", "Risky", "Profit sell", "Urgent sell", "Unrealized P/L", "Weight",
                       "Flagged", "Volatility", "Leveraged ETF (2x, 3x)"}
IDEAS_ONLY_CATEGORY_NAMES = {"Your portfolio": "Price moves & risk"}

# Claude-authored (2026-10-22, at Andres's request): the glossary shows only the terms its digest uses
# (terms_in_use). A term counts as used when any of its markers is in the email's text: a string as written (case
# matters), or a pattern. A term without markers here is looked for as written.
USAGE_MARKERS: dict[str, tuple] = {
    "Hold": (re.compile(r"\bHold\b"),),
    "Sentiment": ("Bullish", "Bearish", "Neutral"),
    "Swing idea": ("Swing idea",),
    "Long-term idea": ("Long-term idea",),
    "Prev. day / Day": ("prev. day", "Prev. day", "% day", "Day ·", "Day =", "today"),
    "Unrealized P/L": ("Unrealized",),
    "Weight": ("weight", "Weight"),
    "Leveraged ETF (2x, 3x)": ("2x", "3x", "2X", "3X", "Leveraged", "leveraged"),
    "RSI 14": ("RSI",),
    "EMA 9 / 21": ("EMA",),
    "50 / 200-day average": ("50-day", "200-day"),
    "Bollinger bands": ("Bollinger",),
    "Parabolic SAR": ("SAR",),
    "Volume vs 3-month avg": ("Volume", "3-month"),
    "52-week range": ("52-week",),
    "Entry / Stop / Target": ("Entry", "Stop", "Target", "entry $", "stop $", "target $"),
    "Reward : risk": ("Reward", "reward"),
    "Market cap": ("Market cap", "market cap"),
    "P/E (trailing, forward)": ("P/E",),
    "Revenue / earnings growth YoY": ("YoY",),
    "Net margin": ("margin",),
    "Debt / equity": ("Debt", "debt"),
    "Analyst rating & target": ("Analyst", "analyst"),
    "Earnings (before open, after close)": ("Earnings", "earnings"),
    "Signal grades": ("Key signals", "All indicators"),
    "S&P 500 (SPY)": ("S&P 500",),
    "Nasdaq-100 (QQQ)": ("Nasdaq",),
    "Dow Jones (DIA)": (re.compile(r"\bDow\b"),),
    "NYSE Composite": ("NYSE",),
    "Russell 2000 (IWM)": ("Russell",),
    "A vs B (pts)": ("vs Dow", "vs S&P"),
    "Market mood": ("Mood", "mood"),
    "Put/call ratio": ("Put/call",),
    "S&P breadth": ("breadth",),
    "US Dollar (DXY)": ("DXY", "Dollar"),
    "10Y yield": ("10Y",),
    "10Y-2Y spread": ("10Y–2Y", "10Y-2Y", "spread"),
    "Crude oil (WTI)": ("WTI", "Crude"),
    "5D": ("5D",),
    "bps": ("bps",),
    "pts": ("pts",),
}


def visible_text(page: str) -> str:
    """An email's text as a reader sees it: no <style> blocks, no tags, entities decoded."""
    page = re.sub(r"<style[^>]*>.*?</style>", " ", page, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def terms_in_use(categories: list[dict], text: str) -> list[dict]:
    """
    Claude-authored (2026-10-22): the glossary categories ({"category", "terms": [(term, explanation), ...]}) with
    only the terms `text` (the digest's own visible text, without the glossary) uses; empty categories are dropped.
    """
    def used(term: str) -> bool:
        return any(marker.search(text) if isinstance(marker, re.Pattern) else marker in text
                   for marker in USAGE_MARKERS.get(term, (term,)))

    kept = [{**category, "terms": [entry for entry in category["terms"] if used(entry[0])]} for category in categories]
    return [category for category in kept if category["terms"]]
