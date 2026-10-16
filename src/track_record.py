# File to keep an append-only track record of every universal and personal recommendation, and report how they played out.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-15, at Andres's request.

Why: the watchdog state keeps only each stock's latest evaluation, so there was no way to tell whether Finnbot's
advice was any good. Every recommendation is now appended (never rewritten) to data/track_record/:
- personal.jsonl: each new holding evaluation from the digest or the watchdog - time, source, user, symbol,
  recommendation, sentiment, rationale, price and currency at the time, model.
- universal.jsonl: each stock picked by the universal recommendations - time, list (long_term / swing), symbol,
  longScore, swingScore, price at the time.

`python -m src.track_record [--since YYYY-MM-DD]` reports each recommendation's return 1, 5, and 20 trading days
later (Yahoo daily closes; too-recent ones count as pending) next to the S&P 500 (SPY) over the same days,
grouped by recommendation (personal) and by list (universal). Returns are percentages, so currency doesn't matter.
"""
import argparse
import json
import math
import sys
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

TRACK_DIR = project_root / "data" / "track_record"
PERSONAL_PATH = TRACK_DIR / "personal.jsonl"
UNIVERSAL_PATH = TRACK_DIR / "universal.jsonl"
HORIZONS = (1, 5, 20)  # Trading days after the recommendation
BENCHMARK = "SPY"
MARKET_TZ = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)
_LOCK = threading.Lock()  # Users run in parallel threads and append to the same file


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _append(path: Path, records: list[dict]) -> None:
    """Appends JSON lines. A failure is only a warning: the track record must never stop a digest or watchdog run."""
    if not records:
        return
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record, default=str) + "\n")
    except OSError as e:
        print(f"[WARNING] Could not write the track record to {path}: {e}")


def log_personal(
    user_name: str,
    evaluated_df: pd.DataFrame,
    evaluated_at: datetime,
    source: str,
    model: Optional[str] = None,
) -> None:
    """Records each stock's new evaluation (one line per stock, even if it's held in several accounts)."""
    if evaluated_df is None or evaluated_df.empty or "recommendation" not in evaluated_df.columns:
        return
    rows = evaluated_df[evaluated_df["recommendation"].notna()].drop_duplicates("symbol")
    records = []
    for _, row in rows.iterrows():
        price = _number(row.get("session_price")) or _number(row.get("current_price"))
        records.append({
            "recorded_at": evaluated_at.isoformat(),
            "source": source,
            "user": user_name,
            "symbol": row["symbol"],
            "recommendation": row["recommendation"],
            "sentiment": _text(row.get("sentiment")),
            "rationale": _text(row.get("recommendation_rationale")),
            "price": price,
            "currency": _text(row.get("currency")),
            "model": model,
        })
    _append(PERSONAL_PATH, records)


def log_universal(
    recommendations: dict, sanitized_inputs: dict, generated_at: Optional[datetime] = None
) -> None:
    """Records each universal pick with its scores and the price in its sanitized market data."""
    recorded_at = (generated_at or datetime.now(timezone.utc)).isoformat()
    records = []
    for list_name, key in (("long_term", "top_long_term"), ("swing", "top_swing")):
        for pick in recommendations.get(key) or []:
            ticker = pick.get("ticker")
            price_action = (sanitized_inputs.get(ticker) or {}).get("price_action") or {}
            records.append({
                "recorded_at": recorded_at,
                "list": list_name,
                "symbol": ticker,
                "longScore": _number(pick.get("longScore")),
                "swingScore": _number(pick.get("swingScore")),
                "price": _number(price_action.get("current_price")),
                # Claude-authored (2026-10-16): so the record can show whether sentiment and the swing levels held up
                "sentiment": pick.get("sentiment"),
                "entry_price": _number(pick.get("entry_price")),
                "stop_loss": _number(pick.get("stop_loss")),
                "price_target": _number(pick.get("price_target")),
            })
    _append(UNIVERSAL_PATH, records)


# ---------------------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------------------
def load_records(path: Path, since: Optional[date] = None) -> pd.DataFrame:
    """The records in a track-record file (empty if there is none), optionally only those from `since` on."""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    records = pd.read_json(path, lines=True, dtype=False)
    records["recorded_at"] = pd.to_datetime(records["recorded_at"], utc=True, errors="coerce")
    records = records.dropna(subset=["recorded_at", "symbol", "price"])
    if since is not None:
        records = records[records["recorded_at"].dt.tz_convert(MARKET_TZ).dt.date >= since]
    return records.reset_index(drop=True)


def fetch_daily_closes(symbols: list[str], start: date) -> dict[str, pd.Series]:
    """{symbol: daily closes indexed by session date} from `start` until today, in one Yahoo download."""
    yahoo = {symbol: symbol.replace(":", "-") for symbol in set(symbols)}
    data = yf.download(tickers=sorted(set(yahoo.values())), start=start.isoformat(), interval="1d", progress=False, threads=True)
    closes = data["Close"]
    out = {}
    for symbol, yahoo_symbol in yahoo.items():
        series = closes if isinstance(closes, pd.Series) else closes.get(yahoo_symbol)
        if series is None:
            continue
        series = series.dropna()
        series.index = pd.DatetimeIndex(series.index).date
        out[symbol] = series
    return out


def _closes_after(closes: pd.Series, recorded_at: pd.Timestamp) -> pd.Series:
    """Closes that happened after the recommendation: its own day's close counts only if made before the close."""
    local = recorded_at.tz_convert(MARKET_TZ)
    first_day = local.date() if local.time() < MARKET_CLOSE else local.date() + timedelta(days=1)
    return closes[closes.index >= first_day]


