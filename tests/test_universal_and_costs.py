"""Universal content failure handling (shared keys, Finnhub, Twelve Data, data gaps, macro news), the data on universal
picks, and API cost tracking."""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import finnhub
import httpx2
import pytest
from google.genai import errors as genai_errors

import src.universal.market_news as market_news
import src.universal.recommendations.LLM_recommendation as universal_llm
import src.universal.universal_consolidated as universal
import src.usage_tracking as usage
from src.parameters import params_dict
from src.personalized.api_errors import ApiKeyUnusable
from src.universal.recommendations import data_source_guard as guard
from src.universal.recommendations import enriched_rec
from src.universal.recommendations.sanitizer import sanitize_ticker_payload


# --------------------------------------------------------------------------------------------- shared Anthropic key
def test_bad_shared_key_skips_data_fetch_and_alerts(monkeypatch, telegram):
    def rejected(api_key, user_name):
        raise ApiKeyUnusable("Anthropic", "no_credits", None, "Your credit balance is too low.")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-shared")
    monkeypatch.setattr(universal, "verify_anthropic_key", rejected)
    monkeypatch.setattr(universal, "Tickers", lambda: pytest.fail("screener fetched despite an unusable key"))
    content = universal.get_universal_content()
    assert content["recommendations"]["top_long_term"] == [] and content["recommendations"]["top_swing"] == []
    assert content["universal_issues"][0]["reason"] == "no_credits"
    assert "ANTHROPIC_API_KEY" in content["universal_issues"][0]["message"]
    assert any("universal content: Anthropic (shared key) no_credits" in body for _, body in telegram.messages)


def test_missing_finnhub_key_no_longer_aborts_every_digest(monkeypatch, telegram):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-shared")
    monkeypatch.setattr(universal, "verify_anthropic_key", lambda api_key, user_name: None)
    monkeypatch.setattr(universal, "Tickers", lambda: SimpleNamespace(fetch_ticker_data=lambda **kwargs: None))

    def no_finnhub():
        raise ValueError("'FINNHUB_API_KEY' not found")

    monkeypatch.setattr(universal, "DataFetcher", no_finnhub)
    content = universal.get_universal_content()  # Used to call sys.exit(1)
    assert content["universal_issues"] == [{"source": "Finnhub", "reason": "missing_key", "message": "'FINNHUB_API_KEY' not found"}]


def test_scoring_stops_on_a_shared_key_failure(monkeypatch):
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        return httpx2.Response(402, json={"type": "error", "error": {"type": "billing_error", "message": "Billing issue"}})

    real = universal_llm.AsyncAnthropic
    monkeypatch.setattr(universal_llm, "AsyncAnthropic", lambda **kw: real(
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), **kw))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-shared")
    issues = []
    scores = universal_llm.run_llm_evaluation({f"T{i}": {"symbol": f"T{i}"} for i in range(6)},
                                              config={"max_concurrency": 1}, issues=issues)
    assert scores == {} and calls["n"] == 1
    assert issues[0]["source"] == "Anthropic (shared key)" and issues[0]["reason"] == "no_credits"


# --------------------------------------------------------------------------------------------- Finnhub
class FakeFinnhubResponse:
    def __init__(self, status):
        self.status_code = status
        self.text = ""

    def json(self):
        return {"error": f"status {self.status_code}"}


def finnhub_error(status):
    return finnhub.FinnhubAPIException(FakeFinnhubResponse(status))


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(guard.time, "sleep", slept.append)
    return slept


def guarded(behaviour):
    issues = guard.DataSourceIssues()
    state = {"calls": 0}

    def quote(symbol):
        state["calls"] += 1
        return behaviour(state["calls"])

    return guard.GuardedFinnhubClient(SimpleNamespace(quote=quote), issues), issues, state


def test_finnhub_rate_limit_is_retried(no_sleep):
    def behaviour(n):
        if n <= 2:
            raise finnhub_error(429)
        return {"c": 100}

    client, issues, state = guarded(behaviour)
    assert client.quote("AAPL") == {"c": 100} and state["calls"] == 3 and no_sleep == [2, 5]
    assert issues.items == []


