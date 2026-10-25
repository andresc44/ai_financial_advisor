# File to keep the shared market-data APIs (Finnhub, Twelve Data) from failing silently: retry rate limits, stop on a bad key, flag gaps.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-16, at Andres's request.

Why: Finnhub and Twelve Data errors were printed as [DEBUG] lines and skipped. A rate limit, a used-up daily
allowance, or a revoked key quietly produced recommendations scored on partial data, and nobody was told.
- GuardedFinnhubClient wraps finnhub.Client: rate limits (429) are retried with backoff; after a rejected key
  (401) Finnhub isn't called again for the rest of the run. Other errors still reach the caller as before.
- twelvedata_request handles Twelve Data's errors: per-minute credit limits wait for the next minute (up to
  TWELVEDATA_MINUTE_WAITS times); a used-up daily allowance or a rejected key stops Twelve Data for the run.
- missing_data_issues flags any source missing for more than UNIVERSAL_DATA_QUALITY.MAX_MISSING_PCT of tickers.
Every problem is recorded once in DataSourceIssues; the universal content reports them to the admins and puts
them in the digest payload (universal_issues).

2026-10-18: Finnhub calls are paced to FINNHUB_CALLS_PER_MINUTE (FinnhubPacer) instead of being sent as fast as
possible and retried on 429. With ~1,100 calls a run after the looser filters, three retries (2, 5, 15 s) sometimes
all landed in the same blocked minute and the call was given up (TEL's recommendation trends on 2026-10-17). A
429 can still come from another process using the key in the same minute, so a last retry waits 60 s, longer than
Finnhub's window (X-Ratelimit-Reset is 60 s after the window's first call).
"""
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import finnhub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.ops.run_logging import debug
from src.config.parameters import params_dict  # noqa: E402

# Seconds between retries of a rate-limited Finnhub call. The last outlasts Finnhub's one-minute window (2026-10-18)
FINNHUB_RETRY_DELAYS = (2, 5, 15, 60)
TWELVEDATA_MINUTE_WAITS = 2        # How many times to wait out Twelve Data's per-minute credit limit


class DataSourceUnavailable(RuntimeError):
    """Raised instead of calling a data source already found unusable this run (e.g. its key was rejected)."""


class DataSourceIssues:
    """Problems with the shared data keys found during one run, recorded once per (source, reason)."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def add(self, source: str, reason: str, message: str) -> None:
        with self._lock:
            if (source, reason) not in self._items:
                print(f"[ERROR] {source} problem ({reason}): {message}")
                self._items[(source, reason)] = {"source": source, "reason": reason, "message": message}

    @property
    def items(self) -> list[dict]:
        with self._lock:
            return list(self._items.values())


SHARE_CLASS = re.compile(r"^([A-Z]{1,5})[-/.]([A-Z])$")  # e.g. BRK-B, BRK/B, BRK.B


def yahoo_symbol(symbol: str) -> str:
    """
    Claude-authored (2026-10-17): a share-class ticker in Yahoo's form, BRK-B. The NASDAQ screener writes BRK/B,
    which Yahoo doesn't know, so Berkshire was dropped from the ideas every run. Other tickers are unchanged.
    """
    return SHARE_CLASS.sub(r"\1-\2", symbol.strip()) if isinstance(symbol, str) else symbol


def finnhub_symbol(symbol: str) -> str:
    """Claude-authored (2026-10-17): a share-class ticker in Finnhub's form, BRK.B (the pipeline uses BRK-B)."""
    return SHARE_CLASS.sub(r"\1.\2", symbol) if isinstance(symbol, str) else symbol


