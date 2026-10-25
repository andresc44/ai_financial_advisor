"""Claude-authored (2026-10-25, Andres): only the best long-term candidates by the rubric's own figures are scored."""
from src.universal.recommendations import long_term_screen as lts


def stock(**figures):
    return {"valuation_and_fundamentals": figures}


STRONG = dict(peg_ratio=0.8, pe_forward=10, debt_to_equity=20, current_ratio=2.0, free_cash_flow=5e9,
              revenue_growth_yoy=0.35, earnings_growth_yoy=0.4, operating_margin=0.3, gross_margin=0.7,
              return_on_equity=0.3)
WEAK = dict(peg_ratio=3.5, pe_forward=40, debt_to_equity=200, current_ratio=0.6, free_cash_flow=-1e9,
            revenue_growth_yoy=0.02, earnings_growth_yoy=0.01, operating_margin=0.02, gross_margin=0.1,
            return_on_equity=0.05)


def test_points_follow_the_rubrics_top_bands():
    assert lts.screen_points(stock(**STRONG)) == 100
    assert lts.screen_points(stock(**WEAK)) == 0
    assert lts.screen_points({}) == 50  # Nothing known counts as the middle, neither helping nor sinking a stock
    assert lts.screen_points(stock(peg_ratio=1.0)) > lts.screen_points(stock(peg_ratio=2.0)) > lts.screen_points(stock(peg_ratio=3.0))


def test_only_the_best_go_on_and_ties_keep_a_fixed_order(monkeypatch):
    monkeypatch.setitem(lts.params_dict, "LONG_TERM_SCREEN", {"MAX_CANDIDATES": 2})
    data = {"AAA": stock(**WEAK), "CCC": stock(**STRONG), "BBB": stock(**STRONG), "DDD": stock()}
    assert lts.best_long_term_candidates(["AAA", "CCC", "BBB", "DDD"], data) == ["BBB", "CCC"]
    assert lts.best_long_term_candidates(["AAA"], data) == ["AAA"]  # Fewer than the limit: all of them
    monkeypatch.setitem(lts.params_dict, "LONG_TERM_SCREEN", {"MAX_CANDIDATES": 0})
    assert lts.best_long_term_candidates(["AAA", "CCC", "BBB", "DDD"], data) == ["AAA", "CCC", "BBB", "DDD"]
