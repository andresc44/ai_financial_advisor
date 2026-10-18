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


# The swing risk/reward sub-score (second) is recomputed from each plan (plan_risk_reward_score, 2026-10-18)
EVALUATIONS = {
    "AAA": evaluation((22, 21, 20, 20), (20, 20, 15, 15), (200, 190, 220)),   # Long 83: the long-term pick
    "BBB": evaluation((10, 10, 15, 15), (26, 25, 17, 17), (100, 104, 120)),   # Stop above entry: risk/reward 0, swing 60
    "CCC": evaluation((12, 12, 12, 12), (25, 24, 16, 16), (100, 96, 116)),    # 4:1 plan: risk/reward 28.8, swing 85.8
    "DDD": evaluation((10, 10, 10, 10), (24, 24, 16, 16), (50, 48, 58)),      # 4:1 plan: swing 84.8
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

    # AAA is already the long-term pick; BBB's model scores added up to 85, but its plan has the stop above the entry
    assert [p["ticker"] for p in picks["top_swing"]] == ["CCC", "DDD"]
    good = picks["top_swing"][0]
    assert good["sentiment"] == "Neutral" and good["thesis"]
    assert good["swingScore"] == 85.8 and good["sub_scores"]["risk_reward_ratio_score"] == 28.8  # Not the model's 24
    assert (good["entry_price"], good["stop_loss"], good["price_target"], good["risk_reward"]) == (100, 96, 116, 4.0)
    from src.universal.recommendations.scorer import swing_reasoning

    bad = swing_reasoning("BBB", {"sub_scores": EVALUATIONS["BBB"]})
    assert (bad["entry_price"], bad["stop_loss"], bad["price_target"], bad["risk_reward"]) == (None,) * 4


def test_risk_reward_is_scored_from_the_plan_by_the_rubric_bands():
    from src.universal.recommendations.LLM_recommendation import plan_risk_reward_score as score

    # Top band from 2.5:1 (3:1 until 2026-10-18)
    assert [score(100, 95, 100 + 5 * ratio) for ratio in (0.5, 1, 1.5, 2, 2.5, 3.5, 4.5, 8)] == [
        0.0, 5.5, 12.0, 18.0, 25.0, 27.5, 30.0, 30.0]
    assert score(100, 104, 120) == 0.0 and score(100, 95, 99) == 0.0 and score(None, 95, 110) == 0.0  # Inconsistent
    assert score(100, 99.9, 102.5) == score(100, 99, 102.5) == 25.0  # A stop under 1% away counts as 1%: 2.5:1
    # A stop tighter than a normal day's range counts as one ATR away: APH on 2026-10-16, still falling, with its stop
    # 1.1 under the entry, scores the full 30 (5.5:1); with its ~2.4 ATR it is 2.1:1
    assert score(79.4, 78.3, 84.5) == 30.0 and score(79.4, 78.3, 84.5, atr=2.4) == 19.5
    assert score(100, 90, 125, atr=2.0) == 25.0  # A stop wider than the ATR is used as it is: 2.5:1


def test_daily_atr_is_a_normal_days_range():
    from src.universal.recommendations.LLM_recommendation import daily_atr

    bars = [{"high": 11.0, "low": 10.0, "close": 10.5}] * 6  # Newest first; each day's range is 1
    assert daily_atr(bars) == 1.0
    gap = [{"high": 13.0, "low": 12.5, "close": 12.8}] + bars  # Gapped up from 10.5: true range 13 - 10.5 = 2.5
    assert daily_atr(gap) == (2.5 + 5 * 1.0) / 6
    assert daily_atr(bars[:3]) is None and daily_atr(None) is None  # Too few bars


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
                                                 "universal_issues": [], "screening": {}}


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


