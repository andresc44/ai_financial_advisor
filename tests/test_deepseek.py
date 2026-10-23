"""DeepSeek as a drop-in for Claude: requests, parsing, key failures, per-user settings, costs, and config checks."""
import json
from datetime import datetime, timezone

import httpx
import pytest

import src.llm.deepseek_client as ds
import src.llm.llm_models as llm_models
import src.personalized.api_errors as api_errors
import src.personalized.recommendation as rec
import src.universal.recommendations.LLM_recommendation as universal_llm
import src.tracking.usage_tracking as usage
from src.config.check_config import check_user
from src.personalized.user_settings import get_llm_settings
from src.universal.recommendations.scorer import select_top_candidates


def completion(arguments=None, content=None, finish="tool_calls", name=rec.EVALUATION_TOOL_NAME):
    message = {"role": "assistant", "content": content}
    if arguments is not None:
        message["tool_calls"] = [{"id": "call_1", "type": "function",
                                  "function": {"name": name, "arguments": json.dumps(arguments)}}]
    return {"id": "x", "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": finish, "message": message}],
            "usage": {"prompt_tokens": 1200, "prompt_cache_hit_tokens": 200, "prompt_cache_miss_tokens": 1000,
                      "completion_tokens": 300, "completion_tokens_details": {"reasoning_tokens": 0}}}


@pytest.fixture
def deepseek_server(monkeypatch):
    """Routes DeepSeekClient (both evaluators) to a mock transport; `plan(n, body)` returns each response."""
    state = {"plan": None, "requests": []}

    def handler(request):
        body = json.loads(request.content)
        state["requests"].append(body)
        status, payload = state["plan"](len(state["requests"]), body)
        return httpx.Response(status, json=payload)

    real = ds.DeepSeekClient
    for module in (rec, universal_llm):
        monkeypatch.setattr(module, "DeepSeekClient", lambda api_key: real(api_key=api_key, transport=httpx.MockTransport(handler)))

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(ds.asyncio, "sleep", no_wait)
    return state


# --------------------------------------------------------------------------------------------- requests
def test_request_without_thinking_forces_the_tool():
    body = ds.build_request("deepseek-flash", "none", "system", "user", rec.EVALUATION_TOOL_SPEC)
    assert body["thinking"] == {"type": "disabled"} and "reasoning_effort" not in body
    assert body["tool_choice"] == {"type": "function", "function": {"name": rec.EVALUATION_TOOL_NAME}}
    assert body["tools"][0]["function"]["parameters"]["properties"]["recommendation"]["enum"]


def test_request_with_thinking_lets_the_model_choose():
    body = ds.build_request("deepseek-v4-pro", "high", "system", "user", universal_llm.LONG_TERM_TOOL_SPEC)
    assert body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "high"
    assert body["tool_choice"] == "auto"  # A forced tool choice returns 400 in thinking mode
    text = json.dumps(body["tools"])
    assert "$ref" not in text and "$defs" not in text  # Nested schemas are inlined
    assert "thesis" in body["tools"][0]["function"]["parameters"]["properties"]


def test_tool_arguments_from_a_call_or_from_json_text():
    assert ds.tool_call_arguments(completion({"a": 1}), rec.EVALUATION_TOOL_NAME).arguments == {"a": 1}
    text = completion(content='```json\n{"a": 2}\n```', finish="stop")
    assert ds.tool_call_arguments(text, rec.EVALUATION_TOOL_NAME).arguments == {"a": 2}
    cut = ds.tool_call_arguments(completion(finish="length"), rec.EVALUATION_TOOL_NAME)
    assert cut.arguments is None and "max_tokens" in cut.problem


# --------------------------------------------------------------------------------------------- holdings on DeepSeek
HOLD = {"recommendation": "profit_sell", "sentiment": "Bearish", "rationale": "RSI 74 after a 30% run."}
PAYLOADS = {f"S{i}": {"symbol": f"S{i}"} for i in range(4)}
DEEPSEEK = {"model": "deepseek-flash", "effort": "none", "max_concurrency": 1}


def test_holdings_evaluated_on_deepseek_with_costs(deepseek_server):
    deepseek_server["plan"] = lambda n, body: (503, {"error": {"message": "busy"}}) if n == 1 else (200, completion(HOLD))
    evaluations, key_error = rec.run_holding_evaluation(PAYLOADS, api_key="ds-key", config=DEEPSEEK, user_name="Andres")
    assert key_error is None and set(evaluations) == set(PAYLOADS) and evaluations["S0"]["recommendation"] == "profit_sell"
    assert len(deepseek_server["requests"]) == 5  # One retry after the 503
    assert all(body["model"] == "deepseek-flash" for body in deepseek_server["requests"])
    rows = [json.loads(line) for line in usage.LEDGER_PATH.read_text().splitlines()]
    assert {(r["provider"], r["model"], r["user"]) for r in rows} == {("deepseek", "deepseek-flash", "Andres")}
    assert (rows[0]["input_tokens"], rows[0]["cache_read_tokens"], rows[0]["output_tokens"]) == (1000, 200, 300)
    assert rows[0]["cost_usd"] > 0


