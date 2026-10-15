# File to classify Anthropic and Gemini API errors that make a user's key unusable, and to retry only what can recover.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code), at Andres's request.
- 2026-10-15: created. Why: each user now brings their own Anthropic and Gemini keys, so a missing, invalid,
  expired, revoked, out-of-credit, or quota-exhausted key is a normal situation, not a crash. Before this module,
  such errors were retried (Gemini: ~16 s of waiting per stock) and then repeated for every remaining stock, and
  the watchdog fetched market data and counted Claude calls for evaluations that could never run.
  Callers now stop at the first key error, skip the work that depends on it, and report one clear notice.

Error formats (checked 2026-10-15):
- Anthropic (platform.claude.com/docs/en/api/errors, /api/rate-limits): 401 invalid/revoked/expired key;
  402 billing_error; 403 permission_error; 400 "You have reached your specified (workspace) API usage limits";
  429 with error.details.error_code "enforced_spend_limit_reached" for the tier's monthly cap (no retry-after,
  keeps failing until next month). The SDK already retries ordinary 429/5xx/529 itself.
- Gemini (ai.google.dev/gemini-api/docs/api-errors, /troubleshooting): invalid or expired key as 400
  INVALID_ARGUMENT (reason API_KEY_INVALID) or 401; 402 depleted Prepay credits; 400 FAILED_PRECONDITION when
  billing is required; 403 PERMISSION_DENIED (incl. keys reported as leaked); 429 for both per-minute limits
  (retryable, short RetryInfo delay) and daily quotas (quotaId "...PerDay...", status "quota_exceeded").
  The google-genai SDK does not retry by default, so GEMINI_RETRY below is the only retry layer.
"""
import hashlib
import re
import sys
from pathlib import Path
from typing import Any, Optional

import anthropic
import httpx
from google.genai import errors as genai_errors
from tenacity import retry_if_exception, stop_after_attempt

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.personalized.user_profiles import USER_PROFILES

PROFILE_KEYS = {"Anthropic": "anthropic_api_key", "Gemini": "gemini_api_key"}
CONSOLES = {"Anthropic": "platform.claude.com", "Gemini": "Google AI Studio"}

REASON_TEXT = {
    "missing": "is not set",
    "invalid": "is invalid, revoked, or expired",
    "expired": "has expired",
    "permission": "is not allowed to use this API, project, model, or location",
    "no_credits": "has no credits left, or billing is not set up",
    "spend_limit": "has reached its spend limit",
    "quota_exhausted": "has used up its usage quota",
    "rate_limited": "kept hitting rate limits after retries",
}
FIX_HINTS = {
    "missing": "Add {env_key} to .env.",
    "invalid": "Create a new key in {console} and update {env_key} in .env.",
    "expired": "Create a new key in {console} and update {env_key} in .env.",
    "permission": "Check the key's project/workspace access and model availability in {console}.",
    "no_credits": "Add credits or enable billing in {console}.",
    "spend_limit": "Raise the spend limit in {console}, or wait until it resets.",
    "quota_exhausted": "Wait for the quota to reset, or upgrade the plan in {console}.",
    "rate_limited": "It will be retried on the next run.",
}

try:  # google-genai raises httpx or httpx2 network errors depending on the transport in use
    import httpx2
    TRANSIENT_NETWORK_ERRORS = (httpx.TimeoutException, httpx.ConnectError, httpx2.TimeoutException, httpx2.ConnectError)
except ImportError:
    TRANSIENT_NETWORK_ERRORS = (httpx.TimeoutException, httpx.ConnectError)

GEMINI_RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}
GEMINI_MAX_RETRY_DELAY_SECONDS = 60  # A 429 asking to wait longer than this is treated as an exhausted quota


class ApiKeyUnusable(ValueError):
    """
    The user's key for `provider` can't be used for the rest of this run, so callers stop sending requests.
    Subclasses ValueError so existing `except ValueError` handlers around key lookup keep working.
    """

    def __init__(self, provider: str, reason: str, user_name: Optional[str] = None, detail: str = ""):
        self.provider = provider
        self.reason = reason
        self.user_name = user_name
        self.detail = detail
        super().__init__(self._describe())

    @property
    def blocks_run(self) -> bool:
        """False only for rate limits, which can clear within minutes; every other reason needs the user to act."""
        return self.reason != "rate_limited"

    def _describe(self) -> str:
        env_key = (USER_PROFILES.get(self.user_name) or {}).get(PROFILE_KEYS.get(self.provider, ""))
        env_key = env_key or f"<USER>_{self.provider.upper()}_API_KEY"
        hint = FIX_HINTS[self.reason].format(env_key=env_key, console=CONSOLES.get(self.provider, self.provider))
        text = f"{self.provider} API key for '{self.user_name}' {REASON_TEXT[self.reason]}. {hint}"
        return f"{text} (API said: {self.detail[:200]})" if self.detail else text

    def as_notice(self) -> dict:
        """Payload-friendly summary for the digest."""
        return {"provider": self.provider, "reason": self.reason, "message": str(self)}


def _expired_or_invalid(message: str) -> str:
    """'expired' only when the API says so unambiguously ("missing, invalid, or expired" stays 'invalid')."""
    return "expired" if "expired" in message and "invalid" not in message else "invalid"


def key_fingerprint(api_key: str) -> str:
    """Short, non-reversible id for a key, so keys are never kept or logged in plain text."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------------------------------------
