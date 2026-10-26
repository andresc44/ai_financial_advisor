"""Claude-authored (2026-10-26): the model comparison tool (src/llm/compare_models.py): its metrics, and its score and report steps."""
import json

import pytest

import src.llm.compare_models as cm


def test_settings_parse_and_slug():
    assert cm.parse_setting("claude-haiku-5-5:low") == ("claude-haiku-5-5", "low")
    assert cm.parse_setting(" gemini-3.5-flash-lite ") == ("gemini-3.5-flash-lite", None)
    with pytest.raises(ValueError):
        cm.parse_setting(":low")
    assert cm.slug("claude-haiku-5-5:low") == "claude-haiku-5-5-low"


def test_a_setting_that_cant_run_a_job_is_named():
    assert cm.setting_problem("holdings", "claude-haiku-5-5", "low") is None
    assert cm.setting_problem("long_term", "deepseek-flash", "high") is None
    assert "ALLOWED_CLAUDE_MODELS" in cm.setting_problem("holdings", "claude-next", "low")
    assert "accepts the efforts" in cm.setting_problem("holdings", "claude-haiku-5-5", "turbo")
    assert cm.setting_problem("news", "gemini-3.5-flash-lite", None) is None
    assert "isn't a Gemini model" in cm.setting_problem("news", "claude-haiku-5-5", "low")


def test_score_metrics():
    ref = {"A": 90.0, "B": 80.0, "C": 70.0, "D": 60.0, "E": 40.0}
    same = dict(ref)
    assert cm.rank_correlation(ref, same) == pytest.approx(1.0)
    assert cm.rank_correlation(ref, {t: -v for t, v in ref.items()}) == pytest.approx(-1.0)
    assert cm.rank_correlation({"A": 1.0, "B": 2.0}, {"A": 1.0, "B": 2.0}) is None  # Too few stocks
    assert cm.score_gap(ref, {t: v + 2 for t, v in ref.items()}) == (2.0, 2.0)
    assert cm.score_gap(ref, {t: v + (4 if t in "AB" else -2) for t, v in ref.items()}) == (pytest.approx(2.8), pytest.approx(0.4))

    # Pass / fail at 70: the reference passes A, B, C; a candidate passing A, B, D finds 2 of 3 and agrees on 2 of 3
    cand = {"A": 90.0, "B": 75.0, "C": 65.0, "D": 71.0, "E": 10.0}
    assert cm.pass_agreement(ref, cand, 70) == pytest.approx(2 / 3)
    assert cm.pass_agreement(ref, same, 70) == pytest.approx(1.0)
    assert cm.pass_agreement({"A": 10.0}, {"A": 20.0}, 70) is None  # Nobody passes
    assert cm.pass_agreement(ref, {t: 0.0 for t in ref}, 70) == 0.0
    # The candidate's own top 3 are A, B, D: 2 of the reference's 3
    assert cm.same_size_overlap(ref, cand, 70) == pytest.approx(2 / 3)
    assert cm.top_n_overlap(ref, cand, 2) == pytest.approx(1.0) and cm.top_n_overlap(ref, cand, 3) == pytest.approx(2 / 3)


def test_label_comparison():
    ref = {"A": "hold", "B": "urgent_sell", "C": "profit_sell", "D": "buy_more"}
    cand = {"A": "risky", "B": "urgent_sell", "C": "hold", "D": "profit_sell"}
    m = cm.label_comparison(ref, cand)
    assert m["agree"] == 0.25
    assert m["steps"] == pytest.approx((2 + 0 + (-1) + 2) / 4)  # More cautious is positive
    assert m["sell_caught"] == 0.5  # Of the reference's sells (B, C), only B
    assert m["false_sells"] == 0.25  # D: a sell where the reference isn't
    assert cm.label_comparison(ref, {})["agree"] is None


def test_runs_are_compared_pairwise_and_the_reference_against_itself():
    ref_runs = [{"A": 90.0, "B": 80.0, "C": 70.0}, {"A": 92.0, "B": 78.0, "C": 70.0}]
    cand_runs = [{"A": 90.0, "B": 80.0, "C": 70.0}]
    m = cm.compare_idea_runs(ref_runs, cand_runs, (70,), 2)
    assert m["gap"] == pytest.approx((0 + 1.33333) / 2, abs=1e-3)
    own = cm.compare_idea_runs(ref_runs, ref_runs, (70,), 2, same=True)  # One pair: run 1 against run 2
    assert own["gap"] == pytest.approx(4 / 3, abs=1e-3)
    assert cm.compare_idea_runs([ref_runs[0]], [ref_runs[0]], (70,), 2, same=True) == {}  # One run: nothing to compare