@pytest.mark.parametrize("status, reason", [(402, "no_credits"), (401, "invalid")])
def test_deepseek_key_failure_stops_the_run(deepseek_server, status, reason):
    deepseek_server["plan"] = lambda n, body: (status, {"error": {"message": "Insufficient Balance"}})
    evaluations, key_error = rec.run_holding_evaluation(PAYLOADS, api_key="ds-key", config=DEEPSEEK, user_name="Andres")
    assert evaluations == {} and key_error.provider == "DeepSeek" and key_error.reason == reason
    assert len(deepseek_server["requests"]) == 1  # The rest were never sent
    assert "platform.deepseek.com" in str(key_error)  # Where to top up or create a new key


def test_answers_that_dont_fit_the_schema_are_asked_once_more_then_skipped(deepseek_server):
    deepseek_server["plan"] = lambda n, body: (200, completion({"recommendation": "sell_half", "sentiment": "Bullish", "rationale": "x"}))
    evaluations, key_error = rec.run_holding_evaluation({"S0": {}}, api_key="ds-key", config=DEEPSEEK, user_name="Andres")
    assert evaluations == {} and key_error is None
    assert len(deepseek_server["requests"]) == 2  # The first answer and one retry
    rows = [json.loads(line) for line in usage.LEDGER_PATH.read_text().splitlines()]
    assert len(rows) == 2  # Both were billed


def test_an_invalid_answer_is_replaced_by_the_retry(deepseek_server):
    answers = {1: completion(content="I think it's a hold.", finish="stop"), 2: completion(HOLD)}
    deepseek_server["plan"] = lambda n, body: (200, answers[n])
    evaluations, _ = rec.run_holding_evaluation({"S0": {}}, api_key="ds-key", config=DEEPSEEK, user_name="Andres")
    assert evaluations["S0"]["recommendation"] == "profit_sell" and len(deepseek_server["requests"]) == 2


def test_a_cut_off_answer_is_not_asked_again(deepseek_server):
    deepseek_server["plan"] = lambda n, body: (200, completion(finish="length"))
    evaluations, _ = rec.run_holding_evaluation({"S0": {}}, api_key="ds-key", config=DEEPSEEK, user_name="Andres")
    assert evaluations == {} and len(deepseek_server["requests"]) == 1  # It would be cut off again


# --------------------------------------------------------------------------------------------- universal on DeepSeek
LONG = {"valuation_score": 22, "financial_health_score": 22, "growth_momentum_score": 20, "competitive_moat_score": 20,
        "sentiment": "Bullish", "thesis": "t", "key_risk": "r"}
SWING = {"technical_setup_score": 26, "momentum_volume_score": 17, "catalyst_sentiment_score": 17,
         "sentiment": "Neutral", "thesis": "s"}
SETUP = {"setup": "pullback", "facts": {"atr": 2.0},
         "plan": {"entry_price": 100, "stop_loss": 96, "price_target": 116, "risk_reward": 4.0}}


def answer_by_tool(answers):
    """A deepseek_server plan answering each request with the arguments for the tool it offers."""
    return lambda n, body: (200, completion(answers[body["tools"][0]["function"]["name"]],
                                            name=body["tools"][0]["function"]["name"]))


def test_universal_scoring_on_deepseek(deepseek_server, monkeypatch):
    monkeypatch.setitem(universal_llm.params_dict, "LLM", {"UNIVERSAL": {"model": "deepseek-v4-pro", "effort": "high"},
                                                           "HOLDINGS": {"model": "claude-sonnet-5-5", "effort": "medium"}})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-shared")
    deepseek_server["plan"] = answer_by_tool({universal_llm.LONG_TERM_TOOL_NAME: LONG, universal_llm.SWING_TOOL_NAME: SWING})
    picks = select_top_candidates({"AAA": {"symbol": "AAA"}}, long_n=2, swing_n=5, min_long_score=80,  # 84 < the 85 bar
                                  swing_setups={"AAA": SETUP})
    assert [(p["ticker"], p["model"], p["longScore"]) for p in picks["top_long_term"]] == [("AAA", "deepseek-v4-pro", 84)]
    # 2026-10-20: the swing call adds the plan's risk/reward (4:1, 30 since 2026-10-22) to the model's three sub-scores
    assert [(p["ticker"], p["swingScore"]) for p in picks["top_swing"]] == [("AAA", 90.0)]
    assert all(request["reasoning_effort"] == "high" for request in deepseek_server["requests"])
    assert len(deepseek_server["requests"]) == 2  # One call per horizon


