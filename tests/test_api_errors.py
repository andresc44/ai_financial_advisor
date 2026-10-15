"""API key failure handling for Anthropic and Gemini, exercised through the real SDKs with mock HTTP transports."""
import json

import anthropic
import httpx
import httpx2
import pytest
from google import genai
from google.genai import types

import src.personalized.api_errors as api_errors
import src.personalized.symbol_specific_news as news

USER = "Andres"


# --------------------------------------------------------------------------------------------- Anthropic
def anthropic_error(status, error_type, message, details=None, headers=None):
    error = {"type": error_type, "message": message}
    if details:
        error["details"] = details
    return httpx2.Response(status, json={"type": "error", "error": error}, headers=headers or {})


ANTHROPIC_CASES = [
    (anthropic_error(401, "authentication_error", "invalid x-api-key"), "invalid"),
    (anthropic_error(401, "authentication_error", "API key expired"), "expired"),
    (anthropic_error(403, "permission_error", "no permission"), "permission"),
    (anthropic_error(402, "billing_error", "billing problem"), "no_credits"),
    (anthropic_error(400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API."), "no_credits"),
    (anthropic_error(400, "invalid_request_error", "You have reached your specified workspace API usage limits."), "spend_limit"),
    (anthropic_error(429, "rate_limit_error", "monthly cap", {"error_code": "enforced_spend_limit_reached"}), "spend_limit"),
    (anthropic_error(429, "rate_limit_error", "per-minute limit", headers={"retry-after": "0"}), None),
    (anthropic_error(529, "overloaded_error", "Overloaded", headers={"retry-after": "0"}), None),
    (anthropic_error(400, "invalid_request_error", "some per-request problem"), None),
]


@pytest.fixture
def anthropic_server(monkeypatch):
    """Routes anthropic.Anthropic (used by verify_anthropic_key) to a mock transport; returns the call log."""
    state = {"response": None, "calls": 0}

    def handler(request):
        state["calls"] += 1
        return state["response"]

    real = anthropic.Anthropic
    monkeypatch.setattr(
        api_errors.anthropic, "Anthropic",
        lambda **kw: real(http_client=httpx2.Client(transport=httpx2.MockTransport(handler)), **kw),
    )
    monkeypatch.setattr(api_errors, "_VERIFIED_ANTHROPIC_KEYS", set())
    return state


@pytest.mark.parametrize("response, reason", ANTHROPIC_CASES)
def test_verify_anthropic_key_classifies_errors(anthropic_server, response, reason):
    anthropic_server["response"] = response
    if reason is None:
        api_errors.verify_anthropic_key("sk-test", USER)  # Not a key problem: the real request decides
    else:
        with pytest.raises(api_errors.ApiKeyUnusable) as raised:
            api_errors.verify_anthropic_key("sk-test", USER)
        assert raised.value.reason == reason
        assert "ANDRES_ANTHROPIC_API_KEY" in str(raised.value) or reason not in ("invalid", "expired")


def test_valid_anthropic_key_is_checked_once(anthropic_server):
    anthropic_server["response"] = httpx2.Response(
        200, json={"data": [], "has_more": False, "first_id": None, "last_id": None}
    )
    api_errors.verify_anthropic_key("sk-good", USER)
    api_errors.verify_anthropic_key("sk-good", USER)
    assert anthropic_server["calls"] == 1


def test_missing_key_is_an_api_key_error_and_a_value_error():
    from src.personalized.user_settings import get_anthropic_api_key

    with pytest.raises(ValueError) as raised:
        get_anthropic_api_key("NobodyInProfiles")
    assert isinstance(raised.value, api_errors.ApiKeyUnusable) and raised.value.reason == "missing"


# --------------------------------------------------------------------------------------------- Gemini
INFO = "type.googleapis.com/google.rpc.ErrorInfo"


def gemini_error(code, message, status, details=None):
    body = {"error": {"code": code, "message": message, "status": status}}
    if details:
        body["error"]["details"] = details
    return httpx.Response(code, json=body)


def quota(quota_id, delay):
    return [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
    ]


def gemini_ok(text):
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}]})


