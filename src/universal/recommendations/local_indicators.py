# File to compute technical indicators from Yahoo daily prices, in Twelve Data's response format, instead of calling Twelve Data.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-16, at Andres's request.

Why: Twelve Data's free plan allows 8 credits a minute, and each indicator costs one credit per ticker, so the ~40
universal candidates (and any holdings refresh of more than 8 stocks) never got their indicators. The same formulas
computed from Yahoo's unadjusted daily bars matched Twelve Data exactly: 0.000% difference over 20 days for AAPL,
JPM, and NVDA on RSI 14, EMA 9/21, Bollinger Bands 20/2, Parabolic SAR 0.02/0.2, and ADX 14
(tests/test_local_indicators.py replays that comparison on saved AAPL data). Computing them here is free, has no
rate limit, and needs one Yahoo download for all tickers.

- Conventions follow TA-Lib, which Twelve Data uses: EMAs and Wilder averages are seeded with the simple average of
  their first n values, Bollinger Bands use the population standard deviation, and Parabolic SAR starts as TA-Lib's.
- Unadjusted prices, as Twelve Data: dividend-adjusted ones drift (up to 6.5% for JPM).
- Output matches Twelve Data's: {"values": [{"datetime": "YYYY-MM-DD", "<field>": value}, ...]}, newest first, the
  last `outputsize` points (default 30), so the filters and the sanitizer are unchanged.
- "1week" bars are built from the daily ones and dated by their Monday, like Twelve Data's.
- "beta" is market beta: the slope of the stock's returns on SPY's over `time_period` bars. (Twelve Data's beta is a
  statistic between two price series of the same stock, so it read ~0.02 where the market beta is ~1.1.)
- Anything not implemented here (another indicator, an intraday interval, an option like ma_type) still goes to
  Twelve Data: see supports() and filter_tickers.DataFetcher.fetch_twelvedata_batch_data.
"""
from typing import Any, Optional

import numpy as np
import pandas as pd
import yfinance as yf

MARKET_SYMBOL = "SPY"  # Benchmark for beta
HISTORY_PERIOD = "2y"  # ~500 daily bars: ample warm-up for the recursive averages, and 100+ weekly bars for beta
DEFAULT_OUTPUTSIZE = 30  # Twelve Data's default

# Indicator -> (output fields, {parameter: default}). A rule with any other parameter goes to Twelve Data.
INDICATORS: dict[str, tuple[tuple[str, ...], dict[str, Any]]] = {
    "rsi": (("rsi",), {"time_period": 14}),
    "ema": (("ema",), {"time_period": 9}),
    "sma": (("sma",), {"time_period": 9}),
    "bbands": (("upper_band", "middle_band", "lower_band"), {"time_period": 20, "sd": 2}),
    "sar": (("sar",), {"acceleration": 0.02, "maximum": 0.2}),
    "adx": (("adx",), {"time_period": 14}),
    "atr": (("atr",), {"time_period": 14}),
    "beta": (("beta",), {"time_period": 52}),
}
INTERVALS = ("1day", "1week")
COMMON_PARAMS = {"outputsize"}


def supports(indicator: str, interval: str, params: Optional[dict] = None) -> bool:
    """Whether this indicator, interval, and parameter set are computed here (otherwise Twelve Data is called)."""
    spec = INDICATORS.get(indicator.lower())
    return spec is not None and interval in INTERVALS and set(params or {}) <= set(spec[1]) | COMMON_PARAMS


# ------------------------------------------------------------------------------------------------- prices
def download_daily_bars(symbols: list[str], period: str = HISTORY_PERIOD) -> dict[str, pd.DataFrame]:
    """{symbol: unadjusted daily Open/High/Low/Close/Volume, oldest first} in one Yahoo download. Missing symbols are left out."""
    wanted = sorted(set(symbols))
    data = yf.download(tickers=wanted, period=period, interval="1d", auto_adjust=False, actions=False,
                       group_by="ticker", progress=False, threads=True)
    bars: dict[str, pd.DataFrame] = {}
    if data is None or data.empty:
        return bars
    for symbol in wanted:
        if isinstance(data.columns, pd.MultiIndex):
            if symbol in data.columns.get_level_values(0):
                frame = data[symbol]
            elif symbol in data.columns.get_level_values(1):
                frame = data.xs(symbol, axis=1, level=1)
            else:
                continue
        else:
            frame = data
        frame = frame.reindex(columns=["Open", "High", "Low", "Close", "Volume"]).dropna(subset=["High", "Low", "Close"])
        if not frame.empty:
            frame.index = pd.to_datetime(frame.index).tz_localize(None) if frame.index.tz is not None else pd.to_datetime(frame.index)
            bars[symbol] = frame.sort_index()
    return bars


def weekly_bars(daily: pd.DataFrame) -> pd.DataFrame:
    """Daily bars rolled up into weeks (Monday-Sunday), each dated by its Monday, like Twelve Data's weekly series."""
    weeks = daily.groupby(daily.index.to_period("W-SUN"))
    weekly = weeks.agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    weekly.index = weekly.index.start_time
    return weekly


# ------------------------------------------------------------------------------------------------- formulas
def _ema(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) >= n:
        out[n - 1] = values[:n].mean()
        k = 2 / (n + 1)
        for i in range(n, len(values)):
            out[i] = out[i - 1] + k * (values[i] - out[i - 1])
    return out


def _wilder_mean(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) >= n:
        out[n - 1] = values[:n].mean()
        for i in range(n, len(values)):
            out[i] = (out[i - 1] * (n - 1) + values[i]) / n
    return out