def test_misshapen_answers_are_repaired_from_the_schema():
    # Seen live from deepseek-flash with the combined schema used until 2026-10-20: a section nested inside its sibling
    # (the swing section inside long_term: ABNB, AVGO, INTU, GOOGL) ...
    schema = {"type": "object", "additionalProperties": False, "required": ["long_term", "swing"],
              "properties": {"long_term": universal_llm.LONG_TERM_TOOL_SPEC["input_schema"],
                             "swing": universal_llm.SWING_TOOL_SPEC["input_schema"]}}
    fixed, changes = ds.repair_arguments({"long_term": {**LONG, "swing": SWING}}, schema)
    assert fixed == {"long_term": LONG, "swing": SWING} and changes == ["moved long_term.swing to swing"]
    # ... and keys the schema doesn't have (V, WDC; still seen on 2026-10-19, e.g. "growth_momentum_score_note")
    long_schema = universal_llm.LONG_TERM_TOOL_SPEC["input_schema"]
    fixed, changes = ds.repair_arguments({**LONG, "valuation_score_note": "", "x": 1}, long_schema)
    assert fixed == LONG and changes == ["dropped valuation_score_note", "dropped x"]
    # A valid answer is left alone, and the original is never changed
    original = dict(LONG)
    assert ds.repair_arguments(original, long_schema) == (original, []) and original == LONG
    # Values aren't touched: an out-of-range score still fails validation
    fixed, _ = ds.repair_arguments({**LONG, "valuation_score": 99}, long_schema)
    assert fixed["valuation_score"] == 99


def test_universal_scoring_repairs_extra_keys_without_asking_again(deepseek_server, monkeypatch):
    monkeypatch.setitem(universal_llm.params_dict, "LLM", {"UNIVERSAL": {"model": "deepseek-flash", "effort": "none"},
                                                           "HOLDINGS": {"model": "claude-sonnet-5-5", "effort": "medium"}})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-shared")
    deepseek_server["plan"] = answer_by_tool({universal_llm.LONG_TERM_TOOL_NAME: {**LONG, "growth_momentum_score_note": ""}})
    picks = select_top_candidates({"AAA": {"symbol": "AAA"}}, long_n=2, swing_n=5, min_long_score=80)
    assert [(p["ticker"], p["longScore"]) for p in picks["top_long_term"]] == [("AAA", 84)]
    assert len(deepseek_server["requests"]) == 1


# --------------------------------------------------------------------------------------------- key check
@pytest.mark.parametrize("status, body, reason", [(401, {"error": "Authentication Fails"}, "invalid"),
                                                  (200, {"is_available": False}, "no_credits")])
def test_free_balance_check(monkeypatch, status, body, reason):
    monkeypatch.setattr(ds, "check_balance", lambda api_key: (status, body))
    with pytest.raises(api_errors.ApiKeyUnusable) as raised:
        api_errors.verify_llm_key("DeepSeek", f"ds-{reason}", "Andres")
    assert raised.value.reason == reason


def test_funded_key_is_checked_once(monkeypatch):
    calls = []
    monkeypatch.setattr(ds, "check_balance", lambda api_key: calls.append(api_key) or (200, {"is_available": True}))
    api_errors.verify_llm_key("DeepSeek", "ds-funded", "Andres")
    api_errors.verify_llm_key("DeepSeek", "ds-funded", "Andres")
    assert calls == ["ds-funded"]