def _anthropic_error_body(error: anthropic.APIStatusError) -> dict:
    body = error.body if isinstance(error.body, dict) else {}
    inner = body.get("error", body)
    return inner if isinstance(inner, dict) else {}


def anthropic_key_error(error: BaseException, user_name: Optional[str]) -> Optional[ApiKeyUnusable]:
    """ApiKeyUnusable when `error` means the key can't be used this run; None for transient or per-request errors."""
    if not isinstance(error, anthropic.APIStatusError):
        return None  # Connection errors and timeouts: transient
    body = _anthropic_error_body(error)
    message = str(body.get("message") or error.message or "")
    lowered = message.lower()
    details = body.get("details") if isinstance(body.get("details"), dict) else {}

    reason = None
    if error.status_code == 401:
        reason = _expired_or_invalid(lowered)
    elif error.status_code == 402 or body.get("type") == "billing_error" or "credit balance" in lowered:
        reason = "no_credits"
    elif error.status_code == 403:
        reason = "permission"
    elif details.get("error_code") == "enforced_spend_limit_reached" or (
        error.status_code == 400 and "api usage limits" in lowered
    ):
        reason = "spend_limit"
    return ApiKeyUnusable("Anthropic", reason, user_name, message) if reason else None


_VERIFIED_ANTHROPIC_KEYS: set[str] = set()  # Fingerprints that passed verify_anthropic_key in this process


def verify_anthropic_key(api_key: str, user_name: Optional[str]) -> None:
    """
    Free validity check (lists one model; no tokens are billed) that raises ApiKeyUnusable for an invalid,
    revoked, expired, or unpermitted key before any market data is fetched for Claude. It cannot detect an empty
    credit balance; that surfaces on the first real request. Outages and rate limits don't block: the real
    request decides. A key that passes is not re-checked in the same process.
    """
    fingerprint = key_fingerprint(api_key)
    if fingerprint in _VERIFIED_ANTHROPIC_KEYS:
        return
    try:
        anthropic.Anthropic(api_key=api_key, max_retries=1, timeout=15).models.list(limit=1)
    except anthropic.APIError as e:
        key_error = anthropic_key_error(e, user_name)
        if key_error is not None:
            raise key_error from e
        print(f"[WARNING] Could not verify the Anthropic key for '{user_name}' ({e}); continuing.")
        return
    _VERIFIED_ANTHROPIC_KEYS.add(fingerprint)


# ---------------------------------------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------------------------------------
def _gemini_detail_items(error: genai_errors.APIError) -> list[dict]:
    details = error.details if isinstance(error.details, dict) else {}
    inner = details.get("error", details)
    items = inner.get("details") if isinstance(inner, dict) else None
    return [item for item in items or [] if isinstance(item, dict)]


