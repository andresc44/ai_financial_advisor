outputs_twelvedata_context = 20
params_dict = {
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
    # Stage 1 Ticker Filter Parameters
    "MKTCAP_MIN": 50000.0,  # Minimum market cap in Millions USD
    "MKTCAP_MAX": None,  # Maximum market cap in Millions USD
    "VOLUME_MIN": 0.1,  # Minimum trading volume in Millions
    "VOLUME_MAX": None,  # Maximum trading volume in Millions
    "LASTSALE_MIN": 346.0,  # Minimum share price USD
    "LASTSALE_MAX": 500,  # Maximum share price USD
    "REGION": None,  # Takes priority over 'country'
    "COUNTRY": None,  # Ignored when region is defined
    "SECTOR": None,  # Takes priority over 'industry'
    "INDUSTRY": "Software",  # Ignored when sector is defined
    "CLEAR_EXISTING_DATA": True,  # Deletes previous CSV exports in output dir
    "MAX_NEWS_ARTICLES": 10,  # Maximum number of news articles to include per ticker
    
    #Stage 2 Yahoo Filter Parameters
    "YAHOO_FILTERS": [ #["<", "<=", ">", ">=", "==", "=", "!="]
        ("marketCap", ">=", 10_000),
        ("regularMarketPreviousClose", ">", 350),
        # Add future Yahoo filters here:
        # ("trailingPE", "<=", 25),
        # ("dividendYield", ">", 0.02),
    ],
    "FINNHUB_FILTERS": [
        ("profile", "marketCapitalization", ">=", 1_000),
        ("quote", "t", ">", 100_000),
        ("basic_financials", "netProfitMarginTTM", ">", 1),
        ("basic_financials", "peTTM", ">", 0),
        ("basic_financials", "peTTM", "<=", 90),
        ("basic_financials", "revenueGrowthTTMYoy", ">", -30),
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
    
    "CLAUDE_CONFIG": {
        "model": "claude-opus-5",
        "max_tokens": 16000,           # Caps thinking + tool output together, so leave headroom
        "effort": "medium",              # low | medium | high | xhigh | max - primary cost/latency lever
        # No temperature: sampling params are rejected by current models
        "max_concurrency": 15,         # Controls API rate limits & parallel throughput
        # Claude-authored (2026-10-13): "system_prompt" removed from here. It overrode the scoring rubric in
        # src/universal/recommendations/LLM_rec_instr.py and named a tool that doesn't exist. Prompts now live
        # only in LLM_rec_instr.py (new-stock scoring) and src/personalized/LLM_hold_instr.py (holdings).
    },
    "LONG_TERM_N_AMOUNT": 2,
    "SWING_TERM_N_AMOUNT": 5,
    "MAX_NEWS_ARTICLES": 20,
    "MIN_LONG_SCORE": 10.0,
    "MIN_SWING_SCORE": 15.0,

    # Claude-authored (2026-10-15): the users the digest and the watchdog run for, in this order. Each name needs a
    # profile in src/personalized/user_profiles.py. Their .env settings still decide what they get: the
    # pre-/post-market digest (Receive_*_Digest), and the watchdog (holdings source + Telegram chat ID, and
    # watchdog_interval_hours > 0). Remove a name here to pause both for that user.
    "ACTIVE_USERS": ["Andres", "Mauricio", "Steven"],

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
        "MAX_CLAUDE_CALLS_PER_USER_DAY": 40,     # Cap on watchdog Claude calls per user per market-local day (digest calls not counted)
        "FRESH_CONTEXT_MAX_AGE_MINUTES": 30,     # Quote + news in the cached market context older than this are re-fetched
        "CONTEXT_CACHE_RETENTION_DAYS": 7,       # Drop cached market context for symbols not refreshed within this many days
        "MAX_PARALLEL_USERS": 4,                 # Claude-authored (2026-10-15): users checked at the same time each hour
    },
    # Claude-authored (2026-10-14): email digest display fields (src/personalized/digest_fields.py)
    "DIGEST": {
        "CATALYST_WINDOW_DAYS": 14,              # Holding cards list upcoming earnings within this many days
        "SYMBOL_NEWS_BULLETS": 3,                # News bullets per holding card (src/personalized/symbol_specific_news.py)
        "SYMBOL_NEWS_LOOKBACK_DAYS": 7,          # How many days back Gemini searches for each holding's news
        "PORTFOLIO_REVIEW_BULLETS": 5,           # Max notes in the whole-portfolio review (src/personalized/portfolio_review.py)
        # Claude-authored (2026-10-15): parallelism and reuse
        "SYMBOL_NEWS_CONCURRENCY": 4,            # Gemini news requests in flight at once per user (each user has their own key)
        "MAX_PARALLEL_USERS": 4,                 # Users whose digest content is built at the same time (alongside the universal content)
        "REUSE_EVALUATION_MAX_AGE_MINUTES": 60,  # Reuse a watchdog evaluation this recent instead of calling Claude again (0 = off)
        # Claude-authored (2026-10-15): when the digest is due (src/digest/digest_schedule.py). Cron runs it hourly at :10.
        "PRE_MARKET_TIME_ET": "07:00",           # Pre-Market digest from this US Eastern time until the open
        "POST_MARKET_DELAY_MINUTES": 10,         # Post-Market digest this long after the close (16:10 ET), so closing data is final
        "POST_MARKET_SEND_WINDOW_MINUTES": 240,  # A missed Post-Market run is retried for this long after it was due
    },
    # Server-side refusal fallback (fallbacks="default") in both Claude evaluators: LLM_recommendation.py
    # (new-stock scoring) and src/personalized/recommendation.py (holdings). Only sent for the models listed below.
    "CLAUDE_REFUSAL_FALLBACK": True,
    "CLAUDE_FALLBACK_DEFAULT_MODELS": ["claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"],
    # Models a user may select for holdings evaluation (holdings_claude_model), with the effort levels each accepts.
    # Both evaluators send a strict tool schema with tool_choice "auto", so every model here must support strict
    # tool use (2026-10-13: Opus 4.7, Opus 4.6, and Sonnet 4.6 removed because they don't). CLAUDE_CONFIG["model"]
    # above should also be one of these.
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
}
