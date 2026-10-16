"""The universal pipeline: Claude's reasoning on picks, the ticker funnel's filters and caches, and the macro content."""
import json
import threading
import time
from types import SimpleNamespace

import httpx2
import pandas as pd
import pytest
import requests

import src.track_record as track_record
import src.universal.macro_indicators as macro
import src.universal.market_news as market_news
import src.universal.recommendations.LLM_recommendation as universal_llm
import src.universal.recommendations.filter_tickers as filter_tickers
import src.universal.universal_consolidated as universal
from src.personalized.api_errors import ApiKeyUnusable
from src.universal.recommendations import data_source_guard as guard
from src.universal.recommendations.scorer import select_top_candidates
from src.universal.recommendations.twelveData_filter_helper import filter_twelvedata_tickers
from src.universal.recommendations.yahoo_filter_helper import filter_yahoo_tickers


# --------------------------------------------------------------------------------------------- reasoning on picks
def evaluation(long_scores, swing_scores, levels):
    entry, stop, target = levels
    return {
        "long_term": dict(zip(("valuation_score", "financial_health_score", "growth_momentum_score",
                               "competitive_moat_score"), long_scores),
                          sentiment="Bullish", thesis="Forward P/E of 22 against 15% revenue growth.",
                          key_risk="Debt-to-equity of 45% limits room for buybacks."),
        "swing": dict(zip(("technical_setup_score", "risk_reward_ratio_score", "momentum_volume_score",
                           "catalyst_sentiment_score"), swing_scores),
                      sentiment="Neutral", thesis="Holding the 21-day EMA at 196 with RSI 48.",
                      entry_price=entry, stop_loss=stop, price_target=target),
    }


EVALUATIONS = {
    "AAA": evaluation((22, 21, 20, 20), (20, 20, 15, 15), (200, 190, 220)),   # Long 83: the long-term pick
    "BBB": evaluation((10, 10, 15, 15), (26, 25, 17, 17), (100, 104, 120)),   # Swing 85, stop above entry
    "CCC": evaluation((12, 12, 12, 12), (25, 24, 16, 16), (100, 95, 112.5)),  # Swing 81
}


@pytest.fixture
def claude_scores(monkeypatch):
    async def handler(request):
        prompt = json.loads(request.content)["messages"][0]["content"]
        symbol = prompt.split()[1]  # "Evaluate <symbol> against ..."
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5", "stop_reason": "tool_use",
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [{"type": "tool_use", "id": "toolu_1", "name": universal_llm.EVALUATION_TOOL_NAME,
                         "input": EVALUATIONS[symbol]}]})

    real = universal_llm.AsyncAnthropic
    monkeypatch.setattr(universal_llm, "AsyncAnthropic", lambda **kw: real(
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), **kw))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-shared")


def test_picks_carry_claudes_reasoning_and_sane_levels(claude_scores):
    picks = select_top_candidates({symbol: {"symbol": symbol} for symbol in EVALUATIONS}, long_n=2, swing_n=5)

    [long_pick] = picks["top_long_term"]
    assert long_pick["ticker"] == "AAA" and long_pick["longScore"] == 83
    assert long_pick["sentiment"] == "Bullish" and long_pick["thesis"].startswith("Forward P/E")
    assert long_pick["key_risk"].startswith("Debt-to-equity")
    assert long_pick["sub_scores"] == {"valuation_score": 22, "financial_health_score": 21,
                                       "growth_momentum_score": 20, "competitive_moat_score": 20}

    assert [p["ticker"] for p in picks["top_swing"]] == ["BBB", "CCC"]  # AAA is already the long-term pick
    bad, good = picks["top_swing"]
    assert bad["sentiment"] == "Neutral" and bad["thesis"]
    assert (bad["entry_price"], bad["stop_loss"], bad["price_target"], bad["risk_reward"]) == (None,) * 4
    assert (good["entry_price"], good["stop_loss"], good["price_target"], good["risk_reward"]) == (100, 95, 112.5, 2.5)


def test_tool_schema_asks_for_the_written_fields():
    properties = universal_llm.EVALUATION_TOOL_SPEC["input_schema"]["$defs"]
    long_term, swing = properties["LongTermSubScores"]["properties"], properties["SwingSubScores"]["properties"]
    assert long_term["sentiment"]["enum"] == swing["sentiment"]["enum"] == ["Bullish", "Neutral", "Bearish"]
    assert {"thesis", "key_risk"} <= set(long_term) and {"thesis", "entry_price", "stop_loss", "price_target"} <= set(swing)
    assert set(properties["SwingSubScores"]["required"]) == set(swing)  # Strict tool use needs every field required