# --------------------------------------------------------------------------------------------- screening counts
def test_scorer_reports_how_many_were_scored_and_cleared_each_bar(claude_scores):
    stats = {}
    select_top_candidates({symbol: {"symbol": symbol} for symbol in EVALUATIONS}, long_n=2, swing_n=1,
                          min_long_score=80, min_swing_score=80, stats=stats)
    # AAA is long 83, CCC and DDD swing 85.8 and 84.8 (BBB 60): both swing setups count, though only one is picked
    assert stats == {"sent": 4, "scored": 4, "long_qualified": 1, "swing_qualified": 2, "swing_early": 2}
    empty = {}
    select_top_candidates({}, stats=empty)
    assert empty == {"sent": 0, "scored": 0, "long_qualified": 0, "swing_qualified": 0, "swing_early": 0}


def test_filter_all_records_how_many_each_stage_kept():
    fetcher = filter_tickers.DataFetcher.__new__(filter_tickers.DataFetcher)
    fetcher.all_tickers = ["A", "B", "C", "D", "E"]
    fetcher.yahoo_filter = lambda tickers: tickers[:4]
    fetcher.finnhub_filter = lambda tickers: tickers[:2]
    fetcher.twelvedata_filter = lambda tickers: tickers[:2]
    assert fetcher.filter_all() == ["A", "B"]
    assert fetcher.stage_counts == {"screener": 5, "yahoo": 4, "finnhub": 2, "technical": 2}


def test_screener_keeps_the_listing_count_next_to_its_file(tmp_path, monkeypatch):
    from src.universal.recommendations.ticker_fetcher import Tickers

    raw = pd.DataFrame({"symbol": ["A", "B", "C"], "name": ["a", "b", "c"], "marketCapNumeric": [5e4, 2e4, 10.0],
                        "lastSaleNumeric": [50.0, 40.0, 5.0], "volumeNumeric": [2.0, 3.0, 0.1]})
    tickers = Tickers(output_dir=tmp_path)
    monkeypatch.setattr(tickers, "_fetch_from_nasdaq", lambda: raw.copy())
    path = tickers.fetch_ticker_data(mktcap_min=10_000, lastsale_min=30)
    assert len(pd.read_csv(path)) == 2 and Tickers.listing_count(path) == 3  # Counted before any filter
    assert Tickers.listing_count(tmp_path / "01-01-26-AM_x_ticker_data.csv") is None  # Not recorded
    tickers._clear_ticker_data_folder(tmp_path)
    assert list(tmp_path.iterdir()) == []  # The count goes with its file


def test_screening_counts_cover_every_stage(tmp_path, monkeypatch):
    from src.universal.recommendations.ticker_fetcher import Tickers

    csv = tmp_path / "17-10-26-AM_abc_ticker_data.csv"
    Tickers.listings_path(csv).write_text('{"listings": 7012}', encoding="utf-8")
    fetcher = SimpleNamespace(ticker_file=csv,
                              stage_counts={"screener": 635, "yahoo": 85, "finnhub": 37, "technical": 37})
    stats = {"sent": 37, "scored": 36, "long_qualified": 16, "swing_qualified": 0}
    picks = {"top_long_term": [{"ticker": "NVDA"}, {"ticker": "TSM"}], "top_swing": []}
    monkeypatch.setitem(universal.params_dict, "MIN_LONG_SCORE", 80.0)
    monkeypatch.setitem(universal.params_dict, "TWELVEDATA_FILTERS", [("adx", "1day", "<=", 45, {"time_period": 14})])
    monkeypatch.setitem(universal.params_dict, "YAHOO_FILTERS", [("averageVolume", ">=", 1_000_000),
                                                                ("recommendationMean", "<=", 2.2)])
    stages = universal.screening_counts(fetcher, stats, picks)["stages"]
    assert [(s["stage"], s["count"]) for s in stages] == [
        ("listings", 7012), ("screener", 635), ("yahoo", 85), ("finnhub", 37), ("technical", 37), ("scored", 36),
        ("long_term_score", 16), ("swing_score", 0), ("picked", 2)]
    by_stage = {s["stage"]: s for s in stages}
    assert by_stage["technical"]["rules"] == ["adx (1day) <= 45"]
    assert by_stage["yahoo"]["rules"] == ["averageVolume >= 1,000,000", "recommendationMean <= 2.2"]
    assert by_stage["scored"]["of"] == 37 and by_stage["long_term_score"]["min_score"] == 80.0
    assert (by_stage["picked"]["long_term"], by_stage["picked"]["swing"]) == (2, 0)
    # A filter stage without rules lets everything through, so it isn't listed; nor is an unrecorded listing count
    monkeypatch.setitem(universal.params_dict, "TWELVEDATA_FILTERS", [])
    fetcher.ticker_file = tmp_path / "missing_ticker_data.csv"
    stages = [s["stage"] for s in universal.screening_counts(fetcher, stats, picks)["stages"]]
    assert stages[:4] == ["screener", "yahoo", "finnhub", "scored"]


