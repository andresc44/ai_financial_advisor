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
"""
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

import finnhub

FINNHUB_RETRY_DELAYS = (2, 5, 15)  # Seconds between retries of a rate-limited Finnhub call
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


class GuardedFinnhubClient:
    """finnhub.Client with rate-limit retries and a stop after a rejected key (see module docstring)."""

    def __init__(self, client: finnhub.Client, issues: DataSourceIssues) -> None:
        self._client = client
        self._issues = issues
        self._key_rejected = False

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._client, name)
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            if self._key_rejected:
                raise DataSourceUnavailable("Finnhub key was rejected earlier in this run")
            for delay in (*FINNHUB_RETRY_DELAYS, None):
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
    for attempt in range(TWELVEDATA_MINUTE_WAITS + 1):
        data = get(url, params=params, timeout=10).json()
        if not (isinstance(data, dict) and data.get("status") == "error"):
            return data
        code = data.get("code")
        message = str(data.get("message", ""))
        per_day = "day" in message.lower()
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
            print(f"[DEBUG API Error] Twelve Data returned error: {message}")
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


COMPLETENESS_CHECKS = {"Yahoo": _has_yahoo, "Finnhub": _has_finnhub, "Twelve Data": _has_twelvedata}


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