# --------------------------------------------------------------------------------------------- per-user settings
@pytest.fixture
def clean_env(monkeypatch):
    for key in ("HOLDINGS_LLM_MODEL", "HOLDINGS_LLM_EFFORT", "HOLDINGS_CLAUDE_MODEL", "HOLDINGS_CLAUDE_EFFORT",
                "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "HOLDINGS_SOURCE"):
        monkeypatch.delenv(f"ANDRES_{key}", raising=False)
    return monkeypatch


@pytest.mark.parametrize("env", [
    {},
    {"HOLDINGS_LLM_MODEL": "deepseek-v4-pro"},
    {"HOLDINGS_LLM_MODEL": "deepseek-flash", "HOLDINGS_LLM_EFFORT": "high"},
    {"HOLDINGS_CLAUDE_MODEL": "claude-opus-5-5", "HOLDINGS_CLAUDE_EFFORT": "high"},
])
def test_every_user_gets_the_holdings_model_from_parameters(clean_env, env):
    # 2026-10-22 (Andres): LLM["HOLDINGS"] for everyone (the tests' conftest sets it to Sonnet); per-user keys are ignored
    for key, value in env.items():
        clean_env.setenv(f"ANDRES_{key}", value)
    settings = get_llm_settings("Andres")
    assert (settings["provider"], settings["model"], settings["effort"]) == ("Anthropic", "claude-sonnet-5-5", "medium")


def test_check_config_requires_the_key_the_holdings_model_needs(clean_env):
    clean_env.setenv("ANDRES_HOLDINGS_SOURCE", "SNAPTRADE")
    clean_env.setenv("ANDRES_HOLDINGS_LLM_MODEL", "deepseek-flash")  # Ignored: LLM["HOLDINGS"] is a Claude model
    rows = {key: status for status, key, _ in check_user("Andres")}
    assert rows["ANDRES_ANTHROPIC_API_KEY"] == "MISSING" and rows["ANDRES_DEEPSEEK_API_KEY"] == "unset"



# --------------------------------------------------------------------------------------------- universal model in .env
UNIVERSAL_KEYS = {"UNIVERSAL": ("UNIVERSAL_LLM_MODEL", "UNIVERSAL_LLM_EFFORT")}


@pytest.fixture
def universal_env(monkeypatch):
    monkeypatch.setattr(llm_models, "ENV_OVERRIDES", UNIVERSAL_KEYS)  # conftest turns them off for other tests
    for key in UNIVERSAL_KEYS["UNIVERSAL"]:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.mark.parametrize("env, expected", [
    ({}, ("Anthropic", "claude-sonnet-5-5", "medium")),                                    # LLM["UNIVERSAL"]
    ({"UNIVERSAL_LLM_MODEL": "deepseek-flash"}, ("DeepSeek", "deepseek-flash", "low")),      # Not parameters.py's "medium"
    ({"UNIVERSAL_LLM_MODEL": "deepseek-flash", "UNIVERSAL_LLM_EFFORT": "HIGH"}, ("DeepSeek", "deepseek-flash", "high")),
    ({"UNIVERSAL_LLM_EFFORT": "low"}, ("Anthropic", "claude-sonnet-5-5", "low")),          # Effort alone
    ({"UNIVERSAL_LLM_MODEL": "gpt-9"}, ("Anthropic", "claude-sonnet-5-5", "medium")),       # Not allowed: parameters.py
    ({"UNIVERSAL_LLM_MODEL": "deepseek-flash", "UNIVERSAL_LLM_EFFORT": "medium"}, ("DeepSeek", "deepseek-flash", "low")),
])
def test_universal_model_and_effort_come_from_env_first(universal_env, env, expected):
    for key, value in env.items():
        universal_env.setenv(key, value)
    settings = llm_models.job_settings("UNIVERSAL")
    assert (settings["provider"], settings["model"], settings["effort"]) == expected
    assert llm_models.job_settings("HOLDINGS")["model"] == "claude-sonnet-5-5"  # Holdings are set per user instead


def test_check_config_flags_an_unusable_universal_override(universal_env, capsys):
    import src.config.check_config as check_config

    universal_env.setattr(check_config, "load_dotenv", lambda *args, **kwargs: None)
    universal_env.setenv("UNIVERSAL_LLM_MODEL", "deepseek-flash")
    universal_env.setenv("UNIVERSAL_LLM_EFFORT", "medium")  # A Claude effort
    check_config.run_check()
    lines = {line.split()[1]: line.split()[0] for line in capsys.readouterr().out.splitlines()
             if line.strip().split()[1:2] in (["UNIVERSAL_LLM_MODEL"], ["UNIVERSAL_LLM_EFFORT"])}
    assert lines == {"UNIVERSAL_LLM_MODEL": "OK", "UNIVERSAL_LLM_EFFORT": "INVALID"}


# --------------------------------------------------------------------------------------------- costs
def test_deepseek_peak_and_off_peak_prices():
    tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_tokens": 1_000_000}
    monday_peak = datetime(2026, 10, 19, 7, 30, tzinfo=timezone.utc)
    monday_off = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    saturday = datetime(2026, 10, 17, 7, 30, tzinfo=timezone.utc)
    assert usage.deepseek_cost("deepseek-flash", tokens, monday_peak) == pytest.approx(0.30 + 1.20 + 0.006)
    assert usage.deepseek_cost("deepseek-flash", tokens, monday_off) == pytest.approx(0.15 + 0.60 + 0.003)
    assert usage.deepseek_cost("deepseek-v4-pro", tokens, saturday) == pytest.approx(0.66 + 1.98 + 0.022)


def test_usage_summary_has_a_deepseek_block():
    usage.record_deepseek_response("Andres", "digest", "deepseek-flash", completion(HOLD))
    summary = usage.usage_summary("Andres", datetime(2026, 1, 1, tzinfo=timezone.utc))["this_digest"]
    assert summary["deepseek"]["calls"] == 1 and "search_queries" not in summary["deepseek"]
    assert summary["total_cost_usd"] == pytest.approx(summary["deepseek"]["cost_usd"])