def test_finnhub_rejected_key_stops_further_calls(no_sleep):
    client, issues, state = guarded(lambda n: (_ for _ in ()).throw(finnhub_error(401)))
    with pytest.raises(finnhub.FinnhubAPIException):
        client.quote("AAPL")
    with pytest.raises(guard.DataSourceUnavailable):
        client.quote("MSFT")
    assert state["calls"] == 1 and [i["reason"] for i in issues.items] == ["invalid_key"]


def test_finnhub_persistent_rate_limit_is_reported(no_sleep):
    client, issues, state = guarded(lambda n: (_ for _ in ()).throw(finnhub_error(429)))
    with pytest.raises(finnhub.FinnhubAPIException):
        client.quote("AAPL")
    assert state["calls"] == 4 and [i["reason"] for i in issues.items] == ["rate_limited"]


# --------------------------------------------------------------------------------------------- Twelve Data
def twelvedata_get(responses):
    calls = []

    def get(url, params, timeout):
        calls.append(url)
        body = responses[min(len(calls), len(responses)) - 1]
        return SimpleNamespace(json=lambda: body)

    return get, calls


MINUTE_LIMIT = {"status": "error", "code": 429, "message": "You have run out of API credits for the current minute."}
DAY_LIMIT = {"status": "error", "code": 429, "message": "You have run out of API credits for the day."}
BAD_KEY = {"status": "error", "code": 401, "message": "**apikey** parameter is incorrect or not specified."}


def test_twelvedata_waits_out_the_minute_limit(no_sleep):
    get, calls = twelvedata_get([MINUTE_LIMIT, {"values": [1]}])
    issues = guard.DataSourceIssues()
    assert guard.twelvedata_request(get, "u", {}, issues, {}) == {"values": [1]}
    assert len(calls) == 2 and len(no_sleep) == 1 and issues.items == []


@pytest.mark.parametrize("error, reason", [(DAY_LIMIT, "quota_exhausted"), (BAD_KEY, "invalid_key")])
def test_twelvedata_stops_for_the_run(no_sleep, error, reason):
    get, calls = twelvedata_get([error])
    issues, state = guard.DataSourceIssues(), {}
    assert guard.twelvedata_request(get, "u", {}, issues, state) is None
    assert guard.twelvedata_request(get, "u", {}, issues, state) is None
    assert len(calls) == 1 and [i["reason"] for i in issues.items] == [reason]


def test_twelvedata_per_request_errors_are_not_issues(no_sleep):
    get, _ = twelvedata_get([{"status": "error", "code": 400, "message": "symbol not found"}])
    issues = guard.DataSourceIssues()
    assert guard.twelvedata_request(get, "u", {}, issues, {}) is None and issues.items == []


def test_missing_data_issues():
    packets = {f"T{i}": {"yahoo": {"currentPrice": 10}, "finnhub": {"basic_financials": {"beta": 1}},
                         "twelvedata": {"rsi_14": {"values": [{"rsi": 50}]}}} for i in range(10)}
    for ticker in ("T1", "T2", "T3"):
        packets[ticker]["twelvedata"] = {}
    issues = guard.missing_data_issues(packets, max_missing_pct=20)
    assert [(i["source"], i["reason"]) for i in issues] == [("Technical indicators", "incomplete_data")]
    assert "3 of 10 tickers (30%)" in issues[0]["message"]
    assert guard.missing_data_issues(packets, max_missing_pct=40) == []


