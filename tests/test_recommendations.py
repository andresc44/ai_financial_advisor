"""Holdings evaluation: key failures, reuse of recent evaluations, portfolio weights, and the track record it writes."""
import json
import threading
from datetime import datetime, timedelta, timezone
from functools import partial
from types import SimpleNamespace

import httpx2
import pandas as pd
import pytest

import src.personalized.api_errors as api_errors
import src.personalized.holdings_state as holdings_state
import src.personalized.recommendation as rec
import src.tracking.track_record as track_record
from src.personalized.pull_holdings_data import portfolio_weights

OK_MESSAGE = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5", "stop_reason": "tool_use",
    "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
    "content": [{"type": "tool_use", "id": "toolu_1", "name": "record_holding_recommendation",
                 "input": {"recommendation": "hold", "sentiment": "Neutral", "rationale": "Fine."}}],
}


def claude_error(status, error_type, message):
    return httpx2.Response(status, json={"type": "error", "error": {"type": error_type, "message": message}})


@pytest.fixture
def claude_server(monkeypatch):
    state = {"plan": None, "calls": 0}
    lock = threading.Lock()

    async def handler(request):
        with lock:
            state["calls"] += 1
            n = state["calls"]
        return state["plan"](n)

    real = rec.AsyncAnthropic
    monkeypatch.setattr(rec, "AsyncAnthropic", lambda **kw: real(
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), **kw))
    return state


PAYLOADS = {f"S{i}": {"symbol": f"S{i}"} for i in range(6)}


def test_key_failure_stops_further_requests(claude_server):
    claude_server["plan"] = lambda n: claude_error(402, "billing_error", "Billing issue")
    evaluations, key_error = rec.run_holding_evaluation(PAYLOADS, api_key="k", config={"max_concurrency": 2}, user_name="Andres")
    assert evaluations == {} and key_error.reason == "no_credits"
    assert claude_server["calls"] <= 2  # At most one round of in-flight requests


def test_mid_run_spend_limit_keeps_finished_evaluations(claude_server):
    claude_server["plan"] = lambda n: (
        httpx2.Response(200, json=OK_MESSAGE) if n <= 2
        else claude_error(400, "invalid_request_error", "You have reached your specified workspace API usage limits.")
    )
    evaluations, key_error = rec.run_holding_evaluation(PAYLOADS, api_key="k", config={"max_concurrency": 1}, user_name="Andres")
    assert sorted(evaluations) == ["S0", "S1"] and key_error.reason == "spend_limit"
    assert claude_server["calls"] == 3


def test_non_key_errors_do_not_stop_the_run(claude_server):
    claude_server["plan"] = lambda n: claude_error(400, "invalid_request_error", "per-request problem")
    _, key_error = rec.run_holding_evaluation(PAYLOADS, api_key="k", config={"max_concurrency": 2}, user_name="Andres")
    assert key_error is None and claude_server["calls"] == 6


# --------------------------------------------------------------------------------------------- add_recommendations
@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "watchdog_state.json"
    monkeypatch.setattr(rec, "reusable_evaluations", partial(holdings_state.reusable_evaluations, path=path))
    monkeypatch.setattr(rec, "record_evaluations", partial(holdings_state.record_evaluations, path=path))
    return path


@pytest.fixture
def fake_claude(monkeypatch):
    sent = []

    def evaluate(payloads, api_key, config=None, market_session=None, user_name=None, source=None):
        sent.append(sorted(payloads))
        return {s: {"recommendation": "risk_alert", "sentiment": "Bearish", "rationale": "new"} for s in payloads}, None

    monkeypatch.setattr(rec, "run_holding_evaluation", evaluate)
    monkeypatch.setattr(rec, "resolve_llm_key", lambda user, provider="Anthropic": "k")
    return sent


def holdings(symbols=("AAA", "BBB", "CCC", "DDD"), prices=(100.5, 50.0, 21.0, 10.0)):
    return pd.DataFrame({"symbol": list(symbols), "current_price": list(prices), "natr": [2.0] * len(symbols),
                         "market_value": [1.0] * len(symbols), "market_context": [None] * len(symbols),
                         "currency": ["USD"] * len(symbols)})


