# File to record the tokens every Claude and Gemini call uses, estimate what it cost, and summarize it per user for the digest.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-16, at Andres's request.

Why: each user pays for holdings evaluation (their Anthropic key) and holding news / portfolio review (their Gemini
key), and nothing showed what that cost. Every call now appends one line to data/usage/api_usage.jsonl (user,
source, provider, model, token counts, estimated cost), and usage_summary() turns that into the digest's
`api_usage` block: this digest, today (US Eastern date), and the month so far (UTC calendar month, as billed).

Costs are ESTIMATES at the list prices in params_dict["API_PRICES"] (with effective dates, so a published price
change applies from its date). They don't know about credits, discounts, or other use of the same key outside
Finnbot. Details:
- Claude: input, output (thinking included), cache writes, and cache reads each at their own rate. When a refused
  request was re-run on a fallback model, each attempt in usage.iterations is billed at its own model's rate.
- Gemini: uncached input (prompt + tool-use prompt), cached input, and output (thinking included). Google Search
  grounding is free for the first GEMINI_FREE_SEARCH_REQUESTS_PER_MONTH requests per key per month, so search cost
  is only counted for requests beyond that within the month (Finnbot's own requests only).
The shared keys used for the universal content are recorded under user "_shared".
- 2026-10-16: DeepSeek (when a job uses a DeepSeek model, src/llm_models.py): cache misses, cache hits, and output
  (thinking included) at the price in effect at the time of the call, peak or off-peak (API_PRICES["deepseek"],
  DEEPSEEK_PEAK_HOURS_UTC). The model recorded is the one requested, which is the one billed.
"""
import json
import sys
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.parameters import params_dict

LEDGER_PATH = project_root / "data" / "usage" / "api_usage.jsonl"
SHARED = "_shared"  # Ledger "user" for the universal content's shared keys
MARKET_TZ = ZoneInfo("America/New_York")
_LOCK = threading.Lock()  # Users and their news requests run in parallel threads
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens", "search_queries")
PROVIDERS = ("anthropic", "deepseek", "gemini")  # Each gets its own block in the digest's api_usage


def _count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _price(provider: str, model: Optional[str], on: date) -> Optional[dict]:
    """The price entry in effect on `on` for the longest model id `model` starts with (claude-opus-5-5 vs claude-opus-5)."""
    table = params_dict["API_PRICES"][provider]
    matches = [name for name in table if model and model.startswith(name)]
    if not matches:
        return None
    schedule = [entry for entry in table[max(matches, key=len)] if date.fromisoformat(entry["from"]) <= on]
    return max(schedule, key=lambda entry: entry["from"]) if schedule else None


def anthropic_cost(model: Optional[str], tokens: dict, on: date) -> Optional[float]:
    """Estimated USD for one Claude attempt, or None when the model has no price (it's then listed as unpriced)."""
    price = _price("anthropic", model, on)
    if price is None:
        return None
    if price.get("above_tokens") and tokens["input_tokens"] + tokens["cache_read_tokens"] + tokens["cache_write_tokens"] > price["above_tokens"]:
        price = {**price, **price["above"]}  # Long-prompt rates (Claude Haiku 5.5 above 100K prompt tokens)
    return (
        tokens["input_tokens"] * price["input"]
        + tokens["output_tokens"] * price["output"]
        + tokens["cache_write_tokens"] * price["input"] * params_dict["API_PRICES"]["ANTHROPIC_CACHE_WRITE_MULTIPLIER"]
        + tokens["cache_read_tokens"] * price["cache_read"]
    ) / 1_000_000


def is_deepseek_peak(at: datetime) -> bool:
    """Whether `at` falls in DeepSeek's peak hours: DEEPSEEK_PEAK_HOURS_UTC on Monday to Friday, UTC."""
    utc = at.astimezone(timezone.utc)
    hours = params_dict["API_PRICES"].get("DEEPSEEK_PEAK_HOURS_UTC", [])
    return utc.weekday() < 5 and any(start <= utc.hour < end for start, end in hours)


def deepseek_cost(model: Optional[str], tokens: dict, at: datetime) -> Optional[float]:
    """Estimated USD for one DeepSeek call at the peak or off-peak price in effect at `at`; None if unpriced."""
    price = _price("deepseek", model, at.astimezone(timezone.utc).date())
    if price is None:
        return None
    if not is_deepseek_peak(at) and price.get("off_peak"):
        price = {**price, **price["off_peak"]}
    return (
        tokens["input_tokens"] * price["input"]
        + tokens["output_tokens"] * price["output"]
        + tokens["cache_read_tokens"] * price["cache_read"]
    ) / 1_000_000


def gemini_token_cost(model: Optional[str], tokens: dict, on: date) -> Optional[float]:
    """Estimated USD for one Gemini call's tokens (Google Search is costed per month in usage_summary)."""
    price = _price("gemini", model, on)
    if price is None:
        return None
    return (
        tokens["input_tokens"] * price["input"]
        + tokens["output_tokens"] * price["output"]
        + tokens["cache_read_tokens"] * price["cached_input"]
    ) / 1_000_000


def _append(record: dict) -> None:
    """Appends one ledger line. A failure is only a warning: usage tracking must never stop a run."""
    try:
        with _LOCK:
            LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LEDGER_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
    except OSError as e:
        print(f"[WARNING] Could not write API usage to {LEDGER_PATH}: {e}")


def record_anthropic_response(user_name: Optional[str], source: str, response: Any) -> None:
    """Records a Claude response: one line per billed attempt (usage.iterations when a refusal fallback ran)."""
    try:
        usage = response.usage
        attempts = [(getattr(it, "model", None) or response.model, it) for it in (getattr(usage, "iterations", None) or [])]
        now = datetime.now(timezone.utc)
        for model, attempt_usage in attempts or [(response.model, usage)]:
            tokens = {
                "input_tokens": _count(getattr(attempt_usage, "input_tokens", 0)),
                "output_tokens": _count(getattr(attempt_usage, "output_tokens", 0)),
                "cache_write_tokens": _count(getattr(attempt_usage, "cache_creation_input_tokens", 0)),
                "cache_read_tokens": _count(getattr(attempt_usage, "cache_read_input_tokens", 0)),
                "search_queries": 0,
            }
            cost = anthropic_cost(model, tokens, now.date())
            _append({"at": now.isoformat(), "user": user_name or SHARED, "source": source, "provider": "anthropic",
                     "model": model, **tokens, "cost_usd": cost})
    except Exception as e:  # Usage tracking must never break an evaluation
        print(f"[WARNING] Could not record Claude usage: {e}")


def record_deepseek_response(user_name: Optional[str], source: str, model: str, response: dict) -> None:
    """Records a DeepSeek chat-completions response (its JSON): cache misses, cache hits, and output tokens."""
    try:
        usage = response.get("usage") or {}
        hit = _count(usage.get("prompt_cache_hit_tokens"))
        miss = usage.get("prompt_cache_miss_tokens")
        miss = _count(miss) if miss is not None else max(_count(usage.get("prompt_tokens")) - hit, 0)
        tokens = {
            "input_tokens": miss,
            "output_tokens": _count(usage.get("completion_tokens")),  # Reasoning tokens included
            "cache_write_tokens": 0,
            "cache_read_tokens": hit,
            "search_queries": 0,
        }
        now = datetime.now(timezone.utc)
        _append({"at": now.isoformat(), "user": user_name or SHARED, "source": source, "provider": "deepseek",
                 "model": model, **tokens, "cost_usd": deepseek_cost(model, tokens, now)})
    except Exception as e:  # Usage tracking must never break an evaluation
        print(f"[WARNING] Could not record DeepSeek usage: {e}")


def record_gemini_response(user_name: Optional[str], source: str, model: str, response: Any) -> None:
    """Records a Gemini response's tokens and the number of Google Search queries it ran."""
    try:
        meta = getattr(response, "usage_metadata", None)
        cached = _count(getattr(meta, "cached_content_token_count", 0))
        prompt = _count(getattr(meta, "prompt_token_count", 0)) + _count(getattr(meta, "tool_use_prompt_token_count", 0))
        candidates = getattr(response, "candidates", None) or []
        grounding = getattr(candidates[0], "grounding_metadata", None) if candidates else None
        tokens = {
            "input_tokens": max(prompt - cached, 0),
            "output_tokens": _count(getattr(meta, "candidates_token_count", 0)) + _count(getattr(meta, "thoughts_token_count", 0)),
            "cache_write_tokens": 0,
            "cache_read_tokens": cached,
            "search_queries": len(getattr(grounding, "web_search_queries", None) or []),
        }
        now = datetime.now(timezone.utc)
        _append({"at": now.isoformat(), "user": user_name or SHARED, "source": source, "provider": "gemini",
                 "model": model, **tokens, "cost_usd": gemini_token_cost(model, tokens, now.date())})
    except Exception as e:  # Usage tracking must never break a news fetch
        print(f"[WARNING] Could not record Gemini usage: {e}")


def _load(user_name: str, since: datetime) -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    records = []
    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("user") == user_name and datetime.fromisoformat(record["at"]) >= since:
                records.append(record)
    return sorted(records, key=lambda record: record["at"])


def _with_search_cost(records: list[dict]) -> None:
    """Adds each record's billable Google Search cost: only queries beyond the month's free allowance count."""
    free = params_dict["API_PRICES"]["GEMINI_FREE_SEARCH_REQUESTS_PER_MONTH"]
    used, month = 0, None
    for record in records:
        if record["at"][:7] != month:  # The allowance resets each UTC month ("at" is a UTC timestamp)
            used, month = 0, record["at"][:7]
        queries = _count(record.get("search_queries"))
        billable = max(0, used + queries - free) - max(0, used - free)
        used += queries
        price = _price("gemini", record.get("model"), datetime.fromisoformat(record["at"]).date()) if record["provider"] == "gemini" else None
        record["search_cost_usd"] = billable * price["search_per_1000"] / 1000 if price and billable else 0.0


def _totals(records: list[dict]) -> dict:
    out: dict[str, Any] = {}
    for provider in PROVIDERS:
        mine = [record for record in records if record["provider"] == provider]
        totals = {"calls": len(mine), **{field: sum(_count(r.get(field)) for r in mine) for field in TOKEN_FIELDS}}
        if provider != "gemini":  # Only Gemini calls run Google searches
            totals.pop("search_queries")
        cost = sum((r.get("cost_usd") or 0.0) + r.get("search_cost_usd", 0.0) for r in mine)
        totals["cost_usd"] = round(cost, 4)
        unpriced = sorted({r["model"] for r in mine if r.get("cost_usd") is None})
        if unpriced:
            totals["unpriced_models"] = unpriced  # No price in API_PRICES: their tokens are counted but not costed
        out[provider] = totals
    out["total_cost_usd"] = round(sum(out[provider]["cost_usd"] for provider in PROVIDERS), 4)
    return out


def usage_summary(user_name: str, run_started_at: datetime, now: Optional[datetime] = None) -> dict:
    """The digest's `api_usage` block for one user: this digest, today (US Eastern), and the month to date (UTC)."""
    now = now or datetime.now(timezone.utc)
    month_start = now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today = now.astimezone(MARKET_TZ).date()
    # A digest after 20:00 ET on the last day of a month runs in the next UTC month, so "today" starts before it
    today_start = datetime.combine(today, datetime.min.time(), tzinfo=MARKET_TZ)
    records = _load(user_name, min(month_start, today_start))
    _with_search_cost(records)
    prices = params_dict["API_PRICES"]
    return {
        "currency": "USD",
        "note": f"Estimated from token counts at list prices as of {prices['AS_OF']}; excludes credits, discounts, "
                "and use of the same keys outside Finnbot.",
        "this_digest": _totals([r for r in records if datetime.fromisoformat(r["at"]) >= run_started_at]),
        "today": _totals([r for r in records if datetime.fromisoformat(r["at"]).astimezone(MARKET_TZ).date() == today]),
        "month_to_date": _totals([r for r in records if datetime.fromisoformat(r["at"]) >= month_start]),
    }
