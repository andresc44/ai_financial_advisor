outputs_twelvedata_context = 20
params_dict = {
    # Claude-authored (2026-10-15): the users the digest and the watchdog run for, in this order. Each name needs a
    # profile in src/personalized/user_profiles.py. Their .env settings still decide what they get: the
    # pre-/post-market digest (Receive_*_Digest), and the watchdog (holdings source + Telegram chat ID, and
    # watchdog_interval_hours > 0).
    # 2026-10-22: synced from the sign-up sheet at the start of every cron digest run (src/import_signup.py): the
    # sheet's users with nothing required missing. Changes made here are replaced; change the sheet instead.
    "ACTIVE_USERS": [  # Synced from the sign-up sheet (src/import_signup.py): edit the sheet
        "Andres",
    ],
    # Lookback and Lookahead Windows (Days)
    "COMPANY_NEWS_LOOKBACK_DAYS": 30,
    "EARNINGS_FUTURE_LOOKAHEAD_DAYS": 100,
    "FUNDAMENTAL_MONTHS_LOOKBACK": 12, # int: Number of months to look back for fundamental data
    "EARNINGS_SURPRISE": {
        "LIMIT": 3 # int: Number of past quarters (e.g. 4, 8, 12)
    },
    # 3. Insider Sentiment (Monthly MSPR and transaction scores window)
    "INSIDER_SENTIMENT": {
        "DAYS_BACK": 365
    },

    # API Settings
    "FINNHUB_RATE_LIMIT_SLEEP": 0.05,
    # Claude-authored (2026-10-18): Finnhub calls are paced to this many a minute (data_source_guard.FinnhubPacer).
    # 60 is the free plan's limit (Finnhub's X-Ratelimit-Limit header). Raise it with a paid plan.
    "FINNHUB_CALLS_PER_MINUTE": 60,
    # Stage 1 Ticker Filter Parameters
    "MKTCAP_MIN": 10000.0,  # Minimum market cap in Millions USD
    "MKTCAP_MAX": None,  # Maximum market cap in Millions USD
    "VOLUME_MIN": 1.0,  # Minimum trading volume in Millions
    "VOLUME_MAX": None,  # Maximum trading volume in Millions
    "LASTSALE_MIN": 30.0,  # Minimum share price USD
    "LASTSALE_MAX": None,  # Maximum share price USD
    "REGION": None,  # Takes priority over 'country'
    "COUNTRY": None,  # Ignored when region is defined
    "SECTOR": None,  # Takes priority over 'industry'
    "INDUSTRY": None,  # Ignored when sector is defined
    "CLEAR_EXISTING_DATA": True,  # Deletes previous CSV exports in output dir
    "MAX_NEWS_ARTICLES": 20,  # Maximum number of news articles to include per ticker
    
    # Claude-authored (2026-10-16): stages 2 and 3, rewritten to send roughly 30-50 candidates to Claude: profitable,
    # growing, reasonably valued companies that analysts favour. Calibrated against that day's live data with the
    # stage 1 settings above: 595 tickers -> 83 after Yahoo -> 38 after Finnhub (37 companies; GOOG/GOOGL count once).
    # 2026-10-18: loosened a step at Andres's request, to send ~100 to the LLM. Calibrated on that day's data (635 ->
    # 86 -> 38 with the old values): 635 -> 183 after Yahoo -> 98 after Finnhub. The old value is noted on each
    # changed rule. More candidates also mean a longer run (the Finnhub stage is 4 calls per Yahoo survivor) and
    # ~2.5x the LLM cost of scoring them.
    # Counts move with markets and earnings seasons; each run's log shows how many tickers each stage kept.
    # Why the split: Yahoo is free and fetched YAHOO_FETCH_CONCURRENCY tickers at a time, so it does most of the
    # narrowing. Finnhub's free plan allows 60 calls a minute and costs 4 calls per ticker (~4 s each), so it only
    # confirms the quality screen on the ~80 that are left, using trailing-twelve-month (TTM) figures.
    # A ticker missing any value a rule needs is dropped. Units are as each source reports them (below).
    "YAHOO_FETCH_CONCURRENCY": 4,  # Yahoo info requests at once in the stage-2 filter (4 fetched ~400 in 2 min, no errors)

    #Stage 2 Yahoo Filter Parameters
    "YAHOO_FILTERS": [ #["<", "<=", ">", ">=", "==", "=", "!="]
        ("averageVolume", ">=", 1_000_000),     # 3-month average daily volume (shares): liquid on most days, not just one
        ("numberOfAnalystOpinions", ">=", 8),   # Enough coverage for the consensus below to mean something (was 10)
        ("recommendationMean", "<=", 2.4),      # Analyst consensus, 1 = Strong Buy ... 3 = Hold ... 5 = Sell (was 2.2)
        ("forwardPE", ">", 0),                  # Expected to be profitable over the next 12 months
        ("forwardPE", "<=", 35),                # Not priced for perfection (was 30)
        ("profitMargins", ">=", 0.07),          # Net margin, a fraction: >= 7% (was 10%)
        ("revenueGrowth", ">=", 0.05),          # Latest quarter's revenue vs a year earlier, a fraction: >= 5% (was 8%)
        ("returnOnEquity", ">=", 0.12),         # A fraction: >= 12% (was 18%). Missing for negative-equity companies, which drop out
        # Other fields the stage can use: trailingPE, pegRatio, earningsGrowth, debtToEquity (a percentage, missing for
        # banks), freeCashflow, dividendYield, beta, marketCap (dollars)
    ],
    #Stage 3 Finnhub Filter Parameters: (section, field, operator, threshold); percentages are in percent
    "FINNHUB_FILTERS": [
        ("basic_financials", "revenueGrowthTTMYoy", ">=", 9),             # TTM revenue growth >= 9% (was 12%): sustained, not one quarter
        ("basic_financials", "epsGrowthTTMYoy", ">", 0),                   # TTM earnings per share growing
        ("basic_financials", "totalDebt/totalEquityQuarterly", "<=", 1.75), # Debt-to-equity as a ratio, 1.75 = 175% (was 1.5)
        ("basic_financials", "peTTM", ">", 0),                             # Profitable over the last 12 months
        ("basic_financials", "peTTM", "<=", 65),                           # Trailing valuation sanity check (was 60)
        # Other fields: netProfitMarginTTM, roeTTM, currentRatioQuarterly, 52WeekPriceReturnDaily, beta (all in
        # basic_financials), and ("profile", "marketCapitalization", ...) in millions
    ],
    
# ==============================================================================================
    # TWELVEDATA_FILTERS Configuration Guide
    # ==============================================================================================
    # Each rule is a tuple in the format:
    #   (indicator, interval, operator, threshold, [optional_params])
    #
    # ----------------------------------------------------------------------------------------------
    # 1. INDICATOR (str, required)
    #    Technical indicator endpoint supported by Twelve Data (case-insensitive).
    #
    # ----------------------------------------------------------------------------------------------
    # 2. INTERVAL (str, required)
    #    Timeframe evaluated for the indicator. 
    #    NOTE: All technical indicators mathematically require an interval (e.g., "1day", "1h") 
    #    to construct the candlestick time-series bars used in lookback calculations.
    #    Supported: "1min", "5min", "15min", "30min", "45min", "1h", "2h", "4h", "1day", "1week", "1month"
    #
    # ----------------------------------------------------------------------------------------------
    # 3. OPERATOR (str, required)
    #    Comparison operator applied between the latest indicator output and the threshold.
    #    Supported values: "<", "<=", ">", ">=", "==", "!="
    #
    # ----------------------------------------------------------------------------------------------
    # 4. THRESHOLD (int | float, required)
    #    Numeric target value to evaluate against (e.g., 70 for RSI, 20 for ADX).
    #
    # ----------------------------------------------------------------------------------------------
    # 5. OPTIONAL PARAMETERS (dict, optional - 5th tuple element)
    #    Passes custom query parameters directly to the Twelve Data API (e.g., {"time_period": 9}).
    #
    # ----------------------------------------------------------------------------------------------
    # 6. STANDALONE vs. CONTEXT / TIME-SERIES DEPENDENT INDICATORS
    #
    #    ========================================================================================
    #    [CATEGORY A: STANDALONE SCALAR INDICATORS (SAFE FOR STATIC THRESHOLDS)]
    #    ========================================================================================
    #    These output bounded or price-normalized values that evaluate cleanly against static scalars:
    #
    #    • Bounded Momentum Oscillators:
    #        - "rsi"      : Scale 0–100 (Relative Strength Index). Standard: `<= 70` (Overbought), `>= 30` (Oversold).
    #        - "mfi"      : Scale 0–100 (Money Flow Index). Volume-weighted RSI (`<= 80`).
    #        - "willr"    : Scale -100 to 0 (Williams %R). Overbought `> -20`, Oversold `< -80`.
    #        - "stochrsi" : Scale 0–100 (Stochastic RSI). Applied Stochastic to RSI for sensitive momentum.
    #        - "cci"      : Centered at 0 (~80% bounded between -100 and +100). Cyclical price momentum.
    #        - "ultosc"   : Scale 0–100 (Ultimate Oscillator). Blends 3 timeframes (7, 14, 28) to reduce false signals.
    #        - "cmo"      : Scale -100 to +100 (Chande Momentum Oscillator). Direct directional momentum check.
    #
    #    • Bounded & Normalized Trend / Volatility Metrics:
    #        - "adx"      : Scale 0–100 (Average Directional Index). Trend strength (`>= 20` indicates active trend).
    #        - "aroonosc" : Scale -100 to +100 (Aroon Oscillator). Trend direction (`> 0` = uptrend, `> 50` = strong uptrend).
    #        - "roc"      : Percentage change (% return over N bars). Price-agnostic momentum (`>= 0`).
    #        - "beta"     : Systematic volatility ratio vs. market index (`1.0` baseline). Risk filter (`<= 1.5`).
    #
    #    ========================================================================================
    #    [CATEGORY B: CONTEXT & TIME-SERIES DEPENDENT METRICS (REQUIRES SPECIAL HANDLING)]
    #    ========================================================================================
    #    These indicators CANNOT be evaluated effectively against static scalar thresholds. They require 
    #    price-relative context, multi-key JSON parsing, or multi-bar time-series logic:
    #
    #    • Moving Averages ("sma", "ema", "wma"):
    #        - Context Needed: Absolute dollar values (e.g., 200-day SMA = $150.25) depend on stock scale.
    #        - Logic Required: Must be compared dynamically relative to current price (`Price > SMA_200`) 
    #          or as dual moving average crossovers (`EMA_50 > EMA_200`).
    #
    #    • Moving Average Convergence Divergence ("macd"):
    #        - Context Needed: Returns 3 distinct keys (`macd`, `macd_signal`, `macd_hist`). Raw values scale with price.
    #        - Logic Required: Requires key comparison (`macd > macd_signal`) or evaluating if the histogram is positive 
    #          and expanding over time (`macd_hist > 0`).
    #
    #    • Bollinger Bands ("bbands"):
    #        - Context Needed: Returns 3 price-level keys (`upper_band`, `middle_band`, `lower_band`).
    #        - Logic Required: Requires comparing current market price to outer bands (`Price < lower_band`) 
    #          or calculating normalized Bandwidth % `((upper - lower) / middle)`.
    #
    #    • Stochastic Oscillator ("stoch"):
    #        - Context Needed: Returns two lines (`%k` and `%d`). 
    #        - Logic Required: Full signal validation requires tracking historical time-series bars to detect 
    #          a bullish crossover (`%k` crossing above `%d` while below 20).
    #
    #    • Volume & Anchored Metrics ("vwap", "obv"):
    #        - Context Needed: Volume Weighted Average Price (`vwap`) is an intraday benchmark; On-Balance Volume (`obv`) 
    #          is a cumulative running total unbounded over time.
    #        - Logic Required: `vwap` requires comparing against current stock price (`Price > VWAP`); `obv` requires 
    #          evaluating N-period slope or moving average crossover.
    # ==============================================================================================
    # Claude-authored (2026-10-16): where the indicators in TWELVEDATA_FILTERS / TWELVEDATA_CONTEXT come from.
    # "yahoo" (default) computes them from Yahoo's unadjusted daily bars (src/universal/recommendations/local_indicators.py):
    # free, no rate limit, one download for every ticker, and the same values as Twelve Data for rsi, ema, sma, bbands,
    # sar, adx, and atr (checked against Twelve Data to within 0.0001%). "beta" is market beta against SPY. Intervals
    # 1day and 1week only. Anything else (another indicator, an intraday interval, an option such as ma_type) still
    # goes to Twelve Data, whose free plan allows 8 credits a minute (one per ticker per indicator).
    # "twelvedata" sends everything to Twelve Data, as before.
    "TECHNICAL_INDICATOR_SOURCE": "yahoo",
    # Claude-authored (2026-10-18): how many of the most recent daily bars (date, open, high, low, close, volume) go to
    # the LLM with each stock, for new-stock scoring and holdings alike. They show the price path the indicators only
    # summarize: pullbacks into support, higher lows, bounces, gaps, and whether volume is heavier on up or down days.
    # They come from the same Yahoo download as the indicators (no extra calls). At 21 they add ~0.9K input tokens per
    # stock: ~33K a run for 37 candidates, about $0.01 on deepseek-flash or $0.07 on claude-sonnet-5-5. 0 leaves them out.
    "DAILY_BARS_DAYS": 21,
    "TWELVEDATA_FILTERS": [
        # --- Category A: Standalone Bounded Momentum ---
        # ("rsi", "1day", "<=", 70),                                            # Standard 14-period RSI
        # ("rsi", "1day", "<=", 35, {"time_period": 14}),                       # 14-period Daily RSI Exhaustion (Oversold Hard-Deck), the 45-30 (strict) 

        # --- Category B: Trend Strength & Directional Index ---
        # ("adx", "1day", ">=", 25, {"time_period": 14}),                       # 14-period Daily ADX (Ensures Active Strong Trend)
        ("adx", "1day", "<=", 45, {"time_period": 14}),                       # 14-period Daily ADX (Ensures Active Strong Trend)

        # --- Category C: Relative Volume Ratio --- #For Pro Twelvedata plan
        ## ("rvol", "1day", ">=", 1.5),                                          # Relative Volume (Confirms High Liquidity Absorption) 1.2 to 2.0 (strict)

        # --- Category D: Market Volatility & Sensitivity ---
        # ("beta", "1week", ">=", 1.2, {"time_period": 52}),                    # 52-Week Weekly Beta (Selects High-Responsiveness Swing Candidates) 1.05 to 1.5 (strict)

        
    ],
    # Context/time-series indicators fetched for data enrichment (No comparison operators or thresholds)
    
    "TWELVEDATA_CONTEXT": [
        # ("macd", "1day", {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
        # ("bbands", "1day", {"time_period": 20}),
        # ("stoch", "1day", {"fastkperiod": 14, "slowkperiod": 3}),
        # ("sma", "1day", {"time_period": 200}),
        # -----------------------------------------------------------------------------------------
                # ENDPOINT OPTIONS:
                #   - "time_series": Full OHLCV candlestick historical series (Open, High, Low, Close, Volume)
                #   - "quote": Single-candle real-time snapshot (bid, ask, 52-week highs/lows, open/close)
                #   - "eod": End-of-Day single candle data
                #   - "price": Latest single price point
                #
                # INTERVAL OPTIONS:
                #   - Intraday: "1min", "5min", "15min", "30min", "45min", "1h", "2h", "4h", "8h"
                #   - Daily/Long-term: "1day", "1week", "1month"
                #
                # EXTRA_PARAMS DICTIONARY OPTIONS:
                #   - "outputsize": (int, 1-5000, default=30) Number of historical candles/data points to return
                #   - "start_date": (str, 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS') Filter series start boundary
                #   - "end_date":   (str, 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS') Filter series end boundary
                #   - "order":      (str, 'ASC' | 'DESC') Sort order of returned candles (default='DESC')
                #   - "dp":         (int, 0-11) Number of decimal places for price/volume values
                #   - "timezone":   (str, 'Exchange' | 'UTC' | 'America/New_York' etc.) Timezone of timestamps
                # -----------------------------------------------------------------------------------------
                # 1. Standard daily candlestick history (last 30 trading days)
        # ("time_series", "1day", {"outputsize": 60}),
        # ("atr", "1day", {"time_period": 14, "outputsize": 1}),
    # -----------------------------------------------------------------------------------------
        # Determined Metrics for Maximized LLM context
        # 1. BOLLINGER BANDS (BBANDS)
        # Question Answered: "Is the stock unusually cheap right now?"
        # Multi-week Setup: Identifies macro oversold extremes at the lower band on the daily chart.
        # -----------------------------------------------------------------------------------------
        ("bbands", "1day", {"time_period": 20, "sd": 2, "outputsize": outputs_twelvedata_context}),

        # -----------------------------------------------------------------------------------------
        # 2. EXPONENTIAL MOVING AVERAGE (EMA)
        # Question Answered: "Has the price actually started to turn around?"
        # Multi-week Setup: Confirms structural trend direction and momentum shifts over several weeks.
        # -----------------------------------------------------------------------------------------
        ("ema", "1day", {"time_period": 9, "outputsize": outputs_twelvedata_context}),
        ("ema", "1day", {"time_period": 21, "outputsize": outputs_twelvedata_context}),

        # -----------------------------------------------------------------------------------------
        # 3. PARABOLIC SAR (SAR)
        # Question Answered: "Where exactly do we exit if the trade goes wrong?"
        # Multi-week Setup: Provides dynamic daily trailing stops suitable for holding positions weeks/months.
        # -----------------------------------------------------------------------------------------
        ("sar", "1day", {"acceleration": 0.02, "maximum": 0.2, "outputsize": outputs_twelvedata_context}),

        # -----------------------------------------------------------------------------------------
        # 4. RELATIVE STRENGTH INDEX (RSI)
        # Question Answered: "Has the panic selling completely exhausted itself?"
        # Multi-week Setup: Detects daily momentum exhaustion and multi-week bullish divergence.
        # -----------------------------------------------------------------------------------------
        ("rsi", "1day", {"time_period": 14, "outputsize": outputs_twelvedata_context}),

        # -----------------------------------------------------------------------------------------
        # 5. BETA
        # Question Answered: "Will this bounce hard and fast enough to be worth our time?"
        # Multi-week Setup: Evaluates relative market sensitivity calculated over a 1-year weekly window.
        # -----------------------------------------------------------------------------------------
        # Claude-authored (2026-10-16): computed locally as market beta: the slope of the stock's weekly returns on SPY's
        # over 52 weeks. (Twelve Data's "beta" is a statistic between two price series of the same stock, which read
        # 0.02 for SNPS, whose market beta is ~1.1; with TECHNICAL_INDICATOR_SOURCE "twelvedata", comment this out.)
        # Yahoo's own 5-year monthly beta is also in the data, as price_action.beta.
        ("beta", "1week", {"time_period": 52, "outputsize": outputs_twelvedata_context}),
    ],
    # 1. Recommendation Trends (SDK only accepts 'symbol')
    # "RECOMMENDATION_TRENDS": {},
    # 2. Earnings Surprise (Historical quarterly EPS surprise count)

    # 4. Financials Reported (As-reported SEC filings) UNNECESSARY SEC-filing data, waste of tokens
    # "FINANCIALS_REPORTED": {
    #     "FREQ": "annual", # str: 'annual' or 'quarterly'
    #     "ID": None, # str | None: Specific statement ID filter
    #     "ACCESS_NUMBER": None, # str | None: SEC filing access number
    # },
    # 5. Company Basic Financials (Time-series metrics filter)
    "COMPANY_BASIC_FINANCIALS": {
        "METRIC": "all" # str: 'all', 'margin', 'valuation', 'price', etc.
    },

    # Claude-authored (2026-10-16): the Gemini model for each use (both Google Search grounded). Each user's own
    # GEMINI key pays for HOLDING_NEWS; the shared GEMINI_API_KEY pays for MACRO_NEWS. A model without a price in
    # API_PRICES["gemini"] is still counted in api_usage, just not costed.
    # 2026-10-23: no PORTFOLIO_REVIEW any more: the portfolio review is written by LLM["HOLDINGS"] (below)
    "GEMINI_MODELS": {
        "HOLDING_NEWS": "gemini-3.6-flash",      # News bullets per holding (src/personalized/symbol_specific_news.py)
        "MACRO_NEWS": "gemini-3.6-flash",        # Top macro stories in the universal content (src/universal/market_news.py)
    },
    # Claude-authored (2026-10-23, at Andres's request): settings for every Gemini call in the project
    # (src/gemini_settings.py). Until then no call set a thinking level, so gemini-3.6-flash thought as much as it
    # liked: ~90% of the Gemini bill was output tokens, mostly thinking (~2.9K output tokens for ~1.1K in per holding).
    "GEMINI_CONFIG": {
        "effort": "low",            # How much Gemini thinks before answering: minimal | low | medium | high, or
                                    # default (the model decides, as before 2026-10-23). Holding news and macro news
        "max_output_tokens": 8192,  # Safety cap on each answer, thinking included (the largest before was ~5.3K, at the
                                    # model's own effort). Holding news cut off by it is asked for again per stock
    },

    # Claude-authored (2026-10-16): which LLM does each job. "model" is any key of ALLOWED_CLAUDE_MODELS (Anthropic) or
    # ALLOWED_DEEPSEEK_MODELS (DeepSeek); the provider follows from it (src/llm_models.py). "effort" must be one the
    # model accepts:
    #   Claude:   low | medium | high | xhigh | max
    #   DeepSeek: none (non-thinking, like the old V3 / deepseek-chat) | low | high | max (thinking, like the old R1 /
    #             deepseek-reasoner). DeepSeek retired R1 and V3; deepseek-flash and deepseek-v4-pro both do either.
    # Costs follow the model automatically (API_PRICES, shown in each digest's api_usage). Per run of ~37 candidates
    # (2026-10-16 token counts): claude-sonnet-5-5 ~$1.23, deepseek-v4-pro ~$0.38, deepseek-flash ~$0.09 (off-peak,
    # effort "none"; thinking adds output tokens).
    # 2026-10-17: UNIVERSAL switched to deepseek-flash at Andres's request. Measured on the same 37 candidates:
    # $0.06 vs $1.23 for claude-sonnet-5-5, with the same two long-term picks. Its misshapen answers are repaired or
    # asked for again (deepseek_client.validated_answer).
    # 2026-10-19: effort "low" (thinking) at Andres's request. On 8 candidates scored twice each, swing scores moved
    # 3.5 points on average between runs (max 6) instead of 9.9 (max 31.5) without thinking. About 5x the output
    # tokens: the first full run (95 candidates) cost $0.28 instead of ~$0.11 and took 27 minutes instead of ~17.
    # 2026-10-22: effort "high", chosen by Andres from the model comparison (docs/model_selection.md): on the same 90
    # long-term and 30 swing candidates, against Claude Opus 5.5 as the reference, Flash 4.1 high matched Opus's swing
    # picks in every run and ranked long-term candidates best of the Flash settings, for ~$0.23 a run (low: $0.20).
    "LLM": {
        "UNIVERSAL": {"model": "deepseek-flash", "effort": "high"},  # New-stock scoring: shared ANTHROPIC_API_KEY or DEEPSEEK_API_KEY
        # Default for holdings; each user's own key. 2026-10-22: claude-haiku-5-5 (was claude-sonnet-5-5) at Andres's
        # request: Sonnet's reviews averaged 342 output tokens at medium effort, so ~85% of its $0.027 per stock was the
        # ~11.7K input tokens at Sonnet's price; Haiku is ~$0.0013 per stock and keeps users' data with Anthropic
        # (a DeepSeek default would send it to DeepSeek without them choosing).
        # 2026-10-22: effort "low" (was medium), chosen by Andres from the model comparison (docs/model_selection.md):
        # with the holdings rubric worked from code-computed checks, Haiku 5.5 low gave Opus 5.5's label for 93% of 29
        # holdings (medium: 91%), at ~$0.05 a run for both test portfolios against Opus's $2.04.
        "HOLDINGS": {"model": "claude-haiku-5-5", "effort": "low"},
        # 2026-10-22 (Andres): HOLDINGS applies to every user; the sign-up form no longer asks, and the old per-user
        # <NAME>_HOLDINGS_LLM_MODEL / _EFFORT are ignored (user_settings.get_llm_settings). UNIVERSAL_LLM_MODEL /
        # UNIVERSAL_LLM_EFFORT in .env override UNIVERSAL on one machine.
    },
    # Claude-authored (2026-10-16): DeepSeek request settings (src/deepseek_client.py). Note: prompts and the user's
    # holdings data go to DeepSeek's servers when a DeepSeek model is chosen.
    "DEEPSEEK_CONFIG": {
        "base_url": "https://api.deepseek.com",
        "effort": "low",           # Default when a DeepSeek model is chosen without an effort: low thinking (2026-10-19)
        "max_tokens": None,        # None = DeepSeek's default (8K without thinking, 64K with); thinking counts toward it
        "max_concurrency": 10,     # Requests in flight at once (DeepSeek has no fixed rate limit; it slows down instead)
        "timeout_seconds": 300,    # Thinking responses can take minutes
    },

    "CLAUDE_CONFIG": {
        # Claude-authored (2026-10-16): the model moved to LLM above; "effort" here is only the default for a Claude
        # model chosen without one
        "max_tokens": 16000,           # Caps thinking + tool output together, so leave headroom
        "effort": "medium",              # low | medium | high | xhigh | max - primary cost/latency lever
        # No temperature: sampling params are rejected by current models
        "max_concurrency": 15,         # Controls API rate limits & parallel throughput
        # Claude-authored (2026-10-13): "system_prompt" removed from here. It overrode the scoring rubric in
        # src/universal/recommendations/LLM_rec_instr.py and named a tool that doesn't exist. Prompts now live
        # only in LLM_rec_instr.py (new-stock scoring) and src/personalized/LLM_hold_instr.py (holdings).
    },
    "LONG_TERM_N_AMOUNT": 3,  # 2026-10-22, Andres: 3 long-term picks in the digest (was 2)
    "SWING_TERM_N_AMOUNT": 5,
    # Claude-authored (2026-10-22, at Andres's request): a new-stock score that could go either way is scored again.
    # The same stock scored twice can come out ~4 points apart (13.5 at most), so a candidate within BELOW_MIN /
    # ABOVE_MIN points of its list's minimum (MIN_LONG_SCORE / MIN_SWING_SCORE) is scored EXTRA_RUNS more times and
    # keeps the middle score (scorer.rescore_borderline). A list that already has as many candidates clear of the band
    # as it shows (LONG_TERM_N_AMOUNT / SWING_TERM_N_AMOUNT) isn't re-scored: its picks are clear.
    "IDEA_RESCORE": {
        # A score more than this above the minimum is clear: it stays above even after a bad run (long-term 90+,
        # swing 80+). Was an absolute CLEAR_SCORE of 85 until swing's minimum went to 75 (2026-10-22, Andres: "+-5%")
        "ABOVE_MIN": 5.0,
        "BELOW_MIN": 5.0,     # How far under the minimum a score can be and still be re-scored (long-term 80, swing 70)
        "EXTRA_RUNS": 2,      # More scores per borderline candidate (3 in all; the middle one is kept)
    },
    # Claude-authored (2026-10-22, at Andres's request): a holding whose first review says one of SIGNALS is reviewed
    # EXTRA_REVIEWS more times and gets the majority's label (a tie goes to the milder label), so a one-off
    # 'urgent_sell' can't send a Telegram alert, nor a one-off 'profit_sell' flag a holding
    # (recommendation.ParallelHoldingEvaluator). Sentiment alone never triggers a re-review.
    "HOLDINGS_RECHECK": {"SIGNALS": ["urgent_sell", "profit_sell"], "EXTRA_REVIEWS": 2},
    # Both totals are out of 100: the sum of the sub-scores in src/universal/recommendations/LLM_rec_instr.py
    # 2026-10-22, Andres: 85 (was 80). Flash 4.1 scores long-term ideas ~2 points above Opus 5.5 (the model
    # comparison), so ~19 of 90 cleared 80 against Opus's ~9; at 85 about as many clear as with Opus at 82-83
    "MIN_LONG_SCORE": 85.0,
    # 2026-10-22, Andres: 75 for swing (was 80). The swing rubric now scores from checks worked out in code
    # (swing_screen.rubric_checks), and on that day only one of 30 candidates reached 80; the next were 73-74
    "MIN_SWING_SCORE": 75.0,
    # Claude-authored (2026-10-17): the sector mix of each list of picks (scorer.select_top_candidates), by Yahoo's
    # sector. "all_different": no two picks share a sector. "majority_different": more than half the picks are from
    # different sectors, i.e. distinct sectors > picks / 2 (3 sectors among 5 picks, 2 among 2). "any": no rule.
    # A stock the rule leaves out makes room for the next best that fits, if it clears the minimum score, so a
    # list can come up short; the log says which stocks were left out.
    "PICK_SECTOR_RULES": {"LONG_TERM": "all_different", "SWING": "majority_different"},
    # Claude-authored (2026-10-22, Andres's rule): at least this many long-term picks are not tech stocks
    # (scorer.with_non_tech_picks). When the picks have fewer, the best non-tech candidate scoring at least
    # LONG_TERM_NON_TECH_MIN_SCORE joins them (in place of the lowest-scoring tech pick when the list is full); with none,
    # the picks stay as they are (logged). It only applies when something cleared MIN_LONG_SCORE. Tech means Yahoo's sectors
    # in TECH_SECTORS. Communication Services is included: it holds the internet and ad-tech names (GOOGL, META, RDDT,
    # APP, SPOT), and with LONG_TERM "all_different" only one "Technology" stock can be picked anyway, so with Technology
    # alone the rule would never change a pick.
    "LONG_TERM_MIN_NON_TECH_PICKS": 1,
    # 2026-10-22, Andres: the non-tech pick may score from 80, under MIN_LONG_SCORE (85). On that day only chipmakers
    # reached 85, and the best non-tech candidates scored 82-84
    "LONG_TERM_NON_TECH_MIN_SCORE": 80.0,
    # Claude-authored (2026-10-23, Andres's rule): with 3 or more long-term picks, at most this many are tech stocks
    # (TECH_SECTORS), and tech stocks may share a sector with each other; PICK_SECTOR_RULES still applies to the rest.
    # On 2026-10-22 all 9 candidates at 85+ were Technology, and "all_different" let only one in, so the list had 2
    # picks (TSM, and BAM from the non-tech rule) instead of 3. None = tech follows PICK_SECTOR_RULES like the rest
    "LONG_TERM_MAX_TECH_PICKS": 2,
    "TECH_SECTORS": ["Technology", "Communication Services"],
    # Claude-authored (2026-10-18): a swing pick must still be early in its move. A stock past any of these is left
    # out however it scores (scorer.too_far_along): more than MAX_RUN_ATR normal days' ranges (14-day ATR) above
    # its LOOKBACK_DAYS low, an RSI above MAX_RSI, or a close above its upper Bollinger Band. Calibrated on
    # 2026-10-16's 97 candidates: the ones just turning up near support were 0.9-1.9 ranges above their 10-day low;
    # 26 of 97 were more than 3, e.g. TRU, up 20% in 10 days and then the best swing score.
    "SWING_EARLY_ENTRY": {"LOOKBACK_DAYS": 10, "MAX_RUN_ATR": 3.0, "MAX_RSI": 70},
    # Claude-authored (2026-10-20): the swing screen (src/universal/recommendations/swing_screen.py), run beside the
    # long-term screens on the same stage-1 list (the NASDAQ screener above: market cap, volume, and price minimums,
    # so no penny stocks). It looks for decent businesses that are cheap right now and set up to go back up soon.
    # Its stages, each counted in the digest's "About this digest". First settings, on 2026-10-19's data: 613 -> 395
    # fundamentals -> 118 in a setup -> 67 with a plan of 1.5:1 or better -> the best 20 to the LLM. Each swing call
    # costs about a third more than a long-term one, but both are much smaller than the combined call they replaced:
    # that run scored 93 + 20 for $0.14 off-peak, against $0.24-0.26 for 93-96.
    # Loosened a step the same day at Andres's request, for a larger pool to pick the best from (the old value is
    # noted on each changed rule): on the same data, 596 -> 468 -> 187 -> 108 -> the best 40 to the LLM.
    # Tightened 2026-10-22 at Andres's request, to match the rubric without sending fewer ("2026-10-22" on each
    # change); the best are now the ones the code scores highest (swing_screen.screen_points). On that day's data:
    # 476 fundamentals -> 50 in a setup (145 before) -> 30 plans (78) -> 30 to the LLM (40). About 10 of either list
    # could reach 80 (MIN_SWING_SCORE then) even at the top of every band; the ones no longer sent couldn't, apart from NSC
    # (reporting that day), TGT (a 1.4-ATR pullback), and ALAB (still falling: it would need full marks everywhere).
    "SWING_SCREEN": {
        "ENABLED": True,
        # 1. Fundamentals (Yahoo, already fetched for the long-term screen): a decent business, looser than
        # YAHOO_FILTERS. Same (field, operator, threshold) format; a stock missing a field drops out.
        "YAHOO_FILTERS": [
            ("averageVolume", ">=", 1_000_000),     # Liquid (same as long-term)
            ("numberOfAnalystOpinions", ">=", 3),   # Covered by analysts (was 5; long-term: 8)
            ("recommendationMean", "<=", 3.0),      # Consensus Hold or better (was 2.8; long-term: 2.4)
            ("forwardPE", ">", 0),                  # Expected to be profitable over the next 12 months
            ("forwardPE", "<=", 60),                # Not priced for perfection (was 50; long-term: 35)
            ("profitMargins", ">=", 0.01),          # Profitable now: net margin >= 1% (was 3%; long-term: 7%)
            ("revenueGrowth", ">=", -0.05),         # Revenue at most 5% below a year ago (was 0; long-term: +5%)
        ],
        # 2. The setup, from the daily bars. Distances are in ATRs: a normal day's range (14-day average true range).
        "SETUP": {
            "MIN_PRICE": 10.0,             # Not a penny stock, whatever stage 1 lets through
            "MIN_DOLLAR_VOLUME_M": 20.0,   # Trades at least $20M a day (20-day average), so orders fill near the price
            "MIN_RSI": 20,                 # Not in free fall (was 25)...
            "MAX_RSI": 65,                 # ...and not stretched (was 60; SWING_EARLY_ENTRY's MAX_RSI also applies)
            # 2026-10-22: the rubric's four signs of a turn (swing_screen.turn_signs). Requiring one (1) would have
            # cut 2026-10-22's 78 plans by 31: a dip in a good trend that is still falling is ranked down instead...
            "MIN_TURN_SIGNS": 0,
            "EXCLUDE_FALLING_KNIVES": True,  # ...unless RSI is also at a 10-day low: still falling, so left out
            # 2026-10-22: no report this many trading days ahead or fewer (Yahoo's date): event risk, not a setup.
            # The rubric caps the catalyst score at 10 within 5 days, and the swing idea would be a bet on the report
            "EARNINGS_BLACKOUT_SESSIONS": 5,
            # Either setup qualifies (each can be switched off):
            "PULLBACK": {                  # A dip in a healthy longer trend
                "ENABLED": True,
                "MAX_BELOW_SMA200_PCT": 5.0,   # Trend intact: at most 5% below its 200-day average (was 3)...
                # ...or (True) any depth while its 50-day average is still above the 200-day. Off since 2026-10-22: it
                # let through dips 6-20% under the 200-day (SE, CAT, APO), below both averages, which the rubric scores
                # as a weaker trend. The ones within 5% (a test of the 200-day) are still sent, ranked lower.
                "OR_SMA50_ABOVE_SMA200": False,
                "LOOKBACK_DAYS": 20,           # The recent high it pulled back from: the highest close of these days
                "MIN_PULLBACK_ATR": 1.5,       # Pulled back at least this many ATRs from that high (2026-10-22: the
                                               # rubric's orderly pullback is 1.5-6; was 1.0, and 1.5 before that)
                "MAX_PULLBACK_PCT": 30.0,      # but not collapsed (was 25)
            },
            "NEW_UPTREND": {               # The first days of a turn up
                "ENABLED": True,
                "CROSS_WITHIN_DAYS": 5,        # The 9-day average crossed above the 21-day within this many sessions
                                               # (2026-10-22: at most 4 sessions ago, as the rubric; was 7, and 5 before)
            },
            # Also applied: SWING_EARLY_ENTRY above (not too far along its run).
        },
        # 3. The trade plan, set from the chart for every stock the same way (swing_screen.trade_plan). The
        # risk/reward sub-score is computed from it (LLM_recommendation.plan_risk_reward_score), not chosen by the LLM.
        "LEVELS": {
            "SUPPORT_LOOKBACK_DAYS": 10,   # Supports: EMA 21, 50- and 200-day averages, lower Bollinger Band, this low
            "STOP_BUFFER_ATR": 0.5,        # Stop this far below the nearest support under the price...
            "MIN_RISK_ATR": 1.5,           # ...and at least this far below the entry (ordinary noise shouldn't hit it)
            "TARGET_LOOKBACK_DAYS": 60,    # Targets: the recent and this-many-day high closes, the 50- and 200-day
            "MIN_TARGET_ATR": 1.5,         # averages from below, the upper band: the nearest at least this far up,
            "MAX_TARGET_ATR": 8.0,         # capped at this far up (reachable in 2-8 weeks)
        },
        # Claude-authored (2026-10-22, re-banded at Andres's request): the plan's risk/reward points (of 30) at each
        # reward-to-risk ratio, [ratio, points], with straight lines between them (0 at 0.5:1 up to the first) and full
        # marks from the last. Full marks at 4:1 (was 4.5:1): the best an ideal pullback can reach, as its target (the
        # recent high) is at most ~6 ATRs up and its stop at least MIN_RISK_ATR down. No jump at 2.5:1 any more (24 just
        # below it, 25 at it). On 2026-10-20/21's 120 candidates the average fell slightly (24.2 -> 23.6 on 10-21) and
        # the points spread more (sd 2.9 -> 3.4), so plans are told apart better without inflating the totals.
        "RISK_REWARD_POINTS": [[1.5, 12], [2.0, 18], [2.5, 22.5], [3.0, 26], [3.5, 28.5], [4.0, 30]],
        # Kept at 1.5 when loosening: a plan under 1.5:1 earns under 12 of the 30 risk/reward points, so it would need
        # 64+ of the other 70 to reach MIN_SWING_SCORE (75); sending it would only add cost
        "MIN_PLAN_RR": 1.5,                # Plans with less reward per unit of risk can't score well; not sent to the LLM
        # 2026-10-22: the price at most this many ATRs above the support its stop sits under; further, the rubric
        # calls the setup stretched (and the stop is more than 2.5 ATRs away)
        "MAX_ABOVE_SUPPORT_ATR": 2.0,
        # The best plans sent to the LLM (was 20): ranked by the part of the swing score the code can check (2026-10-22:
        # the plan's risk/reward points, the technical start, and the momentum band; was risk/reward alone), then the
        # earliest in its move
        "MAX_CANDIDATES": 40,
    },
    # Claude-authored (2026-10-20): the sanitized sections each horizon's LLM call is sent (sanitizer.horizon_payload).
    # Each call only gets what its rubric uses, which keeps the input tokens down. Swing calls also get the swing
    # screen's setup, facts, and plan.
    "LLM_PAYLOAD": {
        "LONG_TERM": ["profile", "price_action", "valuation_and_fundamentals", "sentiment_and_analysts",
                      "catalysts_and_earnings", "quarterly_financial_trends", "recent_headlines_and_catalysts"],
        "SWING": ["profile", "price_action", "sentiment_and_analysts", "catalysts_and_earnings",
                  "technical_indicators", "daily_bars", "recent_headlines_and_catalysts"],
        # The few fundamentals a swing call gets ("key_fundamentals"), to judge whether a dip changes the business
        "SWING_FUNDAMENTALS": ["market_cap", "pe_forward", "revenue_growth_yoy", "earnings_growth_yoy",
                               "profit_margin", "debt_to_equity"],
    },

    # Claude-authored (2026-10-16): data attached to each universal pick for the digest
    # (src/universal/recommendations/enriched_rec.py), as "name in the digest": "section.field" in the sanitized data
    # Claude scored. "all" goes on every pick; a list's own entries win on the same name. A path that doesn't exist
    # is attached as null with a [WARNING] in the log. Available (src/universal/recommendations/sanitizer.py):
    #   profile: name, sector, industry, business_summary, full_time_employees, ipo_date
    #   price_action: current_price, previous_close, open, day_low, day_high, 52_week_low, 52_week_high,
    #       52_week_change_percent, 50_day_average, 200_day_average, volume, average_volume_10d, average_volume_3m, beta
    #   valuation_and_fundamentals: market_cap, enterprise_value, pe_trailing, pe_forward, peg_ratio, price_to_book,
    #       price_to_sales, enterprise_to_ebitda, enterprise_to_revenue, revenue_total, revenue_growth_yoy,
    #       revenue_growth_3y_cagr, revenue_growth_5y_cagr, gross_margin, ebitda_margin, operating_margin,
    #       profit_margin, earnings_growth_yoy, eps_trailing, eps_forward, free_cash_flow, operating_cash_flow,
    #       total_cash, total_debt, debt_to_equity, current_ratio, quick_ratio, return_on_assets, return_on_equity,
    #       return_on_invested_capital
    #   sentiment_and_analysts: recommendation_key, recommendation_mean, target_mean_price, target_high_price,
    #       target_low_price, analyst_opinions_count, short_percent_of_float, short_ratio, held_percent_insiders,
    #       held_percent_institutions, insider_sentiment_series
    #   catalysts_and_earnings: upcoming_earnings, past_earnings_surprises
    #   quarterly_financial_trends (series): eps, revenue_per_share, operating_margin, net_margin, free_cash_flow_margin
    #   technical_indicators (series, newest first): rsi_14_1day, ema_9_1day, ema_21_1day, bbands_20_1day, sar_1day,
    #       beta_52_1week
    #   recent_headlines_and_catalysts (a whole section: the list of headlines)
    # A number picks a list item: series are newest first, so "technical_indicators.rsi_14_1day.0.rsi" is the latest
    # RSI, and upcoming_earnings is soonest first, so ".0.date" is the next report.
    # Units as the sources give them: margins, growth, and returns are fractions (0.25 = 25%), debt_to_equity is a
    # percentage (150 = 1.5x), market_cap and free_cash_flow are in dollars, prices are in the trading currency.
    # Claude's own output is added to every pick by the scorer, not here: sentiment, thesis, sub_scores, plus
    # key_risk (long-term) and entry_price / stop_loss / price_target / risk_reward (swing).
    "RECOMMENDATION_FIELDS": {
        # Who the company is, where the price sits, and what analysts and the calendar say
        "all": {
            "name": "profile.name",
            "sector": "profile.sector",
            "industry": "profile.industry",
            "current_price": "price_action.current_price",
            "market_cap": "valuation_and_fundamentals.market_cap",
            "52_week_low": "price_action.52_week_low",
            "52_week_high": "price_action.52_week_high",
            "52_week_change_percent": "price_action.52_week_change_percent",
            "analyst_rating": "sentiment_and_analysts.recommendation_key",
            "analyst_target_price": "sentiment_and_analysts.target_mean_price",
            "analyst_count": "sentiment_and_analysts.analyst_opinions_count",
            "next_earnings_date": "catalysts_and_earnings.upcoming_earnings.0.date",
        },
        # Is the business worth owning at this price
        "top_long_term": {
            "pe_trailing": "valuation_and_fundamentals.pe_trailing",
            "pe_forward": "valuation_and_fundamentals.pe_forward",
            "peg_ratio": "valuation_and_fundamentals.peg_ratio",
            "revenue_growth_yoy": "valuation_and_fundamentals.revenue_growth_yoy",
            "earnings_growth_yoy": "valuation_and_fundamentals.earnings_growth_yoy",
            "profit_margin": "valuation_and_fundamentals.profit_margin",
            "return_on_equity": "valuation_and_fundamentals.return_on_equity",
            "debt_to_equity": "valuation_and_fundamentals.debt_to_equity",
            "free_cash_flow": "valuation_and_fundamentals.free_cash_flow",
        },
        # Where the trade stands technically, to judge the entry, stop, and target Claude gives
        "top_swing": {
            "rsi_14": "technical_indicators.rsi_14_1day.0.rsi",
            "ema_9": "technical_indicators.ema_9_1day.0.ema",
            "ema_21": "technical_indicators.ema_21_1day.0.ema",
            "bollinger_upper": "technical_indicators.bbands_20_1day.0.upper_band",
            "bollinger_lower": "technical_indicators.bbands_20_1day.0.lower_band",
            "parabolic_sar": "technical_indicators.sar_1day.0.sar",
            "50_day_average": "price_action.50_day_average",
            "200_day_average": "price_action.200_day_average",
            "beta": "price_action.beta",
            "volume": "price_action.volume",
            "average_volume_3m": "price_action.average_volume_3m",
        },
    },


    # Claude-authored (2026-10-15): holdings source health (src/personalized/pull_holdings_data.py)
    "HOLDINGS": {
        "STALE_SYNC_HOURS": 96,  # Notify the user when SnapTrade hasn't synced an account's holdings for this long
        # Claude-authored (2026-10-21): the currencies a user can set as their home currency (<NAME>_HOME_CURRENCY).
        # The digest's overview shows the portfolio in it, and also in USD when it isn't USD; the holdings stay in USD.
        # Holdings themselves can be in these currencies too (a .DE, .L, .AX, or .CL listing, or one SnapTrade or the
        # sheet says is in EUR, GBP, AUD, or COP); src/fx_rates.py converts them to USD.
        "HOME_CURRENCIES": ["USD", "CAD", "EUR", "GBP", "AUD", "COP"],
        "DEFAULT_HOME_CURRENCY": "USD",  # For a user without a valid <NAME>_HOME_CURRENCY
    },

    # Claude-authored (2026-10-16): the universal content alerts the admins when a data source is missing for more
    # than this share of the screened tickers (src/universal/recommendations/data_source_guard.py)
    "UNIVERSAL_DATA_QUALITY": {
        "MAX_MISSING_PCT": 20,
    },

    # Claude-authored (2026-10-16): the rest of the universal content (src/universal/universal_consolidated.py).
    # Both run in the background while the picks are built; set one to False to leave it out (e.g. to save the
    # Gemini call while testing).
    "UNIVERSAL_EXTRAS": {
        "MACRO_NEWS": True,        # Top macro stories from Gemini with Google Search, shared GEMINI_API_KEY
        "MACRO_NEWS_STORIES": 4,   # How many (Claude-authored, 2026-10-20: 4, was 5, to shorten the email)
        "MARKET_DASHBOARD": True,  # Index/VIX/yield moves, 10Y-2Y spread (FRED_API_KEY), Fear & Greed, S&P breadth,
                                   # Cboe put/call ratio
    },
    # Claude-authored (2026-10-21): each market indicator's grade (green / yellow / red, grey for no clear signal) in
    # the email's Markets & macro section (create_html._signals). Started from Andres's "Global Macro Pulse" table;
    # the levels were then set from research and 2 years of data (2026-10-21, at his request; the reasons are on each).
    # The glossary (src/digest/glossary.py) quotes these same levels.
    "MARKET_SIGNALS": {
        # Shown first when Markets & macro opens, in this order; the rest are under "All indicators". "Market mood" is
        # VIX and Fear & Greed read together (MOOD_MATRIX below).
        "KEY_SIGNALS": ["S&P 500", "Market mood", "S&P breadth", "10Y yield", "Nasdaq-100 vs Dow"],
        # Benchmarks: the S&P 500 against its 50- and 200-day averages; the Nasdaq-100 against the Dow (growth vs
        # value), the NYSE Composite against the S&P (concentration), and the Russell 2000 against the S&P (credit)
        "RELATIVE_WINDOW": "5D",        # The window those comparisons use: "1D", "5D", or "1M"
        "RELATIVE_GAP_PTS": 2.0,        # A lead of this many percentage points counts (Andres, 2026-10-21; was 1, which
                                        # flagged a lead on 36-55% of days over 2 years: noise rather than a signal)
        # Sentiment
        "VIX_CALM_BELOW": 20,           # VIX's long-run average is about 19.5-20: under it calm (78% of days in the last
        "VIX_PANIC_ABOVE": 30,          # 2 years), 20-30 elevated, over 30 stress (3% of days)
        "PUT_CALL_LOW_BELOW": 0.70,     # Cboe total put/call: under it heavy call buying, i.e. over-exuberance: yellow
        "PUT_CALL_HIGH_ABOVE": 1.00,    # Over it heavy put buying, i.e. fear: red. Between: a normal balance, green
        # CNN's own Fear & Greed bands: 0-24 extreme fear (red), 25-44 fear (yellow), 45-55 neutral and 56-75 greed
        # (green), 76-100 extreme greed (yellow: froth, often near tops)
        "FEAR_GREED_EXTREME_FEAR_BELOW": 25,
        "FEAR_GREED_FEAR_BELOW": 45,
        "FEAR_GREED_GREED_ABOVE": 55,
        "FEAR_GREED_EXTREME_GREED_ABOVE": 75,
        "BREADTH_WEAK_BELOW": 40,       # % of S&P 500 stocks above their 50-day average: red under it (a narrow market),
        "BREADTH_STRONG_ABOVE": 60,     # green over it (broad strength), yellow between. No "overbought" flag: readings
                                        # over 90% have historically been followed by gains (SentimentTrader)
        # Macro
        "DOLLAR_FALL_PCT": 1.0,         # US Dollar Index over 5 days: green when down this much (a tailwind), red when up
        "DOLLAR_FAST_RISE_PCT": 1.5,    # this much (a headwind; the top ~10% of weeks), grey between (a typical week
                                        # moves about 0.6%)
        "YIELD_MOVE_BPS": 25,           # 10Y yield over a month: red when up this many basis points (a quarter point;
                                        # 11% of months), green when down as much (a typical month moves ~12 bps)
        "CURVE_RECENT_DAYS": 182,       # 10Y-2Y spread: red when inverted, yellow when it turned positive within this many
                                        # days after inverting (historically closer to recessions than the inversion)
        "OIL_BREAKOUT_PCT": 10.0,       # WTI over 5 days: red when up this much (inflation), yellow when down as much (the
                                        # top ~10% of weekly moves; a typical week moves about 3%)
        # The market's mood, from VIX and Fear & Greed read together (create_html._mood): VIX comes from S&P 500 option
        # prices and jumps within hours of a shock, while Fear & Greed blends seven gauges and moves over days. VIX
        # state (calm / elevated / panic, at the VIX levels above) -> Fear & Greed state (fear under 45, neutral, greed
        # over 55) -> (label, dot: "pos" green / "warn" yellow / "neg" red, what it means). The label is also the
        # collapsed Markets & macro line.
        "MOOD_MATRIX": {
            "calm": {
                "greed": ("Confident", "pos", "Low volatility and eager buyers: a supportive backdrop for stocks, though "
                                              "complacency tends to build when everyone is relaxed."),
                "neutral": ("Steady", "pos", "Low volatility and a balanced mood: normal conditions, with no strong pull "
                                             "either way."),
                "fear": ("Quietly cautious", "warn", "Prices are calm but investors are wary: more often a slow grind or a "
                                                     "rotation between sectors than a sell-off."),
            },
            "elevated": {
                "greed": ("Jittery rally", "warn", "Buyers are still eager while options price in bigger swings: rallies "
                                                   "can stall or reverse quickly."),
                "neutral": ("Unsettled", "warn", "Volatility is rising before the broad mood has turned: an early sign of "
                                                 "stress worth watching."),
                "fear": ("Stressed", "neg", "Rising volatility and fearful investors: a risk-off market, where selling can "
                                            "feed on itself."),
            },
            "panic": {
                "greed": ("Fresh shock", "neg", "Volatility has spiked while the slower gauges still look upbeat: a new "
                                                "shock the market is still absorbing."),
                "neutral": ("Shock spreading", "neg", "A volatility spike the broad mood is starting to reflect: the "
                                                      "sell-off is widening."),
                "fear": ("Capitulation", "neg", "Fear on both gauges: the worst of a sell-off, which has often come near "
                                                "short-term bottoms for patient buyers."),
            },
        },
    },

    # Claude-authored (2026-10-16): list prices for the cost estimates in each digest (src/usage_tracking.py).
    # USD per 1M tokens. Each model has a schedule of {"from": date, ...} entries so a published price change applies
    # from its date. Update AS_OF and the entries when prices change; models missing here are counted but not costed.
    "API_PRICES": {
        "AS_OF": "2026-10-16",
        "SOURCES": ["https://platform.claude.com/docs/en/about-claude/pricing", "https://ai.google.dev/gemini-api/docs/pricing",
                    "https://api-docs.deepseek.com/quick_start/pricing"],
        "ANTHROPIC_CACHE_WRITE_MULTIPLIER": 1.25,  # 5-minute cache writes cost 1.25x the input price
        "GEMINI_FREE_SEARCH_REQUESTS_PER_MONTH": 5000,  # Google Search grounding, shared across Gemini 3+ models per key
        "anthropic": {
            "claude-fable-5-1": [{"from": "2026-01-01", "input": 10.0, "output": 50.0, "cache_read": 0.25}],
            "claude-fable-5": [{"from": "2026-01-01", "input": 10.0, "output": 50.0, "cache_read": 1.0}],
            "claude-opus-5-5": [{"from": "2026-01-01", "input": 4.0, "output": 20.0, "cache_read": 0.20}],
            "claude-opus-5": [{"from": "2026-01-01", "input": 5.0, "output": 25.0, "cache_read": 0.50}],
            "claude-opus-4-8": [{"from": "2026-01-01", "input": 5.0, "output": 25.0, "cache_read": 0.50}],
            "claude-sonnet-5-5": [{"from": "2026-01-01", "input": 2.0, "output": 10.0, "cache_read": 0.20}],
            "claude-sonnet-5": [{"from": "2026-01-01", "input": 2.0, "output": 10.0, "cache_read": 0.20}],
            "claude-haiku-5-5": [{"from": "2026-01-01", "input": 0.10, "output": 0.50, "cache_read": 0.01,
                                  # Prompts over 100K tokens are billed at higher rates (cache read assumed 0.1x input)
                                  "above_tokens": 100_000, "above": {"input": 0.50, "output": 2.50, "cache_read": 0.05}}],
        },
        "gemini": {
            # Output includes thinking tokens. Google's announced price change takes effect on 2027-01-01.
            "gemini-3.6-flash": [
                {"from": "2026-01-01", "input": 0.75, "output": 3.75, "cached_input": 0.075, "search_per_1000": 14.0},
                {"from": "2027-01-01", "input": 1.50, "output": 7.50, "cached_input": 0.15, "search_per_1000": 14.0},
            ],
        },
        # Claude-authored (2026-10-16): peak prices, with "off_peak" ones (50%) outside DEEPSEEK_PEAK_HOURS_UTC.
        # "input" is a cache miss, "cache_read" a cache hit (DeepSeek caches repeated prompt prefixes automatically).
        # Output includes thinking tokens. deepseek-v4-flash is the legacy name, billed as deepseek-flash.
        "deepseek": {
            "deepseek-flash": [{"from": "2026-10-16", "input": 0.30, "output": 1.20, "cache_read": 0.006,
                                "off_peak": {"input": 0.15, "output": 0.60, "cache_read": 0.003}}],
            "deepseek-v4-flash": [{"from": "2026-10-16", "input": 0.30, "output": 1.20, "cache_read": 0.006,
                                   "off_peak": {"input": 0.15, "output": 0.60, "cache_read": 0.003}}],
            "deepseek-v4-pro": [{"from": "2026-10-16", "input": 1.32, "output": 3.96, "cache_read": 0.044,
                                 "off_peak": {"input": 0.66, "output": 1.98, "cache_read": 0.022}}],
        },
        # Peak = these UTC hours, Monday to Friday (Chinese public holidays are off-peak too, which isn't modelled, so
        # those days are slightly overestimated). The digest (11:10 / 20:10 UTC) and the watchdog run off-peak.
        "DEEPSEEK_PEAK_HOURS_UTC": [[1, 4], [6, 10]],
    },

    # Claude-authored (2026-10-15): failure alerts and run logs (src/telegram_alert/notices.py, src/run_logging.py)
    "OPS": {
        "ALERT_USERS": ["Andres"],     # Users (with a Telegram chat ID) told when a digest or watchdog run fails
        "ALERT_COOLDOWN_HOURS": 6,     # The same failure is reported at most once per this many hours
        # Claude-authored (2026-10-21): on a weekday the NYSE is closed (a US market holiday), tell the ALERT_USERS on
        # Telegram, once, from the first digest run after DIGEST.PRE_MARKET_TIME_ET (src/telegram_alert/holiday_notice.py)
        "HOLIDAY_NOTICE": True,
        "LOG_MAX_MB": 5,               # logs/<run>.log rotates at this size ...
        "LOG_BACKUPS": 5,              # ... keeping this many old files
    },

    # ==============================================================================================
    # Claude-authored (2026-10-13): holdings watchdog scheduling + per-user Claude model selection.
    # Why: lets each user choose how often their holdings are re-evaluated, during which market
    # session, and with which Claude model/effort, while keeping Finnhub / Twelve Data / Claude
    # spend bounded by only re-evaluating stocks that changed. Full walkthrough: docs/watchdog.md
    # ==============================================================================================
    "WATCHDOG": {
        "SESSION": "regular",                    # Default for all users: "regular" | "extended" | "24_7" (per-user override: watchdog_session)
        "TIMEZONE": "America/New_York",          # Market-local timezone used for trading dates and the "evaluated at" time
        "EXCHANGE_CALENDARS": ["XNYS", "XTSE"],  # exchange_calendars codes; a day is a trading day if ANY of these trades
        "SESSIONS": {
            # Offsets are measured from each day's actual open/close (not fixed clock times),
            # so half-days (e.g. 13:00 closes) shorten the window automatically.
            # The "extended" offsets also define the pre_market / after_hours phases reported to Claude.
            "regular": {"pre_open_minutes": 0, "post_close_minutes": 0, "trading_days_only": True},
            "extended": {"pre_open_minutes": 330, "post_close_minutes": 240, "trading_days_only": True},  # 4:00-20:00 ET on full days
            "24_7": {"trading_days_only": False},
        },
        "DEFAULT_INTERVAL_HOURS": 4,             # Hours between scheduled checks (per-user override: watchdog_interval_hours; 0 = watchdog off)
        "MAX_INTERVAL_HOURS": 168,               # Upper bound accepted from a user's setting (1 week)
        "TRIPWIRE_DEFAULT": True,                # Hourly price check between scheduled checks (per-user override: watchdog_tripwire)
        "REEVAL_NATR_MULTIPLE": 1.0,             # On a scheduled check, re-evaluate a stock that moved >= this many NATRs since its last evaluation
        "TRIPWIRE_NATR_MULTIPLE": 2.0,           # Between scheduled checks, force an early evaluation at >= this many NATRs
        "MAX_EVALUATION_AGE_HOURS": 24,          # On a scheduled check, re-evaluate anything older than this even if it hasn't moved
        "EARNINGS_WINDOW_DAYS": 7,               # On a scheduled check, re-evaluate a stock whose next earnings date is within this many days
        "ALERT_RECOMMENDATIONS": ["urgent_sell"],  # Recommendations that trigger a Telegram alert
        "ALERT_COOLDOWN_HOURS": 24,              # Suppress a repeat alert for the same (user, symbol, recommendation)
        "ALERT_LOG_RETENTION_DAYS": 30,          # Days of alert history kept in data/alerts_sent/
        # Claude-authored (2026-10-21): a Telegram reminder when a held stock reports earnings within this many hours,
        # once per stock and report (src/telegram_alert/earnings_reminder.py, from the hourly digest run; 0 = off)
        "EARNINGS_REMINDER_HOURS": 24,
        "MAX_CLAUDE_CALLS_PER_USER_DAY": 40,     # Cap on watchdog LLM calls (Claude or DeepSeek) per user per market-local day (digest calls not counted)
        "FRESH_CONTEXT_MAX_AGE_MINUTES": 30,     # Quote + news in the cached market context older than this are re-fetched
        "CONTEXT_CACHE_RETENTION_DAYS": 7,       # Drop cached market context for symbols not refreshed within this many days
        "MAX_PARALLEL_USERS": 4,                 # Claude-authored (2026-10-15): users checked at the same time each hour
    },
    # Claude-authored (2026-10-14): email digest display fields (src/personalized/digest_fields.py)
    "DIGEST": {
        "CATALYST_WINDOW_DAYS": 14,              # Holding cards list upcoming earnings within this many days
        "SYMBOL_NEWS_BULLETS": 2,                # News bullets per holding card (src/personalized/symbol_specific_news.py); 2026-10-20: 2, was 3
        "SYMBOL_NEWS_MIN_PRIORITY": "MEDIUM",    # Claude-authored (2026-10-20): lower-priority stories (routine commentary, recaps) are left out
        "SYMBOL_NEWS_LOOKBACK_DAYS": 7,          # How many days back Gemini searches for each holding's news
        # Claude-authored (2026-10-22, Andres's rule): with more holdings than NEWS_FULL_UP_TO_HOLDINGS, each card shows
        # its NEWS_LARGE_PORTFOLIO_BULLETS most important stories, except stocks flagged NEWS_FULL_SIGNALS, which keep
        # SYMBOL_NEWS_BULLETS; so a big portfolio fits Gmail's size limit before fit_for_gmail has to cut more
        "NEWS_FULL_UP_TO_HOLDINGS": 10,
        "NEWS_LARGE_PORTFOLIO_BULLETS": 1,
        "NEWS_FULL_SIGNALS": ["urgent_sell", "risky"],
        "PORTFOLIO_REVIEW_BULLETS": 5,           # Max notes in the whole-portfolio review (src/personalized/portfolio_review.py)
        # Claude-authored (2026-10-23): the review is written by LLM["HOLDINGS"] (Claude Haiku 5.5, low) instead of
        # Gemini; this caps its answer, thinking included (5 notes are ~400 tokens)
        "PORTFOLIO_REVIEW_MAX_TOKENS": 8000,
        # Claude-authored (2026-10-23): this many holdings share one Gemini news request (was one request each). A
        # stock the answer leaves out is asked for again on its own. 3 (Andres, was 5): on his 10 holdings, 5 per
        # request kept 11 stories against 13 for one each, at about half the cost ($0.012 against $0.022)
        "SYMBOL_NEWS_BATCH_SIZE": 3,
        # Claude-authored (2026-10-23): a holding's news fetched earlier the same US Eastern day (the Pre-Market
        # digest's) is shown again in a later digest instead of asking Gemini, unless its price moved more than
        # MAX_MOVE_NATR normal days' ranges (its NATR) since, its signal changed, or it reported earnings
        # (src/personalized/news_reuse.py). ENABLED False always fetches
        "NEWS_REUSE": {"ENABLED": True, "MAX_MOVE_NATR": 1.0},
        # Claude-authored (2026-10-15): parallelism and reuse
        "SYMBOL_NEWS_CONCURRENCY": 4,            # Gemini news requests (of SYMBOL_NEWS_BATCH_SIZE holdings) in flight at once per user
        "MAX_PARALLEL_USERS": 4,                 # Users whose digest content is built at the same time (alongside the universal content)
        "REUSE_EVALUATION_MAX_AGE_MINUTES": 60,  # Reuse a watchdog evaluation this recent instead of calling Claude again (0 = off)
        # Claude-authored (2026-10-15): when the digest is due (src/digest/digest_schedule.py). Cron runs it hourly at :10.
        "PRE_MARKET_TIME_ET": "07:00",           # Pre-Market digest from this US Eastern time until the open
        "POST_MARKET_DELAY_MINUTES": 10,         # Post-Market digest this long after the close (16:10 ET), so closing data is final
        "POST_MARKET_SEND_WINDOW_MINUTES": 240,  # A missed Post-Market run is retried for this long after it was due
        # Claude-authored (2026-10-17): the email itself (src/digest/create_html.py)
        "AMP_EMAIL": True,      # Also send an AMP version, whose dropdowns work in the Gmail app (needs setup: docs/watchdog.md)
        # Claude-authored (2026-10-21): the button under the greeting: Finviz's S&P 500 heat map, sized by market cap
        # and coloured by the past week's performance (st=w1; st=d1 is one day, w4 a month)
        "HEAT_MAP_URL": "https://finviz.com/map?t=sec&st=w1",
        "HEAT_MAP_LABEL": "S&P 500 heat map · 1 week",
        # Claude-authored (2026-10-21): where each market signal's name links (Andres asked for Yahoo or another site
        # than StockCharts). Google Finance opens at its 1-month view (window=1M) for the series it has; its links
        # can't preset a comparison, so each "A vs B" opens A. It has no dollar index (Yahoo, which opens on 1 day),
        # no breadth series (Barchart's $S5FI, on 3 months), and no spread (FRED, the full recent history). CNN's
        # page has Fear & Greed and its put/call chart. A signal without a link here shows its name as plain text.
        "INDICATOR_LINKS": {
            "S&P 500": "https://www.google.com/finance/quote/SPY:NYSEARCA?window=1M",
            "Nasdaq-100 vs Dow": "https://www.google.com/finance/quote/QQQ:NASDAQ?window=1M",
            "NYSE vs S&P 500": "https://www.google.com/finance/quote/NYA:INDEXNYSEGIS?window=1M",
            "Russell 2000 vs S&P": "https://www.google.com/finance/quote/IWM:NYSEARCA?window=1M",
            "Market mood": "https://www.cnn.com/markets/fear-and-greed",
            "VIX": "https://www.google.com/finance/quote/VIX:INDEXCBOE?window=1M",
            "Put/call ratio": "https://www.cnn.com/markets/fear-and-greed",
            "Fear & Greed": "https://www.cnn.com/markets/fear-and-greed",
            "S&P breadth": "https://www.barchart.com/stocks/quotes/$S5FI/overview",
            "US Dollar (DXY)": "https://finance.yahoo.com/quote/DX-Y.NYB/",
            "10Y yield": "https://www.google.com/finance/quote/TNX:INDEXCBOE?window=1M",
            "10Y–2Y spread": "https://fred.stlouisfed.org/series/T10Y2Y",
            "Crude oil (WTI)": "https://www.google.com/finance/quote/CLW00:NYMEX?window=1M",
        },
        # Main sections that start expanded; the rest start collapsed, showing a one-line summary. Any of:
        # "risk", "markets", "holdings", "swing", "long_term" (the overview at the top is always shown)
        "OPEN_SECTIONS": [],
    },
    # Server-side refusal fallback (fallbacks="default") in both Claude evaluators: LLM_recommendation.py
    # (new-stock scoring) and src/personalized/recommendation.py (holdings). Only sent for the models listed below.
    "CLAUDE_REFUSAL_FALLBACK": True,
    "CLAUDE_FALLBACK_DEFAULT_MODELS": ["claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"],
    # Claude models that LLM (above) and each user's HOLDINGS_LLM_MODEL may use, with the effort levels each accepts.
    # Both evaluators send a strict tool schema with tool_choice "auto", so every model here must support strict
    # tool use (2026-10-13: Opus 4.7, Opus 4.6, and Sonnet 4.6 removed because they don't).
    "ALLOWED_CLAUDE_MODELS": {
        "claude-fable-5-1": ["low", "medium", "high", "xhigh", "max"],
        "claude-fable-5": ["low", "medium", "high", "xhigh", "max"],
        "claude-opus-5-5": ["low", "medium", "high", "xhigh", "max"],
        "claude-opus-5": ["low", "medium", "high", "xhigh", "max"],
        "claude-opus-4-8": ["low", "medium", "high", "xhigh", "max"],
        "claude-sonnet-5-5": ["low", "medium", "high", "xhigh", "max"],
        "claude-sonnet-5": ["low", "medium", "high", "xhigh", "max"],
        "claude-haiku-5-5": ["low", "medium", "high", "xhigh", "max"],
    },
    # Claude-authored (2026-10-16): DeepSeek models LLM and HOLDINGS_LLM_MODEL may use. "none" = non-thinking (V3-like),
    # low / high / max = thinking (R1-like). Flash is the cheap fast tier, v4-pro the stronger one.
    # 2026-10-22 (checked with GET /models): "deepseek-flash" is served by DeepSeek-V4.1-Flash-1017 (V4.1-Flash since
    # 2026-09-10, its 1017 update since 2026-10-17; same price), so Finnbot is on Flash 4.1 with no change; the old
    # "deepseek-v4-flash" is retired and routes to it. The API's effort levels are low / high / max (default high):
    # "medium" is accepted but scored like "high" in the 2026-10-22 model comparison, so it isn't offered here.
    "ALLOWED_DEEPSEEK_MODELS": {
        "deepseek-flash": ["none", "low", "high", "max"],
        "deepseek-v4-pro": ["none", "low", "high", "max"],
    },
}

# Claude-authored (2026-10-23, at Andres's request): values changed in the "Parameters" tab of the sign-up spreadsheet
# replace the ones above. Each cron digest run reads that tab and keeps the accepted values in data/remote_settings.json
# (src/remote_settings.py), and every Finnbot process applies them here as it starts. This file stays the defaults.
from src.settings_overrides import apply_saved  # noqa: E402

apply_saved(params_dict)