def test_digest_reuses_recent_stable_evaluations(state_file, fake_claude):
    now = datetime.now(timezone.utc)
    holdings_state.save_state({"users": {"Andres": {"symbols": {
        "AAA": {"evaluated_at": (now - timedelta(minutes=10)).isoformat(), "price": 100.0, "recommendation": "hold",
                "sentiment": "Bullish", "rationale": "fresh"},
        "BBB": {"evaluated_at": (now - timedelta(minutes=90)).isoformat(), "price": 50.0, "recommendation": "trim"},
        "CCC": {"evaluated_at": (now - timedelta(minutes=5)).isoformat(), "price": 20.0, "recommendation": "hold"},
    }}}}, state_file)
    fetcher = SimpleNamespace(user_name="Andres", llm_settings={"model": "claude-opus-5"})

    out = rec.add_recommendations(fetcher, holdings(), reuse_recent_evaluations=True)

    assert fake_claude[-1] == ["BBB", "CCC", "DDD"]  # Too old, moved >= 1 NATR, never evaluated
    assert out.set_index("symbol").loc["AAA", ["recommendation", "sentiment"]].tolist() == ["hold", "Bullish"]
    saved = holdings_state.load_state(state_file)["users"]["Andres"]["symbols"]
    assert saved["AAA"]["evaluated_at"] == (now - timedelta(minutes=10)).isoformat()  # Reuse doesn't refresh it
    assert saved["DDD"]["sentiment"] == "Bearish"

    logged = track_record.load_records(track_record.PERSONAL_PATH)
    assert sorted(logged["symbol"]) == ["BBB", "CCC", "DDD"]  # Only new evaluations
    assert set(logged["source"]) == {"digest"} and set(logged["model"]) == {"claude-opus-5"}


def test_watchdog_never_reuses(state_file, fake_claude):
    rec.add_recommendations(SimpleNamespace(user_name="Andres", llm_settings=None), holdings())
    assert fake_claude[-1] == ["AAA", "BBB", "CCC", "DDD"]
    assert set(track_record.load_records(track_record.PERSONAL_PATH)["source"]) == {"watchdog"}


def test_missing_key_leaves_recommendations_empty(state_file, monkeypatch):
    def missing(user, provider="Anthropic"):
        raise api_errors.ApiKeyUnusable("Anthropic", "missing", user)

    monkeypatch.setattr(rec, "resolve_llm_key", missing)
    fetcher = SimpleNamespace(user_name="Andres", llm_settings=None)
    out = rec.add_recommendations(fetcher, holdings())
    assert fetcher.llm_key_error.reason == "missing"
    assert out["recommendation"].isna().all()


# --------------------------------------------------------------------------------------------- portfolio weights
def test_weights_use_the_whole_portfolio_even_for_a_subset():
    full = pd.DataFrame({"symbol": ["A", "A", "B", "C"], "market_value": [100.0, 100.0, 600.0, 200.0],
                         "market_context": [None] * 4})
    full = full.assign(portfolio_weight_pct=portfolio_weights(full))
    payload = rec.build_holding_payloads(full[full["symbol"] == "A"])["A"]
    assert payload["portfolio_weight_pct"] == 20.0
    assert "portfolio_weight_pct" not in payload["positions"][0]


def test_weights_without_column_fall_back_to_the_subset():
    df = pd.DataFrame({"symbol": ["A", "B"], "market_value": [25.0, 75.0], "market_context": [None, None]})
    assert rec.build_holding_payloads(df)["B"]["portfolio_weight_pct"] == 75.0


def test_holdings_labels_and_older_ones():
    from pydantic import ValidationError

    from src.personalized.LLM_hold_instr import RECOMMENDATIONS, current_recommendation

    assert RECOMMENDATIONS == ("urgent_sell", "risky", "profit_sell", "buy_more", "hold")
    for label in RECOMMENDATIONS:
        rec.HoldingEvaluation(recommendation=label, sentiment="Neutral", rationale="x")
    with pytest.raises(ValidationError):  # Labels from before 2026-10-18 are no longer accepted from the model
        rec.HoldingEvaluation(recommendation="trim", sentiment="Neutral", rationale="x")
    assert [current_recommendation(v) for v in ("risk_alert", "trim", "hold", None)] == ["risky", "profit_sell", "hold", None]
    schema = json.dumps(rec.EVALUATION_TOOL_SPEC["input_schema"])
    assert all(label in schema for label in RECOMMENDATIONS) and "risk_alert" not in schema


def test_reused_evaluations_come_back_in_todays_labels(state_file):
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=10)).isoformat()
    holdings_state.save_state({"users": {"Andres": {"symbols": {  # Saved before 2026-10-18
        "AAA": {"evaluated_at": recent, "price": 100.0, "recommendation": "risk_alert"},
        "BBB": {"evaluated_at": recent, "price": 50.0, "recommendation": "trim"},
    }}}}, state_file)
    reused = holdings_state.reusable_evaluations("Andres", holdings(("AAA", "BBB"), (100.0, 50.0)), now, 60, 1.0,
                                                 path=state_file)
    assert {symbol: record["recommendation"] for symbol, record in reused.items()} == {"AAA": "risky", "BBB": "profit_sell"}