# --------------------------------------------------------------------------------------------- sector rules
def ranked(*tickers):
    return [(ticker, {"score": 100 - i}) for i, ticker in enumerate(tickers)]


SECTORS = {"T1": "Tech", "T2": "Tech", "T3": "Tech", "T4": "Tech", "H": "Health", "E": "Energy", "F": "Finance"}


def test_long_term_picks_are_all_from_different_sectors():
    from src.universal.recommendations.scorer import pick_with_sector_rule

    picks = pick_with_sector_rule(ranked("T1", "T2", "H", "E"), 2, SECTORS.get, "all_different", "long-term")
    assert [t for t, _ in picks] == ["T1", "H"]  # T2 scored higher than H but shares T1's sector
    assert [t for t, _ in pick_with_sector_rule(ranked("T1", "T2"), 2, SECTORS.get, "all_different", "x")] == ["T1"]


def test_more_than_half_of_the_swing_picks_are_from_different_sectors():
    from src.universal.recommendations.scorer import pick_with_sector_rule

    def picks(*tickers, n=5):
        return [t for t, _ in pick_with_sector_rule(ranked(*tickers), n, SECTORS.get, "majority_different", "swing")]

    # A better stock from a sector already in the list waits until another sector makes room for it
    assert picks("T1", "T2", "T3", "H", "E", n=3) == ["T1", "T2", "H"]
    assert picks("T1", "T2", "T3", "T4", "H", "E", "F") == ["T1", "T2", "T3", "H", "E"]  # 3 sectors among 5
    assert picks("T1", "T2", "T3", "H") == ["T1", "T2", "H"]  # 2 sectors allow 3 picks, not 4
    assert picks("T1", "T2", "T3") == ["T1"]  # One sector: one pick (1 of 1 is more than half)
    for chosen in (picks("T1", "T2", "H", "T3", "E", "T4", "F"), picks("H", "T1", "T2", "T3", "T4")):
        assert len({SECTORS[t] for t in chosen}) > len(chosen) / 2


def test_sector_rule_edge_cases(caplog):
    from src.universal.recommendations.scorer import candidate_sector, pick_with_sector_rule

    # A stock without a sector counts as its own, so missing data never blocks it
    inputs = {"A": {"profile": {"sector": "Tech"}}, "B": {"profile": {}}, "C": {}}
    assert [candidate_sector(t, inputs) for t in "ABC"] == ["Tech", "(no sector: B)", "(no sector: C)"]
    # An unknown rule name is reported and applies no rule
    picks = pick_with_sector_rule(ranked("T1", "T2"), 2, SECTORS.get, "all-different", "swing")
    assert [t for t, _ in picks] == ["T1", "T2"] and "Unknown PICK_SECTOR_RULES" in caplog.text


