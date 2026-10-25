# Claude-authored (2026-10-22): second looks at scores that could go either way, and the leaner email
# (scorer.rescore_borderline, recommendation's re-reviews, create_html's news scaling, glossary, and style classes)
import asyncio

import pytest

import src.universal.recommendations.scorer as scorer
from src.digest import create_html as ch
from src.digest.glossary import terms_in_use
from src.config.parameters import params_dict
from src.personalized.LLM_hold_instr import current_recommendation  # noqa: F401  (the labels' module)
from src.personalized.recommendation import EVALUATION_TOOL_NAME, HoldingEvaluation, ParallelHoldingEvaluator, majority_review
from tests.test_create_html import TEMPLATE_DIR, glossary_in_the_email, holding, payload  # noqa: F401  (autouse fixture)


@pytest.fixture
def rescoring(monkeypatch):
    monkeypatch.setitem(params_dict, "IDEA_RESCORE", {"ABOVE_MIN": 5.0, "BELOW_MIN": 5.0, "EXTRA_RUNS": 2})


def runs(*results):
    """A re-scorer that returns each of `results` in turn ({ticker: score}), and remembers what it was asked."""
    asked = []

    def rescore(tickers):
        asked.append(list(tickers))
        return {t: {"longScore": s, "run": len(asked)} for t, s in results[len(asked) - 1].items() if t in tickers}
    return rescore, asked


def test_borderline_scores_keep_the_middle_of_three(rescoring):
    scores = {"A": {"longScore": 90.0}, "B": {"longScore": 82.0, "run": 0}, "C": {"longScore": 77.0, "run": 0},
              "D": {"longScore": 70.0}, "E": {"longScore": 86.0}}
    rescore, asked = runs({"B": 78.0, "C": 81.0}, {"B": 84.0, "C": 79.0})
    out = scorer.rescore_borderline(scores, "longScore", 3, 80.0, rescore, "long-term")
    assert asked == [["B", "C"], ["B", "C"]]  # 75-85 only: D is too low to reach 80, A and E are clear
    assert (out["B"]["longScore"], out["B"]["run"]) == (82.0, 0)  # 78, 82, 84 -> the first score stands
    assert (out["C"]["longScore"], out["C"]["run"]) == (79.0, 2)  # 77, 79, 81 -> the second re-score's run
    assert out["A"] is scores["A"] and out["D"] is scores["D"]


def test_no_rescoring_when_the_list_already_has_enough_clear_scores(rescoring):
    scores = {"A": {"longScore": 90.0}, "E": {"longScore": 86.0}, "B": {"longScore": 82.0}}
    rescore, asked = runs()
    # Two above 85 and a list of 2 (LONG_TERM_N_AMOUNT): nothing to re-score
    assert scorer.rescore_borderline(scores, "longScore", 2, 80.0, rescore, "long-term") == scores and asked == []
    # A list of 5 (SWING_TERM_N_AMOUNT) with only two clear scores is re-scored
    rescore, asked = runs({"B": 83.0}, {"B": 81.0})
    scorer.rescore_borderline({k: dict(v, swingScore=v["longScore"]) for k, v in scores.items()}, "swingScore", 5, 80.0,
                              lambda t: {k: {"swingScore": v["longScore"]} for k, v in rescore(t).items()}, "swing")
    assert asked == [["B"], ["B"]]


def test_a_failed_rescore_keeps_the_lower_middle(rescoring):
    rescore, _ = runs({"B": 88.0}, {})  # The second re-score failed
    out = scorer.rescore_borderline({"B": {"longScore": 81.0}}, "longScore", 2, 80.0, rescore, "long-term")
    assert out["B"]["longScore"] == 81.0  # 81 and 88: the lower one


def test_the_band_follows_each_lists_minimum(rescoring):
    # Swing's minimum of 75: 70-80 is re-scored, and 80+ is clear (2026-10-22)
    scores = {t: {"swingScore": s} for t, s in {"A": 82.0, "B": 79.0, "C": 71.0, "D": 69.0}.items()}
    asked = []
    scorer.rescore_borderline(scores, "swingScore", 5, 75.0, lambda t: asked.append(list(t)) or {}, "swing")
    assert asked == [["B", "C"], ["B", "C"]]