# --------------------------------------------------------------------------------------------- macro news
def test_macro_news_key_failure_becomes_an_issue(monkeypatch, telegram):
    error = genai_errors.APIError(400, {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT"}})

    def failing():
        raise error

    monkeypatch.setattr(market_news, "fetch_live_macro_stories", failing)
    stories, issues = market_news.get_macro_stories()
    assert stories is None and issues[0]["reason"] == "invalid"
    assert "GEMINI_API_KEY" in issues[0]["message"]
    assert any("Gemini (shared key) invalid" in body for _, body in telegram.messages)


def test_macro_news_missing_shared_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    stories, issues = market_news.get_macro_stories()
    assert stories is None and issues[0]["reason"] == "missing"


# --------------------------------------------------------------------------------------------- cost tracking
def ledger():
    return [json.loads(line) for line in usage.LEDGER_PATH.read_text().splitlines()]


def anthropic_response(model, input_tokens, output_tokens, iterations=None, cache_read=0):
    return SimpleNamespace(model=model, usage=SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens, cache_creation_input_tokens=0,
        cache_read_input_tokens=cache_read, iterations=iterations))


def test_claude_cost_uses_the_exact_model_price():
    usage.record_anthropic_response("Andres", "digest", anthropic_response("claude-opus-5-5", 1000, 500))
    usage.record_anthropic_response("Andres", "digest", anthropic_response("claude-opus-5", 1000, 500))
    first, second = ledger()
    assert first["cost_usd"] == pytest.approx((1000 * 4 + 500 * 20) / 1e6)   # Not Opus 5's price
    assert second["cost_usd"] == pytest.approx((1000 * 5 + 500 * 25) / 1e6)


def test_fallback_attempts_are_priced_per_model():
    iterations = [SimpleNamespace(type="message", model="claude-fable-5-1", input_tokens=100, output_tokens=0,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
                  SimpleNamespace(type="fallback_message", model="claude-opus-5-5", input_tokens=100, output_tokens=50,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0)]
    usage.record_anthropic_response("Andres", "watchdog", anthropic_response("claude-opus-5-5", 100, 50, iterations))
    rows = ledger()
    assert [r["model"] for r in rows] == ["claude-fable-5-1", "claude-opus-5-5"]
    assert rows[0]["cost_usd"] == pytest.approx(100 * 10 / 1e6)


def test_unpriced_models_are_counted_not_costed():
    usage.record_anthropic_response("Andres", "digest", anthropic_response("claude-future-9", 10, 10))
    totals = usage.usage_summary("Andres", datetime.now(timezone.utc) - timedelta(minutes=1))["this_digest"]["anthropic"]
    assert totals["calls"] == 1 and totals["cost_usd"] == 0 and totals["unpriced_models"] == ["claude-future-9"]


def test_haiku_long_prompt_rates():
    tokens = {"input_tokens": 150_000, "output_tokens": 1000, "cache_write_tokens": 0, "cache_read_tokens": 0}
    assert usage.anthropic_cost("claude-haiku-5-5", tokens, date(2026, 10, 16)) == pytest.approx((150_000 * 0.5 + 1000 * 2.5) / 1e6)


def test_gemini_tokens_and_scheduled_price_change():
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(prompt_token_count=2000, cached_content_token_count=500, candidates_token_count=300,
                                       thoughts_token_count=200, tool_use_prompt_token_count=100),
        candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(web_search_queries=["a", "b", "c"]))])
    usage.record_gemini_response("Andres", "digest", "gemini-3.6-flash", response)
    row = ledger()[0]
    assert (row["input_tokens"], row["cache_read_tokens"], row["output_tokens"], row["search_queries"]) == (1600, 500, 500, 3)
    assert row["cost_usd"] == pytest.approx((1600 * 0.75 + 500 * 0.075 + 500 * 3.75) / 1e6)
    tokens = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_tokens": 0}
    assert usage.gemini_token_cost("gemini-3.6-flash", tokens, date(2027, 1, 2)) == pytest.approx(1.50)


def test_usage_summary_periods_and_free_search_allowance(monkeypatch):
    monkeypatch.setitem(usage.params_dict["API_PRICES"], "GEMINI_FREE_SEARCH_REQUESTS_PER_MONTH", 2)
    now = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)

    def row(at, provider, cost, queries=0, user="Andres"):
        return {"at": at.isoformat(), "user": user, "source": "digest", "provider": provider, "model": "gemini-3.6-flash",
                "input_tokens": 1, "output_tokens": 1, "cache_write_tokens": 0, "cache_read_tokens": 0,
                "search_queries": queries, "cost_usd": cost}

    rows = [row(datetime(2026, 9, 30, 12, tzinfo=timezone.utc), "gemini", 5.0, 9),   # Last month: excluded
            row(datetime(2026, 10, 2, 12, tzinfo=timezone.utc), "gemini", 0.10, 2),  # Uses the free allowance
            row(datetime(2026, 10, 16, 13, tzinfo=timezone.utc), "anthropic", 0.20),  # Today (ET)
            row(now - timedelta(minutes=5), "gemini", 0.01, 1),                        # This digest: 1 billable query
            row(now - timedelta(minutes=5), "gemini", 9.99, 0, user="Steven")]         # Another user
    usage.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    usage.LEDGER_PATH.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    summary = usage.usage_summary("Andres", run_started_at=now - timedelta(minutes=10), now=now)
    assert summary["this_digest"]["gemini"]["cost_usd"] == pytest.approx(0.01 + 0.014)
    assert summary["this_digest"]["anthropic"]["calls"] == 0
    assert summary["today"]["total_cost_usd"] == pytest.approx(0.20 + 0.024)
    assert summary["month_to_date"]["gemini"]["search_queries"] == 3
    assert summary["month_to_date"]["total_cost_usd"] == pytest.approx(0.10 + 0.20 + 0.024)