def test_selection_applies_the_configured_sector_rules(monkeypatch):
    import src.universal.recommendations.scorer as scorer

    scores = {"NVDA": (95, 40), "TSM": (93, 45), "NBIX": (86, 50), "ROST": (60, 85), "TJX": (60, 84), "BSY": (60, 83),
              "ADBE": (60, 82), "NEM": (60, 81)}
    sectors = {"NVDA": "Technology", "TSM": "Technology", "NBIX": "Healthcare", "ROST": "Consumer Cyclical",
               "TJX": "Consumer Cyclical", "BSY": "Technology", "ADBE": "Technology", "NEM": "Basic Materials"}
    monkeypatch.setattr(scorer, "run_llm_evaluation", lambda sanitized_inputs, config=None, issues=None: {
        t: {"longScore": long, "swingScore": swing, "sub_scores": {}} for t, (long, swing) in scores.items()})
    monkeypatch.setitem(scorer.params_dict, "PICK_SECTOR_RULES", {"LONG_TERM": "all_different", "SWING": "majority_different"})
    inputs = {t: {"profile": {"sector": sector}} for t, sector in sectors.items()}
    picks = scorer.select_top_candidates(inputs, long_n=2, swing_n=5, min_long_score=80, min_swing_score=80)
    assert [p["ticker"] for p in picks["top_long_term"]] == ["NVDA", "NBIX"]
    # TSM, left out of long-term, isn't a swing setup; 5 swing picks need 3 sectors: ROST, TJX, BSY, ADBE, NEM has 3
    assert [p["ticker"] for p in picks["top_swing"]] == ["ROST", "TJX", "BSY", "ADBE", "NEM"]
    monkeypatch.setitem(scorer.params_dict, "PICK_SECTOR_RULES", {"LONG_TERM": "any", "SWING": "any"})
    assert [p["ticker"] for p in scorer.select_top_candidates(inputs, long_n=2, swing_n=5, min_long_score=80,
                                                                min_swing_score=80)["top_long_term"]] == ["NVDA", "TSM"]


def test_configured_sector_rules_are_known():
    from src.parameters import params_dict
    from src.universal.recommendations.scorer import SECTOR_RULES

    assert set(params_dict["PICK_SECTOR_RULES"]) == {"LONG_TERM", "SWING"}
    assert all(rule in SECTOR_RULES for rule in params_dict["PICK_SECTOR_RULES"].values())


# --------------------------------------------------------------------------------------------- daily bars
def bars_frame(days, start=100.0):
    index = pd.bdate_range("2026-09-01", periods=days)
    closes = [start + i for i in range(days)]
    return pd.DataFrame({"Open": [c - 0.5 for c in closes], "High": [c + 1 for c in closes],
                         "Low": [c - 1 for c in closes], "Close": closes, "Volume": [1000.0 + i for i in range(days)]},
                        index=index)


def fetcher_with_bars(cache):
    fetcher = filter_tickers.DataFetcher.__new__(filter_tickers.DataFetcher)
    fetcher._daily_bars_cache = cache
    return fetcher


def test_recent_daily_bars_are_the_last_n_newest_first():
    penny = bars_frame(25)
    penny[["Open", "High", "Low", "Close"]] /= 1000.123  # A stock under $1, e.g. 0.12398
    fetcher = fetcher_with_bars({"AAA": bars_frame(30), "PENNY": penny, "SPY": bars_frame(30), "GONE": None})
    bars = fetcher.recent_daily_bars(["AAA", "PENNY", "GONE"], 21)
    assert len(bars["AAA"]) == 21 and bars["AAA"][0] == {
        "date": "2026-10-12", "open": 128.5, "high": 130.0, "low": 128.0, "close": 129.0, "volume": 1029}
    assert bars["AAA"][-1]["date"] == "2026-09-14" and bars["AAA"][-1]["close"] == 109.0  # 21 trading days back
    assert bars["PENNY"][0]["close"] == round(124 / 1000.123, 4) == 0.124  # Under $1: 4 decimals
    assert bars["PENNY"][-1]["close"] == round(104 / 1000.123, 4) == 0.104
    assert bars["GONE"] == []  # No prices from Yahoo
    assert fetcher.recent_daily_bars(["AAA"], 0) == {}  # DAILY_BARS_DAYS = 0 leaves them out