def add_forward_returns(
    records: pd.DataFrame, closes: dict[str, pd.Series], horizons: tuple[int, ...] = HORIZONS
) -> pd.DataFrame:
    """
    Adds return_<h>d (%, from the recorded price to the h-th close after the recommendation), spy_<h>d (SPY over the
    same closes), and excess_<h>d (the difference). NaN means the horizon hasn't been reached yet (pending).
    """
    records = records.copy()
    benchmark = closes.get(BENCHMARK)
    for h in horizons:
        returns, spy_returns = [], []
        for _, record in records.iterrows():
            series = closes.get(record["symbol"])
            after = _closes_after(series, record["recorded_at"]) if series is not None else pd.Series(dtype=float)
            if len(after) < h:
                returns.append(float("nan"))
                spy_returns.append(float("nan"))
                continue
            end_day = after.index[h - 1]
            returns.append((after.iloc[h - 1] / record["price"] - 1) * 100)
            spy = benchmark if benchmark is not None else pd.Series(dtype=float)
            spy_start = spy[spy.index < after.index[0]]
            spy_end = spy[spy.index <= end_day]
            spy_returns.append(
                (spy_end.iloc[-1] / spy_start.iloc[-1] - 1) * 100 if not spy_start.empty and not spy_end.empty else float("nan")
            )
        records[f"return_{h}d"] = returns
        records[f"spy_{h}d"] = spy_returns
        records[f"excess_{h}d"] = records[f"return_{h}d"] - records[f"spy_{h}d"]
    return records


def summarize(records: pd.DataFrame, by: str, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    """Per group: how many, and for each horizon the average return, share positive, and average excess over SPY."""
    if records.empty:
        return pd.DataFrame()
    rows = []
    for group, frame in records.groupby(by):
        row = {by: group, "count": len(frame)}
        for h in horizons:
            done = frame[f"return_{h}d"].dropna()
            row[f"avg_{h}d_%"] = round(done.mean(), 2) if not done.empty else None
            row[f"up_{h}d_%"] = round((done > 0).mean() * 100, 0) if not done.empty else None
            excess = frame[f"excess_{h}d"].dropna()
            row[f"vs_spy_{h}d_%"] = round(excess.mean(), 2) if not excess.empty else None
            row[f"pending_{h}d"] = int(frame[f"return_{h}d"].isna().sum())
        rows.append(row)
    return pd.DataFrame(rows)


def report(since: Optional[date] = None) -> dict[str, pd.DataFrame]:
    """Prints and returns the personal (by recommendation) and universal (by list) track-record summaries."""
    personal, universal = load_records(PERSONAL_PATH, since), load_records(UNIVERSAL_PATH, since)
    if personal.empty and universal.empty:
        print("No recommendations recorded yet.")
        return {}
    combined = pd.concat([frame for frame in (personal, universal) if not frame.empty])
    start = combined["recorded_at"].min().tz_convert(MARKET_TZ).date() - timedelta(days=7)
    closes = fetch_daily_closes(sorted(set(combined["symbol"])) + [BENCHMARK], start)

    summaries = {}
    if not personal.empty:
        summaries["personal"] = summarize(add_forward_returns(personal, closes), "recommendation")
    if not universal.empty:
        summaries["universal"] = summarize(add_forward_returns(universal, closes), "list")
    for name, table in summaries.items():
        print(f"\n=== {name} recommendations (returns in %, vs_spy = average return minus SPY's) ===")
        print(table.to_string(index=False))
    return summaries


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Finnbot recommendation track record")
    parser.add_argument("--since", type=date.fromisoformat, default=None, help="Only recommendations from this date (YYYY-MM-DD).")
    report(parser.parse_args().since)
