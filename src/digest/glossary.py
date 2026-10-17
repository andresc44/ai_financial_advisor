"""
The digest email's glossary: the terms, abbreviations, and indicators it uses, and how to read them.

Authorship: Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
- 2026-10-17: created. Static text, shown in a collapsed card at the bottom of the email (create_html.py and
  email_template.html). Keep entries to a sentence or two: fit_for_gmail drops the whole glossary first when a
  digest would pass Gmail's ~102 KB clipping limit.
"""

# (category, [(term, explanation), ...]), in the order the email shows them
GLOSSARY = [
    ("Signals & ideas", [
        ("Hold", "The position looks fine; no action suggested."),
        ("Trim", "Consider selling part of the position, e.g. to lock in gains or shrink an oversized weight."),
        ("Risk alert", "Something is going wrong, such as a broken trend, bad news, or too much weight. Watch closely "
                       "and decide soon."),
        ("Urgent sell", "A serious, time-sensitive problem. Consider selling at the next chance; it also triggers a "
                        "phone alert."),
        ("Sentiment", "The model's overall read on a stock: Bullish (likely up), Neutral, or Bearish (likely down)."),
        ("Score", "0-100: how strongly a new idea meets the model's checklist. Higher is stronger; 90+ shows in green."),
        ("Swing idea", "A short-term trade lasting days to a few weeks, with a planned entry, stop, and target."),
        ("Long-term idea", "A business worth owning for years, judged on valuation, financial health, growth, and "
                           "competitive moat."),
    ]),
    ("Your portfolio", [
        ("Prev. day / Day", "The change over one full trading session, close to close. The pre-market digest shows "
                            "the previous session; the post-market digest shows today's."),
        ("Unrealized P/L", "Your gain or loss on positions you still hold, versus what you paid. It becomes realized "
                           "only when you sell."),
        ("Weight", "A position's share of your total portfolio value."),
        ("Flagged", "Positions marked Trim, Risk alert, or Urgent sell."),
        ("Beta", "How much a stock or portfolio tends to move with the S&P 500: 1 moves with it, 2 about twice as "
                 "much, below 1 is calmer."),
        ("Volatility", "How much the price typically swings over a year. Higher means a bumpier ride."),
        ("ATR", "Average True Range: the typical daily price range, here as a % of the price. Useful for setting "
                "stops."),
        ("Leveraged ETF (2x, 3x)", "A fund that multiplies an index's daily move. Gains and losses compound, and it "
                                   "loses value in choppy markets."),
    ]),
    ("Technical indicators", [
        ("RSI 14", "Relative Strength Index: momentum on a 0-100 scale over 14 days. Above 70 is overbought (may "
                   "pull back); below 30 is oversold (may bounce)."),
        ("EMA 9 / 21", "Exponential moving averages over 9 and 21 days, weighted to recent prices. Price above the "
                       "21 EMA suggests a short-term uptrend; below, a downtrend."),
        ("50 / 200-day average", "Medium- and long-term trend lines. Price above both is a healthy trend; the 50 "
                                 "crossing above the 200 is a bullish 'golden cross'."),
        ("Bollinger bands", "Bands two standard deviations around the 20-day average. Near the upper band the price "
                            "is stretched high; near the lower band, stretched low."),
        ("Parabolic SAR", "A trailing stop level: below the price in an uptrend, above it in a downtrend. A 'flip' to "
                          "the other side signals a possible reversal."),
        ("Volume vs 3-month avg", "Trading volume relative to normal. Well above 1x means unusual interest, which "
                                  "gives a price move more weight."),
        ("52-week range", "The lowest and highest prices of the past year, and where the price sits between them."),
        ("Entry / Stop / Target", "The suggested buy price, the price at which to cut the loss, and the profit "
                                  "target."),
        ("Reward : risk", "Potential gain to the target for each $1 risked to the stop. 2 : 1 or better is generally "
                          "favourable."),
    ]),
    ("Valuation & fundamentals", [
        ("Market cap", "Share price times shares outstanding: the company's total market value."),
        ("P/E (trailing, forward)", "Price divided by earnings per share over the past 12 months (trailing) or the "
                                    "next 12 (forward). Lower can mean cheaper, compared with peers and growth."),
        ("PEG", "P/E divided by the earnings growth rate. Around 1 is fair for its growth; below 1 is cheap for it."),
        ("EPS", "Earnings per share: the company's profit divided by its number of shares."),
        ("Revenue / earnings growth YoY", "The change versus the same period a year earlier."),
        ("Net margin", "Profit as a share of revenue: how much of each sales dollar the company keeps."),
        ("ROE", "Return on equity: profit relative to shareholders' equity. Above about 15% is strong."),
        ("Debt / equity", "Debt relative to shareholders' equity, in %. Higher means more leverage and more risk."),
        ("EBITDA", "Earnings before interest, taxes, depreciation, and amortization: a rough measure of operating "
                   "profit."),
        ("Analyst rating & target", "Wall Street's consensus (Strong buy to Sell) and average 12-month price target; "
                                    "the % is the upside from the current price."),
        ("Earnings (before open, after close)", "When a company reports its quarterly results. Prices often move "
                                                "sharply around them."),
    ]),
    ("Market indicators", [
        ("S&P 500, Nasdaq-100, Dow Jones", "Broad US stock indexes: 500 large companies, 100 large non-financial "
                                           "Nasdaq stocks (tech-heavy), and 30 blue chips. Shown through their ETFs "
                                           "(SPY, QQQ, DIA)."),
        ("Russell 2000, NYSE Comp.", "Small US companies (via IWM), and every stock listed on the New York Stock "
                                     "Exchange."),
        ("VIX", "The market's expected S&P 500 volatility over the next 30 days, the 'fear gauge'. Below about 15 is "
                "calm; above about 25, stressed."),
        ("10Y yield", "The interest rate on 10-year US Treasuries. Rising yields tend to weigh on stock valuations, "
                      "especially growth stocks."),
        ("10Y-2Y spread", "The 10-year minus the 2-year Treasury yield. Below zero ('inverted') has often come "
                          "before recessions."),
        ("US Dollar (DXY)", "The dollar against major currencies. A stronger dollar weighs on multinationals' "
                            "earnings and on commodity prices."),
        ("Crude oil (WTI)", "The US oil price. It drives energy stocks and feeds into inflation."),
        ("Fear & Greed", "CNN's 0-100 gauge of investor mood. Low (fear) has often come near market bottoms, high "
                         "(greed) near tops."),
        ("S&P breadth", "The share of S&P 500 stocks above their 50-day average. Above about 60% is broad strength; "
                        "below about 40%, weakness under the surface."),
        ("5D / 1M", "The change over the last 5 trading days and the last month."),
        ("bps", "Basis points: hundredths of a percentage point, so 100 bps = 1%."),
    ]),
]
