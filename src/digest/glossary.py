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
"""

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
        ("Swing idea", "A pulled-back stock likely to bounce within days to weeks, with a planned entry, stop, and "
                       "target."),
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
    ("Market indicators", [
        ("S&P 500, Nasdaq-100, Dow Jones", "US indexes of 500 large companies, 100 large non-financial Nasdaq stocks "
                                           "(tech-heavy), and 30 blue chips, via SPY, QQQ, and DIA."),
        ("Russell 2000, NYSE Comp.", "Small US companies (via IWM), and every NYSE-listed stock."),
        ("VIX", "Expected S&P 500 volatility over 30 days, the 'fear gauge': under about 15 calm, over about 25 "
                "stressed."),
        ("10Y yield", "The 10-year US Treasury rate; rising yields tend to weigh on stocks, especially growth stocks."),
        ("10Y-2Y spread", "The 10-year minus the 2-year Treasury yield; below zero ('inverted') has often preceded "
                          "recessions."),
        ("US Dollar (DXY)", "The dollar against major currencies; a stronger dollar weighs on multinationals' earnings "
                            "and commodity prices."),
        ("Crude oil (WTI)", "The US oil price: it drives energy stocks and feeds inflation."),
        ("Fear & Greed", "CNN's 0-100 investor-mood gauge; fear has often come near market bottoms, greed near tops."),
        ("S&P breadth", "The share of S&P 500 stocks above their 50-day average: over about 60% broad strength, under "
                        "about 40% weakness beneath the surface."),
        ("5D / 1M", "The change over the last 5 trading days and the last month."),
        ("bps", "Basis points: hundredths of a percent, so 100 bps = 1%."),
    ]),
]