GEMINI_KEY_CASES = [
    (gemini_error(400, "API key not valid. Please pass a valid API key.", "INVALID_ARGUMENT", [{"@type": INFO, "reason": "API_KEY_INVALID"}]), "invalid"),
    (gemini_error(400, "API key expired. Please renew the API key.", "INVALID_ARGUMENT", [{"@type": INFO, "reason": "API_KEY_INVALID"}]), "expired"),
    (gemini_error(401, "The API key is missing, invalid, or expired.", "authentication"), "invalid"),
    (gemini_error(402, "Your Prepay credit balance is depleted.", "payment_required"), "no_credits"),
    (gemini_error(403, "Your API key was reported as leaked.", "PERMISSION_DENIED"), "invalid"),
    (gemini_error(403, "API has not been used in project or it is disabled.", "PERMISSION_DENIED"), "permission"),
    (gemini_error(400, "Free tier is not available in your country. Please enable billing.", "FAILED_PRECONDITION"), "no_credits"),
    (gemini_error(400, "User location is not supported for the API use.", "FAILED_PRECONDITION"), "permission"),
    (gemini_error(429, "You exceeded your current quota.", "RESOURCE_EXHAUSTED", quota("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "41s")), "quota_exhausted"),
    (gemini_error(429, "You have exceeded your daily quota.", "quota_exceeded"), "quota_exhausted"),
    (gemini_error(429, "Quota exceeded.", "RESOURCE_EXHAUSTED", quota("SomeQuota", "3600s")), "quota_exhausted"),
]


@pytest.fixture
def gemini_server(monkeypatch):
    """Routes genai.Client to a mock transport; `plan(n)` returns the n-th response (None = success)."""
    state = {"plan": None, "calls": 0}

    def handler(request):
        state["calls"] += 1
        response = state["plan"](state["calls"])
        if response is None:
            is_review = b"Review this portfolio" in request.content
            text = json.dumps({"notes": []} if is_review else {"news_items": [
                {"priority": "high", "event_summary": "x.", "stock_impact": "y.", "source_url": None}]})
            return gemini_ok(text)
        return response

    real = genai.Client
    monkeypatch.setattr(genai, "Client", lambda api_key=None, **kw: real(
        api_key=api_key, http_options=types.HttpOptions(httpx_client=httpx.Client(transport=httpx.MockTransport(handler)))))
    return state


COMPANIES = {"AAA": "Alpha", "BBB": "Beta", "CCC": "Gamma"}


@pytest.mark.parametrize("response, reason", GEMINI_KEY_CASES)
def test_gemini_key_failure_stops_news_without_retrying(gemini_server, response, reason):
    gemini_server["plan"] = lambda n: response
    result, key_error = news.fetch_symbol_news(COMPANIES, "g-key", user_name=USER, max_workers=1)
    assert key_error is not None and key_error.reason == reason and key_error.blocks_run
    assert gemini_server["calls"] == 1  # No retries, and the remaining stocks are skipped
    assert all(items == [] for items in result.values())


def test_gemini_per_minute_limit_is_retried_then_succeeds(gemini_server):
    limited = gemini_error(429, "slow down", "RESOURCE_EXHAUSTED", quota("GenerateRequestsPerMinutePerProjectPerModel", "0.1s"))
    gemini_server["plan"] = lambda n: limited if n <= 2 else None
    result, key_error = news.fetch_symbol_news({"AAA": "Alpha"}, "g-key", user_name=USER)
    assert key_error is None and len(result["AAA"]) == 1
    assert gemini_server["calls"] == 3


def test_gemini_persistent_rate_limit_becomes_rate_limited(gemini_server):
    limited = gemini_error(429, "slow down", "RESOURCE_EXHAUSTED", quota("GenerateRequestsPerMinutePerProjectPerModel", "0.05s"))
    gemini_server["plan"] = lambda n: limited
    _, key_error = news.fetch_symbol_news({"AAA": "Alpha"}, "g-key", user_name=USER, max_workers=1)
    assert key_error.reason == "rate_limited" and not key_error.blocks_run
    assert gemini_server["calls"] == 4  # GEMINI_RETRY's attempts


def test_gemini_non_key_errors_fail_per_stock_without_retry(gemini_server):
    gemini_server["plan"] = lambda n: gemini_error(404, "model not found", "NOT_FOUND")
    result, key_error = news.fetch_symbol_news(COMPANIES, "g-key", user_name=USER, max_workers=1)
    assert key_error is None and gemini_server["calls"] == 3
    assert all(items == [] for items in result.values())