def test_track_record_keeps_sentiment_and_levels():
    picks = {"top_long_term": [{"ticker": "AAA", "longScore": 83, "swingScore": 70, "sentiment": "Bullish"}],
             "top_swing": [{"ticker": "CCC", "longScore": 48, "swingScore": 81, "sentiment": "Neutral",
                            "entry_price": 100, "stop_loss": 95, "price_target": 112.5}]}
    track_record.log_universal(picks, {"AAA": {"price_action": {"current_price": 201}},
                                       "CCC": {"price_action": {"current_price": 100.5}}})
    rows = [json.loads(line) for line in track_record.UNIVERSAL_PATH.read_text().splitlines()]
    assert rows[0]["sentiment"] == "Bullish" and rows[0]["stop_loss"] is None
    assert (rows[1]["entry_price"], rows[1]["stop_loss"], rows[1]["price_target"]) == (100, 95, 112.5)


# --------------------------------------------------------------------------------------------- Twelve Data on a small plan
def test_batch_larger_than_the_plan_allows_is_skipped_at_once(monkeypatch):
    monkeypatch.setattr(guard.time, "sleep", lambda s: pytest.fail("waited for a request that can never fit"))
    calls = []

    def get(url, params, timeout):
        calls.append(params["symbol"])
        n = len(params["symbol"].split(","))
        if n > 8:
            return SimpleNamespace(json=lambda: {"status": "error", "code": 429, "message": (
                f"You have run out of API credits for the current minute. {n} API credits were used, with the "
                "current limit being 8. Wait for the next minute or consider switching to a higher tier plan.")})
        return SimpleNamespace(json=lambda: {"values": [1]})

    issues, state = guard.DataSourceIssues(), {}
    twenty = {"symbol": ",".join(f"T{i}" for i in range(20))}
    assert guard.twelvedata_request(get, "u", twenty, issues, state) is None
    assert guard.twelvedata_request(get, "u", twenty, issues, state) is None  # Not sent again this run
    assert guard.twelvedata_request(get, "u", {"symbol": "A,B,C"}, issues, state) == {"values": [1]}
    assert len(calls) == 2 and [i["reason"] for i in issues.items] == ["plan_limit"]
    assert "20 tickers" in issues.items[0]["message"]


def test_missing_technicals_keep_tickers_instead_of_dropping_them():
    rule = [("adx", "1day", "<=", 45, {"time_period": 14})]

    def fetcher(batch):
        return SimpleNamespace(all_tickers=[], fetch_twelvedata_batch_data=lambda symbols, indicators: batch)

    assert filter_twelvedata_tickers(fetcher({}), ["AAA", "BBB"], rule) == ["AAA", "BBB"]  # Whole batch failed
    partial = {"AAA": {"adx_14": {"values": [{"adx": "50"}]}},                         # Fails the rule
               "BBB": {"adx_14": {"code": 429, "status": "error"}},                    # Error for this symbol
               "DDD": {"adx_14": {"values": [{"adx": "20"}]}}}                         # Passes
    assert filter_twelvedata_tickers(fetcher(partial), ["AAA", "BBB", "CCC", "DDD"], rule) == ["BBB", "CCC", "DDD"]


# --------------------------------------------------------------------------------------------- Yahoo stage and caches
def test_yahoo_stage_fetches_in_parallel_and_keeps_order(monkeypatch):
    monkeypatch.setitem(filter_tickers.params_dict, "YAHOO_FETCH_CONCURRENCY", 4)
    running = {"now": 0, "max": 0}
    lock = threading.Lock()

    class Client:
        all_tickers = []

        def fetch_yahoo_data(self, symbol, fetch_news=False):
            with lock:
                running["now"] += 1
                running["max"] = max(running["max"], running["now"])
            time.sleep(0.1)
            with lock:
                running["now"] -= 1
            return {"marketCap": 50 if symbol in ("B", "D") else 5}

        def get_metric_with_alert(self, data, key, symbol, default=None):
            return data.get(key, default)

    kept = filter_yahoo_tickers(Client(), list("ABCDEFGH"), [("marketCap", ">=", 10)])
    assert kept == ["B", "D"] and running["max"] > 1