def test_late_digest_on_the_last_day_of_the_month(monkeypatch):
    monkeypatch.setitem(usage.params_dict["API_PRICES"], "GEMINI_FREE_SEARCH_REQUESTS_PER_MONTH", 1)
    now = datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc)  # 20:30 ET on Oct 31

    def row(at, cost, queries):
        return {"at": at.isoformat(), "user": "Andres", "source": "digest", "provider": "gemini", "model": "gemini-3.6-flash",
                "input_tokens": 1, "output_tokens": 1, "cache_write_tokens": 0, "cache_read_tokens": 0,
                "search_queries": queries, "cost_usd": cost}

    usage.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    usage.LEDGER_PATH.write_text("\n".join(json.dumps(r) for r in [
        row(datetime(2026, 10, 31, 15, tzinfo=timezone.utc), 0.10, 1),   # Oct 31 ET, October (UTC)
        row(now - timedelta(minutes=5), 0.01, 1),                          # Oct 31 ET, November (UTC): new allowance
    ]) + "\n")
    summary = usage.usage_summary("Andres", run_started_at=now - timedelta(minutes=10), now=now)
    assert summary["today"]["gemini"]["calls"] == 2 and summary["today"]["total_cost_usd"] == pytest.approx(0.11)
    assert summary["month_to_date"]["gemini"]["calls"] == 1 and summary["month_to_date"]["total_cost_usd"] == pytest.approx(0.01)


# --------------------------------------------------------------------------------------------- data on universal picks
SANITIZED = {"AAA": {
    "symbol": "AAA", "profile": {"name": "Alpha Corp", "sector": None},
    "price_action": {"52_week_high": 210.5, "50_day_average": 190.0},
    "valuation_and_fundamentals": {"pe_trailing": 31.2},
    "sentiment_and_analysts": {"recommendation_key": "buy"},
    "catalysts_and_earnings": {"upcoming_earnings": [{"date": "2026-10-28"}]},
}}


def test_picks_get_their_fields_from_the_nested_sanitized_data():
    fields = {"top_long_term": {"pe_trailing": "valuation_and_fundamentals.pe_trailing",
                                "rating": "sentiment_and_analysts.recommendation_key"},
              "top_swing": {"52_week_high": "price_action.52_week_high", "50_day_average": "price_action.50_day_average",
                            "next_earnings": "catalysts_and_earnings.upcoming_earnings.0.date"}}
    picks = {"top_long_term": [{"ticker": "AAA", "longScore": 85.0, "swingScore": 40.0}],
             "top_swing": [{"ticker": "AAA", "swingScore": 82.0, "longScore": 85.0}]}
    out = enriched_rec.enrich_recommendations(picks, SANITIZED, fields)
    assert out["top_long_term"][0] == {"ticker": "AAA", "longScore": 85.0, "swingScore": 40.0,
                                       "pe_trailing": 31.2, "rating": "buy"}
    swing = out["top_swing"][0]
    assert (swing["52_week_high"], swing["50_day_average"], swing["next_earnings"]) == (210.5, 190.0, "2026-10-28")
    assert "pe_trailing" not in picks["top_long_term"][0]  # The scorer's picks aren't changed


def test_series_paths_and_missing_series():
    data = {"technical_indicators": {"rsi_14_1day": [{"date": "2026-10-16", "rsi": 41.2}, {"date": "2026-10-15", "rsi": 39.0}],
                                     "sar_1day": None}, "catalysts_and_earnings": {"upcoming_earnings": []}}
    assert enriched_rec.lookup(data, "technical_indicators.rsi_14_1day.0.rsi") == 41.2  # Newest first
    assert enriched_rec.lookup(data, "technical_indicators.rsi_14_1day.1.date") == "2026-10-15"
    assert enriched_rec.lookup(data, "technical_indicators.sar_1day.0.sar") is None  # Not fetched: no data, no typo
    assert enriched_rec.lookup(data, "catalysts_and_earnings.upcoming_earnings.0.date") is None  # No report scheduled
    assert enriched_rec.lookup(data, "technical_indicators.rsi_14_1dy.0.rsi") is enriched_rec._MISSING  # Typo