def _wilder_sum(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) >= n:
        out[n - 1] = values[:n].sum()
        for i in range(n, len(values)):
            out[i] = out[i - 1] - out[i - 1] / n + values[i]
    return out


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """True range from the second bar on (the first has no previous close)."""
    previous = close[:-1]
    return np.maximum.reduce([high[1:] - low[1:], np.abs(high[1:] - previous), np.abs(low[1:] - previous)])


def rsi(close: np.ndarray, n: int) -> np.ndarray:
    diff = np.diff(close)
    avg_gain = _wilder_mean(np.clip(diff, 0, None), n)
    avg_loss = _wilder_mean(np.clip(-diff, 0, None), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        values = 100 - 100 / (1 + avg_gain / avg_loss)
    return np.concatenate([[np.nan], values])


def bbands(close: np.ndarray, n: int, sd: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    series = pd.Series(close)
    middle = series.rolling(n).mean()
    std = series.rolling(n).std(ddof=0)  # Population standard deviation, as TA-Lib
    return (middle + sd * std).to_numpy(), middle.to_numpy(), (middle - sd * std).to_numpy()


def sar(high: np.ndarray, low: np.ndarray, step: float, maximum: float) -> np.ndarray:
    """Wilder's Parabolic SAR with TA-Lib's start: short only if the first bar's -DM beats its +DM."""
    out = np.full(len(high), np.nan)
    if len(high) < 2:
        return out
    is_long = not ((low[0] - low[1]) > max(high[1] - high[0], 0))
    extreme, stop = (high[1], low[0]) if is_long else (low[1], high[0])
    factor = step
    for i in range(1, len(high)):
        if is_long and low[i] <= stop:  # Reversal to short
            is_long, stop, extreme, factor = False, extreme, low[i], step
            stop = max(stop, high[i], high[i - 1])
        elif not is_long and high[i] >= stop:  # Reversal to long
            is_long, stop, extreme, factor = True, extreme, high[i], step
            stop = min(stop, low[i], low[i - 1])
        elif is_long and high[i] > extreme:
            extreme, factor = high[i], min(factor + step, maximum)
        elif not is_long and low[i] < extreme:
            extreme, factor = low[i], min(factor + step, maximum)
        out[i] = stop
        if is_long:
            stop = min(stop + factor * (extreme - stop), low[i], low[i - 1])
        else:
            stop = max(stop + factor * (extreme - stop), high[i], high[i - 1])
    return out


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    up, down = np.diff(high), -np.diff(low)
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus, minus = _wilder_sum(plus_dm, n), _wilder_sum(minus_dm, n)
    # DX = |+DI - -DI| / (+DI + -DI) with DI = smoothed DM / smoothed true range: the true range cancels out
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = 100 * np.abs(plus - minus) / (plus + minus)
    out = np.full(len(close), np.nan)
    valid = np.flatnonzero(~np.isnan(dx))
    out[valid + 1] = _wilder_mean(dx[valid], n)
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    return np.concatenate([[np.nan], _wilder_mean(_true_range(high, low, close), n)])


def market_beta(stock_close: pd.Series, market_close: pd.Series, n: int) -> pd.Series:
    """Rolling slope of the stock's simple returns on the market's over n bars."""
    returns = pd.concat([stock_close.pct_change(), market_close.pct_change()], axis=1, join="inner").dropna()
    stock, market = returns.iloc[:, 0], returns.iloc[:, 1]
    beta = stock.rolling(n).cov(market) / market.rolling(n).var()
    return beta.reindex(stock_close.index)


# ------------------------------------------------------------------------------------------------- Twelve Data shape
def compute(
    indicator: str,
    interval: str,
    params: Optional[dict],
    bars: Optional[pd.DataFrame],
    market_bars: Optional[pd.DataFrame] = None,
) -> list[dict[str, Any]]:
    """
    One indicator for one symbol as Twelve Data's "values" list (newest first), or [] when there isn't enough
    price history (or no market history for beta). `bars` are the symbol's daily bars from download_daily_bars.
    """
    indicator = indicator.lower()
    fields, defaults = INDICATORS[indicator]
    settings = {**defaults, **{k: v for k, v in (params or {}).items() if k in defaults}}
    outputsize = int((params or {}).get("outputsize", DEFAULT_OUTPUTSIZE))
    if bars is None or bars.empty:
        return []
    if interval == "1week":
        bars = weekly_bars(bars)
        market_bars = weekly_bars(market_bars) if market_bars is not None and not market_bars.empty else market_bars
    high, low, close = (bars[c].to_numpy(dtype=float) for c in ("High", "Low", "Close"))
    n = int(settings.get("time_period", 0))

    if indicator == "rsi":
        columns = [rsi(close, n)]
    elif indicator == "ema":
        columns = [_ema(close, n)]
    elif indicator == "sma":
        columns = [pd.Series(close).rolling(n).mean().to_numpy()]
    elif indicator == "bbands":
        columns = list(bbands(close, n, float(settings["sd"])))
    elif indicator == "sar":
        columns = [sar(high, low, float(settings["acceleration"]), float(settings["maximum"]))]
    elif indicator == "adx":
        columns = [adx(high, low, close, n)]
    elif indicator == "atr":
        columns = [atr(high, low, close, n)]
    else:  # beta
        if market_bars is None or market_bars.empty:
            return []
        columns = [market_beta(bars["Close"], market_bars["Close"], n).to_numpy()]

    table = pd.DataFrame(dict(zip(fields, columns)), index=bars.index).dropna()
    table = table.iloc[::-1].head(outputsize)  # Newest first, like Twelve Data
    return [
        {"datetime": day.strftime("%Y-%m-%d"), **{field: round(float(row[field]), 5) for field in fields}}
        for day, row in table.iterrows()
    ]