def test_filter_stage_data_is_reused_for_the_survivors(monkeypatch):
    fetcher = object.__new__(filter_tickers.DataFetcher)  # No .env, screener file, or clients needed
    created = []

    class Ticker:
        def __init__(self, symbol):
            created.append(symbol)
            self.info = {"symbol": symbol, "marketCap": 1}
            self.news = [{"title": "headline"}]

    monkeypatch.setattr(filter_tickers.yf, "Ticker", Ticker)
    first = fetcher.fetch_yahoo_data("AAA")
    first["marketCap"] = 999  # A caller changing its copy doesn't change the cache
    assert fetcher.fetch_yahoo_data("AAA") == {"symbol": "AAA", "marketCap": 1} and created == ["AAA"]
    fetcher.fetch_yahoo_data("AAA", fetch_news=True)
    assert created == ["AAA", "AAA"]  # News isn't cached

    calls = []

    def endpoint(name, fail=False):
        def call(*args, **kwargs):
            calls.append(name)
            if fail:
                raise RuntimeError("429")
            return {"metric": {"peTTM": 20}} if name == "financials" else {}
        return call

    monkeypatch.setattr(filter_tickers.time, "sleep", lambda s: None)
    fetcher.finnhub_client = SimpleNamespace(company_profile2=endpoint("profile"), quote=endpoint("quote"),
                                             company_basic_financials=endpoint("financials"),
                                             earnings_calendar=endpoint("calendar"))
    assert fetcher.fetch_finnhub_data("AAA")["basic_financials"] == {"peTTM": 20}
    fetcher.fetch_finnhub_data("AAA")
    assert len(calls) == 4  # Second call served from the cache

    fetcher.finnhub_client.quote = endpoint("quote", fail=True)
    fetcher.fetch_finnhub_data("BBB")
    fetcher.fetch_finnhub_data("BBB")
    assert len(calls) == 12  # A failed call isn't cached, so it's tried again


# --------------------------------------------------------------------------------------------- macro content
def test_dashboard_problems_become_issues_and_never_raise(monkeypatch):
    monkeypatch.setattr(macro, "get_market_dashboard_data", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert macro.get_market_dashboard() == ({}, [{"source": "Market dashboard", "reason": "failed",
                                                  "message": "Market indicators couldn't be fetched (RuntimeError)."}])
    monkeypatch.setattr(macro, "get_market_dashboard_data", lambda: {
        "yahoo_finance": {"VIX Index": {"Latest": 15.2}}, "yield_curve_10y_2y": {"Spread": "0.5%"},
        "fear_and_greed": {"Error": "Scraping Failed (403)"}, "sp500_breadth": {"% > 50MA": "61.00%"}})
    dashboard, issues = macro.get_market_dashboard()
    assert dashboard["yahoo_finance"] and [i["source"] for i in issues] == ["Market dashboard: fear_and_greed"]


def test_breadth_uses_enough_history_and_fred_errors_hide_the_key(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "SECRET123")
    periods = []

    def download(tickers, period, interval, progress):
        periods.append(period)
        days = pd.date_range("2026-04-01", periods=120, freq="B")
        prices = {t: [100 + (i if t != "CCC" else -i) * 0.5 for i in range(120)] for t in tickers}
        return pd.concat({"Close": pd.DataFrame(prices, index=days)}, axis=1)

    def get(url, headers=None, params=None, timeout=None):
        assert timeout, f"no timeout on {url}"
        if "stlouisfed" in url:
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}?api_key={params['api_key']}")
        if "wikipedia" in url:
            rows = "".join(f"<tr><td>{t}</td></tr>" for t in ("AAA", "BBB", "CCC"))
            return SimpleNamespace(text=f'<table id="constituents"><tr><th>Symbol</th></tr>{rows}</table>')
        return SimpleNamespace(json=lambda: {"fear_and_greed": {"score": 55.04, "rating": "neutral"}})

    monkeypatch.setattr(macro.yf, "download", download)
    monkeypatch.setattr(macro.requests, "get", get)
    data = macro.get_market_dashboard_data()
    assert periods[-1] == "6mo" and data["sp500_breadth"] == {"% > 50MA": "66.67%"}  # 2 of 3 above the 50-day
    assert "SECRET123" not in json.dumps(data) and data["fear_and_greed"]["Rating"] == "Neutral"