def gemini_retry_delay(error: BaseException) -> Optional[float]:
    """Seconds from the error's RetryInfo ("17s", "0.5s"), if the API sent one."""
    if not isinstance(error, genai_errors.APIError):
        return None
    for item in _gemini_detail_items(error):
        match = re.fullmatch(r"\s*([\d.]+)s\s*", str(item.get("retryDelay") or ""))
        if match:
            return float(match.group(1))
    return None


def _gemini_reason(error: BaseException) -> Optional[str]:
    if not isinstance(error, genai_errors.APIError):
        return None
    status = str(error.status or "").lower()
    message = str(error.message or "").lower()
    items = _gemini_detail_items(error)
    reasons = {str(item.get("reason") or "").upper() for item in items}
    quota_ids = [
        str(violation.get("quotaId") or "")
        for item in items
        for violation in item.get("violations") or []
        if isinstance(violation, dict)
    ]

    if error.code == 401 or status in ("unauthenticated", "authentication") or reasons & {"API_KEY_INVALID", "API_KEY_EXPIRED"} \
            or "api key not valid" in message or "api key expired" in message:
        return "expired" if "API_KEY_EXPIRED" in reasons else _expired_or_invalid(message)
    if error.code == 402 or status == "payment_required" or "prepay" in message or "credit balance" in message:
        return "no_credits"
    if error.code == 403 or status == "permission_denied":
        return "invalid" if "leaked" in message else "permission"  # Leaked keys are blocked for good
    if status == "failed_precondition":
        return "no_credits" if "billing" in message or "free tier" in message else "permission"
    if error.code == 429:
        delay = gemini_retry_delay(error)
        if status == "quota_exceeded" or "daily" in message or any("PerDay" in quota_id for quota_id in quota_ids) \
                or (delay is not None and delay > GEMINI_MAX_RETRY_DELAY_SECONDS):
            return "quota_exhausted"
    return None


def gemini_key_error(error: BaseException, user_name: Optional[str]) -> Optional[ApiKeyUnusable]:
    """
    ApiKeyUnusable when `error` means the remaining requests with this key would fail the same way this run:
    a key problem, or a 429 that survived GEMINI_RETRY ("rate_limited"). None for per-request errors.
    """
    reason = _gemini_reason(error)
    if reason is None and isinstance(error, genai_errors.APIError) and error.code == 429:
        reason = "rate_limited"
    if reason is None:
        return None
    return ApiKeyUnusable("Gemini", reason, user_name, str(getattr(error, "message", "") or error))


def _is_retryable_gemini_error(error: BaseException) -> bool:
    if isinstance(error, TRANSIENT_NETWORK_ERRORS):
        return True
    return (
        isinstance(error, genai_errors.APIError)
        and error.code in GEMINI_RETRYABLE_CODES
        and _gemini_reason(error) is None
    )


def _gemini_wait(retry_state) -> float:
    """Honors the API's RetryInfo delay when given, otherwise 4 s, 8 s, 16 s (capped at 20 s)."""
    delay = gemini_retry_delay(retry_state.outcome.exception())
    if delay is not None:
        return min(delay + 1, GEMINI_MAX_RETRY_DELAY_SECONDS)
    return min(4 * 2 ** (retry_state.attempt_number - 1), 20)


def _log_gemini_retry(retry_state) -> None:
    error = retry_state.outcome.exception()
    print(
        f"[DIAGNOSTIC] Gemini attempt {retry_state.attempt_number} failed ({getattr(error, 'code', type(error).__name__)} "
        f"{getattr(error, 'status', '') or ''}). Retrying in {retry_state.next_action.sleep:.0f}s..."
    )


# tenacity settings shared by every Gemini call: retries only timeouts, connection errors, per-minute 429s, and
# 5xx; never key problems, billing, exhausted quotas, or other 4xx, which fail identically on every attempt.
GEMINI_RETRY: dict[str, Any] = {
    "stop": stop_after_attempt(4),
    "wait": _gemini_wait,
    "retry": retry_if_exception(_is_retryable_gemini_error),
    "before_sleep": _log_gemini_retry,
    "reraise": True,
}
