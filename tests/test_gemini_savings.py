"""
Cheaper Gemini use (2026-10-23): one thinking level for every Gemini call, the portfolio review written by the
holdings model instead of Gemini, and holding news reused later the same day. Written by Claude (Claude Opus 5.5 via
Claude Code), at Andres's request.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx2
import pytest
from google.genai import types

import src.personalized.news_reuse as news_reuse
import src.personalized.personalized_consolidated as pc
import src.personalized.portfolio_review as review
import src.universal.market_news as market_news
from src import gemini_settings
from src.parameters import params_dict
from src.personalized.symbol_specific_news import SymbolNewsItem

MORNING = datetime(2026, 10, 23, 11, 10, tzinfo=timezone.utc)  # 07:10 US Eastern
EVENING = datetime(2026, 10, 23, 20, 10, tzinfo=timezone.utc)  # 16:10 US Eastern
NEXT_DAY = datetime(2026, 10, 24, 11, 10, tzinfo=timezone.utc)


# --------------------------------------------------------------------------------------------- Gemini settings
def test_every_gemini_request_gets_the_project_wide_effort_and_output_cap(monkeypatch):
    monkeypatch.setitem(params_dict, "GEMINI_CONFIG", {"effort": "low", "max_output_tokens": 4096})
    config = gemini_settings.generation_config(system_instruction="x")
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW and config.max_output_tokens == 4096
    monkeypatch.setitem(params_dict, "GEMINI_CONFIG", {"effort": "default", "max_output_tokens": None})
    config = gemini_settings.generation_config()
    assert config.thinking_config is None and config.max_output_tokens is None  # The model decides, as before
    monkeypatch.setitem(params_dict, "GEMINI_CONFIG", {"effort": "Lots"})
    assert gemini_settings.gemini_effort() == "default"  # Not a Gemini level: left to the model


def test_the_macro_news_request_uses_the_gemini_settings(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-shared")
    monkeypatch.setitem(params_dict, "GEMINI_CONFIG", {"effort": "minimal", "max_output_tokens": 2000})
    monkeypatch.setitem(params_dict, "UNIVERSAL_EXTRAS", {**params_dict["UNIVERSAL_EXTRAS"], "MACRO_NEWS_STORIES": 1})
    seen = {}
    body = {"macro_stories": [{"priority": "HIGH", "event_summary": "Fed held.", "market_impact": "Yields fell.",
                               "source_url": None}] * 3}
    response = SimpleNamespace(text=json.dumps(body), usage_metadata=None, candidates=[])

    def create(model, config):
        seen.update(model=model, config=config)
        return SimpleNamespace(send_message=lambda prompt: response)

    monkeypatch.setattr(market_news.genai, "Client", lambda api_key: SimpleNamespace(chats=SimpleNamespace(create=create)))
    assert len(market_news.fetch_live_macro_stories().macro_stories) == 1  # The story count is read when used
    assert seen["config"].thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    assert seen["config"].max_output_tokens == 2000 and "top 1 major" in seen["config"].system_instruction


# --------------------------------------------------------------------------------------------- news reuse
def row(symbol="AAA", price=100.0, natr=2.0, recommendation="hold", earnings="2026-11-05"):
    return {"symbol": symbol, "current_price": price, "natr": natr, "recommendation": recommendation,
            "next_earnings_date": earnings}


def item(text="Beat estimates."):
    return SymbolNewsItem(priority="HIGH", summary=text, source_url=None)


def test_news_from_earlier_today_is_reused_and_older_news_is_not():
    news_reuse.remember_news("Andres", {"AAA": [item()], "BBB": []}, [row(), row("BBB")], now=MORNING)
    reused = news_reuse.same_day_news("Andres", ["AAA", "BBB", "CCC"], now=EVENING)
    assert set(reused) == {"AAA", "BBB"} and news_reuse.as_items(reused["AAA"])[0].summary == "Beat estimates."
    assert news_reuse.same_day_news("Andres", ["AAA"], now=NEXT_DAY) == {}  # Only the same US Eastern day
    assert news_reuse.same_day_news("Steven", ["AAA"], now=EVENING) == {}  # Each user's own


def test_reuse_can_be_switched_off(monkeypatch):
    news_reuse.remember_news("Andres", {"AAA": [item()]}, [row()], now=MORNING)
    monkeypatch.setitem(params_dict["DIGEST"], "NEWS_REUSE", {"ENABLED": False, "MAX_MOVE_NATR": 1.0})
    assert news_reuse.same_day_news("Andres", ["AAA"], now=EVENING) == {}


@pytest.mark.parametrize("now, reason", [
    (row(price=101.9), None),                                   # 1.9% move, under 1 NATR (2%)
    (row(price=97.5), "moved 2.5%, more than 2.0%"),            # A move of more than its NATR
    (row(recommendation="risky"), "signal changed from hold to risky"),
    (row(earnings="2027-02-05"), "it has reported earnings"),   # A new date means it reported
    (row(natr=None, price=None), "can't measure its move"),
])
def test_reused_news_is_fetched_again_when_the_stock_moved_changed_signal_or_reported(now, reason):
    entry = {"price": 100.0, "natr": 2.0, "recommendation": "hold", "next_earnings_date": "2026-11-05"}
    assert news_reuse.refresh_reason(entry, now, "2026-10-23") == reason


def test_a_report_due_that_day_is_always_fetched_again():
    entry = {"price": 100.0, "natr": 2.0, "recommendation": "hold", "next_earnings_date": "2026-10-23"}
    assert news_reuse.refresh_reason(entry, row(earnings="2026-10-23"), "2026-10-23") == "it has reported earnings"


def test_the_evening_digest_only_asks_gemini_for_what_changed(monkeypatch):
    monkeypatch.setattr(news_reuse, "_market_date", lambda moment: "2026-10-23")  # Morning and evening: today
    news_reuse.remember_news("Andres", {"AAA": [item("Morning AAA.")], "BBB": [item("Morning BBB.")],
                                        "CCC": [item("Morning CCC.")]},
                             [row(), row("BBB"), row("CCC")], now=MORNING)
    reused = news_reuse.same_day_news("Andres", ["AAA", "BBB", "CCC"], now=EVENING)
    asked = []

    def fetch(user_name, companies):
        asked.append(sorted(companies))
        return {"BBB": [item("Evening BBB.")]}, None  # CCC's request fails: its morning news stays

    monkeypatch.setattr(pc, "fetch_holdings_news", fetch)
    evening_rows = [row(), row("BBB", price=90.0), row("CCC", recommendation="risky")]
    news, key_error = pc.settle_holdings_news("Andres", {"AAA": "Alpha", "BBB": "Beta", "CCC": "Gamma"}, reused,
                                              {}, None, evening_rows)
    assert asked == [["BBB", "CCC"]] and key_error is None
    assert {symbol: items[0].summary for symbol, items in news.items()} == {
        "AAA": "Morning AAA.", "BBB": "Evening BBB.", "CCC": "Morning CCC."}
    saved = news_reuse.load_news("Andres")
    assert saved["BBB"]["price"] == 90.0 and saved["AAA"]["price"] == 100.0  # Reused news keeps the morning's entry


# --------------------------------------------------------------------------------------------- portfolio review
HOLDINGS = [{"symbol": "AAA", "market_value": 600.0, "recommendation": "hold"},
            {"symbol": "BBB", "market_value": 400.0, "recommendation": "risky"}]


@pytest.fixture
def claude_review(monkeypatch):
    state = {"requests": [], "response": None}

    def handler(request):
        state["requests"].append(json.loads(request.content))
        return state["response"]

    real = review.Anthropic
    monkeypatch.setattr(review, "Anthropic", lambda **kw: real(
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)), max_retries=0, **kw))
    return state


def review_message(notes, stop_reason="tool_use"):
    return httpx2.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5", "stop_reason": stop_reason,
        "stop_sequence": None, "usage": {"input_tokens": 1000, "output_tokens": 200},
        "content": [{"type": "tool_use", "id": "toolu_1", "name": review.REVIEW_TOOL_NAME, "input": {"notes": notes}}]})


def test_the_portfolio_review_is_written_by_the_holdings_model(claude_review, monkeypatch):
    monkeypatch.setitem(params_dict, "LLM", {**params_dict["LLM"], "HOLDINGS": {"model": "claude-haiku-5-5", "effort": "low"}})
    claude_review["response"] = review_message([
        {"category": "WATCH", "title": "BBB flagged risky", "text": "40% sits in BBB.", "symbols": ["bbb", "ZZZ"]}])
    started = datetime.now(timezone.utc)
    notes, key_error = review.review_portfolio(HOLDINGS, 1.1, "a-key", user_name="Andres")
    assert key_error is None
    assert [(note.category, note.symbols) for note in notes] == [("WATCH", ["BBB"])]  # Only held tickers
    sent = claude_review["requests"][0]
    assert sent["model"] == "claude-haiku-5-5" and sent["output_config"] == {"effort": "low"}
    assert sent["tools"][0]["strict"] and sent["max_tokens"] == params_dict["DIGEST"]["PORTFOLIO_REVIEW_MAX_TOKENS"]
    import src.usage_tracking as usage
    assert usage.usage_summary("Andres", started)["this_digest"]["anthropic"]["calls"] == 1  # On the user's bill


def test_a_review_cut_off_or_refused_is_left_out_and_a_bad_key_is_reported(claude_review):
    claude_review["response"] = review_message([], stop_reason="max_tokens")
    assert review.review_portfolio(HOLDINGS, 1.1, "a-key", user_name="Andres") == ([], None)
    claude_review["response"] = httpx2.Response(401, json={"type": "error", "error": {
        "type": "authentication_error", "message": "invalid x-api-key"}})
    notes, key_error = review.review_portfolio(HOLDINGS, 1.1, "a-key", user_name="Andres")
    assert notes == [] and key_error.provider == "Anthropic" and key_error.reason == "invalid"


def test_the_review_skips_a_key_the_holdings_evaluation_found_broken(monkeypatch):
    monkeypatch.setattr(pc, "review_portfolio", lambda *args, **kwargs: pytest.fail("review requested"))
    monkeypatch.setattr(pc, "get_llm_api_key", lambda user, provider: "a")
    issues = [{"provider": "Anthropic", "reason": "no_credits"}]
    assert pc.get_portfolio_review("Andres", HOLDINGS, 1.0, llm_key_issues=issues) == ([], None)
    monkeypatch.setattr(pc, "review_portfolio", lambda *args, **kwargs: ([], None))
    rate_limited = [{"provider": "Anthropic", "reason": "rate_limited"}]  # Passing: still tried
    assert pc.get_portfolio_review("Andres", HOLDINGS, 1.0, llm_key_issues=rate_limited) == ([], None)