def test_daily_bars_reach_the_llm_input(monkeypatch):
    from src.universal.recommendations.sanitizer import sanitize_ticker_payload

    fetcher = fetcher_with_bars({"AAA": bars_frame(30), "SPY": bars_frame(30)})
    fetcher.fetch_all = lambda tickers: {t: {"yahoo": {"currentPrice": 129.0}, "finnhub": {}, "twelvedata": {}} for t in tickers}
    fetcher.fetch_all_news = lambda tickers: {}
    fetcher.fetch_series_data = lambda tickers: {}
    monkeypatch.setitem(filter_tickers.params_dict, "DAILY_BARS_DAYS", 5)
    packet = fetcher.congregate_LLM_input_data(["AAA"])["AAA"]
    assert [bar["date"] for bar in packet["daily_bars"]] == ["2026-10-12", "2026-10-09", "2026-10-08", "2026-10-07",
                                                              "2026-10-06"]  # Newest first, weekend skipped
    sanitized = sanitize_ticker_payload("AAA", packet)
    assert sanitized["daily_bars"] == packet["daily_bars"]
    assert sanitize_ticker_payload("BBB", {"yahoo": {}})["daily_bars"] == []  # Older cached packets have none


def test_daily_bars_setting():
    from src.parameters import params_dict

    assert params_dict["DAILY_BARS_DAYS"] == 21


# --------------------------------------------------------------------------------------------- early entries only
def run_context(lows, last_close, rsi=55.0, upper=None):
    """A context whose 10 newest bars have these lows (newest first) and a normal daily range of 1.0."""
    bars = [{"date": f"2026-10-{16 - i:02d}", "open": low + 0.5, "high": low + 1.0, "low": low, "close": low + 0.5}
            for i, low in enumerate(lows)]
    bars[0]["close"] = last_close
    bars[0]["high"] = max(bars[0]["high"], last_close)
    indicators = {"rsi_14_1day": [{"rsi": rsi}]}
    if upper is not None:
        indicators["bbands_20_1day"] = [{"upper_band": upper, "middle_band": upper - 5, "lower_band": upper - 10}]
    return {"daily_bars": bars, "technical_indicators": indicators}


def test_a_stock_too_far_along_its_run_is_named():
    from src.universal.recommendations.scorer import too_far_along

    flat = [100.0] * 12  # Lows of 100, ranges of about 1
    assert too_far_along(run_context(flat, 101.5)) is None  # 1.5 ranges off the low: still early
    assert too_far_along(run_context(flat, 105.0)).startswith("up 3.7 days")  # The jump day widens the ATR too
    assert too_far_along(run_context(flat, 101.0, rsi=74)) == "RSI 74"
    assert too_far_along(run_context(flat, 101.0, upper=100.8)) == "above its upper Bollinger Band"
    assert too_far_along({}) is None and too_far_along(None) is None  # Missing data proves nothing


def test_swing_picks_already_far_along_are_left_out(monkeypatch):
    import src.universal.recommendations.scorer as scorer

    scores = {"EARLY": (60, 85), "JUMPED": (60, 90), "HOT": (60, 88)}
    monkeypatch.setattr(scorer, "run_llm_evaluation", lambda sanitized_inputs, config=None, issues=None: {
        t: {"longScore": long, "swingScore": swing, "sub_scores": {}} for t, (long, swing) in scores.items()})
    flat = [100.0] * 12
    inputs = {"EARLY": run_context(flat, 101.5), "JUMPED": run_context(flat, 106.0), "HOT": run_context(flat, 101.0, rsi=78)}
    stats = {}
    picks = scorer.select_top_candidates(inputs, long_n=2, swing_n=5, min_long_score=80, min_swing_score=80, stats=stats)
    assert [p["ticker"] for p in picks["top_swing"]] == ["EARLY"]  # The two higher scores had already run
    assert (stats["swing_qualified"], stats["swing_early"]) == (3, 1)
    fetcher = SimpleNamespace(stage_counts={}, ticker_file=None)
    stages = {s["stage"]: s["count"] for s in universal.screening_counts(fetcher, stats, picks)["stages"]}
    assert (stages["swing_score"], stages["swing_early"]) == (3, 1)
    stats.update(swing_qualified=0, swing_early=0)  # No 80+ swing: no "still early" row
    assert "swing_early" not in {s["stage"] for s in universal.screening_counts(fetcher, stats, picks)["stages"]}