def test_the_scorer_rescores_both_lists_with_their_own_pick_counts(monkeypatch):
    calls = []
    monkeypatch.setattr(scorer, "rescore_borderline",
                        lambda scores, key, picks, minimum, rescore, label: calls.append((key, picks, minimum)) or scores)
    monkeypatch.setattr(scorer, "horizon_payload", lambda data, horizon, setup=None: data)
    monkeypatch.setattr(scorer, "run_llm_evaluation", lambda sanitized_inputs, config=None, issues=None, horizon="": {
        t: {"longScore" if horizon == "long_term" else "swingScore": 70.0} for t in sanitized_inputs})
    scorer.select_top_candidates({"AAA": {}, "BBB": {}}, long_n=2, swing_n=5, min_long_score=80, min_swing_score=80,
                                 long_tickers=["AAA"], swing_setups={"BBB": {"setup": "pullback"}})
    assert calls == [("longScore", 2, 80), ("swingScore", 5, 80)]


def review(label, rationale=""):
    return HoldingEvaluation(recommendation=label, sentiment="Neutral", rationale=rationale or label)


def test_the_majority_label_wins_and_ties_go_to_the_milder_one():
    assert majority_review([review("urgent_sell"), review("urgent_sell", "second"), review("risky")]).rationale == (
        "urgent_sell")  # The first review with the winning label, so its rationale matches
    assert majority_review([review("urgent_sell"), review("risky"), review("hold")]).recommendation == "risky"
    assert majority_review([review("urgent_sell"), review("hold")]).recommendation == "hold"  # One failed re-review
    assert majority_review([review("profit_sell"), review("hold"), review("hold")]).recommendation == "hold"


@pytest.mark.parametrize("first, later, expected, reviews", [
    ("urgent_sell", ["risky", "risky"], "risky", 3),     # A one-off urgent sell: no alert
    ("urgent_sell", ["urgent_sell", "hold"], "urgent_sell", 3),
    ("profit_sell", ["hold", "profit_sell"], "profit_sell", 3),
    ("risky", [], "risky", 1),                           # Any other first review stands
    ("hold", [], "hold", 1),
])
def test_urgent_and_profit_sells_are_reviewed_twice_more(monkeypatch, first, later, expected, reviews):
    monkeypatch.setitem(params_dict, "HOLDINGS_RECHECK", {"SIGNALS": ["urgent_sell", "profit_sell"], "EXTRA_REVIEWS": 2})
    evaluator = ParallelHoldingEvaluator(api_key="sk-ant-test", user_name="Tess", source="digest")
    answers = iter([review(first)] + [review(label) for label in later])
    asked = []

    async def fake_review(symbol, prompt, cache_prompt=False):
        asked.append((symbol, cache_prompt))
        return next(answers)

    monkeypatch.setattr(evaluator, "_review", fake_review)
    result = asyncio.run(evaluator._evaluate_single_holding("SWK", {"symbol": "SWK"}))
    assert result.recommendation == expected and len(asked) == reviews
    # 2026-10-24: only the re-reviews, which send the same prompt again, cache it
    assert [cached for _, cached in asked] == [False] + [True] * (reviews - 1)


def test_big_portfolios_get_one_story_per_stock_except_flagged_ones():
    def cards(n):
        return [{"signal_key": "risky" if i == 0 else "urgent_sell" if i == 1 else "hold",
                 "news": [{"priority": "HIGH", "text": "a"}, {"priority": "MEDIUM", "text": "b"}]} for i in range(n)]
    small = cards(params_dict["DIGEST"]["NEWS_FULL_UP_TO_HOLDINGS"])
    ch._scale_news(small)
    assert all(len(c["news"]) == 2 for c in small)
    big = cards(params_dict["DIGEST"]["NEWS_FULL_UP_TO_HOLDINGS"] + 1)
    ch._scale_news(big)
    assert [len(c["news"]) for c in big[:3]] == [2, 2, 1] and big[2]["news"][0]["text"] == "a"  # The most important
    # Trimming cuts the flagged stocks' news last
    view = {"holdings": cards(3)}
    ch._top_news_only(view)
    assert [len(c["news"]) for c in view["holdings"]] == [2, 2, 1]
    ch._flagged_top_news_only(view)
    assert [len(c["news"]) for c in view["holdings"]] == [1, 1, 1]
    names = [step.__name__ for step in ch.TRIM_STEPS]
    assert names.index("_no_hold_news") < names.index("_flagged_top_news_only")