def test_macro_content_is_included_even_without_picks(monkeypatch, telegram):
    def rejected(provider, api_key, user_name):
        raise ApiKeyUnusable("Anthropic", "invalid", None, "invalid x-api-key")

    stories = market_news.DigestPayload(macro_stories=[market_news.MacroStory(
        priority="HIGH", event_summary="Fed held rates.", market_impact="Yields fell.", source_url=None)])
    news_issue = {"source": "Gemini (macro news)", "reason": "failed", "message": "x"}
    dashboard_issue = {"source": "Market dashboard: fear_and_greed", "reason": "failed", "message": "403"}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-shared")
    monkeypatch.setattr(universal, "verify_llm_key", rejected)
    monkeypatch.setattr(universal, "get_macro_stories", lambda: (stories, [news_issue]))
    monkeypatch.setattr(universal, "get_market_dashboard", lambda: ({"sp500_breadth": {"% > 50MA": "61%"}}, [dashboard_issue]))

    content = universal.get_universal_content()
    assert content["recommendations"]["top_long_term"] == []
    assert content["headline_news"]["macro_stories"][0]["event_summary"] == "Fed held rates."
    assert content["market_data"] == {"sp500_breadth": {"% > 50MA": "61%"}}
    assert [i["source"] for i in content["universal_issues"]] == [
        "Anthropic (shared key)", "Gemini (macro news)", "Market dashboard: fear_and_greed"]
    assert any("Market dashboard: fear_and_greed" in body for _, body in telegram.messages)


def test_extras_can_be_switched_off(monkeypatch):
    monkeypatch.setitem(universal.params_dict, "UNIVERSAL_EXTRAS", {"MACRO_NEWS": False, "MARKET_DASHBOARD": False})
    monkeypatch.setattr(universal, "get_macro_stories", lambda: pytest.fail("macro news fetched while switched off"))
    monkeypatch.setattr(universal, "get_market_dashboard", lambda: pytest.fail("dashboard fetched while switched off"))
    monkeypatch.setattr(universal, "_get_recommendations", lambda: {"recommendations": {}, "universal_issues": []})
    assert universal.get_universal_content() == {"recommendations": {}, "market_data": {}, "headline_news": {},
                                                 "universal_issues": []}


def test_macro_news_keeps_only_grounded_links(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-shared")
    body = {"macro_stories": [
        {"priority": "HIGH", "event_summary": "Fed held.", "market_impact": "Yields fell.",
         "source_url": "https://reuters.com/markets/fed-holds/"},
        {"priority": "MEDIUM", "event_summary": "CPI cooled.", "market_impact": "Stocks rose.",
         "source_url": "https://made.up/story"}]}
    response = SimpleNamespace(
        text=json.dumps(body), usage_metadata=None,
        candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(
            grounding_chunks=[SimpleNamespace(web=SimpleNamespace(uri="https://www.reuters.com/markets/fed-holds"))],
            web_search_queries=["fed decision"]))])
    chat = SimpleNamespace(send_message=lambda prompt: response)
    monkeypatch.setattr(market_news.genai, "Client", lambda api_key: SimpleNamespace(
        chats=SimpleNamespace(create=lambda model, config: chat)))

    stories = market_news.fetch_live_macro_stories().macro_stories
    assert [s.source_url for s in stories] == ["https://www.reuters.com/markets/fed-holds", None]


def test_one_listing_per_company_keeps_the_most_traded():
    from src.universal.recommendations.yahoo_filter_helper import one_listing_per_company

    metrics = {"GOOGL": {"longName": "Alphabet Inc.", "averageVolume": 30e6},
               "GOOG": {"longName": "Alphabet Inc.", "averageVolume": 20e6},
               "MSFT": {"longName": "Microsoft Corporation", "averageVolume": 25e6},
               "XYZ": {}}  # No name: kept under its own symbol
    assert one_listing_per_company(["GOOG", "MSFT", "GOOGL", "XYZ"], metrics) == ["MSFT", "GOOGL", "XYZ"]


def test_configured_filters_are_well_formed():
    """A malformed rule would raise mid-run (unknown operator) or never match (wrong section)."""
    from src.parameters import params_dict

    operators = {"<", "<=", ">", ">=", "==", "=", "!="}
    assert all(len(rule) == 3 and rule[1] in operators for rule in params_dict["YAHOO_FILTERS"])
    assert all(len(rule) == 4 and rule[0] in ("profile", "quote", "basic_financials") and rule[2] in operators
               for rule in params_dict["FINNHUB_FILTERS"])