@pytest.fixture
def results(tmp_path, monkeypatch):
    """Two settings, two runs of long-term scoring and one run of holdings, written by the score step with the LLM stubbed."""
    inputs, out = tmp_path / "inputs", tmp_path / "out"
    inputs.mkdir()
    (inputs / "ideas_long_term.json").write_text(json.dumps({t: {} for t in "ABCD"}))
    (inputs / "holdings_Ann.json").write_text(json.dumps({"X": {}, "Y": {}}))
    calls = []

    def run_job(job, model, effort, inputs_dir, user):
        calls.append((job, model, effort, user))
        if job == "long_term":
            bump = 5 if model == "other" else 0
            return {"results": {t: {"longScore": s + bump} for t, s in zip("ABCD", (90, 80, 70, 60))}, "inputs": 4}
        label = "hold" if model == "ref" else "risky"
        return {"results": {s: {"recommendation": label, "sentiment": "Neutral", "rationale": "x"} for s in "XY"}, "inputs": 2}

    monkeypatch.setattr(cm, "_run_job", run_job)
    # The made-up models of these tests aren't in ALLOWED_*; only "claude-next" is refused (test_a_setting_that_cant_...)
    monkeypatch.setattr(cm, "setting_problem",
                        lambda job, model, effort: "claude-next isn't in ALLOWED (stub)" if model == "claude-next" else None)
    return inputs, out, calls


def test_score_saves_each_run_and_skips_finished_ones(results, capsys):
    inputs, out, calls = results
    args = ["score", "--inputs", str(inputs), "--out", str(out), "--setting", "ref:medium", "--setting", "other:low",
            "--jobs", "long_term,holdings", "--runs", "2"]
    assert cm.main(args) == 0
    assert len(calls) == 8  # 2 settings x 2 runs x (long-term + Ann's holdings)
    assert ("holdings", "other", "low", "Ann") in calls and ("long_term", "ref", "medium", None) in calls
    saved = json.loads((out / "results" / "ref-medium__long_term__run1.json").read_text())
    assert saved["setting"] == "ref:medium" and saved["run"] == 1 and saved["inputs"] == 4 and "cost_usd" in saved
    calls.clear()
    cm.main(args)  # Resumed: everything is there
    assert calls == [] and "done already" in capsys.readouterr().out
    # A setting the job can't use is skipped with the reason, not sent off
    cm.main(["score", "--inputs", str(inputs), "--out", str(out), "--setting", "claude-next:low", "--jobs", "holdings",
             "--runs", "1"])
    assert calls == [] and "skipped: claude-next isn't in ALLOWED" in capsys.readouterr().out


def test_report_compares_settings_with_the_reference(results):
    inputs, out, _ = results
    cm.main(["score", "--inputs", str(inputs), "--out", str(out), "--setting", "ref:medium", "--setting", "other:low",
             "--jobs", "long_term,holdings", "--runs", "2"])
    text = cm.report(cm.argparse.Namespace(results=str(out), reference="ref:medium", marks="70"))
    assert "Reference: ref:medium" in text and "## long-term ideas" in text and "## Holdings" in text
    other = next(line for line in text.splitlines() if line.startswith("| other:low") and "$" in line and "Answered" not in line)
    assert "+5.0" in other  # Scores 5 points above the reference
    holdings_other = [line for line in text.splitlines() if line.startswith("| other:low")][-1]
    assert "0%" in holdings_other and "+2.0" in holdings_other  # Never the reference's label; two steps more cautious
    assert (out / "report.md").read_text() == text


def test_report_copes_with_a_single_run(results):
    # A reference with one run has nothing to be compared with itself (shown as "-"); found on the first live run
    inputs, out, _ = results
    cm.main(["score", "--inputs", str(inputs), "--out", str(out), "--setting", "ref:medium", "--setting", "other:low",
             "--jobs", "long_term,holdings", "--runs", "1"])
    text = cm.report(cm.argparse.Namespace(results=str(out), reference="ref:medium", marks="70"))
    ref_row = next(line for line in text.splitlines() if line.startswith("| ref:medium") and "Same label" not in line)
    assert ref_row.count("| - |") >= 1 and "## Holdings" in text