def test_shared_fields_typos_and_names_the_scorer_uses(capsys):
    fields = {"all": {"name": "profile.name", "sector": "profile.sector"},
              "top_swing": {"earnings": "catalysts_and_earnings.upcoming_earnings",
                            "high": "price_action.52_wk_high", "ticker": "profile.name"}}
    picks = {"top_long_term": [{"ticker": "AAA"}, {"ticker": "ZZZ"}], "top_swing": [{"ticker": "AAA"}, {"ticker": "AAA"}]}
    out = enriched_rec.enrich_recommendations(picks, SANITIZED, fields)
    assert out["top_long_term"] == [{"ticker": "AAA", "name": "Alpha Corp", "sector": None},
                                    {"ticker": "ZZZ", "name": None, "sector": None}]  # ZZZ has no data
    swing = out["top_swing"][0]
    assert swing["earnings"] == [{"date": "2026-10-28"}]
    assert swing["earnings"] is not SANITIZED["AAA"]["catalysts_and_earnings"]["upcoming_earnings"]
    assert swing["high"] is None and swing["ticker"] == "AAA"
    printed = capsys.readouterr().out
    assert printed.count("52_wk_high") == 1 and printed.count("'ticker' is already used") == 1  # Once per run


def _series(field, values):
    return {"values": [{"datetime": f"2026-10-{16 - i:02d}", **{f: str(v) for f, v in zip(field, row)}}
                       for i, row in enumerate(values)]}


# Raw data shaped like what DataFetcher gathers, with every field the default RECOMMENDATION_FIELDS read
COMPLETE_RAW = {
    "yahoo": {"longName": "Alpha Corp", "sector": "Technology", "industry": "Software", "currentPrice": 200.0,
              "marketCap": 80_000_000_000, "fiftyTwoWeekLow": 150.0, "fiftyTwoWeekHigh": 230.0, "52WeekChange": 0.12,
              "recommendationKey": "buy", "targetMeanPrice": 240.0, "numberOfAnalystOpinions": 30, "trailingPE": 28.0,
              "forwardPE": 22.0, "pegRatio": 1.4, "revenueGrowth": 0.15, "earningsGrowth": 0.2, "profitMargins": 0.25,
              "returnOnEquity": 0.3, "debtToEquity": 45.0, "freeCashflow": 5_000_000_000, "fiftyDayAverage": 195.0,
              "twoHundredDayAverage": 185.0, "beta": 1.1, "volume": 3_000_000, "averageVolume": 2_500_000},
    "finnhub": {"earnings_calendar": {"earningsCalendar": [{"date": "2027-01-28"}, {"date": "2026-10-28"}]}},
    "twelvedata": {"rsi_14_1day": _series(["rsi"], [[48.0], [45.0]]), "ema_9_1day": _series(["ema"], [[198.0]]),
                   "ema_21_1day": _series(["ema"], [[196.0]]), "sar_1day": _series(["sar"], [[188.0]]),
                   "bbands_20_1day": _series(["upper_band", "middle_band", "lower_band"], [[215.0, 197.0, 179.0]])},
}


def test_every_configured_field_comes_through_from_complete_data():
    """Catches a typo in RECOMMENDATION_FIELDS (or a renamed sanitizer field) before a digest shows nulls."""
    sanitized = sanitize_ticker_payload("AAA", COMPLETE_RAW)
    configured = params_dict["RECOMMENDATION_FIELDS"]
    for list_name in configured:
        for name, path in enriched_rec.fields_for(list_name, configured).items():
            value = enriched_rec.lookup(sanitized, path)
            assert value is not enriched_rec._MISSING and value is not None, f"{list_name}.{name}: {path}"
    assert enriched_rec.lookup(sanitized, "catalysts_and_earnings.upcoming_earnings.0.date") == "2026-10-28"  # Soonest
    assert enriched_rec.lookup(sanitized, "technical_indicators.rsi_14_1day.0.rsi") == 48.0


def test_score_thresholds_and_news_limit():
    assert params_dict["MIN_LONG_SCORE"] == params_dict["MIN_SWING_SCORE"] == 80.0
    assert params_dict["MAX_NEWS_ARTICLES"] == 20
