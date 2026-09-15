params_dict = {
    # Lookback and Lookahead Windows (Days)
    "COMPANY_NEWS_LOOKBACK_DAYS": 60,
    "EARNINGS_LOOKBACK_DAYS": 60,
    "EARNINGS_FUTURE_LOOKAHEAD_DAYS": 30,
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
        ("rsi", "1day", "<=", 80, {"time_period": 9}),                        # Custom 9-period RSI
        # ("mfi", "1day", "<=", 80),                                            # Money Flow Index (Volume-backed)
        # ("willr", "1day", ">=", -80),                                         # Williams %R
        # ("stochrsi", "1day", "<=", 80),                                       # Stochastic RSI
        # ("cci", "1day", ">=", -100),                                          # Commodity Channel Index
        # ("ultosc", "1day", ">=", 40),                                         # Ultimate Oscillator
        # ("cmo", "1day", ">=", -50),                                           # Chande Momentum Oscillator

        # --- Category A: Standalone Trend & Volatility ---
        ("adx", "1day", ">=", 10),                                            # Trend strength
        # ("aroonosc", "1day", ">=", 20),                                       # Aroon Oscillator (Uptrend check)
        # ("roc", "1day", ">=", 0, {"time_period": 10}),                        # 10-day Rate of Change (%)
        # ("beta", "1day", "<=", 1.5),                                          # Volatility vs. market index

        # --- Category B: Context-Dependent Examples (Fixed Level Approximations) ---
        # ("sma", "1day", ">=", 200, {"time_period": 300}),                      # Absolute level threshold check
        # ("ema", "1day", ">=", 50,  {"time_period": 50, "series_type": "open"}),# 50-period EMA on Open price
    ],
    # Context/time-series indicators fetched for data enrichment (No comparison operators or thresholds)
    "TWELVEDATA_CONTEXT": [
        ("macd", "1day", {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
        # ("bbands", "1day", {"time_period": 20}),
        # ("stoch", "1day", {"fastkperiod": 14, "slowkperiod": 3}),
        # ("sma", "1day", {"time_period": 200}),
    ],
}