class FinnhubPacer:
    """
    Claude-authored (2026-10-18): at most `calls` Finnhub calls in any `period` seconds, shared by every thread and
    client in the process (one key). A rolling window that never exceeds the limit also never exceeds it in
    Finnhub's own one-minute windows. FINNHUB_RATE_LIMIT_SLEEP still spaces the calls within a minute, which keeps
    them under Finnhub's 30-a-second cap.
    """

    def __init__(self, calls: int, period: float = 60.0) -> None:
        self.calls, self.period = max(1, int(calls)), period
        self._sent: deque = deque()
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Blocks until one more call fits in the window, then counts it."""
        while True:
            with self._lock:
                now = time.monotonic()
                while self._sent and now - self._sent[0] >= self.period:
                    self._sent.popleft()
                if len(self._sent) < self.calls:
                    self._sent.append(now)
                    return
                delay = self.period - (now - self._sent[0]) + 0.05
            time.sleep(delay)


FINNHUB_PACER = FinnhubPacer(params_dict.get("FINNHUB_CALLS_PER_MINUTE", 60))


class GuardedFinnhubClient:
    """
    finnhub.Client with rate-limit retries and a stop after a rejected key (see module docstring).
    2026-10-17: share-class tickers are passed to Finnhub in its own form (finnhub_symbol).
    2026-10-18: every attempt, retries included, waits its turn in FINNHUB_PACER.
    """

    def __init__(self, client: finnhub.Client, issues: DataSourceIssues) -> None:
        self._client = client
        self._issues = issues
        self._key_rejected = False

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._client, name)
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            if isinstance(kwargs.get("symbol"), str):
                kwargs["symbol"] = finnhub_symbol(kwargs["symbol"])
            elif args and isinstance(args[0], str):
                args = (finnhub_symbol(args[0]), *args[1:])
            if self._key_rejected:
                raise DataSourceUnavailable("Finnhub key was rejected earlier in this run")
            for delay in (*FINNHUB_RETRY_DELAYS, None):
                FINNHUB_PACER.wait()
                try:
                    return attribute(*args, **kwargs)
                except finnhub.FinnhubAPIException as e:
                    if e.status_code == 429 and delay is not None:
                        time.sleep(delay)
                        continue
                    if e.status_code == 429:
                        self._issues.add("Finnhub", "rate_limited", "Finnhub kept rate-limiting requests after retries.")
                    elif e.status_code == 401:
                        self._key_rejected = True
                        self._issues.add("Finnhub", "invalid_key", f"FINNHUB_API_KEY was rejected ({e.message}).")
                    elif e.status_code == 403:
                        self._issues.add("Finnhub", "permission", f"Finnhub refused {name} on this plan ({e.message}).")
                    raise

        return call


def twelvedata_request(
    get: Callable[..., Any], url: str, params: dict, issues: DataSourceIssues, state: dict
) -> Optional[Any]:
    """
    Calls one Twelve Data endpoint with `get` (a requests.Session.get) and returns its JSON, or None on an error.
    `state["blocked"]` is set when the daily allowance is used up or the key is rejected, so later calls this run
    return None without a request.
    """
    if state.get("blocked"):
        return None
    # Claude-authored (2026-10-16): a batch request costs one credit per symbol. One that needs more credits than
    # the plan allows per minute can never succeed, so it's skipped at once instead of waiting out two minutes.
    needed = max(1, len([s for s in str(params.get("symbol", "")).split(",") if s.strip()]))
    if needed > state.get("minute_limit", needed):
        return None  # Already reported for this run
    for attempt in range(TWELVEDATA_MINUTE_WAITS + 1):
        data = get(url, params=params, timeout=10).json()
        if not (isinstance(data, dict) and data.get("status") == "error"):
            return data
        code = data.get("code")
        message = str(data.get("message", ""))
        per_day = "day" in message.lower()
        limit = re.search(r"current limit being (\d+)", message)
        if code == 429 and not per_day and limit and needed > int(limit.group(1)):
            state["minute_limit"] = int(limit.group(1))
            issues.add("Twelve Data", "plan_limit", (
                f"A request for {needed} tickers needs {needed} credits, but the plan allows {limit.group(1)} per "
                "minute, so technical indicators were skipped for requests that large this run. Fewer candidates "
                "per run, a paid Twelve Data plan, or computing the indicators locally would fix it."))
            return None
        if code == 429 and not per_day and attempt < TWELVEDATA_MINUTE_WAITS:
            wait = 61 - datetime.now().second  # Credits refill at the start of each minute
            print(f"[WARNING] Twelve Data per-minute limit reached; waiting {wait}s.")
            time.sleep(wait)
            continue
        if code == 429:
            reason = "quota_exhausted" if per_day else "rate_limited"
        elif code in (401, 403):
            reason = "invalid_key" if code == 401 else "permission"
        else:  # A problem with this request only (e.g. an unknown symbol): skipped, as before
            debug(f"[DEBUG API Error] Twelve Data returned error: {message}")
            return None
        if reason in ("quota_exhausted", "invalid_key"):
            state["blocked"] = reason
        issues.add("Twelve Data", reason, message[:200])
        return None
    return None


def _has_yahoo(packet: dict) -> bool:
    yahoo = packet.get("yahoo")
    return isinstance(yahoo, dict) and "error" not in yahoo and bool(yahoo.get("currentPrice") or yahoo.get("regularMarketPrice"))


def _has_finnhub(packet: dict) -> bool:
    financials = (packet.get("finnhub") or {}).get("basic_financials") if isinstance(packet.get("finnhub"), dict) else None
    return isinstance(financials, dict) and bool(financials) and "error" not in financials


def _has_twelvedata(packet: dict) -> bool:
    indicators = packet.get("twelvedata")
    return isinstance(indicators, dict) and any(isinstance(v, dict) and v.get("values") for v in indicators.values())


# Claude-authored (2026-10-16): "Technical indicators", not "Twelve Data": they are now computed from Yahoo prices by
# default (TECHNICAL_INDICATOR_SOURCE), and stored under the same "twelvedata" key either way
COMPLETENESS_CHECKS = {"Yahoo": _has_yahoo, "Finnhub": _has_finnhub, "Technical indicators": _has_twelvedata}


def missing_data_issues(llm_packets: dict[str, dict], max_missing_pct: float) -> list[dict]:
    """One issue per source whose data is missing for more than `max_missing_pct` % of the tickers."""
    if not llm_packets:
        return []
    issues = []
    for source, has_data in COMPLETENESS_CHECKS.items():
        missing = [ticker for ticker, packet in llm_packets.items() if not has_data(packet or {})]
        pct = len(missing) / len(llm_packets) * 100
        if pct > max_missing_pct:
            issues.append({
                "source": source,
                "reason": "incomplete_data",
                "message": f"{len(missing)} of {len(llm_packets)} tickers ({pct:.0f}%) came back without {source} data, "
                           f"so their scores rely on partial data (e.g. {', '.join(sorted(missing)[:5])}).",
            })
    return issues