def test_the_glossary_keeps_only_the_terms_the_digest_uses():
    categories = [{"category": "Signals", "terms": [("Hold", "h"), ("Urgent sell", "u")]},
                  {"category": "Market", "terms": [("Dow Jones (DIA)", "d"), ("RSI 14", "r"), ("EBITDA", "e")]}]
    # "Holdings" isn't "Hold", "Downtrend" isn't the Dow
    assert terms_in_use(categories, "Your holdings · Downtrend · RSI 14 55") == [
        {"category": "Market", "terms": [("RSI 14", "r")]}]
    view = ch.build_view(payload())
    ch.fit_for_gmail(view, TEMPLATE_DIR)
    terms = {term for category in view["glossary"] for term, _ in category["terms"]}
    assert "RSI 14" in terms and "Hold" in terms and "EBITDA" not in terms and "Leveraged ETF (2x, 3x)" not in terms


def test_badges_and_dots_name_classes_instead_of_inline_colours():
    view = ch.build_view(payload(holdings=[holding("AAA", recommendation="risky"), holding("BBB")]))
    badges, dots = ch.style_classes(view)
    risky = next(card["signal"] for card in view["holdings"] if card["symbol"] == "AAA")
    html = ch.render_html(view, TEMPLATE_DIR)
    cls = badges[(risky["bg"], risky["fg"])]
    assert f'<span class="bd {cls}">Risky</span>' in html
    assert f".{cls}{{background-color:{risky['bg']};color:{risky['fg']};}}" in html
    assert 'class="dot" style=' not in html and 'class="bd" style="background-color' not in html
    assert ch.render_html(view, TEMPLATE_DIR, amp=True).count(f'class="bd {cls}"') >= 1


def test_claude_evaluations_share_one_cached_copy_of_their_instructions(monkeypatch):
    # Claude-authored (2026-10-24): the system prompt (and the tools before it) is cached, and the first holding runs
    # alone so the others read it instead of all writing it at once
    from types import SimpleNamespace

    evaluator = ParallelHoldingEvaluator(api_key="sk-ant-test", user_name="Tess", source="digest")
    events, requests = [], []

    async def fake_create(**request):
        symbol = request["messages"][0]["content"].split(" in ")[1].split(" ")[0]
        requests.append(request)
        events.append(("start", symbol))
        await asyncio.sleep(0.01)
        events.append(("end", symbol))
        tool = SimpleNamespace(type="tool_use", name=EVALUATION_TOOL_NAME,
                               input={"recommendation": "hold", "sentiment": "Neutral", "rationale": "Fine."})
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0, iterations=None)
        return SimpleNamespace(stop_reason="end_turn", content=[tool], usage=usage, model=request["model"],
                               stop_details=None)

    monkeypatch.setattr(evaluator.client.messages, "create", fake_create)
    monkeypatch.setattr(evaluator.client.beta.messages, "create", lambda **r: fake_create(**r))
    payloads = {s: {"symbol": s} for s in ("AAA", "BBB", "CCC")}
    results = asyncio.run(evaluator.evaluate_holdings(payloads))
    assert set(results) == {"AAA", "BBB", "CCC"}
    assert events[:2] == [("start", "AAA"), ("end", "AAA")]  # The first finished before any other started
    system = requests[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"} and system[0]["text"] == evaluator.config["system_prompt"]
    assert all(r["system"] == system for r in requests)  # Byte-identical, or nothing is read back
    assert isinstance(requests[0]["messages"][0]["content"], str)  # A stock's own prompt isn't cached

    monkeypatch.setitem(params_dict, "CLAUDE_PROMPT_CACHE", False)  # Switched off: as before
    requests.clear()
    asyncio.run(evaluator.evaluate_holdings(payloads))
    assert all(r["system"] == evaluator.config["system_prompt"] for r in requests)
